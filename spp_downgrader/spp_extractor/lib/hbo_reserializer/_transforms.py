"""TransformMixin for HBOSerializer (see serializer.py). Split out for organization."""
import struct
from . import runtime


class TransformMixin:
    def _apply_data_action_fill_bitmap(self, fields):
        items_field = self._get_field(fields, "items")
        if not items_field:
            return fields
        items_val = items_field[2]
        if items_val[0] != "array":
            return fields
        elem_kind, elems = items_val[1]
        if elem_kind != "object" or not elems:
            return fields
        first = elems[0]
        if first[0] != "object" or first[1][0] != "DataActionFillBitmap":
            return fields

        for i, elem in enumerate(elems):
            if elem[0] != "object":
                continue
            elem_type, elem_fields = elem[1]
            if elem_type != "DataActionFillBitmap":
                continue

            def tc(name):
                return self._get_member_type(elem_type, name) or self._get_member_type("DataActionFillBitmap", name) or 0x09

            # matrix identity
            matrix_tc = tc("matrix")
            identity = (
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            )
            self._set_field(elem_fields, "matrix", matrix_tc, ("primitive", matrix_tc, self._pack_primitive(matrix_tc, identity)))

            # filtering/addressing = 0
            for name in ("filtering", "addressing"):
                t = tc(name)
                self._set_field(elem_fields, name, t, ("primitive", t, self._pack_primitive(t, 0)))

            # enabled = true
            enabled_tc = tc("enabled")
            self._set_field(elem_fields, "enabled", enabled_tc, ("primitive", enabled_tc, self._pack_primitive(enabled_tc, 1)))

            # channelTypes = -1
            ch_tc = tc("channelTypes")
            self._set_field(elem_fields, "channelTypes", ch_tc, ("primitive", ch_tc, self._pack_primitive(ch_tc, -1)))

            # sources array normalization
            sources_field = self._get_field(elem_fields, "sources")
            if sources_field and sources_field[2][0] == "array":
                src_kind, src_elems = sources_field[2][1]
                if src_kind == "object":
                    new_sources = []
                    for src in src_elems:
                        if src[0] != "object":
                            new_sources.append(src)
                            continue
                        src_type, src_fields = src[1]

                        # default channelTypes if missing
                        src_channel = self._get_field(src_fields, "channelTypes")
                        if not src_channel:
                            src_ch_tc = self._get_member_type(src_type, "channelTypes") or ch_tc
                            self._set_field(src_fields, "channelTypes", src_ch_tc, ("primitive", src_ch_tc, self._pack_primitive(src_ch_tc, -1)))
                        elif src_channel[2][0] == "primitive":
                            raw = src_channel[2][2]
                            if len(raw) >= 4 and struct.unpack('<i', raw[:4])[0] == 0x7FFFFF:
                                src_ch_tc = src_channel[1]
                                self._set_field(src_fields, "channelTypes", src_ch_tc, ("primitive", src_ch_tc, self._pack_primitive(src_ch_tc, -1)))

                        if src_type == "DataSourceProcedural":
                            stroke_tc = self._get_member_type(src_type, "strokeWires") or 0x09
                            self._set_field(src_fields, "strokeWires", stroke_tc, ("primitive", stroke_tc, self._pack_primitive(stroke_tc, 16)))

                        url_to_bitmap = False
                        bitmap_field = self._get_field(src_fields, "bitmap")
                        if bitmap_field and bitmap_field[2][0] == "object":
                            bmp_type, bmp_fields = bitmap_field[2][1]
                            url_field = self._get_field(bmp_fields, "urlToBitmapRes")
                            if url_field and url_field[2][0] == "primitive":
                                raw = url_field[2][2]
                                url_to_bitmap = raw[:1] != b"\x00" and any(b != 0 for b in raw)

                        if url_to_bitmap:
                            bg_field = self._get_field(src_fields, "backgroundColor")
                            if bg_field and bg_field[2][0] == "primitive":
                                raw = bg_field[2][2]
                                if len(raw) == 16:
                                    r, g, b, a = struct.unpack('<4f', raw)
                                    color = (r * a, g * a, b * a)
                                    color_tc = self._get_member_type(src_type, "color") or 6
                                    self._set_field(src_fields, "color", color_tc, ("primitive", color_tc, self._pack_primitive(color_tc, color)))
                                    opacity_tc = self._get_member_type(src_type, "opacity") or 3
                                    self._set_field(src_fields, "opacity", opacity_tc, ("primitive", opacity_tc, self._pack_primitive(opacity_tc, a)))
                        else:
                            # force DataSourceBitmap
                            src_type = "DataSourceBitmap"
                            opacity_tc = self._get_member_type(src_type, "opacity") or 3
                            self._set_field(src_fields, "opacity", opacity_tc, ("primitive", opacity_tc, self._pack_primitive(opacity_tc, 1.0)))
                        new_sources.append(("object", (src_type, src_fields)))
                    self._set_field(elem_fields, "sources", sources_field[1], ("array", ("object", new_sources)))

            elems[i] = ("object", (elem_type, elem_fields))

        items_val = ("array", ("object", elems))
        self._set_field(fields, "items", items_field[1], items_val)
        return fields

    def _build_per_channel_blending(self):
        if "DataBlending" not in self.type_map:
            return ("array", ("object", []))

        def entry(channel_types, blend_mode):
            bm_tc = self._get_member_type("DataBlending", "blendingMode") or 11
            ch_tc = self._get_member_type("DataBlending", "channelTypes") or 11
            op_tc = self._get_member_type("DataBlending", "opacity") or 5
            fields = [
                ("blendingMode", bm_tc, ("primitive", bm_tc, self._pack_primitive(bm_tc, blend_mode)), False),
                ("channelTypes", ch_tc, ("primitive", ch_tc, self._pack_primitive(ch_tc, channel_types)), False),
                ("opacity", op_tc, ("primitive", op_tc, self._pack_primitive(op_tc, 1.0)), False),
            ]
            return ("object", ("DataBlending", fields))

        elems = [
            entry(2, 10),
            entry(0x400000, 27),
            entry(0x800000, 4),
        ]
        mask = 0x7FFFFF
        while mask:
            bit = mask & -mask
            if (bit & 0xC00002) == 0:
                elems.append(entry(bit, 2))
            mask &= mask - 1
        return ("array", ("object", elems))

    def _build_action_group_blending(self):
        if "DataBlending" not in self.type_map:
            return ("array", ("object", []))

        bm_tc = self._get_member_type("DataBlending", "blendingMode") or 11
        ch_tc = self._get_member_type("DataBlending", "channelTypes") or 11
        op_tc = self._get_member_type("DataBlending", "opacity") or 5

        elems = []
        mask = 0xFFFFFF
        while mask:
            bit = mask & -mask
            fields = [
                ("blendingMode", bm_tc, ("primitive", bm_tc, self._pack_primitive(bm_tc, 25)), False),
                ("channelTypes", ch_tc, ("primitive", ch_tc, self._pack_primitive(ch_tc, bit)), False),
                ("opacity", op_tc, ("primitive", op_tc, self._pack_primitive(op_tc, 1.0)), False),
            ]
            elems.append(("object", ("DataBlending", fields)))
            mask &= mask - 1
        return ("array", ("object", elems))

    def _build_empty_stack_actions(self):
        if "DataStackActions" not in self.type_map:
            return ("object", ("DataStackActions", []))
        actions_tc = self._get_member_type("DataStackActions", "actions") or self.CODE_ARRAY
        return ("object", ("DataStackActions", [("actions", actions_tc, ("array", ("object", [])), False)]))

    def _split_tonemapping_bounds(self, obj):
        obj_name, fields = obj
        if obj_name != "DataTweakFloat2":
            return None
        ident = self._extract_identifier(obj)
        if not ident or not ident.endswith(".TonemappingBounds"):
            return None
        value_field = self._get_field(fields, "value")
        if not value_field or value_field[2][0] != "primitive":
            return None
        raw = value_field[2][2]
        if len(raw) != 8:
            return None
        try:
            vmin, vmax = struct.unpack("<2f", raw)
        except Exception:
            return None
        base = ident[:-len("TonemappingBounds")]
        min_ident = f"{base}TonemappingMin"
        max_ident = f"{base}TonemappingMax"

        ident_tc = None
        for name, tcode, _value in fields:
            if name == "identifier":
                ident_tc = tcode
                break
        if ident_tc is None:
            ident_tc = self._get_member_type("DataTweakFloat", "identifier") or 0x10

        val_tc = self._get_member_type("DataTweakFloat", "value") or 5
        min_raw = self._pack_primitive(val_tc, float(vmin))
        max_raw = self._pack_primitive(val_tc, float(vmax))

        def build_fields(new_ident, new_raw):
            new_fields = []
            saw_ident = False
            saw_value = False
            for name, tcode, value in fields:
                if name == "identifier":
                    new_fields.append((name, ident_tc, ("string", new_ident.encode("utf-8"))))
                    saw_ident = True
                elif name == "value":
                    new_fields.append((name, val_tc, ("primitive", val_tc, new_raw)))
                    saw_value = True
                else:
                    new_fields.append((name, tcode, value))
            if not saw_ident:
                new_fields.append(("identifier", ident_tc, ("string", new_ident.encode("utf-8"))))
            if not saw_value:
                new_fields.append(("value", val_tc, ("primitive", val_tc, new_raw)))
            return ("DataTweakFloat", new_fields)

        return [build_fields(min_ident, min_raw), build_fields(max_ident, max_raw)]

    def _filter_v11_fields(self, obj, blacklist, depth=0):
        if depth > self.MAX_RECURSION:
            raise ValueError("Max recursion depth exceeded")
        obj_name, fields = obj
        # Drop substance sources whose graph the target engine can't load (format-incompatible).
        if runtime.DROP_SUBSTANCE_GRAPHS and obj_name in ("DataSourceProcedural", "DataActionFilterProcedural"):
            for _n, _t, _v in fields:
                if _n == "procedural" and _v[0] == "object" and _v[1]:
                    for _cn, _ct, _cv in (_v[1][1] or []):
                        if _cn == "urlToSbsRes" and _cv[0] == "string":
                            _u = _cv[1].decode("utf-8", "replace").lower()
                            if any(g in _u for g in runtime.DROP_SUBSTANCE_GRAPHS):
                                return None  # cascade: drops the source, empties/drops its fill
        new_fields = []
        for name, tcode, value in fields:
            # Bare name removes the field everywhere; "Type.field" removes it only
            # inside that object type (needed for common names like "enabled" that
            # are only invalid on a specific v11 object).
            if name in blacklist or (obj_name and f"{obj_name}.{name}" in blacklist):
                continue
            kind = value[0]
            if kind == "string" and name in self.value_rewrites:
                try:
                    s = value[1].decode("utf-8", errors="replace")
                except Exception:
                    s = None
                if s in self.value_rewrites[name]:
                    repl = self.value_rewrites[name][s]
                    value = ("string", repl.encode("utf-8"))
            if kind == "object":
                _, (child_name, child_fields) = value
                if child_name in blacklist:        # field holds an object of a dropped type
                    if obj_name and f"{obj_name}.{name}" in self.CASCADE_DROP_PARENT_FIELDS:
                        return None                # required source dropped -> drop this whole object
                    continue
                if child_fields is None:
                    value = ("object", (child_name, None))
                else:
                    filtered_child = self._filter_v11_fields((child_name, child_fields), blacklist, depth + 1)
                    if filtered_child is None:     # nested object cascade-dropped
                        if obj_name and f"{obj_name}.{name}" in self.CASCADE_DROP_PARENT_FIELDS:
                            return None
                        continue                   # drop this now-empty field
                    value = ("object", filtered_child)
            elif kind == "array":
                elem_kind, elems = value[1]
                if elem_kind == "object":
                    new_elems = []
                    for elem in elems:
                        _, (child_name, child_fields) = elem
                        if child_name in blacklist:  # drop array elements of a dropped type
                            continue
                        filtered = self._filter_v11_fields((child_name, child_fields), blacklist, depth + 1)
                        if filtered is None:       # element cascade-dropped (required child blacklisted)
                            continue
                        split = self._split_tonemapping_bounds(filtered)
                        if split:
                            for obj in split:
                                ident = self._extract_identifier(obj)
                                if ident and ident in self.identifier_blacklist:
                                    continue
                                new_elems.append(("object", obj))
                            continue
                        ident = self._extract_identifier(filtered)
                        if ident and ident in self.identifier_blacklist:
                            continue
                        new_elems.append(("object", filtered))
                    value = ("array", ("object", new_elems))
                    if (obj_name and f"{obj_name}.{name}" in self.CASCADE_DROP_IF_EMPTY_ARRAY
                            and elems and not new_elems):
                        return None  # required source array fully emptied -> drop this object
            new_fields.append((name, tcode, value))
        return (obj_name, new_fields)

    def _normalize_fields(self, fields):
        out = []
        for f in fields:
            if len(f) == 4:
                out.append(f)
            else:
                name, tcode, value = f
                out.append((name, tcode, value, False))
        return out

    def _strip_fields(self, fields):
        return [(name, tcode, value) for name, tcode, value, _ in fields]

    def _strip_fields_recursive(self, obj):
        obj_name, fields = obj
        if fields is None:
            return (obj_name, None)
        new_fields = []
        for f in fields:
            if len(f) == 4:
                name, tcode, value, _ = f
            else:
                name, tcode, value = f
            kind = value[0]
            if kind == "object":
                child_name, child_fields = value[1]
                value = ("object", self._strip_fields_recursive((child_name, child_fields)))
            elif kind == "array":
                elem_kind, elems = value[1]
                if elem_kind == "object":
                    new_elems = []
                    for elem in elems:
                        if elem[0] == "object":
                            new_elems.append(("object", self._strip_fields_recursive(elem[1])))
                        else:
                            new_elems.append(elem)
                    value = ("array", ("object", new_elems))
            new_fields.append((name, tcode, value))
        return (obj_name, new_fields)

    def _drop_unknown_members(self, obj):
        """Drop members whose name is absent from the target version's binary (i.e. added
        in a newer version). Authoritative + sound: a member the target really defines has
        its name in that version's identifier set (runtime.TARGET_MEMBERS), so it is never
        dropped; members new to a later version are. Replaces hand-listing removed members
        per type. No-op when TARGET_MEMBERS is None (exact rebuild / data file missing)."""
        members = runtime.TARGET_MEMBERS
        if members is None:
            return obj
        obj_name, fields = obj
        if fields is None:
            return (obj_name, None)
        new_fields = []
        for f in fields:
            name, tcode, value = f[0], f[1], f[2]
            if name and name not in members:
                continue  # member does not exist in the target version -> drop it
            kind = value[0]
            if kind == "object":
                child_name, child_fields = value[1]
                value = ("object", self._drop_unknown_members((child_name, child_fields)))
            elif kind == "array":
                elem_kind, elems = value[1]
                if elem_kind == "object":
                    new_elems = []
                    for elem in elems:
                        if elem[0] == "object":
                            new_elems.append(("object", self._drop_unknown_members(elem[1])))
                        else:
                            new_elems.append(elem)
                    value = ("array", ("object", new_elems))
            new_fields.append((name, tcode, value))
        return (obj_name, new_fields)

    def _apply_transforms_recursive(self, obj, keep_overrides=False):
        obj_name, fields = obj
        fields4 = self._normalize_fields(fields)
        new_fields = []
        for name, tcode, value, overridden in fields4:
            kind = value[0]
            if kind == "object":
                child_name, child_fields = value[1]
                if child_fields is not None:
                    child = self._apply_transforms_recursive((child_name, child_fields), keep_overrides)
                    value = ("object", child)
            elif kind == "array":
                elem_kind, elems = value[1]
                if elem_kind == "object":
                    new_elems = []
                    for elem in elems:
                        if elem[0] == "object":
                            child_name, child_fields = elem[1]
                            if child_fields is not None:
                                child = self._apply_transforms_recursive((child_name, child_fields), keep_overrides)
                                new_elems.append(("object", child))
                            else:
                                new_elems.append(elem)
                        else:
                            new_elems.append(elem)
                    value = ("array", ("object", new_elems))
            new_fields.append((name, tcode, value, overridden))

        if runtime.FIELD_RENAME or runtime.FIELD_RETYPE or runtime.FIELD_REKIND or runtime.FIELD_VALUE_TRANSFORM:
            new_fields = self._apply_field_xforms(obj_name, new_fields)

        if self._needs_transform(obj_name):
            obj_name, new_fields = self._apply_downgrade_transforms(obj_name, new_fields)

        obj_name = runtime.TYPE_RENAME.get(obj_name, obj_name)

        if keep_overrides:
            return (obj_name, new_fields)
        return self._strip_fields_recursive((obj_name, new_fields))

    def _narrow_primitives(self, obj, depth=0):
        """Blanket primitive downgrade: rewrite every primitive whose code is in
        runtime.PRIMITIVE_RETYPE, anywhere in the tree (fields, nested objects, array elements).
        Catches version-removed primitives (e.g. v12.1 code 22) the per-field maps miss."""
        if not runtime.PRIMITIVE_RETYPE or depth > self.MAX_RECURSION:
            return obj
        name, fields = obj
        if fields is None:
            return obj
        out = []
        for f in fields:
            nm, tc, val = f[0], f[1], f[2]
            val, tc = self._narrow_val(val, tc, depth)
            out.append((nm, tc, val) + tuple(f[3:]))
        return (name, out)

    def _narrow_val(self, val, tc, depth):
        kind = val[0]
        if kind == "primitive":
            to = runtime.PRIMITIVE_RETYPE.get(val[1])
            if to is None:
                return val, tc
            size = self.PRIMITIVE_SIZES.get(to, len(val[2]))
            b = val[2]
            b = b[:size] if len(b) >= size else b + b"\x00" * (size - len(b))
            return ("primitive", to, b), to
        if kind == "object":
            cn, cf = val[1]
            if cf is None:
                return val, tc
            return ("object", self._narrow_primitives((cn, cf), depth + 1)), tc
        if kind == "array":
            ek, elems = val[1]
            return ("array", (ek, [self._narrow_val(e, None, depth + 1)[0] for e in elems])), tc
        return val, tc

    def _apply_field_xforms(self, obj_name, fields4):
        """Per-field downgrades, applied before runtime.TYPE_RENAME/schema projection (keyed by
        source type+field): runtime.FIELD_RETYPE resizes a primitive whose type/width changed
        across versions (e.g. channelTypes 16B->8B, truncating to the target width);
        runtime.FIELD_RENAME renames a member so it survives projection under the new name."""
        out = []
        for name, tcode, value, overridden in fields4:
            vt = runtime.FIELD_VALUE_TRANSFORM.get(f"{obj_name}.{name}")
            # Idempotent guard: only transform when the source is NOT already in the target
            # representation (value[1] != to_code). Prevents double-converting an already-
            # bitmask value (e.g. if a profile is mis-selected) into garbage.
            if vt and value and value[0] == "primitive" and value[1] != int(vt.get("code", tcode)):
                to_code = int(vt.get("code", tcode))
                size = self.PRIMITIVE_SIZES.get(to_code, len(value[2]))
                n = int.from_bytes(value[2], "little")
                if vt.get("op") == "enum_to_bitmask":
                    out_b = (1 << n).to_bytes(size, "little") if n < size * 8 else b"\x00" * size
                    value, tcode = ("primitive", to_code, out_b), to_code
            rt = runtime.FIELD_RETYPE.get(f"{obj_name}.{name}")
            if rt is not None and value and value[0] == "primitive":
                to_code = int(rt)
                size = self.PRIMITIVE_SIZES.get(to_code, len(value[2]))
                b = value[2]
                b = b[:size] if len(b) >= size else b + b"\x00" * (size - len(b))
                value, tcode = ("primitive", to_code, b), to_code
            rk = runtime.FIELD_REKIND.get(f"{obj_name}.{name}")
            if rk == "array" and value and value[0] == "object":
                # wrap a single object into a one-element object array
                value, tcode = ("array", ("object", [value])), 0x13
            elif rk == "object" and value and value[0] == "array":
                elems = value[1][1]
                value = elems[0] if elems else ("object", ("", None))
                tcode = 0x12
            new = runtime.FIELD_RENAME.get(f"{obj_name}.{name}")
            if new is None and "." not in name:
                new = runtime.FIELD_RENAME.get(name)
            out.append((new or name, tcode, value, overridden))
        return out

    def _apply_targeted_overrides(self, obj):
        obj_name, fields = obj
        if fields is None:
            return (obj_name, None)

        def normalize_uv_transformation(value):
            if not value or value[0] != "object":
                return value
            tf_name, tf_fields = value[1]
            if not tf_fields:
                return value

            scale_field = self._get_field(tf_fields, "scale")
            scale_factors = self._get_field(tf_fields, "scaleFactors")
            if scale_field:
                sf_tc = scale_factors[1] if scale_factors else (self._get_member_type(tf_name, "scaleFactors") or scale_field[1])
                self._set_field_simple(tf_fields, "scaleFactors", sf_tc, scale_field[2])
                self._remove_all_fields_simple(tf_fields, "scale")

            if not self._get_field(tf_fields, "scaleMode"):
                sm_tc = self._get_member_type(tf_name, "scaleMode") or 0x09
                self._set_field_simple(tf_fields, "scaleMode", sm_tc, ("primitive", sm_tc, self._pack_primitive(sm_tc, 0)))

            if not self._get_field(tf_fields, "scalePhysicalSize"):
                sp_tc = self._get_member_type(tf_name, "scalePhysicalSize") or 2
                self._set_field_simple(tf_fields, "scalePhysicalSize", sp_tc, ("primitive", sp_tc, struct.pack("<2f", 10.0, 10.0)))

            return ("object", (tf_name, tf_fields))

        new_fields = []
        for name, tcode, value in fields:
            kind = value[0]
            if kind == "object":
                value = ("object", self._apply_targeted_overrides(value[1]))
            elif kind == "array":
                elem_kind, elems = value[1]
                if elem_kind == "object":
                    new_elems = []
                    for elem in elems:
                        if elem[0] == "object":
                            new_elems.append(("object", self._apply_targeted_overrides(elem[1])))
                        else:
                            new_elems.append(elem)
                    value = ("array", ("object", new_elems))
            new_fields.append((name, tcode, value))
        fields = new_fields

        if obj_name == "DataStackActions":
            uid = self._extract_uid((obj_name, fields))
            if uid in ((27, 0), 27, (125, 0), 125):
                ma_field = self._get_field(fields, "maskActions")
                ma_tc = ma_field[1] if ma_field else (self._get_member_type(obj_name, "maskActions") or self.CODE_OBJECT)
                self._set_field_simple(fields, "maskActions", ma_tc, ("object_null", b""))
                if not self._get_field(fields, "maskEnabled"):
                    me_tc = self._get_member_type(obj_name, "maskEnabled") or 0x0A
                    self._set_field_simple(fields, "maskEnabled", me_tc, ("primitive", me_tc, b"\xFF"))
                if not self._get_field(fields, "maskInitial"):
                    mi_tc = self._get_member_type(obj_name, "maskInitial") or 0x09
                    self._set_field_simple(fields, "maskInitial", mi_tc, ("primitive", mi_tc, self._pack_primitive(mi_tc, 1)))
            if uid in ((27, 0), 27):
                if not self._get_field(fields, "tags"):
                    tags_tc = self._get_member_type(obj_name, "tags") or 0x0B
                    self._set_field_simple(fields, "tags", tags_tc, ("primitive", tags_tc, b"\x00\x00\x00\x00"))
            fields = self._reorder_fields_simple(
                fields,
                [
                    "items",
                    "tags",
                    "uid",
                    "colorTag",
                    "enabled",
                    "enabledGeometryMask",
                    "enabledMeshDefault",
                    "enabledMeshList",
                    "enabledUVTileDefault",
                    "enabledUVTileList",
                    "gammaCompensation",
                    "geometryMaskType",
                    "label",
                    "Layer",
                    "maskActions",
                    "maskEnabled",
                    "maskInitial",
                    "perChannelBlending",
                ],
            )

        if obj_name == "DataSourceUniform":
            uid = self._extract_uid((obj_name, fields))
            if uid in ((137, 0), 137):
                uv_field = self._get_field(fields, "uvGrid")
                uv_tc = uv_field[1] if uv_field else (self._get_member_type(obj_name, "uvGrid") or self.CODE_OBJECT)
                self._set_field_simple(fields, "uvGrid", uv_tc, ("object_null", b""))
                if not self._get_field(fields, "uvSamplingWrap"):
                    wrap_tc = self._get_member_type(obj_name, "uvSamplingWrap") or 0x09
                    self._set_field_simple(fields, "uvSamplingWrap", wrap_tc, ("primitive", wrap_tc, self._pack_primitive(wrap_tc, 3)))
                if not self._get_field(fields, "tags"):
                    tags_tc = self._get_member_type(obj_name, "tags") or 0x0B
                    self._set_field_simple(fields, "tags", tags_tc, ("primitive", tags_tc, b"\x00\x00\x00\x00"))
                uv_tf = self._get_field(fields, "uvTransformation")
                if uv_tf and uv_tf[2][0] == "object":
                    uv_tc = uv_tf[1]
                    self._set_field_simple(fields, "uvTransformation", uv_tc, normalize_uv_transformation(uv_tf[2]))
            fields = self._dedupe_fields_simple(fields)
            fields = self._reorder_fields_simple(
                fields,
                [
                    "channelTypes",
                    "color",
                    "opacity",
                    "uid",
                    "tags",
                    "uvGrid",
                    "uvSamplingWrap",
                    "uvTransformation",
                ],
            )

        if obj_name == "DataTweakFloat":
            ident = self._extract_identifier((obj_name, fields))
            uid = self._extract_uid((obj_name, fields))
            if ident == "brush_pattern":
                if uid in ((200, 0), 200):
                    uv_field = self._get_field(fields, "uvGrid")
                    uv_tc = uv_field[1] if uv_field else (self._get_member_type(obj_name, "uvGrid") or self.CODE_OBJECT)
                    self._set_field_simple(fields, "uvGrid", uv_tc, ("object_null", b""))
            elif ident == "Noise_Opacity" and uid in ((152, 0), 152):
                uv_field = self._get_field(fields, "uvGrid")
                uv_tc = uv_field[1] if uv_field else (self._get_member_type(obj_name, "uvGrid") or self.CODE_OBJECT)
                self._set_field_simple(fields, "uvGrid", uv_tc, ("object_null", b""))
            if uid in ((200, 0), 200, (152, 0), 152):
                if not self._get_field(fields, "uvSamplingWrap"):
                    wrap_tc = self._get_member_type(obj_name, "uvSamplingWrap") or 0x09
                    self._set_field_simple(fields, "uvSamplingWrap", wrap_tc, ("primitive", wrap_tc, self._pack_primitive(wrap_tc, 3)))
                if not self._get_field(fields, "tags"):
                    tags_tc = self._get_member_type(obj_name, "tags") or 0x0B
                    self._set_field_simple(fields, "tags", tags_tc, ("primitive", tags_tc, b"\x00\x00\x00\x00"))
                uv_tf = self._get_field(fields, "uvTransformation")
                if uv_tf and uv_tf[2][0] == "object":
                    uv_tc = uv_tf[1]
                    self._set_field_simple(fields, "uvTransformation", uv_tc, normalize_uv_transformation(uv_tf[2]))
            fields = self._reorder_fields_simple(
                fields,
                [
                    "identifier",
                    "uid",
                    "value",
                    "urlToSbsRes",
                    "resource",
                    "randomDrawsCountLog2",
                    "randomDrawsSeed",
                    "stampCycleCount",
                    "strokeWires",
                    "tags",
                    "uvGrid",
                    "uvSamplingWrap",
                    "uvTransformation",
                ],
            )

        if obj_name == "Aluminum":
            ma_field = self._get_field(fields, "maskActions")
            if ma_field:
                ma_tc = ma_field[1]
                self._set_field_simple(fields, "maskActions", ma_tc, ("object_null", b""))

        return (obj_name, fields)

    def _tweak_identifier_str(self, tfields):
        for f in tfields:
            if f[0] == "identifier" and f[2][0] == "string":
                try:
                    return f[2][1].decode("utf-8", "replace")
                except Exception:
                    return None
        return None

    def _migrate_baking_tweaklist(self, fields):
        """Reorder/rename/retype a BakingTweakList's tweaks to the v10 baker schema.
        v11 bakers added/renamed parameters (e.g. Detail.CageMode int ->
        Detail.UseCage bool); v10's bakers read tweaks positionally/by id, so v11
        tweak lists must be projected onto the v10 schema for that bakerId."""
        baker = self._get_field(fields, "bakerId")
        if not baker or baker[2][0] != "string":
            return fields
        try:
            baker_id = baker[2][1].decode("utf-8", "replace")
        except Exception:
            return fields
        # v11 uses short baker ids; v10 expects fully-qualified ones. Rename and
        # rewrite the bakerId field so v10's baker lookup succeeds.
        v10_baker_id = runtime.BAKING_BAKER_ID_RENAME.get(baker_id, baker_id)
        if v10_baker_id != baker_id:
            self._set_field(fields, "bakerId", baker[1], ("string", v10_baker_id.encode("utf-8")))
            baker_id = v10_baker_id
        schema = runtime.V10_BAKING_SCHEMA.get(baker_id)
        tw = self._get_field(fields, "tweaks")
        if not schema or not tw or tw[2][0] != "array":
            return fields
        elem_kind, elems = tw[2][1]
        if elem_kind != "object":
            return fields

        type_for_value = {"DataTweakBool": 0x0A, "DataTweakInt": 0x05,
                          "DataTweakFloat": 0x01}

        by_id = {}
        for elem in elems:
            if elem[0] != "object" or not isinstance(elem[1], tuple):
                continue
            objtype, tfields = elem[1]
            ident = self._tweak_identifier_str(tfields)
            if ident is None:
                continue
            new_id = runtime.BAKING_TWEAK_RENAME.get(ident, ident)
            tfields = [list(f) for f in tfields]
            if new_id != ident:
                for f in tfields:
                    if f[0] == "identifier":
                        f[2] = ("string", new_id.encode("utf-8"))
            by_id[new_id] = (objtype, [tuple(f) for f in tfields])

        new_elems = []
        for v10_id, v10_type in schema:
            if v10_id not in by_id:
                continue  # leave missing params out; v10 supplies its own default
            objtype, tfields = by_id[v10_id]
            if objtype != v10_type:
                # retype (e.g. DataTweakInt -> DataTweakBool): rebuild value field
                pv = type_for_value.get(v10_type)
                tfields = [list(f) for f in tfields]
                for f in tfields:
                    if f[0] == "value" and pv is not None:
                        old = f[2]
                        raw = old[2] if old[0] == "primitive" else b""
                        if pv == 0x0A:  # bool: nonzero -> 0x01 else 0x00
                            f[2] = ("primitive", 0x0A, b"\x01" if any(raw) else b"\x00")
                        else:
                            f[2] = ("primitive", pv, raw)
                tfields = [tuple(f) for f in tfields]
                objtype = v10_type
            new_elems.append(("object", (objtype, tfields)))

        self._set_field(fields, "tweaks", tw[1], ("array", ("object", new_elems)))
        return fields

    def _apply_downgrade_transforms(self, obj_name, fields):
        if not obj_name:
            return (obj_name, fields)

        new_name = obj_name

        if obj_name == "BakingTweakList":
            fields = self._migrate_baking_tweaklist(fields)

        if obj_name == "DataActionFill":
            fields = [f for f in fields if f[0] != "symmetry"]
            fields = self._apply_data_action_fill_bitmap(fields)

        if obj_name == "DataActionPaint":
            brush = next((f for f in fields if f[0] == "brush"), None)
            if brush and brush[2][0] == "object" and brush[2][1][0] == "DataBrushFill":
                fields = [f for f in fields if f[0] != "sourceTransparent"]

        # ponytail: object->primitive resolutionOverride is inline-format (v10-) only.
        # v11+ (registry) keeps the DataResolutionOverride struct, so skip it there.
        if obj_name in ("DataSourceText", "DataSourceVectorial") and runtime.PROFILE.data.get("target_format", "inline") != "registry":
            res_idx = next((i for i, f in enumerate(fields) if f[0] == "resolutionOverride"), None)
            if res_idx is not None:
                name, type_code, value, overridden = fields[res_idx]
                if not overridden and value[0] == "object" and value[1][0] == "DataResolutionOverride":
                    _, res_fields = value[1]
                    mode = next((f for f in res_fields if f[0] == "mode"), None)
                    manual = next((f for f in res_fields if f[0] == "manualValue"), None)
                    mode_raw = b"\x00\x00\x00\x00"
                    if mode and mode[2][0] == "primitive":
                        mode_raw = mode[2][2]
                        if len(mode_raw) < 4:
                            mode_raw = mode_raw + b"\x00" * (4 - len(mode_raw))
                    manual_entry = None
                    if manual and manual[2][0] == "primitive":
                        manual_entry = (
                            "manualResolutionOverride",
                            manual[2][1],
                            manual[2],
                            False,
                        )
                    res_entry = (
                        "resolutionOverride",
                        0x09,
                        ("primitive", 0x09, mode_raw[:4]),
                        False,
                    )
                    new_fields = []
                    for idx, f in enumerate(fields):
                        if f[0] == "manualResolutionOverride":
                            continue
                        if idx == res_idx:
                            if manual_entry:
                                new_fields.append(manual_entry)
                            new_fields.append(res_entry)
                            continue
                        new_fields.append(f)
                    fields = new_fields

        if obj_name.startswith("DataAction"):
            if not self._get_field(fields, "enabled"):
                enabled_tc = self._get_member_type(obj_name, "enabled")
                if enabled_tc:
                    self._set_field(fields, "enabled", enabled_tc, ("primitive", enabled_tc, self._pack_primitive(enabled_tc, 1)))
            ch_field = self._get_field(fields, "channelTypes")
            if not ch_field:
                ch_tc = self._get_member_type(obj_name, "channelTypes")
                if ch_tc:
                    self._set_field(fields, "channelTypes", ch_tc, ("primitive", ch_tc, self._pack_primitive(ch_tc, -1)))
            elif ch_field[2][0] == "primitive":
                raw = ch_field[2][2]
                if len(raw) >= 4 and struct.unpack('<i', raw[:4])[0] == 0x7FFFFF:
                    ch_tc = ch_field[1]
                    self._set_field(fields, "channelTypes", ch_tc, ("primitive", ch_tc, self._pack_primitive(ch_tc, -1)))

        if obj_name.startswith("DataLayer"):
            if not self._get_field(fields, "maskEnabled"):
                mask_tc = self._get_member_type(obj_name, "maskEnabled")
                if mask_tc:
                    self._set_field(fields, "maskEnabled", mask_tc, ("primitive", mask_tc, self._pack_primitive(mask_tc, 1)))

        if obj_name in ("DataSourceUniform", "DataTweakFloat", "DataTweakFloat3", "DataTweakFloat4"):
            if obj_name == "DataSourceUniform":
                uid = self._extract_uid((obj_name, fields))
                if uid in ((137, 0), 137):
                    uv_field = self._get_field(fields, "uvGrid")
                    if uv_field:
                        uv_tc = uv_field[1]
                        self._set_field(fields, "uvGrid", uv_tc, ("object_null", b""))
                fields = self._dedupe_fields_simple(fields)
                order = {
                    "channelTypes": 0,
                    "color": 1,
                    "opacity": 2,
                    "uid": 3,
                    "tags": 4,
                    "uvGrid": 5,
                    "uvSamplingWrap": 5,
                    "uvTransformation": 6,
                }
            else:
                if obj_name == "DataTweakFloat":
                    ident = self._extract_identifier((obj_name, fields))
                    uid = self._extract_uid((obj_name, fields))
                    if ident == "brush_pattern" and uid in ((200, 0), 200):
                        uv_field = self._get_field(fields, "uvGrid")
                        if uv_field:
                            uv_tc = uv_field[1]
                            self._set_field(fields, "uvGrid", uv_tc, ("object_null", b""))
                    elif ident == "Noise_Opacity" and uid in ((152, 0), 152):
                        uv_field = self._get_field(fields, "uvGrid")
                        if uv_field:
                            uv_tc = uv_field[1]
                            self._set_field(fields, "uvGrid", uv_tc, ("object_null", b""))
                order = {
                    "identifier": 0,
                    "uid": 1,
                    "value": 2,
                    "urlToSbsRes": 3,
                    "uvGrid": 4,
                    "uvSamplingWrap": 5,
                    "uvTransformation": 6,
                }
            fields[:] = sorted(
                enumerate(fields),
                key=lambda item: (order.get(item[1][0], 999), item[0]),
            )
            fields[:] = [item[1] for item in fields]

        if obj_name == "DataActionFilterLevels":
            level_fields = []
            for name in ("inputMinimum", "inputMaximum", "gamma", "outputMinimum", "outputMaximum", "clamp"):
                field = self._get_field(fields, name)
                if field:
                    level_fields.append((name, field[1], field[2], False))
            if level_fields:
                levels_tc = self._get_member_type(obj_name, "levels")
                levels_field = self._get_field(fields, "levels")
                if levels_field:
                    levels_tc = levels_field[1]
                if levels_tc is None:
                    levels_tc = self.CODE_OBJECT
                self._set_field(fields, "levels", levels_tc, ("object", ("DataLevels", level_fields)))
                for name in ("inputMinimum", "inputMaximum", "gamma", "outputMinimum", "outputMaximum", "clamp"):
                    self._remove_field(fields, name)

        if obj_name == "DataMaterial":
            maps_tc = self._get_member_type(obj_name, "additionalMaps") or self.CODE_ARRAY
            if not self._get_field(fields, "additionalMaps"):
                self._set_field(fields, "additionalMaps", maps_tc, ("array", ("object", [])))

        if obj_name == "DataDocument":
            doc_norm = self._get_field(fields, "normalMapSyscoord")
            mats = self._get_field(fields, "materials")
            if mats and mats[2][0] == "array":
                elem_kind, elems = mats[2][1]
                if elem_kind == "object":
                    first_norm = None
                    new_elems = []
                    for elem in elems:
                        if elem[0] != "object":
                            new_elems.append(elem)
                            continue
                        mat_type, mat_fields = elem[1]
                        if mat_fields is None:
                            new_elems.append(elem)
                            continue
                        norm_field = self._get_field(mat_fields, "normalMapSyscoord")
                        if first_norm is None and norm_field:
                            first_norm = norm_field
                        if norm_field:
                            self._remove_field(mat_fields, "normalMapSyscoord")
                        new_elems.append(("object", (mat_type, mat_fields)))
                    if first_norm and not doc_norm:
                        nm_tc = self._get_member_type(obj_name, "normalMapSyscoord") or first_norm[1]
                        self._set_field(fields, "normalMapSyscoord", nm_tc, first_norm[2])
                    self._set_field(fields, "materials", mats[1], ("array", ("object", new_elems)))
            if not self._get_field(fields, "tangentSpaceMode"):
                ts_tc = self._get_member_type(obj_name, "tangentSpaceMode") or 9
                self._set_field(fields, "tangentSpaceMode", ts_tc, ("primitive", ts_tc, self._pack_primitive(ts_tc, 0)))
            for name in ("colorManagementACE", "colorManagementOCIO"):
                cm_field = self._get_field(fields, name)
                if cm_field:
                    cm_tc = self._get_member_type(obj_name, name) or cm_field[1]
                    cm_val = cm_field[2]
                    if cm_val[0] == "object" and isinstance(cm_val[1], tuple):
                        self._set_field(fields, name, cm_tc, ("object", ("", None)))
                    else:
                        self._set_field(fields, name, cm_tc, ("object_null", b""))

        if obj_name == "DataStackActions":
            actions_field = self._get_field(fields, "actions")
            if actions_field and not self._get_field(fields, "items"):
                items_tc = self._get_member_type(obj_name, "items") or actions_field[1]
                self._set_field(fields, "items", items_tc, actions_field[2])
                self._remove_field(fields, "actions")
            uid = self._extract_uid((obj_name, fields))
            if uid in ((27, 0), 27, (125, 0), 125):
                self._remove_field(fields, "maskActions")
            fields = self._reorder_fields_simple(
                fields,
                [
                    "items",
                    "uid",
                    "tags",
                    "colorTag",
                    "enabled",
                    "enabledGeometryMask",
                    "enabledMeshDefault",
                    "enabledMeshList",
                    "enabledUVTileDefault",
                    "enabledUVTileList",
                    "gammaCompensation",
                    "geometryMaskType",
                    "label",
                    "Layer",
                    "maskActions",
                    "maskEnabled",
                    "maskInitial",
                    "perChannelBlending",
                ],
            )

        if obj_name == "DataMaterialStack":
            self._remove_field(fields, "type")

        if obj_name == "Aluminum":
            ma_field = self._get_field(fields, "maskActions")
            if ma_field:
                ma_tc = ma_field[1]
                self._set_field(fields, "maskActions", ma_tc, ("object_null", b""))

        if obj_name == "DataSourceProcedural":
            proc_field = self._get_field(fields, "procedural")
            if proc_field and proc_field[2][0] == "object":
                proc_type, proc_fields = proc_field[2][1]
                tweaks_field = self._get_field(proc_fields, "tweaks")
                if tweaks_field and tweaks_field[2][0] == "array":
                    elem_kind, elems = tweaks_field[2][1]
                    if elem_kind == "object":
                        new_elems = []
                        moved = False
                        for elem in elems:
                            if elem[0] != "object":
                                new_elems.append(elem)
                                continue
                            tweak_type, tweak_fields = elem[1]
                            ident = self._extract_identifier((tweak_type, tweak_fields))
                            if tweak_type == "DataTweakFloat" and ident in ("Level", "Contrast", "Noise_Opacity", "Occlusion_Amount"):
                                val_field = self._get_field(tweak_fields, "value")
                                if val_field and val_field[2][0] == "primitive":
                                    dst_tc = self._get_member_type(obj_name, ident) or val_field[1]
                                    self._set_field(fields, ident, dst_tc, val_field[2])
                                    moved = True
                                    continue
                            new_elems.append(elem)
                        if moved:
                            tweaks_val = ("array", ("object", new_elems))
                            self._set_field(proc_fields, "tweaks", tweaks_field[1], tweaks_val)
                            self._set_field(fields, "procedural", proc_field[1], ("object", (proc_type, proc_fields)))

        if obj_name == "DataLayerGroup":
            # Only synthesize fields v10 requires that are actually missing; never
            # overwrite values present in the v11 source (it already has the correct
            # enabled/maskEnabled/GUIcollapsedState etc.). Overwriting them with
            # hardcoded defaults corrupted the layer group state.
            if not self._get_field(fields, "enabled"):
                enabled_tc = self._get_member_type(obj_name, "enabled") or 10
                self._set_field(fields, "enabled", enabled_tc, ("primitive", enabled_tc, self._pack_primitive(enabled_tc, 1)))
            if not self._get_field(fields, "label"):
                label_tc = self._get_member_type(obj_name, "label") or self.CODE_STRING
                self._set_field(fields, "label", label_tc, ("string", b"Group"))
            if not self._get_field(fields, "maskInitial"):
                mi_tc = self._get_member_type(obj_name, "maskInitial") or 11
                self._set_field(fields, "maskInitial", mi_tc, ("primitive", mi_tc, self._pack_primitive(mi_tc, 1)))
            if not self._get_field(fields, "maskEnabled"):
                mask_tc = self._get_member_type(obj_name, "maskEnabled") or 10
                self._set_field(fields, "maskEnabled", mask_tc, ("primitive", mask_tc, self._pack_primitive(mask_tc, 1)))
            if not self._get_field(fields, "perChannelBlending"):
                pcb_tc = self._get_member_type(obj_name, "perChannelBlending") or self.CODE_ARRAY
                self._set_field(fields, "perChannelBlending", pcb_tc, self._build_per_channel_blending())
            if not self._get_field(fields, "GUIcollapsedState"):
                gui_tc = self._get_member_type(obj_name, "GUIcollapsedState") or 10
                self._set_field(fields, "GUIcollapsedState", gui_tc, ("primitive", gui_tc, self._pack_primitive(gui_tc, 1)))

        # UIDs are object identities and are referenced elsewhere in the document.
        # The v11 file's UIDs are internally consistent, so they pass through
        # unchanged. The previous code hardcoded specific UID numbers for
        # CameraSettingsData/DataColorProfileParameters/DataPostEffectsParameters,
        # which rewrote definitions without updating their references -> dangling
        # references -> "Null object access" in v10's loader.

        # IraySettingsData: pass mdls/tweaks through unchanged. The v10->v11 upgrade
        # handlers do not touch Iray settings (confirmed: v11 source has the same 20
        # tweaks in the same order as native v10), so the previous hardcoded reorder
        # (a fixed 15-identifier list that silently dropped the other tweaks and
        # reassigned UIDs) was purely destructive and file-specific. Generic
        # type-code mapping + schema projection handle the rest.

        if obj_name == "DataActionGroup":
            if not self._get_field(fields, "label"):
                label_tc = self._get_member_type(obj_name, "label") or self.CODE_STRING
                self._set_field(fields, "label", label_tc, ("string", b""))
            sel_tc = self._get_member_type(obj_name, "selection") or 11
            self._set_field(fields, "selection", sel_tc, ("primitive", sel_tc, self._pack_primitive(sel_tc, 0)))
            tags_tc = self._get_member_type(obj_name, "tags") or 11
            self._set_field(fields, "tags", tags_tc, ("primitive", tags_tc, self._pack_primitive(tags_tc, 0)))
            if not self._get_field(fields, "perChannelBlending"):
                pcb_tc = self._get_member_type(obj_name, "perChannelBlending") or self.CODE_ARRAY
                self._set_field(fields, "perChannelBlending", pcb_tc, self._build_action_group_blending())

        # Only synthesize these v10-required fields when the v11 source lacks them
        # (e.g. an empty project). Never overwrite real paint data (a painted
        # project has actual projMatrix/duplicateMatrix/strokes/stencils that must
        # pass through unchanged).
        identity = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        )
        if obj_name == "DataStroke" or obj_name.startswith("DataDecalHit"):
            if not self._get_field(fields, "duplicateCount"):
                count_tc = self._get_member_type(obj_name, "duplicateCount") or 11
                self._set_field(fields, "duplicateCount", count_tc, ("primitive", count_tc, self._pack_primitive(count_tc, 0)))
            if not self._get_field(fields, "duplicateMatrix"):
                self._set_field(fields, "duplicateMatrix", 14, ("primitive", 14, self._pack_primitive(14, identity)))

        if obj_name in ("DataActionPaint", "DataActionEraser", "DataActionPaintProj"):
            if not self._get_field(fields, "projMatrix"):
                self._set_field(fields, "projMatrix", 14, ("primitive", 14, self._pack_primitive(14, identity)))
            if not self._get_field(fields, "sourceStencil"):
                stencil_tc = self._get_member_type(obj_name, "sourceStencil") or self.CODE_NULL
                self._set_field(fields, "sourceStencil", stencil_tc, ("null", b""))
            strokes_field = self._get_field(fields, "strokes3D")
            if not strokes_field or strokes_field[2][0] != "array":
                strokes_tc = self._get_member_type(obj_name, "strokes3D") or self.CODE_ARRAY
                self._set_field(fields, "strokes3D", strokes_tc, ("array", ("object", [])))

        if obj_name == "DataActionStencil":
            stencil_field = self._get_field(fields, "stencilMatrix")
            if stencil_field:
                proj_tc = self._get_member_type(obj_name, "projMatrix") or stencil_field[1]
                self._set_field(fields, "projMatrix", proj_tc, stencil_field[2])
                self._remove_field(fields, "stencilMatrix")
            stencil_tc = self._get_member_type(obj_name, "sourceStencil") or self.CODE_NULL
            self._set_field(fields, "sourceStencil", stencil_tc, ("null", b""))
            new_name = "DataActionPaintProj"

        strokes_field = self._get_field(fields, "strokes3D")
        brush_field = self._get_field(fields, "brush")
        if strokes_field and brush_field and strokes_field[2][0] == "array" and brush_field[2][0] == "object":
            brush_type, brush_fields = brush_field[2][1]
            emitter_field = self._get_field(brush_fields, "stampsEmitter")
            emitter_fields = None
            if emitter_field and emitter_field[2][0] == "object":
                emitter_type, emitter_fields = emitter_field[2][1]
                if emitter_type != "DataEmitterStroke3D":
                    emitter_fields = None
            if emitter_fields is None:
                size_enabled = False
                flow_enabled = brush_type == "DataBrushRibbon"
            else:
                size_field = self._get_field(emitter_fields, "pressureSizeEnabled")
                flow_field = self._get_field(emitter_fields, "pressureFlowEnabled")
                size_enabled = self._primitive_to_bool(size_field[2]) if size_field else False
                flow_enabled = self._primitive_to_bool(flow_field[2]) if flow_field else False

            elem_kind, elems = strokes_field[2][1]
            if elem_kind == "object":
                for i, stroke in enumerate(elems):
                    if stroke[0] != "object":
                        continue
                    stroke_type, stroke_fields = stroke[1]
                    points_field = self._get_field(stroke_fields, "points")
                    if not points_field or points_field[2][0] != "array":
                        continue
                    p_kind, points = points_field[2][1]
                    if p_kind != "object":
                        continue
                    new_points = []
                    for point in points:
                        if point[0] != "object":
                            new_points.append(point)
                            continue
                        point_type, point_fields = point[1]
                        pressure_field = self._get_field(point_fields, "pressure")
                        pressure_val = None
                        if pressure_field and pressure_field[2][0] == "primitive":
                            pressure_val = self._primitive_to_float(pressure_field[2])
                        size_val = pressure_val if size_enabled and pressure_val is not None else 1.0
                        opacity_val = pressure_val if flow_enabled and pressure_val is not None else 1.0
                        size_tc = self._get_member_type(point_type, "size")
                        if size_tc is None:
                            size_tc = pressure_field[1] if pressure_field else 5
                        opacity_tc = self._get_member_type(point_type, "opacity")
                        if opacity_tc is None:
                            opacity_tc = size_tc
                        self._set_field(point_fields, "size", size_tc, ("primitive", size_tc, self._pack_primitive(size_tc, size_val)))
                        self._set_field(point_fields, "opacity", opacity_tc, ("primitive", opacity_tc, self._pack_primitive(opacity_tc, opacity_val)))
                        self._remove_field(point_fields, "pressure")
                        new_points.append(("object", (point_type, point_fields)))
                    self._set_field(stroke_fields, "points", points_field[1], ("array", ("object", new_points)))
                    elems[i] = ("object", (stroke_type, stroke_fields))
                self._set_field(fields, "strokes3D", strokes_field[1], ("array", ("object", elems)))

        return (new_name, fields)

