"""HelperMixin for HBOSerializer (see serializer.py). Split out for organization."""
import struct


class HelperMixin:
    def _needs_transform(self, obj_name):
        if not obj_name:
            return False
        return (
            obj_name in self.TRANSFORM_TYPES
            or obj_name.startswith("DataAction")
            or obj_name.startswith("DataLayer")
        )

    def _get_member_type(self, obj_name, field_name):
        obj_def = self.type_map.get(obj_name)
        if not obj_def:
            return None
        for m in obj_def.members:
            if m.name == field_name:
                return m.type_code
        return None

    def _find_field_index(self, fields, name):
        for i, f in enumerate(fields):
            if f[0] == name:
                return i
        return None

    def _get_field(self, fields, name):
        idx = self._find_field_index(fields, name)
        if idx is None:
            return None
        return fields[idx]

    def _set_field(self, fields, name, type_code, value, overridden=False):
        idx = self._find_field_index(fields, name)
        if idx is None:
            fields.append((name, type_code, value, overridden))
        else:
            fields[idx] = (name, type_code, value, overridden)

    def _set_field_simple(self, fields, name, type_code, value):
        idx = self._find_field_index(fields, name)
        if idx is None:
            fields.append((name, type_code, value))
        else:
            fields[idx] = (name, type_code, value)

    def _remove_field(self, fields, name):
        idx = self._find_field_index(fields, name)
        if idx is not None:
            fields.pop(idx)

    def _remove_field_simple(self, fields, name):
        idx = self._find_field_index(fields, name)
        if idx is not None:
            fields.pop(idx)

    def _remove_all_fields_simple(self, fields, name):
        return [f for f in fields if f[0] != name]

    def _dedupe_fields_simple(self, fields):
        last_index = {}
        for i, f in enumerate(fields):
            last_index[f[0]] = i
        return [f for i, f in enumerate(fields) if last_index[f[0]] == i]

    def _reorder_fields_simple(self, fields, order):
        order_index = {name: i for i, name in enumerate(order)}
        indexed = list(enumerate(fields))
        indexed.sort(key=lambda item: (order_index.get(item[1][0], 1_000_000), item[0]))
        return [item[1] for item in indexed]

    def _pack_primitive(self, type_code, value):
        size = self.FIELD_PRIMITIVE_SIZES.get(type_code, 0)
        if size == 4:
            if isinstance(value, float):
                return struct.pack('<f', value)
            return struct.pack('<i', int(value))
        if size == 8:
            if isinstance(value, float):
                return struct.pack('<d', value)
            return struct.pack('<q', int(value))
        if size == 12:
            return struct.pack('<3f', *value)
        if size == 16:
            return struct.pack('<4f', *value)
        if size == 64:
            return struct.pack('<16f', *value)
        if size == 1:
            return struct.pack('B', int(bool(value)))
        return struct.pack('<I', int(value))

    def _primitive_to_bool(self, prim):
        if not prim or prim[0] != "primitive":
            return False
        raw = prim[2]
        if not raw:
            return False
        return any(b != 0 for b in raw)

    def _primitive_to_float(self, prim):
        if not prim or prim[0] != "primitive":
            return None
        raw = prim[2]
        if not raw:
            return None
        if len(raw) >= 4:
            return struct.unpack('<f', raw[:4])[0]
        return None

    def _extract_identifier(self, obj):
        _, fields = obj
        for f in fields:
            if len(f) == 4:
                name, _tcode, value, _over = f
            else:
                name, _tcode, value = f
            if name == "identifier" and value[0] == "string":
                try:
                    return value[1].decode("utf-8", errors="replace")
                except Exception:
                    return None
        return None

    def _extract_uid(self, obj):
        _, fields = obj
        uid_field = self._get_field(fields, "uid")
        if not uid_field:
            return None
        value = uid_field[2]
        if not value or value[0] != "primitive":
            return None
        raw = value[2]
        if not raw:
            return None
        if len(raw) >= 8:
            return struct.unpack("<II", raw[:8])
        if len(raw) >= 4:
            return struct.unpack("<I", raw[:4])[0]
        return None

    def _is_null_obj(self, value):
        if value is None:
            return True
        k = value[0]
        if k == "object_null":
            return True
        if k == "object":
            inner = value[1]
            return inner is None or (inner[0] == "" and not inner[1])
        return False

    def _looks_like_field_object(self, data):
        if len(data) < 8:
            return False
        name_len = struct.unpack('<I', data[:4])[0]
        if name_len < 1 or name_len > 256:
            return False
        if 4 + name_len + 4 > len(data):
            return False
        name_bytes = data[4:4 + name_len]
        if not all(48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or b == 95 for b in name_bytes):
            return False
        return True

