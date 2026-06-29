"""RegistryWriterMixin for HBOSerializer (see serializer.py). Split out for organization."""
import struct
from lib.type_code_mapper import map_type_code_v11_to_v10


class RegistryWriterMixin:
    @staticmethod
    def _reg_sig(name, fields):
        return (name or "", tuple((fn, tc) for fn, tc, _ in fields))

    def _collect_reg_typedefs(self, root):
        """Depth-first collect of every distinct (name, [(field,tcode)]) typedef, in
        first-seen order, so objects can reference their type by table index."""
        order = []          # list of (name, fields) preserving the field tuples
        sigs = {}           # sig -> index
        def add(name, fields):
            s = self._reg_sig(name, fields)
            if s not in sigs:
                sigs[s] = len(order)
                order.append((name, fields))
        def visit_obj(obj, depth=0):
            if not obj or obj[1] is None or depth > self.MAX_RECURSION:
                return
            name, fields = obj
            if name == "" and not fields:
                return
            add(name, fields)
            for _fn, _tc, val in fields:
                visit_val(val, depth + 1)
        def visit_val(val, depth):
            if not val:
                return
            if val[0] == "object":
                if not self._is_null_obj(val):
                    visit_obj(val[1], depth)
            elif val[0] == "array":
                for e in val[1][1]:
                    if e and e[0] == "object" and not self._is_null_obj(e):
                        visit_obj(e[1], depth)
        visit_obj(root)
        return order, sigs

    def _write_reg_typedef(self, dst, name, fields):
        dst.write(struct.pack('<I', 0xFFFFFFFF))
        nb = (name or "").encode('utf-8', 'replace')
        dst.write(struct.pack('<I', len(nb))); dst.write(nb)
        dst.write(struct.pack('<I', len(fields)))
        for fname, tcode, _value in fields:
            fb = fname.encode('utf-8', 'replace')
            dst.write(struct.pack('<I', len(fb))); dst.write(fb)
            dst.write(struct.pack('<I', tcode))

    def _reg_typeref(self, dst, name, fields, emitted):
        """Write a type reference: inline typedef (0xFFFFFFFF + def) on first occurrence
        of this signature, else its index. Mirrors the parser's append-on-inline order."""
        sig = self._reg_sig(name, fields)
        idx = emitted.get(sig)
        if idx is None:
            emitted[sig] = len(emitted)
            self._write_reg_typedef(dst, name, fields)        # 0xFFFFFFFF + def (appends)
        else:
            dst.write(struct.pack('<I', idx))                 # back-reference by index

    def _write_reg_object(self, dst, obj, emitted, depth):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded (registry write)")
        if obj is None or obj[1] is None or (obj[0] == "" and not obj[1]):
            dst.write(struct.pack('<I', 0))                   # end_offset 0 == null
            return
        name, fields = obj
        off_pos = dst.tell()
        dst.write(struct.pack('<I', 0))                       # end_offset placeholder
        self._reg_typeref(dst, name, fields, emitted)
        for fname, tcode, value in fields:
            self._write_reg_value(dst, tcode, value, emitted, depth + 1)
        end = dst.tell()                                      # absolute byte past this object
        dst.seek(off_pos); dst.write(struct.pack('<I', end)); dst.seek(end)

    def _write_reg_value(self, dst, tcode, value, emitted, depth):
        kind = value[0] if value else "null"
        if tcode in (0x12, 0x14):
            if self._is_null_obj(value):
                dst.write(struct.pack('<I', 0))
            else:
                self._write_reg_object(dst, value[1], emitted, depth)
        elif tcode == 0x13:
            elems = value[1][1] if kind == "array" else []
            dst.write(struct.pack('<I', len(elems)))
            for e in elems:
                self._write_reg_object(dst, (e[1] if e[0] == "object" else None), emitted, depth)
        elif tcode == 0x11:
            elems = value[1][1] if kind == "array" else []
            dst.write(struct.pack('<I', len(elems)))
            if elems:
                first = next((e[1] for e in elems if e[0] == "object" and not self._is_null_obj(e)), None)
                # one shared element typedef (inline first use / index), then packed values
                self._reg_typeref(dst, first[0] if first else "", first[1] if first else [], emitted)
                order = [(fn, tc) for fn, tc, _ in (first[1] if first else [])]
                for e in elems:
                    ef = dict(((fn, (tc, v)) for fn, tc, v in (e[1][1] if (e[0] == "object" and e[1]) else [])))
                    for fn, tc in order:
                        if fn in ef:
                            self._write_reg_value(dst, ef[fn][0], ef[fn][1], emitted, depth + 1)
                        else:
                            self._write_reg_value(dst, tc, ("primitive", tc, b"\x00" * self.PRIMITIVE_SIZES.get(tc, 0)), emitted, depth + 1)
        elif tcode == 0x10:
            b = value[1] if kind == "string" else b""
            dst.write(struct.pack('<I', len(b))); dst.write(b)
        else:
            if kind == "primitive":
                dst.write(value[2])
            else:
                dst.write(b"\x00" * self.PRIMITIVE_SIZES.get(tcode, 0))

    def _map_entry_type_code(self, type_name, v11_code):
        if self.ver_check != 1:
            return v11_code
        override = self.V10_ENTRY_TYPE_OVERRIDES.get(type_name)
        if override is not None:
            return override
        mapped = map_type_code_v11_to_v10(type_name, v11_code)
        if mapped is None:
            return v11_code
        return mapped

