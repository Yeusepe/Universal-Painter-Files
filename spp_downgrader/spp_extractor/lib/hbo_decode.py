"""Unified HBO decoder: both .spp binary formats -> one object tree.

Substance Painter stores objects in two HBO formats, distinguished by the header's
version_check field:
  - "inline"   (version_check=0; v8/v9/v10): every object is tagged inline.
  - "registry" (version_check=1; v11/v12):   a type table precedes the objects.

Both normalize to the SAME tree so the auto-mapper can diff across the boundary:

  UNode      = (type_name: str, [(field_name: str, Value), ...])
  Value      = ('object', UNode | None)      # None = null object
             | ('array',  [Value, ...])
             | ('string', bytes)
             | ('primitive', code: int, bytes)
             | ('null',)                       # inline 0x00 null leaf

Primitive sizes are format-specific (see profiles/primitive_sizes.json). An unsized
code raises UnknownPrimitive so the caller can inspect/register it (the v12.1 code-22 case).
"""
import io
import os
import sys
import json
import struct

MAGIC = 0x1B7C2FDD


def _data_dir():
    """Bundled 'profiles' dir when frozen (PyInstaller), else the source profiles dir."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "profiles")
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "profiles"))


_SIZES_PATH = os.path.join(_data_dir(), "primitive_sizes.json")


class UnknownPrimitive(Exception):
    def __init__(self, code, dataset=None, offset=None):
        self.code, self.dataset, self.offset = code, dataset, offset
        super().__init__(f"Unknown HBO primitive type code {code} (0x{code:X})"
                         + (f" in {dataset}" if dataset else "")
                         + (f" at offset {offset}" if offset is not None else ""))


def _load_sizes(fmt):
    try:
        with open(_SIZES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): int(v) for k, v in raw.get(fmt, {}).items()}
    except Exception:
        return {}


INLINE_SIZES = _load_sizes("inline")


def read_header(raw):
    """Return (magic, version_check, data_version) from the 12-byte HBO header."""
    if len(raw) < 12:
        return (None, None, None)
    return struct.unpack_from("<III", raw, 0)


# ----------------------------------------------------------------------------- inline

def decode_inline(raw, dataset=None):
    """Decode the inline (v8/v9/v10) tagged format into a UNode."""
    d = raw
    p = [12]

    def u8():
        v = d[p[0]]; p[0] += 1; return v

    def u16():
        v = struct.unpack_from("<H", d, p[0])[0]; p[0] += 2; return v

    def u32():
        v = struct.unpack_from("<I", d, p[0])[0]; p[0] += 4; return v

    def name(ln):
        s = d[p[0]:p[0] + ln].decode("utf-8", "replace"); p[0] += ln; return s

    def obj():
        u8(); u32()                      # tag byte (0x12/0x14) + end-offset (unused here)
        nm = name(u32())
        fc = u16()
        fields = []
        for _ in range(fc):
            fn = name(u32())
            fields.append((fn, val()))
        return (nm, fields)

    def prim(tag):
        sz = INLINE_SIZES.get(tag)
        if sz is None:
            raise UnknownPrimitive(tag, dataset, p[0] - 1)
        v = d[p[0]:p[0] + sz]; p[0] += sz
        return ("primitive", tag, v)

    def val():
        tag = u8()
        if tag == 0x10:
            ln = u32(); v = d[p[0]:p[0] + ln]; p[0] += ln; return ("string", v)
        if tag in (0x12, 0x14):
            if d[p[0]] == 0xFF:
                p[0] += 1; return ("object", None)
            return ("object", obj())
        if tag in (0x13, 0x11):
            u32(); c = u32(); els = []
            for _ in range(c):
                e = u8()
                if e in (0x12, 0x14):
                    if d[p[0]] == 0xFF:
                        p[0] += 1; els.append(("object", None))
                    else:
                        els.append(("object", obj()))
                elif e == 0x10:
                    ln = u32(); els.append(("string", d[p[0]:p[0] + ln])); p[0] += ln
                else:
                    els.append(prim(e))
            return ("array", els)
        if tag == 0x00:
            return ("null",)
        return prim(tag)

    if u8() != 0x12:
        raise ValueError("inline stream does not start with a root object (0x12)")
    return obj()


# --------------------------------------------------------------------------- registry

def _norm_reg_value(v):
    """Normalize an HBOSerializer value tuple into the canonical Value shape."""
    kind = v[0]
    if kind == "object":
        inner = v[1]
        if inner is None:
            return ("object", None)
        nm, fields = inner
        if nm == "" and not fields:
            return ("object", None)        # registry null object (flag 0)
        return ("object", (nm, [(fn, _norm_reg_value(fv)) for fn, _tc, fv in fields]))
    if kind == "array":
        _elemkind, elems = v[1]
        return ("array", [_norm_reg_value(e) for e in elems])
    if kind == "string":
        return ("string", v[1])
    if kind == "primitive":
        return ("primitive", v[1], v[2])
    return v


def decode_registry(raw, dataset=None):
    """Decode the registry (v11/v12) format into a UNode, via HBOSerializer."""
    from lib.hbo_reserializer import HBOSerializer
    s = HBOSerializer(raw)
    src = io.BytesIO(s.data[12:])
    try:
        s._read_u32(src)                   # root tag (0x12/0x13)
        type_count = s._read_u32(src)
        registry = s._parse_v11_registry_table(src, type_count) or []
        nm, fields = s._parse_v11_object(src, registry, [], 0)
    except ValueError as e:
        msg = str(e)
        if "Unhandled v11 type code" in msg:
            code = int(msg.rsplit(":", 1)[1])
            raise UnknownPrimitive(code, dataset)
        raise
    return (nm, [(fn, _norm_reg_value(fv)) for fn, _tc, fv in fields])


# ------------------------------------------------------------------------------- api

def decode(raw, dataset=None):
    """Decode any HBO stream -> (UNode, format_name, data_version)."""
    magic, vc, dv = read_header(raw)
    if magic != MAGIC:
        raise ValueError("not an HBO stream (bad magic)")
    if vc == 0:
        return decode_inline(raw, dataset), "inline", dv
    if vc == 1:
        return decode_registry(raw, dataset), "registry", dv
    raise ValueError(f"unknown version_check {vc}")


def iter_hbo_streams(spp_path):
    """Yield (dataset_name, raw_bytes, (magic,version_check,data_version)) for every
    HDF5 dataset that is an HBO stream (first u32 == MAGIC)."""
    import h5py
    f = h5py.File(spp_path, "r")
    found = []

    def visit(name, obj):
        if isinstance(obj, h5py.Dataset):
            try:
                raw = bytes(obj[()])
            except Exception:
                return
            if len(raw) >= 12 and struct.unpack_from("<I", raw, 0)[0] == MAGIC:
                found.append((name, raw, read_header(raw)))

    try:
        f.visititems(visit)
    finally:
        f.close()
    return found
