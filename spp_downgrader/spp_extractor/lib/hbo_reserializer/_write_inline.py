"""InlineWriterMixin for HBOSerializer (see serializer.py). Split out for organization."""
import struct


class InlineWriterMixin:
    def _write_v10_object_body(self, dst, type_name, fields, depth=0):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        if type_name is None:
            type_name = ""
        uid = self._extract_uid((type_name, fields)) if fields is not None else None
        if uid in ((25, 0), 25, (27, 0), 27, (125, 0), 125, (127, 0), 127):
            new_fields = []
            for name, tcode, value in fields:
                if name == "maskActions":
                    new_fields.append((name, tcode, ("object_null", b"")))
                else:
                    new_fields.append((name, tcode, value))
            fields = new_fields
        if uid in ((137, 0), 137, (200, 0), 200, (152, 0), 152):
            new_fields = []
            for name, tcode, value in fields:
                if name == "uvGrid":
                    new_fields.append((name, tcode, ("object_null", b"")))
                else:
                    new_fields.append((name, tcode, value))
            fields = new_fields
        if type_name in ("DataSourceUniform", "DataTweakFloat", "DataTweakFloat3", "DataTweakFloat4"):
            if type_name == "DataSourceUniform":
                order = {
                    "channelTypes": 0,
                    "color": 1,
                    "opacity": 2,
                    "uid": 3,
                    "tags": 4,
                    "uvGrid": 5,
                    "uvSamplingWrap": 6,
                    "uvTransformation": 7,
                }
            else:
                order = {
                    "identifier": 0,
                    "uid": 1,
                    "value": 2,
                    "urlToSbsRes": 3,
                    "uvGrid": 4,
                    "uvSamplingWrap": 5,
                    "uvTransformation": 6,
                }
            fields = sorted(
                enumerate(fields),
                key=lambda item: (order.get(item[1][0], 999), item[0]),
            )
            fields = [item[1] for item in fields]
        # v10 object header includes absolute end offset for this object.
        header_start = dst.tell()
        dst.write(b'\x00')  # object flag
        dst.write(struct.pack('<I', 0))  # placeholder for end offset
        name_bytes = type_name.encode('utf-8')
        dst.write(struct.pack('<I', len(name_bytes)))
        if name_bytes:
            dst.write(name_bytes)
        if len(fields) > 0xFFFF:
            raise ValueError("Too many object fields for v10 encoding")
        dst.write(struct.pack('<H', len(fields)))
        for m_name, _m_type, m_val in fields:
            m_name_bytes = m_name.encode('utf-8')
            dst.write(struct.pack('<I', len(m_name_bytes)))
            if m_name_bytes:
                dst.write(m_name_bytes)
            elem_tag_override = None
            if m_val[0] == "array":
                elem_kind, elems = m_val[1]
                if elem_kind == "object" and not elems:
                    elem_tag_override = self.V10_ARRAY_FIELD_TAGS.get((type_name, m_name))
            self._write_v10_value(dst, m_val, depth + 1, _m_type, elem_tag_override)
        end_offset = dst.tell()
        dst.seek(header_start + 1)
        dst.write(struct.pack('<I', end_offset))
        dst.seek(end_offset)
        return end_offset

    def _v10_obj_tag(self, fields):
        # v10 tags entity objects (those with a 'uid' member) as 0x12 and value-
        # struct objects (no uid, e.g. DataBlending, Data2DTransformation,
        # DataLayerState, DataColorSpaceOverride, DataProceduralInput/Output) as
        # 0x14. v10's loader uses the tag to decide how to read the object, so a
        # 0x12 where 0x14 is expected mis-reads it -> null deref. Derive generically
        # from the object's own fields instead of a hardcoded type list.
        if not fields:
            return 0x12
        for f in fields:
            if f[0] == "uid":
                return 0x12
        return 0x14

    def _write_v10_value(self, dst, value, depth=0, type_code_override=None, elem_tag_override=None):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        kind = value[0]
        if kind == "string":
            data = value[1]
            dst.write(struct.pack('B', 0x10))
            dst.write(struct.pack('<I', len(data)))
            dst.write(data)
            return
        if kind == "primitive":
            type_code, raw = value[1], value[2]
            dst.write(struct.pack('B', type_code))
            if type_code == 0x09 and len(raw) == 4:
                dst.write(raw + b'\x00\x00\x00\x00')
            else:
                dst.write(raw)
            return
        if kind == "array":
            elem_kind, elems = value[1]
            tag = type_code_override if type_code_override is not None else 0x13
            dst.write(struct.pack('B', tag))
            elem_tag_offset = dst.tell()
            dst.write(struct.pack('<I', 0))
            dst.write(struct.pack('<I', len(elems)))
            elem_tag = 0
            for elem in elems:
                if elem_kind == "object":
                    _, (elem_type, elem_fields) = elem
                    elem_obj_tag = self._v10_obj_tag(elem_fields)
                    dst.write(struct.pack('B', elem_obj_tag))
                    elem_tag = self._write_v10_object_body(dst, elem_type, elem_fields, depth + 1)
                else:
                    self._write_v10_value(dst, elem, depth + 1)
            # For an empty object array, v10 stores the absolute offset just past
            # the array header (dst.tell()), not a hardcoded constant. The previous
            # V10_ARRAY_FIELD_TAGS overrides baked in offsets from one specific file
            # layout, which are wrong for any other layout.
            if elem_kind == "object" and not elems and not elem_tag:
                elem_tag = dst.tell()
            if elem_tag_offset and elem_tag:
                end_pos = dst.tell()
                dst.seek(elem_tag_offset)
                dst.write(struct.pack('<I', elem_tag))
                dst.seek(end_pos)
            return
        if kind == "object":
            _, (type_name, fields) = value
            tag = type_code_override if type_code_override is not None else self._v10_obj_tag(fields)
            dst.write(struct.pack('B', tag))
            if fields is None:
                dst.write(b'\xFF')
                return
            self._write_v10_object_body(dst, type_name, fields, depth + 1)
            return
        if kind == "object_null":
            tag = type_code_override if type_code_override is not None else 0x12
            dst.write(struct.pack('B', tag))
            dst.write(b'\xFF')
            return
        raise ValueError(f"Unhandled v10 encode kind: {kind}")

    def _patch_v10_root_length(self, data: bytes) -> bytes:
        # v10 object header stores the full stream length at offset 14
        if len(data) < 18:
            return data
        if data[12] != 0x12 or data[13] != 0x00:
            return data
        patched = bytearray(data)
        patched[14:18] = struct.pack('<I', len(data))
        return bytes(patched)

    def _get_v10_tag(self, type_code):
        if type_code == self.CODE_STRING: return 0x10
        if type_code == self.CODE_ARRAY: return 0x13 # or 0x11
        if type_code == self.CODE_NULL: return 0x00
        name = self.code_to_name.get(type_code)
        if name and self.type_map.get(name): return 0x12 # Object
        return type_code # Primitives

    def _write_override_value(self, dst, type_code, value):
        v10_tag = self._get_v10_tag(type_code)
        if type_code == self.CODE_STRING:
            if value is None:
                value = ""
            b = value.encode('utf-8')
            dst.write(struct.pack('B', v10_tag))
            dst.write(struct.pack('<I', len(b)))
            dst.write(b)
            return
        if type_code == self.CODE_ARRAY:
            if isinstance(value, (list, tuple)) and len(value) == 0:
                elem_tag = 0
                dst.write(struct.pack('B', v10_tag) + struct.pack('<I', elem_tag) + struct.pack('<I', 0))
                return
        if type_code == self.CODE_NULL:
            dst.write(b'\x00')
            return

        size = self.PRIMITIVE_SIZES.get(type_code, 0)
        if size <= 0:
            dst.write(struct.pack('B', v10_tag))
            return

        raw = bytearray(size)
        if isinstance(value, bool):
            if size >= 4:
                raw[:4] = struct.pack('<I', 1 if value else 0)
            else:
                raw[0] = 1 if value else 0
        elif isinstance(value, int):
            if size == 1:
                raw[0] = value & 0xFF
            elif size == 4:
                raw[:4] = struct.pack('<I', value & 0xFFFFFFFF)
            elif size == 8:
                raw[:8] = struct.pack('<Q', value & 0xFFFFFFFFFFFFFFFF)
            else:
                raw[:4] = struct.pack('<I', value & 0xFFFFFFFF)
        elif isinstance(value, float):
            raw[:4] = struct.pack('<f', value)
        elif isinstance(value, (list, tuple)):
            nums = list(value)
            if size == 8 and len(nums) == 2:
                if any(isinstance(n, float) for n in nums):
                    raw[:8] = struct.pack('<2f', float(nums[0]), float(nums[1]))
                else:
                    raw[:8] = struct.pack('<2i', int(nums[0]), int(nums[1]))
            elif size == 36 and len(nums) >= 9:
                raw[:36] = struct.pack('<9f', *[float(n) for n in nums[:9]])
            elif size == 64 and len(nums) >= 16:
                raw[:64] = struct.pack('<16f', *[float(n) for n in nums[:16]])
        dst.write(struct.pack('B', v10_tag) + raw)

