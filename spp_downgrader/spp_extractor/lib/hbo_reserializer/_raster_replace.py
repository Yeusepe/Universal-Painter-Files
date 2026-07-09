"""Graph rewrites that attach prepared raster fallback resources.

Raster replacements are intentionally boundary-sized. A mask fallback keeps the
layer and replaces only `maskActions`; local content fallbacks replace either an
unsupported source inside an existing fill action or one unsupported action/group
inside a stack.
"""
import struct

from ._raster_plan import (
    S_CONTENT_ACTION,
    S_GROUP,
    S_MASK_STACK,
    S_FULL_STACK_CHANNEL,
    S_SOURCE,
    S_LAYER,
    collect_raster_requests,
)


# Fallback for capture manifests produced before index metadata existed. Indexed
# channel mapping is preferred because it comes directly from DataChannel.type.
_CHANNEL_TYPE_BY_NAME = {
    "basecolor": 0,
    "basecolour": 0,
    "base": 0,
    "diffuse": 0,
    "height": 1,
    "metallic": 7,
    "metalness": 7,
    "roughness": 13,
    "normal": 22,
    "normalopengl": 22,
    "normaldirectx": 22,
}


def _field(fields, name):
    for f in fields or []:
        if f[0] == name:
            return f
    return None


def _set_field(fields, name, type_code, value):
    for i, f in enumerate(fields):
        if f[0] == name:
            fields[i] = (name, type_code, value)
            return
    fields.append((name, type_code, value))


def _primitive_int(value):
    if not value or value[0] != "primitive":
        return None
    raw = value[2]
    if not raw:
        return None
    return int.from_bytes(raw[:min(len(raw), 8)], "little", signed=False)


def _uid_of(obj):
    if not obj or obj[1] is None:
        return None
    f = _field(obj[1], "uid")
    return _primitive_int(f[2]) if f else None


def _object_array_from_field(obj, field_name):
    if not obj or obj[1] is None:
        return []
    f = _field(obj[1], field_name)
    if not f or f[2][0] != "array":
        return []
    elem_kind, elems = f[2][1]
    if elem_kind != "object":
        return []
    return [elem[1] for elem in elems if elem and elem[0] == "object" and isinstance(elem[1], tuple)]


def _max_uid_value(obj):
    max_uid = 0

    def visit(o):
        nonlocal max_uid
        if not o or o[1] is None:
            return
        uid = _uid_of(o)
        if uid is not None:
            max_uid = max(max_uid, uid)
        for _name, _tc, value in o[1]:
            if value[0] == "object" and isinstance(value[1], tuple):
                visit(value[1])
            elif value[0] == "array" and value[1][0] == "object":
                for elem in value[1][1]:
                    if elem and elem[0] == "object" and isinstance(elem[1], tuple):
                        visit(elem[1])

    visit(obj)
    return max_uid


class _UidGen:
    def __init__(self, start):
        self.value = int(start)

    def next(self):
        self.value += 1
        return self.value


def _prim(type_code, raw):
    return ("primitive", type_code, raw)


def _p_i64(value):
    return _prim(12, struct.pack("<q", int(value)))


def _p_i32(value):
    return _prim(9, struct.pack("<i", int(value)))


def _p_bool(value):
    return _prim(10, bytes([1 if value else 0]))


def _p_float(value):
    return _prim(1, struct.pack("<f", float(value)))


def _string(text):
    return ("string", str(text).encode("utf-8"))


def _channel_key(text):
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _entry_int(entry, key):
    value = entry.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _mask_for_channel_type(channel_type):
    if channel_type is None or channel_type < 0 or channel_type >= 63:
        return None
    return 1 << int(channel_type)


def _channel_mask_lookup(root):
    """Map (material_index, stack_index, channel_index) to channelTypes bitmask."""
    out = {}
    if not root:
        return out

    if root[0] == "DataMaterial":
        materials = [root]
    else:
        materials = _object_array_from_field(root, "materials")

    for mi, material in enumerate(materials):
        if material[0] == "DataMaterialStack":
            stacks = [material]
        else:
            stacks = _object_array_from_field(material, "stacks")
        for si, stack in enumerate(stacks):
            channels = _object_array_from_field(stack, "channels")
            for ci, channel in enumerate(channels):
                type_field = _field(channel[1], "type")
                channel_type = _primitive_int(type_field[2]) if type_field else None
                mask = _mask_for_channel_type(channel_type)
                if mask is not None:
                    out[(mi, si, ci)] = mask
    return out


def _entry_channel_mask(entry, channel_lookup):
    channel_type = _entry_int(entry, "channel_type")
    mask = _mask_for_channel_type(channel_type)
    if mask is not None:
        return mask

    mi = _entry_int(entry, "material_index")
    si = _entry_int(entry, "stack_index")
    ci = _entry_int(entry, "channel_index")
    if mi is not None and si is not None and ci is not None:
        mask = channel_lookup.get((mi, si, ci))
        if mask is not None:
            return mask

    channel_name = _channel_key(entry.get("channel"))
    return _mask_for_channel_type(_CHANNEL_TYPE_BY_NAME.get(channel_name))


def _bitmap_source(url, channel_mask, uid_gen):
    bitmap = ("DataBitmap", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("alphaType", 9, _p_i32(0)),
        ("urlToBitmapRes", 16, _string(url)),
    ])
    return ("DataSourceBitmap", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("channelTypes", 12, _p_i64(channel_mask)),
        ("opacity", 1, _p_float(1.0)),
        ("bitmap", 18, ("object", bitmap)),
    ])


def _bitmap_sources_from_entries(entries, channel_lookup, uid_gen):
    sources = []
    combined_mask = 0
    skipped = 0
    seen = set()
    for entry in entries or []:
        if entry.get("kind") == "mask" or not entry.get("url"):
            continue
        channel_mask = _entry_channel_mask(entry, channel_lookup)
        if channel_mask is None:
            skipped += 1
            continue
        dedupe_key = (entry["url"], channel_mask)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        sources.append(("object", _bitmap_source(entry["url"], channel_mask, uid_gen)))
        combined_mask |= channel_mask
    return sources, combined_mask, skipped


def _bitmap_fill_action(entries, channel_lookup, uid_gen, label):
    sources, combined_mask, skipped = _bitmap_sources_from_entries(entries, channel_lookup, uid_gen)
    if not sources:
        return None, skipped
    return ("DataActionFill", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("channelTypes", 12, _p_i64(combined_mask)),
        ("enabled", 10, _p_bool(True)),
        ("filtering", 9, _p_i32(2)),
        ("projection", 9, _p_i32(1)),
        ("label", 16, _string(label)),
        ("sources", 19, ("array", ("object", sources))),
    ]), skipped


def _bitmap_fill_stack(url, uid_gen):
    fill = ("DataActionFill", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("channelTypes", 12, _p_i64(0)),
        ("enabled", 10, _p_bool(True)),
        ("filtering", 9, _p_i32(2)),
        ("projection", 9, _p_i32(1)),
        ("label", 16, _string("Universal SPP raster mask")),
        ("sources", 19, ("array", ("object", [
            ("object", _bitmap_source(url, 1, uid_gen)),
        ]))),
    ])
    stack = ("DataStackActions", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("items", 19, ("array", ("object", [("object", fill)]))),
    ])
    return ("object", stack)


def _raster_action_stack(entries, channel_lookup, uid_gen, label):
    fill, skipped = _bitmap_fill_action(entries, channel_lookup, uid_gen, label)
    if not fill:
        return None, skipped
    stack = ("DataStackActions", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("items", 19, ("array", ("object", [("object", fill)]))),
    ])
    return ("object", stack), skipped


def _raster_layer_stack(entries, channel_lookup, uid_gen):
    actions_value, skipped = _raster_action_stack(
        entries,
        channel_lookup,
        uid_gen,
        "Universal SPP raster full stack",
    )
    if not actions_value:
        return None, skipped
    layer = ("DataLayerColor", [
        ("actions", 18, actions_value),
        ("colorTag", 9, _p_i32(0)),
        ("enabled", 10, _p_bool(True)),
        ("enabledGeometryMask", 10, _p_bool(True)),
        ("enabledMeshDefault", 10, _p_bool(True)),
        ("enabledMeshList", 17, ("array", ("object", []))),
        ("enabledUVTileDefault", 10, _p_bool(True)),
        ("enabledUVTileList", 17, ("array", ("object", []))),
        ("gammaCompensation", 10, _p_bool(False)),
        ("geometryMaskType", 9, _p_i32(0)),
        ("label", 16, _string("Universal SPP raster full stack")),
        ("maskActions", 18, ("object_null", b"")),
        ("maskEnabled", 10, _p_bool(True)),
        ("maskInitial", 9, _p_i32(1)),
        ("perChannelBlending", 17, ("array", ("object", []))),
        ("uid", 12, _p_i64(uid_gen.next())),
    ])
    stack = ("DataStackLayers", [
        ("items", 19, ("array", ("object", [("object", layer)]))),
        ("uid", 12, _p_i64(uid_gen.next())),
    ])
    return ("object", stack), skipped


def _choose_mask_url(entries):
    for entry in entries or []:
        if entry.get("kind") == "mask" and entry.get("url"):
            return entry["url"]
    for entry in entries or []:
        if entry.get("url"):
            return entry["url"]
    return None


def _replacement_maps(root, replacements, dataset, target, requests=None):
    requests = list(requests or collect_raster_requests(root, dataset=dataset, target=target))
    mask_urls = {}
    source_entries = {}
    action_entries = {}
    layer_entries = {}
    full_stack_entries = {}
    for req in requests:
        entries = replacements.get(req.get("id")) or []
        scope = req.get("scope")
        if scope == S_MASK_STACK:
            url = _choose_mask_url(entries)
            if url and req.get("layer_uid") is not None:
                mask_urls[int(req["layer_uid"])] = url
        elif scope == S_SOURCE and req.get("object_uid") is not None and entries:
            source_entries[int(req["object_uid"])] = entries
        elif scope in (S_CONTENT_ACTION, S_GROUP) and req.get("object_uid") is not None and entries:
            action_entries[int(req["object_uid"])] = entries
        elif scope == S_LAYER and entries:
            layer_uid = req.get("layer_uid")
            object_uid = req.get("object_uid")
            uid = layer_uid if layer_uid is not None else object_uid
            if uid is not None:
                layer_entries[int(uid)] = entries
        elif scope == S_FULL_STACK_CHANNEL and req.get("stack_uid") is not None and entries:
            full_stack_entries[int(req["stack_uid"])] = entries
    return mask_urls, source_entries, action_entries, layer_entries, full_stack_entries


def _empty_stats():
    return {
        "mask_stacks_replaced": 0,
        "sources_replaced": 0,
        "content_actions_replaced": 0,
        "layers_replaced": 0,
        "full_stacks_replaced": 0,
        "source_replacements_skipped": 0,
        "content_actions_skipped": 0,
        "layers_skipped": 0,
        "full_stacks_skipped": 0,
        "channel_assets_skipped": 0,
    }


def apply_raster_replacements(root, replacements, *, dataset=None, target=None, requests=None):
    stats = _empty_stats()
    if not replacements:
        return root, stats

    mask_urls, source_entries, action_entries, layer_entries, full_stack_entries = _replacement_maps(
        root,
        replacements,
        dataset,
        target,
        requests=requests,
    )
    if not (mask_urls or source_entries or action_entries or layer_entries or full_stack_entries):
        return root, stats

    channel_lookup = _channel_mask_lookup(root)
    uid_gen = _UidGen(_max_uid_value(root) + 1000)

    def replace_array_elements(elems, allow_sources):
        new_elems = []
        changed = False
        for elem in elems:
            if not (elem and elem[0] == "object" and isinstance(elem[1], tuple)):
                new_elems.append(elem)
                continue

            child = elem[1]
            uid = _uid_of(child)
            if allow_sources and uid in source_entries:
                sources, _mask, skipped = _bitmap_sources_from_entries(
                    source_entries[uid],
                    channel_lookup,
                    uid_gen,
                )
                stats["channel_assets_skipped"] += skipped
                if sources:
                    new_elems.extend(sources)
                    stats["sources_replaced"] += 1
                    changed = True
                    continue
                stats["source_replacements_skipped"] += 1

            if uid in action_entries and child[0].startswith("DataAction"):
                fill, skipped = _bitmap_fill_action(
                    action_entries[uid],
                    channel_lookup,
                    uid_gen,
                    "Universal SPP raster fallback",
                )
                stats["channel_assets_skipped"] += skipped
                if fill:
                    new_elems.append(("object", fill))
                    stats["content_actions_replaced"] += 1
                    changed = True
                    continue
                stats["content_actions_skipped"] += 1

            new_elems.append(elem)
        return new_elems, changed

    def entries_for_stack(entries, ctx):
        mi = ctx.get("material_index")
        si = ctx.get("stack_index")
        out = []
        for entry in entries or []:
            emi = _entry_int(entry, "material_index")
            esi = _entry_int(entry, "stack_index")
            if emi is None or esi is None:
                continue
            if mi is not None and emi != mi:
                continue
            if si is not None and esi != si:
                continue
            out.append(entry)
        return out

    def array_child_ctx(ctx, obj_name, field_name, index):
        child = dict(ctx)
        if obj_name == "DataDocument" and field_name == "materials":
            child["material_index"] = index
            child["stack_index"] = None
        elif obj_name == "DataMaterial" and field_name == "stacks":
            child["stack_index"] = index
        return child

    def visit(obj, ctx=None):
        ctx = ctx or {}
        if not obj or obj[1] is None:
            return
        obj_name, fields = obj
        if obj_name in ("DataLayerColor", "DataLayerGroup"):
            uid = _uid_of(obj)
            url = mask_urls.get(uid)
            if url:
                existing = _field(fields, "maskActions")
                tcode = existing[1] if existing else 18
                _set_field(fields, "maskActions", tcode, _bitmap_fill_stack(url, uid_gen))
                stats["mask_stacks_replaced"] += 1
            entries = layer_entries.get(uid)
            if entries and obj_name == "DataLayerColor":
                actions_value, skipped = _raster_action_stack(
                    entries,
                    channel_lookup,
                    uid_gen,
                    "Universal SPP raster layer",
                )
                stats["channel_assets_skipped"] += skipped
                if actions_value:
                    existing = _field(fields, "actions")
                    _set_field(fields, "actions", existing[1] if existing else 18, actions_value)
                    stats["layers_replaced"] += 1
                else:
                    stats["layers_skipped"] += 1
            elif entries and obj_name == "DataLayerGroup":
                stack_value, skipped = _raster_layer_stack(entries, channel_lookup, uid_gen)
                stats["channel_assets_skipped"] += skipped
                if stack_value:
                    existing = _field(fields, "subStack")
                    _set_field(fields, "subStack", existing[1] if existing else 18, stack_value)
                    stats["layers_replaced"] += 1
                else:
                    stats["layers_skipped"] += 1

        if obj_name == "DataMaterialStack":
            stack_field = _field(fields, "stack")
            if stack_field and stack_field[2][0] == "object" and isinstance(stack_field[2][1], tuple):
                stack_uid = _uid_of(stack_field[2][1])
                entries = full_stack_entries.get(stack_uid)
                if entries:
                    stack_value, skipped = _raster_layer_stack(
                        entries_for_stack(entries, ctx),
                        channel_lookup,
                        uid_gen,
                    )
                    stats["channel_assets_skipped"] += skipped
                    if stack_value:
                        _set_field(fields, "stack", stack_field[1], stack_value)
                        stats["full_stacks_replaced"] += 1
                    else:
                        stats["full_stacks_skipped"] += 1

        for name, tc, value in list(fields):
            current = _field(fields, name)
            if current:
                tc = current[1]
                value = current[2]
            if value[0] == "object" and isinstance(value[1], tuple):
                visit(value[1], ctx)
            elif value[0] == "array" and value[1][0] == "object":
                elems = value[1][1]
                allow_sources = obj_name == "DataActionFill" and name == "sources"
                new_elems, changed = replace_array_elements(elems, allow_sources)
                if changed:
                    _set_field(fields, name, tc, ("array", ("object", new_elems)))
                for i, elem in enumerate(new_elems):
                    if elem and elem[0] == "object" and isinstance(elem[1], tuple):
                        visit(elem[1], array_child_ctx(ctx, obj_name, name, i))

    visit(root, {})
    return root, stats
