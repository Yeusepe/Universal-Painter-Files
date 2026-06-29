"""The HBOSerializer: parses a source HBO stream and reserializes it to a target
version. Behaviour lives in the responsibility mixins; this file holds the class
attributes (codes, sizes, schemas-of-tags), __init__, and the transcode orchestration."""
import struct
import io
from . import runtime
from .models import _load_registry_primitive_sizes
from ._helpers import HelperMixin
from ._readers import ReaderMixin
from ._write_inline import InlineWriterMixin
from ._write_registry import RegistryWriterMixin
from ._transforms import TransformMixin
from ._schema import SchemaMixin


class HBOSerializer(HelperMixin, ReaderMixin, InlineWriterMixin, RegistryWriterMixin, TransformMixin, SchemaMixin):
    CODE_NULL = 0
    CODE_STRING = 16
    CODE_ARRAY = 19
    CODE_OBJECT = 18

    # v11 registry-format primitive sizes.
    PRIMITIVE_SIZES = {
        1: 4,
        2: 8,
        3: 12,
        4: 16,
        5: 4,
        6: 8,
        7: 12,
        8: 16,
        9: 4,
        10: 1,
        11: 4,
        12: 8,
        13: 36,
        14: 64,
        15: 8,
        16: 32,
        21: 8,
    }
    PRIMITIVE_SIZES = _load_registry_primitive_sizes(PRIMITIVE_SIZES)
    FIELD_PRIMITIVE_SIZES = PRIMITIVE_SIZES
    MAX_RECURSION = 64
    MAX_ARRAY_ITEMS = 1000000
    TRANSFORM_TYPES = {
        "BakingTweakList",
        "DataActionFill",
        "DataActionPaint",
        "CameraSettingsData",
        "DataColorProfileParameters",
        "DataDocument",
        "DataMaterial",
        "DataMaterialStack",
        "DataPostEffectsParameters",
        "DataStackActions",
        "DataSourceUniform",
        "DataSourceText",
        "DataSourceVectorial",
        "DataTweakFloat",
        "DataTweakFloat3",
        "DataTweakFloat4",
        "IraySettingsData",
    }
    V10_ARRAY_ELEM_TAGS = {
        "ShaderData": 0x0CAD,
        "DataTweakBool": 0x0C9D,
        "DataTweakFloat": 0x0C9D,
        "DataTweakFloat3": 0x0C9D,
        "DataTweakInt": 0x0C9D,
    }
    V10_ARRAY_FIELD_TAGS = {
        ("DataMaterial", "additionalMaps"): 0x00F4,
        ("DataMaterialStack", "selections"): 0x057F,
    }
    V10_OBJECT_TAGS = {
        "DataBlending": 0x14,
    }
    V10_ENTRY_TYPE_OVERRIDES = {
        "DataStackActions": 0x50002,
        "DataSourceUniform": 0xC0004,
        "DataTweakFloat": 0xA0003,
        "GUIcollapsedState": 0x7FF0A,
    }

    # ponytail: REQUIRED single-object fields. If the field's value type is blacklisted
    # (unsupported in the target version), dropping just the field leaves the parent
    # source-less and the engine derefs null while linking. So drop the whole CONTAINING
    # object from its parent array instead. Extend this set if other "X dangles when its
    # required source Y is removed" cases surface.
    CASCADE_DROP_PARENT_FIELDS = {"DataProceduralInputsSource.source"}
    # Array fields that MUST be non-empty: if every element is blacklisted away (e.g. a
    # fill layer whose only source is an unsupported SVG vector source), the object is
    # meaningless and the engine indexes into the empty list -> null. Drop the parent.
    CASCADE_DROP_IF_EMPTY_ARRAY = {"DataActionFill.sources"}

    def __init__(self, data):
        self.data = data
        if len(data) < 12: raise ValueError("Data too short")
        self.magic, self.ver_check, self.data_ver = struct.unpack('<III', data[:12])
        self.type_map = {} # name -> ObjectDef
        self.code_to_name = {}
        self.data_start = len(data)
        self.root_type_code = 0
        self.entry_headers = []
        self.registry_defs = []
        self.identifier_blacklist = set()
        self.value_rewrites = {}
        self._parse_v11_registry()
        self._parse_entry_headers()
        self._overrides = {}

    def _transcode_v11_registry(self, blacklist, data_version=None, max_array_items=None):
        if self.ver_check != 1:
            return None

        src = io.BytesIO(self.data[12:])
        try:
            tag = self._read_u32(src)
            if tag not in (0x12, 0x13):
                return None
            _type_count = self._read_u32(src)
            registry = self._parse_v11_registry_table(src, _type_count) or []
            entries = []
            root = self._parse_v11_object(src, registry, entries, 0)
            root = self._apply_transforms_recursive(root, keep_overrides=False)
            root = self._narrow_primitives(root)
            root = self._apply_targeted_overrides(root)
            if blacklist:
                root = self._filter_v11_fields(root, set(blacklist), 0)
            # Generic final pass: drop members v10's schema doesn't define for each
            # type and reorder to v10 order. Removes any v11-only field (and anything
            # the per-type heuristics over-added) without per-file hardcoding.
            root = self._project_obj_to_v10_schema(root)
            root = self._drop_unknown_members(root)   # drop members the target version lacks
        except Exception:
            return None

        dst = io.BytesIO()
        header_version = 17 if data_version is None else int(data_version)
        dst.write(struct.pack('<III', 0x1B7C2FDD, 0, header_version))

        # Emit root object in v10 format.
        self._write_v10_value(dst, ("object", root), 0)
        return self._patch_v10_root_length(dst.getvalue())

    # ----------------------------------------------------------- registry writer
    # Inverse of the registry parser, for target_format == "registry" (downgrading
    # to v11/v12). Emits all type defs inline (0xFFFFFFFF prefix) with type_count 0 --
    # the parser accepts this and it re-decodes to an equal tree (byte-identity isn't
    # required; the originals additionally back-reference earlier defs by index).

    def _transcode_inline_source(self, blacklist, data_version=None, max_array_items=None):
        """Downgrade an inline source (v10/v9) to the target inline format, routing it
        through the same transform pipeline registry sources use."""
        if self.ver_check != 0:
            return None
        try:
            root = self._parse_inline_native()
            root = self._apply_transforms_recursive(root, keep_overrides=False)
            root = self._apply_targeted_overrides(root)
            if blacklist:
                root = self._filter_v11_fields(root, set(blacklist), 0)
            root = self._project_obj_to_v10_schema(root)
            root = self._drop_unknown_members(root)   # drop members the target version lacks
        except Exception:
            return None
        dst = io.BytesIO()
        dst.write(struct.pack('<III', 0x1B7C2FDD, 0, 17 if data_version is None else int(data_version)))
        self._write_v10_value(dst, ("object", root), 0)
        return self._patch_v10_root_length(dst.getvalue())

    def _transcode_v11_to_registry(self, blacklist, data_version=None, max_array_items=None):
        if self.ver_check != 1:
            return None
        src = io.BytesIO(self.data[12:])
        try:
            tag = self._read_u32(src)
            if tag not in (0x12, 0x13):
                return None
            type_count = self._read_u32(src)
            registry = self._parse_v11_registry_table(src, type_count) or []
            root = self._parse_v11_object(src, registry, [], 0)
            root = self._apply_transforms_recursive(root, keep_overrides=False)
            root = self._narrow_primitives(root)           # blanket code-22->12 etc. (was inline-only)
            root = self._apply_targeted_overrides(root)
            if blacklist:
                root = self._filter_v11_fields(root, set(blacklist), 0)
            root = self._project_obj_to_v10_schema(root)   # projects to TARGET schema (generic)
            root = self._drop_unknown_members(root)        # drop members the target version lacks
        except Exception:
            return None
        # Native v11 layout (byte-verified against real files): after the 12-byte header,
        #   [root tag u32][type_count u32][root object]
        # Every object is [end_offset u32][typedef-or-index][values], where end_offset is
        # the ABSOLUTE byte position just past the object (0 == null pointer). Typedefs are
        # declared INLINE on first occurrence (0xFFFFFFFF), index-referenced after; there is
        # NO separate table. type_count = number of distinct typedefs (pre-alloc hint).
        # Single buffer so end_offsets are absolute; type_count patched once known.
        dst = io.BytesIO()
        dst.write(struct.pack('<III', 0x1B7C2FDD, 1, 17 if data_version is None else int(data_version)))
        dst.write(struct.pack('<I', 0x12))            # root tag
        dst.write(struct.pack('<I', 0))               # type_count placeholder (patched below)
        emitted = {}                                  # sig -> index, first-seen = parser-append order
        self._write_reg_object(dst, root, emitted, 0)
        data = bytearray(dst.getvalue())
        struct.pack_into('<I', data, 16, len(emitted))
        return bytes(data)

    def _transcode_v11_registry_entries(self, blacklist, data_version=None, max_array_items=None):
        if self.ver_check != 1:
            return None

        src = io.BytesIO(self.data[12:])
        try:
            tag = self._read_u32(src)
            if tag not in (0x12, 0x13):
                return None
            _type_count = self._read_u32(src)
            registry = self._parse_v11_registry_table(src, _type_count) or []
            entries = []
            root = self._parse_v11_object(src, registry, entries, 0)
            if not entries and root and root[0]:
                entries = [root]
        except Exception:
            return None

        name_to_code = {name: code for code, name in self.code_to_name.items()}
        blacklist_set = set(blacklist or [])

        dst = io.BytesIO()
        header_version = 17 if data_version is None else int(data_version)
        dst.write(struct.pack('<III', 0x1B7C2FDD, 0, header_version))

        for obj_name, fields in entries:
            if obj_name in blacklist_set:
                continue
            obj = self._apply_transforms_recursive((obj_name, fields), keep_overrides=True)
            obj = self._strip_fields_recursive(obj)
            obj = self._apply_targeted_overrides(obj)
            if blacklist_set:
                obj = self._filter_v11_fields(obj, blacklist_set, 0)
            obj_name, fields = obj
            if obj_name in blacklist_set:
                continue

            name_bytes = obj_name.encode('utf-8', errors='replace')
            dst.write(struct.pack('<I', len(name_bytes)))
            dst.write(name_bytes)
            v11_code = name_to_code.get(obj_name, 0)
            entry_type_code = self._map_entry_type_code(obj_name, v11_code)
            dst.write(struct.pack('<I', entry_type_code))
            self._write_v10_legacy_object_fields(dst, fields, include_prefix=True)

        return self._patch_v10_root_length(dst.getvalue())

    def prune_and_reserialize(self, blacklist, data_version=None, max_array_items=None, overrides=None, identifier_blacklist=None, value_rewrites=None, force_entry_headers=False):
        # We must output v10 Tagged format
        dst = io.BytesIO()
        header_version = 17 if data_version is None else int(data_version)
        dst.write(struct.pack('<III', 0x1B7C2FDD, 0, header_version)) # v10 Header
        self._overrides = overrides or {}
        self.identifier_blacklist = set(identifier_blacklist or [])
        self.value_rewrites = value_rewrites or {}

        # Target a registry (v11/v12) format instead of v10 inline, per the profile.
        if runtime.PROFILE.data.get("target_format") == "registry" and self.ver_check == 1:
            reg_out = self._transcode_v11_to_registry(blacklist, data_version, max_array_items)
            if reg_out:
                return reg_out

        # Inline source (v8/v9/v10) -> inline target: route through the transform
        # pipeline (the registry transcoders only handle registry sources).
        if self.ver_check == 0 and runtime.PROFILE.data.get("target_format", "inline") == "inline":
            inline_out = self._transcode_inline_source(blacklist, data_version, max_array_items)
            if inline_out:
                return inline_out

        if force_entry_headers and self.ver_check == 1:
            registry_entries_out = self._transcode_v11_registry_entries(blacklist, data_version, max_array_items)
            if registry_entries_out:
                return registry_entries_out

        if not force_entry_headers:
            registry_out = self._transcode_v11_registry(blacklist, data_version, max_array_items)
            if registry_out:
                return registry_out

        if self.entry_headers:
            for entry in self.entry_headers:
                if entry.type_name in blacklist:
                    continue
                name_bytes = entry.type_name.encode('utf-8', errors='replace')
                dst.write(struct.pack('<I', len(name_bytes)))
                dst.write(name_bytes)
                entry_type_code = self._map_entry_type_code(entry.type_name, entry.type_code)
                dst.write(struct.pack('<I', entry_type_code))

                payload_start = entry.type_code_offset + 4
                payload_end = entry.estimated_end_offset
                payload = self.data[payload_start:payload_end]
                src = io.BytesIO(payload)
                tmp = io.BytesIO()
                try:
                    if self._looks_like_field_object(payload):
                        self._transcode_field_object_payload(
                            src, tmp, blacklist, entry.type_name, 0, max_array_items
                        )
                    else:
                        self._transcode_item(src, tmp, entry.type_code, blacklist, 0, max_array_items)
                    if src.tell() <= 0:
                        raise ValueError("No progress while transcoding entry payload")
                    dst.write(tmp.getvalue())
                except Exception:
                    dst.write(payload)
            return self._patch_v10_root_length(dst.getvalue())

        if self.data_start >= len(self.data):
            return dst.getvalue() # Empty stream but valid header

        src = io.BytesIO(self.data[self.data_start:])
        try:
            count = struct.unpack('<I', src.read(4))[0]
            for _ in range(count):
                self._transcode_item(src, dst, self.root_type_code, blacklist, 0, max_array_items)
        except Exception: pass
        return self._patch_v10_root_length(dst.getvalue())

    def _transcode_item(self, src, dst, type_code, blacklist, depth, max_array_items):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        v10_tag = self._get_v10_tag(type_code)

        if type_code == self.CODE_STRING:
            l_raw = src.read(4)
            if not l_raw: return
            l = struct.unpack('<I', l_raw)[0]
            dst.write(struct.pack('B', v10_tag) + l_raw + src.read(l))
            return

        name = self.code_to_name.get(type_code)
        obj_def = self.type_map.get(name)
        if obj_def:
            if self._needs_transform(obj_def.name):
                fields = self._read_registry_object_fields(src, obj_def, blacklist, depth, max_array_items)
                obj_name, fields = self._apply_downgrade_transforms(obj_def.name, fields)
                dst.write(struct.pack('B', v10_tag) + b'\x00')  # Tag 18, Flag 0
                dst.write(struct.pack('<I', len(fields)))
                for name, m_type, value, overridden in fields:
                    n_b = name.encode('utf-8')
                    dst.write(struct.pack('<I', len(n_b)) + n_b)
                    if overridden:
                        self._write_override_value(dst, m_type, value)
                    else:
                        self._write_v10_value(dst, value, depth + 1)
                return

            obj_overrides = self._overrides.get(obj_def.name, {})
            omit_members = set(obj_overrides.get('__omit__', []))
            members = obj_def.members
            if obj_def.name in ("DataSourceUniform", "DataTweakFloat", "DataTweakFloat3", "DataTweakFloat4"):
                if obj_def.name == "DataSourceUniform":
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
                members = sorted(
                    enumerate(members),
                    key=lambda item: (order.get(item[1].name, 999), item[0]),
                )
                members = [item[1] for item in members]
            active = [m for m in members if m.name not in blacklist and m.name not in omit_members]
            dst.write(struct.pack('B', v10_tag) + b'\x00') # Tag 18, Flag 0
            dst.write(struct.pack('<I', len(active)))
            for m in members:
                if m.name in blacklist:
                    self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                elif m.name in omit_members:
                    self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                elif m.name in obj_overrides:
                    self._skip_v11_item(src, m.type_code, depth + 1, max_array_items)
                    n_b = m.name.encode('utf-8')
                    dst.write(struct.pack('<I', len(n_b)) + n_b)
                    self._write_override_value(dst, m.type_code, obj_overrides[m.name])
                else:
                    start_pos = src.tell()
                    n_b = m.name.encode('utf-8')
                    dst.write(struct.pack('<I', len(n_b)) + n_b)
                    self._transcode_item(src, dst, m.type_code, blacklist, depth + 1, max_array_items)
                    if src.tell() == start_pos:
                        raise ValueError(f"No progress while transcoding object member {m.name}")
            return

        if type_code == self.CODE_ARRAY:
            elem_tc_raw = src.read(4)
            if not elem_tc_raw: return
            elem_tc = struct.unpack('<I', elem_tc_raw)[0]
            count_raw = src.read(4)
            if not count_raw: return
            count = self._guard_count(src, struct.unpack('<I', count_raw)[0], max_array_items)

            v10_elem_tag = self._get_v10_tag(elem_tc)
            dst.write(struct.pack('B', v10_tag) + struct.pack('<I', v10_elem_tag) + count_raw)
            for _ in range(count):
                start_pos = src.tell()
                self._transcode_item(src, dst, elem_tc, blacklist, depth + 1, max_array_items)
                if src.tell() == start_pos:
                    raise ValueError(f"No progress while transcoding array element type {elem_tc}")
            return

        size = self.PRIMITIVE_SIZES.get(type_code, 0)
        if size > 0:
            dst.write(struct.pack('B', v10_tag) + src.read(size))
        elif type_code == self.CODE_NULL:
            dst.write(b'\x00')
        else:
            raise ValueError(f"Unknown primitive type code {type_code}")

    def _transcode_field_object_payload(self, src, dst, blacklist, obj_name=None, depth=0, max_array_items=None):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        obj_overrides = self._overrides.get(obj_name, {}) if obj_name else {}
        omit_members = set(obj_overrides.get('__omit__', []))

        fields = []
        while src.tell() < len(src.getbuffer()) - 8:
            name_len_raw = src.read(4)
            if not name_len_raw:
                break
            name_len = struct.unpack('<I', name_len_raw)[0]
            if name_len < 1 or name_len > 256:
                break
            name_bytes = src.read(name_len)
            if len(name_bytes) != name_len:
                break
            try:
                name = name_bytes.decode('utf-8', errors='replace')
            except Exception:
                break
            type_code_raw = src.read(4)
            if len(type_code_raw) != 4:
                break
            type_code = struct.unpack('<I', type_code_raw)[0]

            if name in blacklist or name in omit_members:
                self._skip_v11_item(src, type_code, depth + 1, max_array_items)
                continue

            if name in obj_overrides:
                self._skip_v11_item(src, type_code, depth + 1, max_array_items)
                fields.append((name, type_code, obj_overrides[name], True))
                continue

            value = self._transcode_field_value(src, type_code, blacklist, depth + 1, max_array_items)
            fields.append((name, type_code, value, False))

        if obj_name and self._needs_transform(obj_name):
            obj_name, fields = self._apply_downgrade_transforms(obj_name, fields)

        if obj_name in ("DataSourceUniform", "DataTweakFloat", "DataTweakFloat3", "DataTweakFloat4"):
            if obj_name == "DataSourceUniform":
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

        if obj_name:
            # Write v10 object with name + end offset, using field-based values.
            dst.write(struct.pack('B', 0x12))
            header_start = dst.tell()
            dst.write(b'\x00')
            dst.write(struct.pack('<I', 0))  # placeholder for end offset
            name_bytes = obj_name.encode('utf-8')
            dst.write(struct.pack('<I', len(name_bytes)))
            if name_bytes:
                dst.write(name_bytes)
            if len(fields) > 0xFFFF:
                raise ValueError("Too many object fields for v10 encoding")
            dst.write(struct.pack('<H', len(fields)))
            for name, type_code, value, overridden in fields:
                n_b = name.encode('utf-8')
                dst.write(struct.pack('<I', len(n_b)) + n_b)
                if overridden:
                    self._write_override_value(dst, type_code, value)
                else:
                    self._write_transcoded_field_value(dst, type_code, value, blacklist, depth + 1, max_array_items)
            end_offset = dst.tell()
            dst.seek(header_start + 1)
            dst.write(struct.pack('<I', end_offset))
            dst.seek(end_offset)
        else:
            dst.write(struct.pack('B', 0x12) + b'\x00')  # object tag + flag
            dst.write(struct.pack('<I', len(fields)))
            for name, type_code, value, overridden in fields:
                n_b = name.encode('utf-8')
                dst.write(struct.pack('<I', len(n_b)) + n_b)
                if overridden:
                    self._write_override_value(dst, type_code, value)
                else:
                    self._write_transcoded_field_value(dst, type_code, value, blacklist, depth + 1, max_array_items)

    def _transcode_field_value(self, src, type_code, blacklist, depth, max_array_items):
        if type_code == self.CODE_STRING:
            l_raw = src.read(4)
            if not l_raw:
                return ("string", b"")
            l = struct.unpack('<I', l_raw)[0]
            return ("string", l_raw + src.read(l))
        if type_code == self.CODE_ARRAY:
            elem_tc_raw = src.read(4)
            count_raw = src.read(4)
            if not elem_tc_raw or not count_raw:
                return ("array", (0, []))
            elem_tc = struct.unpack('<I', elem_tc_raw)[0]
            count = self._guard_count(src, struct.unpack('<I', count_raw)[0], max_array_items)
            elements = []
            for _ in range(count):
                start_pos = src.tell()
                elements.append(self._transcode_field_value(src, elem_tc, blacklist, depth + 1, max_array_items))
                if src.tell() == start_pos:
                    raise ValueError(f"No progress while transcoding field array (elem {elem_tc})")
            return ("array", (elem_tc, elements))
        if type_code == self.CODE_OBJECT:
            # Nested field-based object
            nested_src = src
            start = nested_src.tell()
            self._transcode_field_object_payload(nested_src, io.BytesIO(), blacklist, None, depth + 1, max_array_items)
            end = nested_src.tell()
            nested_src.seek(start)
            data = nested_src.read(end - start)
            return ("object", data)
        size = self.FIELD_PRIMITIVE_SIZES.get(type_code, 0)
        if size == 0:
            size = self.PRIMITIVE_SIZES.get(type_code, 0)
        if size > 0:
            return ("primitive", src.read(size))
        if type_code == self.CODE_NULL:
            return ("null", b"")
        raise ValueError(f"Unknown field value type code {type_code}")

    def _write_transcoded_field_value(self, dst, type_code, value, blacklist, depth, max_array_items):
        kind, payload = value
        v10_tag = self._get_v10_tag(type_code)
        if kind == "string":
            dst.write(struct.pack('B', v10_tag) + payload)
            return
        if kind == "array":
            elem_tc, elements = payload
            v10_elem_tag = self._get_v10_tag(elem_tc)
            dst.write(struct.pack('B', v10_tag) + struct.pack('<I', v10_elem_tag) + struct.pack('<I', len(elements)))
            for elem in elements:
                self._write_transcoded_field_value(dst, elem_tc, elem, blacklist, depth + 1, max_array_items)
            return
        if kind == "object":
            nested_src = io.BytesIO(payload)
            self._transcode_field_object_payload(nested_src, dst, blacklist, None, depth + 1, max_array_items)
            return
        if kind == "object_null":
            dst.write(struct.pack('B', v10_tag) + b'\xFF')
            return
        if kind == "primitive":
            dst.write(struct.pack('B', v10_tag) + payload)
            return
        if kind == "null":
            dst.write(b'\x00')
            return
