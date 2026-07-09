"""Painter bitmap resource injection helpers.

Painter stores bitmap resources as a small Qt-serialized metadata map plus a
Qt-serialized `Alg::ResourceImage` payload. This module writes the v12.1/v8-v9
compatible BGRA8 shape observed in the installed sample projects.
"""
import hashlib
import struct
import zlib

import numpy as np


PNG_SIG = b"\x89PNG\r\n\x1a\n"
ALG_META_TAIL = bytes.fromhex(
    "010000000100000049921d6a0000000048bad102f6786fd372f65a8d42ca73c4"
)


class RasterResourceError(ValueError):
    pass


def _qstr(text):
    data = str(text).encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def _qvariant(typ, payload):
    return struct.pack(">I", typ) + b"\x00" + payload


def _qint32(value):
    return _qvariant(3, struct.pack(">i", int(value)))


def _qint64(value):
    return _qvariant(5, struct.pack(">q", int(value)))


def _qlonglong(value):
    return _qvariant(4, struct.pack(">q", int(value)))


def _qstring_value(text):
    return _qvariant(10, _qstr(text))


def _qstring_list(items):
    out = bytearray()
    out += struct.pack(">I", len(items))
    for item in items:
        out += _qstr(item)
    return _qvariant(11, bytes(out))


def _qbytearray(data):
    return _qvariant(12, struct.pack(">I", len(data)) + data)


def _qmap(entries):
    out = bytearray()
    out += struct.pack(">I", len(entries))
    for key, value in entries:
        out += _qstr(key)
        out += value
    return bytes(out)


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_chunks(data):
    if not data.startswith(PNG_SIG):
        raise RasterResourceError("asset is not a PNG")
    pos = len(PNG_SIG)
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        yield ctype, payload
        if ctype == b"IEND":
            break


def _unfilter_png(raw, width, height, channels):
    bpp = channels
    stride = width * channels
    rows = []
    pos = 0
    prev = bytearray(stride)
    for _ in range(height):
        if pos >= len(raw):
            raise RasterResourceError("truncated PNG scanline data")
        ftype = raw[pos]
        pos += 1
        cur = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(cur) != stride:
            raise RasterResourceError("truncated PNG row")
        for i in range(stride):
            left = cur[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            if ftype == 0:
                pass
            elif ftype == 1:
                cur[i] = (cur[i] + left) & 0xFF
            elif ftype == 2:
                cur[i] = (cur[i] + up) & 0xFF
            elif ftype == 3:
                cur[i] = (cur[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                cur[i] = (cur[i] + _paeth(left, up, up_left)) & 0xFF
            else:
                raise RasterResourceError(f"unsupported PNG filter {ftype}")
        rows.append(bytes(cur))
        prev = cur
    return b"".join(rows)


def png_to_bgra8(data):
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    palette = None
    transparency = b""
    for ctype, payload in _png_chunks(data):
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filter, interlace = struct.unpack(">IIBBBBB", payload)
        elif ctype == b"PLTE":
            palette = [payload[i:i + 3] for i in range(0, len(payload), 3)]
        elif ctype == b"tRNS":
            transparency = payload
        elif ctype == b"IDAT":
            idat += payload
    if not width or not height:
        raise RasterResourceError("PNG missing IHDR")
    if interlace:
        raise RasterResourceError("interlaced PNGs are not supported")
    if bit_depth != 8:
        raise RasterResourceError(f"only 8-bit PNG assets are supported for BGRA8 injection, got {bit_depth}-bit")

    channel_count = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if not channel_count:
        raise RasterResourceError(f"unsupported PNG color type {color_type}")
    raw = _unfilter_png(zlib.decompress(bytes(idat)), width, height, channel_count)
    out = bytearray(width * height * 4)
    src = 0
    dst = 0
    for _ in range(width * height):
        if color_type == 0:
            r = g = b = raw[src]
            a = 255
            src += 1
        elif color_type == 2:
            r, g, b = raw[src], raw[src + 1], raw[src + 2]
            a = 255
            src += 3
        elif color_type == 3:
            if palette is None:
                raise RasterResourceError("palette PNG missing PLTE")
            idx = raw[src]
            src += 1
            r, g, b = palette[idx]
            a = transparency[idx] if idx < len(transparency) else 255
        elif color_type == 4:
            r = g = b = raw[src]
            a = raw[src + 1]
            src += 2
        else:
            r, g, b, a = raw[src], raw[src + 1], raw[src + 2], raw[src + 3]
            src += 4
        out[dst:dst + 4] = bytes((b, g, r, a))
        dst += 4
    return width, height, bytes(out)


def lz4_literals_only(data):
    """Valid LZ4 block made of one final literal run.

    This is intentionally simple: Painter only needs a valid LZ4 block, and a
    literal-only block avoids an additional runtime dependency.
    """
    n = len(data)
    out = bytearray()
    token_lit = min(n, 15)
    out.append(token_lit << 4)
    if n >= 15:
        rem = n - 15
        while rem >= 255:
            out.append(255)
            rem -= 255
        out.append(rem)
    out += data
    return bytes(out)


def painter_image_from_png(png_bytes):
    width, height, bgra = png_to_bgra8(png_bytes)
    compressed = lz4_literals_only(bgra)
    header = _qmap([
        ("width", _qint32(width)),
        ("version", _qstring_value("v2")),
        ("pitch", _qlonglong(width * 4)),
        ("height", _qint32(height)),
        ("format", _qstring_value("BGRA8")),
        ("flags", _qint32(0)),
        ("dataSizeRaw", _qint64(len(bgra))),
        ("dataSizeLZ4", _qint64(len(compressed))),
        ("compression", _qstring_value("lz4")),
        ("colorSpace", _qvariant(2, struct.pack(">i", 0))),
        ("alphaFormat", _qvariant(2, struct.pack(">i", 2))),
        ("ICCdataSize", _qint32(0)),
    ])
    return header + compressed


def painter_alg_meta(token):
    meta = _qmap([
        ("local_unversioned_keys", _qstring_list([])),
        ("relative_shelf_path", _qstring_value("/")),
        ("resource_usage", _qint64(16)),
        ("resource_version", _qbytearray(token.encode("ascii"))),
    ])
    return meta + ALG_META_TAIL


def parse_resource_toc(data):
    if not data:
        return {}
    pos = 0

    def u32():
        nonlocal pos
        v = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        return v

    def qstr():
        nonlocal pos
        ln = u32()
        s = data[pos:pos + ln].decode("utf-16-be", "replace")
        pos += ln
        return s

    out = {}
    count = u32()
    for _ in range(count):
        key = qstr()
        n = u32()
        vals = []
        for _ in range(n):
            vals.append(qstr())
        out[key] = vals
    return out


def build_resource_toc(mapping):
    out = bytearray()
    out += struct.pack(">I", len(mapping))
    for key, vals in mapping.items():
        out += _qstr(key)
        out += struct.pack(">I", len(vals))
        for val in vals:
            out += _qstr(val)
    return bytes(out)


def _set_hash_attr(ds, data, hash_func):
    h0, h1 = hash_func(data)
    ds.attrs["m3_x64_128"] = np.array([h0, h1], dtype=np.uint64)


def _write_dataset(group, name, data, hash_func):
    if name in group:
        del group[name]
    arr = np.frombuffer(data, dtype=np.uint8)
    kwargs = {}
    if len(data) > 0x8000:
        kwargs["chunks"] = (min(len(data), 0x10000),)
    ds = group.create_dataset(name, data=arr, **kwargs)
    _set_hash_attr(ds, data, hash_func)
    return ds


def prepare_png_resource(png_bytes, request_id):
    image_blob = painter_image_from_png(png_bytes)
    token = hashlib.sha1(image_blob).hexdigest() + ".image"
    url = f"/Universal SPP Raster {request_id}?version={token}"
    return {
        "url": url,
        "token": token,
        "image_blob": image_blob,
        "meta_blob": painter_alg_meta(token),
        "bytes": len(image_blob),
    }


def write_prepared_resource(hf, prepared, hash_func):
    token = prepared["token"]
    resources = hf.require_group("resources")
    meta_group = resources.require_group(".alg_meta")

    _write_dataset(resources, token, prepared["image_blob"], hash_func)
    _write_dataset(meta_group, token, prepared["meta_blob"], hash_func)

    toc_map = {}
    if "resources.toc" in hf:
        toc_map = parse_resource_toc(bytes(hf["resources.toc"][()]))
        del hf["resources.toc"]
    toc_map.setdefault("Alg::ResourceImage", [])
    if prepared["url"] not in toc_map["Alg::ResourceImage"]:
        toc_map["Alg::ResourceImage"].append(prepared["url"])
    toc = build_resource_toc(toc_map)
    ds = hf.create_dataset("resources.toc", data=np.frombuffer(toc, dtype=np.uint8))
    _set_hash_attr(ds, toc, hash_func)
    return {
        "url": prepared["url"],
        "token": token,
        "bytes": len(prepared["image_blob"]),
    }


def inject_png_resource(hf, png_bytes, request_id, hash_func):
    return write_prepared_resource(hf, prepare_png_resource(png_bytes, request_id), hash_func)
