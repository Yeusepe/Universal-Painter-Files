"""ReaderMixin for HBOSerializer (see serializer.py). Split out for organization."""
import struct
from .models import MemberDef, ObjectDef
try:
    from .hbo_parser import find_all_dict_entries, refine_dict_boundaries
except Exception:
    find_all_dict_entries = None
    refine_dict_boundaries = None


class ReaderMixin:
    def _read_u32(self, src):
        data = src.read(4)
        if len(data) != 4:
            raise ValueError("Unexpected EOF while reading u32")
        return struct.unpack('<I', data)[0]

    def _read_u8(self, src):
        data = src.read(1)
        if len(data) != 1:
            raise ValueError("Unexpected EOF while reading u8")
        return data[0]

    def _read_string(self, src):
        length = self._read_u32(src)
        if length == 0:
            return ""
        data = src.read(length)
        if len(data) != length:
            raise ValueError("Unexpected EOF while reading string")
        return data.decode('utf-8', errors='replace')

    def _guard_count(self, src, count, max_array_items):
        # A count read from the stream drives a per-element loop. The old "hang"
        # was a misparsed primitive (e.g. a float) read as a 500k array count
        # that sat under MAX_ARRAY_ITEMS and spun for minutes. Two hard ceilings:
        # the absolute cap, and remaining bytes (every element needs >=1 byte, so
        # any count larger than what's left in the buffer is always a misparse).
        max_items = self.MAX_ARRAY_ITEMS if max_array_items is None else int(max_array_items)
        if count > max_items:
            raise ValueError(f"Array item count {count} exceeds limit {max_items}")
        try:
            remaining = len(src.getbuffer()) - src.tell()
        except Exception:
            remaining = None
        if remaining is not None and count > remaining:
            raise ValueError(f"Array item count {count} exceeds remaining bytes {remaining}")
        return count

    def _parse_v11_type_def(self, src, registry):
        idx = self._read_u32(src)
        if idx != 0xFFFFFFFF:
            if idx >= len(registry):
                if idx < len(self.registry_defs):
                    return self.registry_defs[idx]
                raise ValueError(f"Type index out of range: {idx}")
            return registry[idx]

        name = self._read_string(src)
        member_count = self._read_u32(src)
        members = []
        for _ in range(member_count):
            m_name = self._read_string(src)
            m_type = self._read_u32(src)
            members.append((m_name, m_type))

        type_def = {'name': name, 'members': members}
        registry.append(type_def)
        return type_def

    def _parse_v11_value(self, src, type_code, registry, entries, depth=0):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        if type_code in (0x12, 0x14):
            return ("object", self._parse_v11_object(src, registry, entries, depth + 1))
        if type_code == 0x13:
            count = self._guard_count(src, self._read_u32(src), None)
            values = []
            for _ in range(count):
                start_pos = src.tell()
                values.append(("object", self._parse_v11_object(src, registry, entries, depth + 1)))
                if src.tell() == start_pos:
                    raise ValueError("No progress while parsing v11 array (0x13)")
            return ("array", ("object", values))
        if type_code == 0x11:
            count = self._guard_count(src, self._read_u32(src), None)
            values = []
            if count:
                elem_def = self._parse_v11_type_def(src, registry)
                for _ in range(count):
                    start_pos = src.tell()
                    values.append(("object", self._parse_v11_object_with_def(src, registry, elem_def, entries, depth + 1)))
                    if src.tell() == start_pos:
                        raise ValueError("No progress while parsing v11 array (0x11)")
            return ("array", ("object", values))

        if type_code == 0x10:
            s = self._read_string(src)
            return ("string", s.encode('utf-8'))
        if type_code == 0x0A:
            return ("primitive", 0x0A, bytes([self._read_u8(src)]))
        if type_code == 0x09:
            raw = src.read(4)
            if len(raw) != 4:
                raise ValueError("Unexpected EOF while reading type 9")
            return ("primitive", 0x09, raw)
        if type_code == 0x0F:
            raw = src.read(8)
            if len(raw) != 8:
                raise ValueError("Unexpected EOF while reading type 15")
            return ("primitive", 0x0F, raw)
        if type_code == 0x15:
            raw = src.read(8)
            if len(raw) != 8:
                raise ValueError("Unexpected EOF while reading type 21")
            return ("primitive", 0x15, raw)

        size = self.FIELD_PRIMITIVE_SIZES.get(type_code, 0)
        if size:
            raw = src.read(size)
            if len(raw) != size:
                raise ValueError("Unexpected EOF while reading primitive")
            return ("primitive", type_code, raw)

        raise ValueError(f"Unhandled v11 type code: {type_code}")

    def _parse_v11_object_with_def(self, src, registry, type_def, entries, depth=0):
        obj = []
        for m_name, m_type in type_def['members']:
            value = self._parse_v11_value(src, m_type, registry, entries, depth + 1)
            obj.append((m_name, m_type, value))
        if type_def['name']:
            entries.append((type_def['name'], obj))
        return (type_def['name'], obj)

    def _parse_v11_object(self, src, registry, entries, depth=0):
        flag = self._read_u32(src)
        if flag == 0:
            return ("", [])
        type_def = self._parse_v11_type_def(src, registry)
        return self._parse_v11_object_with_def(src, registry, type_def, entries, depth + 1)

    def _parse_v11_registry_table(self, src, type_count):
        if type_count <= 0:
            return None
        start = src.tell()
        try:
            first = self._read_u32(src)
            if first != 0xFFFFFFFF:
                src.seek(start)
                return None
            src.seek(start)
            registry = []
            for _ in range(type_count):
                idx = self._read_u32(src)
                if idx != 0xFFFFFFFF:
                    src.seek(start)
                    return None
                name = self._read_string(src)
                member_count = self._read_u32(src)
                members = []
                for _ in range(member_count):
                    m_name = self._read_string(src)
                    m_type = self._read_u32(src)
                    members.append((m_name, m_type))
                registry.append({'name': name, 'members': members})
            return registry
        except Exception:
            src.seek(start)
            return None

    def _parse_inline_native(self):
        """Parse an inline (v8/v9/v10) source into the native tree shape the transform
        pipeline + writers consume: (name, [(field, tcode, value), ...]) where value is
        ('object',(n,f)|None) | ('array',(elem_kind,[...])) | ('string',b) | ('primitive',code,b).
        Field tcode = the inline value tag, so writers reproduce array/object tags faithfully."""
        from lib.hbo_decode import INLINE_SIZES
        d = self.data
        p = [12]

        def u8():
            v = d[p[0]]; p[0] += 1; return v

        def u16():
            v = struct.unpack_from('<H', d, p[0])[0]; p[0] += 2; return v

        def u32():
            v = struct.unpack_from('<I', d, p[0])[0]; p[0] += 4; return v

        def name(ln):
            s = d[p[0]:p[0] + ln].decode('utf-8', 'replace'); p[0] += ln; return s

        def obj():
            u8(); u32(); nm = name(u32()); fc = u16()
            fields = []
            for _ in range(fc):
                fn = name(u32())
                tag, val = value()
                fields.append((fn, tag, val))
            return (nm, fields)

        def value():
            tag = u8()
            if tag == 0x10:
                ln = u32(); b = d[p[0]:p[0] + ln]; p[0] += ln
                return tag, ("string", b)
            if tag in (0x12, 0x14):
                if d[p[0]] == 0xFF:
                    p[0] += 1; return tag, ("object", ("", None))   # null -> writer emits 0xFF
                return tag, ("object", obj())
            if tag in (0x13, 0x11):
                u32(); c = u32(); elems = []; elem_kind = "object"
                for _ in range(c):
                    e = u8()
                    if e in (0x12, 0x14):
                        if d[p[0]] == 0xFF:
                            p[0] += 1; elems.append(("object", ("", None)))
                        else:
                            elems.append(("object", obj()))
                    elif e == 0x10:
                        elem_kind = "string"; ln = u32(); elems.append(("string", d[p[0]:p[0] + ln])); p[0] += ln
                    else:
                        elem_kind = "primitive"; sz = INLINE_SIZES.get(e, 0)
                        elems.append(("primitive", e, d[p[0]:p[0] + sz])); p[0] += sz
                return tag, ("array", (elem_kind, elems))
            if tag == 0x00:
                return tag, ("primitive", 0x00, b"")
            sz = INLINE_SIZES.get(tag, 0)
            b = d[p[0]:p[0] + sz]; p[0] += sz
            return tag, ("primitive", tag, b)

        if u8() != 0x12:
            raise ValueError("inline stream does not start with a root object")
        return obj()

    def _parse_entry_headers(self):
        if self.ver_check != 1 or find_all_dict_entries is None:
            return
        try:
            entries = find_all_dict_entries(self.data)
            if refine_dict_boundaries is not None:
                entries = refine_dict_boundaries(self.data, entries)
            if self.data_start < len(self.data):
                entries = [e for e in entries if e.length_prefix_offset >= self.data_start]
            self.entry_headers = entries
            for entry in entries:
                if entry.type_code not in self.code_to_name and entry.type_name in self.type_map:
                    self.code_to_name[entry.type_code] = entry.type_name
        except Exception:
            self.entry_headers = []

    def _parse_v11_registry(self):
        # Start scanning for names and definitions
        curr = 12
        # Skip the unknown words at 12, 16, 20
        # Wait, if it's v11, header is usually 28 bytes
        if len(self.data) >= 28:
            curr = 28
            # Look for names until first marker
            while curr + 8 <= len(self.data):
                if self.data[curr:curr+4] == b'\xff\xff\xff\xff': break
                l = struct.unpack('<I', self.data[curr:curr+4])[0]
                if l > 256 or curr + 8 + l > len(self.data): break
                name = self.data[curr+4:curr+4+l].decode('utf-8', errors='replace')
                tc = struct.unpack('<I', self.data[curr+4+l:curr+8+l])[0]
                self.code_to_name[tc] = name
                if not self.root_type_code: self.root_type_code = tc
                curr += 8 + l

        # Look for definition blocks (preceded by FFFFFFFF)
        marker_positions = []
        pos = self.data.find(b'\xff\xff\xff\xff', 12)
        while pos != -1:
            marker_positions.append(pos)
            pos = self.data.find(b'\xff\xff\xff\xff', pos + 4)

        max_end = self.data_start
        for marker in marker_positions:
            curr_def = marker + 4
            if curr_def + 8 > len(self.data):
                continue
            name_len = struct.unpack('<I', self.data[curr_def:curr_def+4])[0]
            if name_len > 256 or curr_def + 4 + name_len > len(self.data):
                continue
            name = self.data[curr_def+4:curr_def+4+name_len].decode('utf-8', errors='replace')
            m_count = struct.unpack('<I', self.data[curr_def+4+name_len:curr_def+8+name_len])[0]
            if m_count > 1000:
                continue

            obj = ObjectDef(name)
            ptr = curr_def + 8 + name_len
            valid = True
            for _ in range(m_count):
                if ptr + 8 > len(self.data):
                    valid = False
                    break
                ml = struct.unpack('<I', self.data[ptr:ptr+4])[0]
                if ml > 256 or ptr + 8 + ml > len(self.data):
                    valid = False
                    break
                mn = self.data[ptr+4:ptr+4+ml].decode('utf-8', errors='replace')
                mt = struct.unpack('<I', self.data[ptr+4+ml:ptr+8+ml])[0]
                obj.members.append(MemberDef(mn, mt))
                ptr += 8 + ml
            if not valid:
                continue

            self.registry_defs.append({'name': name, 'members': [(m.name, m.type_code) for m in obj.members]})
            if name and name[0].isupper():
                self.type_map[name] = obj
                if ptr > max_end:
                    max_end = ptr

        if max_end < len(self.data):
            self.data_start = max_end

    def _read_v11_value(self, src, type_code, blacklist, depth, max_array_items):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        if type_code == self.CODE_STRING:
            l_raw = src.read(4)
            if not l_raw:
                return ("string", b"")
            l = struct.unpack('<I', l_raw)[0]
            return ("string", src.read(l))

        name = self.code_to_name.get(type_code)
        obj_def = self.type_map.get(name)
        if obj_def:
            fields = self._read_registry_object_fields(src, obj_def, blacklist, depth, max_array_items)
            if self._needs_transform(obj_def.name):
                obj_name, fields = self._apply_downgrade_transforms(obj_def.name, fields)
                return ("object", (obj_name, fields))
            return ("object", (obj_def.name, fields))

        if type_code == 0x11:
            count_raw = src.read(4)
            if not count_raw:
                return ("array", ("object", []))
            count = self._guard_count(src, struct.unpack('<I', count_raw)[0], max_array_items)
            elements = []
            if count:
                elem_def = self._parse_v11_type_def(src, [])
                for _ in range(count):
                    start_pos = src.tell()
                    obj = self._parse_v11_object_with_def(src, [], elem_def, [], depth + 1)
                    elements.append(("object", obj))
                    if src.tell() == start_pos:
                        raise ValueError("No progress while reading v11 array (0x11)")
            return ("array", ("object", elements))
        if type_code == self.CODE_ARRAY:
            count_raw = src.read(4)
            if not count_raw:
                return ("array", ("object", []))
            count = self._guard_count(src, struct.unpack('<I', count_raw)[0], max_array_items)
            elements = []
            for _ in range(count):
                start_pos = src.tell()
                elements.append(self._read_v11_value(src, 0x12, blacklist, depth + 1, max_array_items))
                if src.tell() == start_pos:
                    raise ValueError("No progress while reading v11 array")
            return ("array", ("object", elements))

        size = self.PRIMITIVE_SIZES.get(type_code, 0)
        if size > 0:
            return ("primitive", type_code, src.read(size))
        if type_code == self.CODE_NULL:
            return ("null", b"")
        raise ValueError(f"Unknown primitive type code {type_code}")

    def _read_registry_object_fields(self, src, obj_def, blacklist, depth, max_array_items):
        obj_overrides = self._overrides.get(obj_def.name, {})
        omit_members = set(obj_overrides.get('__omit__', []))
        fields = []
        for m in obj_def.members:
            if m.name in blacklist:
                self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                continue
            if m.name in omit_members:
                self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                continue
            if m.name in obj_overrides:
                self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                fields.append((m.name, m.type_code, obj_overrides[m.name], True))
                continue
            value = self._read_v11_value(src, m.type_code, blacklist, depth + 1, max_array_items)
            fields.append((m.name, m.type_code, value, False))
        return fields

    def _skip_v11_item(self, src, type_code, depth=0, max_array_items=None):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        if type_code == self.CODE_STRING:
            l_raw = src.read(4)
            if not l_raw: return
            l = struct.unpack('<I', l_raw)[0]
            src.read(l)
        elif type_code == self.CODE_ARRAY:
            elem_tc_raw = src.read(4)
            if not elem_tc_raw: return
            elem_tc = struct.unpack('<I', elem_tc_raw)[0]
            count_raw = src.read(4)
            if not count_raw: return
            count = self._guard_count(src, struct.unpack('<I', count_raw)[0], max_array_items)
            for _ in range(count):
                start_pos = src.tell()
                self._skip_v11_item(src, elem_tc, depth + 1, max_array_items)
                if src.tell() == start_pos:
                    raise ValueError(f"No progress while skipping v11 array (elem {elem_tc})")
        else:
            name = self.code_to_name.get(type_code)
            obj = self.type_map.get(name)
            if obj:
                for m in obj.members:
                    self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
            else:
                size = self.PRIMITIVE_SIZES.get(type_code, 0)
                if size > 0:
                    src.read(size)
                elif type_code != self.CODE_NULL:
                    raise ValueError(f"Unknown primitive type code {type_code}")
