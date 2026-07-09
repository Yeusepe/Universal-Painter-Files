"""Graph rewrites that attach prepared raster fallback resources.

This module intentionally starts with the safest boundary: mask stacks. A mask
fallback keeps the layer and replaces only `maskActions` with a bitmap fill that
points at an injected Painter bitmap resource.
"""
import struct

from ._raster_plan import S_MASK_STACK, collect_raster_requests


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


def _uid_of(obj):
    if not obj or obj[1] is None:
        return None
    f = _field(obj[1], "uid")
    if not f or f[2][0] != "primitive":
        return None
    raw = f[2][2]
    if len(raw) >= 8:
        return int.from_bytes(raw[:8], "little", signed=False)
    if len(raw) >= 4:
        return int.from_bytes(raw[:4], "little", signed=False)
    return None


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


def _bitmap_fill_stack(url, uid_gen):
    bitmap = ("DataBitmap", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("alphaType", 9, _p_i32(0)),
        ("urlToBitmapRes", 16, _string(url)),
    ])
    source = ("DataSourceBitmap", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("channelTypes", 12, _p_i64(1)),
        ("opacity", 1, _p_float(1.0)),
        ("bitmap", 18, ("object", bitmap)),
    ])
    fill = ("DataActionFill", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("channelTypes", 12, _p_i64(0)),
        ("enabled", 10, _p_bool(True)),
        ("filtering", 9, _p_i32(2)),
        ("projection", 9, _p_i32(1)),
        ("label", 16, _string("Universal SPP raster mask")),
        ("sources", 19, ("array", ("object", [("object", source)]))),
    ])
    stack = ("DataStackActions", [
        ("uid", 12, _p_i64(uid_gen.next())),
        ("items", 19, ("array", ("object", [("object", fill)]))),
    ])
    return ("object", stack)


def _choose_mask_url(entries):
    for entry in entries or []:
        if entry.get("kind") == "mask" and entry.get("url"):
            return entry["url"]
    for entry in entries or []:
        if entry.get("url"):
            return entry["url"]
    return None


def _mask_urls_by_layer(root, replacements, dataset, target, requests=None):
    requests = list(requests or collect_raster_requests(root, dataset=dataset, target=target))
    out = {}
    for req in requests:
        if req.get("scope") != S_MASK_STACK:
            continue
        entries = replacements.get(req.get("id")) or []
        url = _choose_mask_url(entries)
        if url and req.get("layer_uid") is not None:
            out[int(req["layer_uid"])] = url
    return out


def apply_raster_replacements(root, replacements, *, dataset=None, target=None, requests=None):
    if not replacements:
        return root, {"mask_stacks_replaced": 0}
    mask_urls = _mask_urls_by_layer(root, replacements, dataset, target, requests=requests)
    if not mask_urls:
        return root, {"mask_stacks_replaced": 0}
    uid_gen = _UidGen(_max_uid_value(root) + 1000)
    replaced = 0

    def visit(obj):
        nonlocal replaced
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
                replaced += 1
                return
        for _name, _tc, value in list(fields):
            if value[0] == "object" and isinstance(value[1], tuple):
                visit(value[1])
            elif value[0] == "array" and value[1][0] == "object":
                for elem in value[1][1]:
                    if elem and elem[0] == "object" and isinstance(elem[1], tuple):
                        visit(elem[1])

    visit(root)
    return root, {"mask_stacks_replaced": replaced}
