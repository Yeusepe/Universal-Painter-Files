"""Painter bitmap resource injection helpers.

Painter stores bitmap resources as a small Qt-serialized metadata map plus a
Qt-serialized `Alg::ResourceImage` payload. This module writes the v12.1/v8-v9
compatible BGRA8 shape observed in the installed sample projects.
"""
import hashlib
import struct

import lz4.block
import numpy as np
import pyspng


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


def _decode_png(data):
    width = height = bit_depth = color_type = interlace = None
    for ctype, payload in _png_chunks(data):
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filter, interlace = struct.unpack(">IIBBBBB", payload)
    if not width or not height:
        raise RasterResourceError("PNG missing IHDR")
    if bit_depth not in (8, 16):
        raise RasterResourceError(f"only 8-bit and 16-bit PNG assets are supported, got {bit_depth}-bit")

    if color_type not in (0, 2, 3, 4, 6):
        raise RasterResourceError(f"unsupported PNG color type {color_type}")
    if bit_depth == 16 and color_type == 3:
        raise RasterResourceError("16-bit palette PNGs are not valid")
    try:
        c = pyspng.c
        formats = {
            (8, 0): c.SPNG_FMT_G8,
            (8, 2): c.SPNG_FMT_RGB8,
            (8, 3): c.SPNG_FMT_RGBA8,
            (8, 4): c.SPNG_FMT_RGBA8,
            (8, 6): c.SPNG_FMT_RGBA8,
            (16, 0): c.SPNG_FMT_GA16,
            (16, 2): c.SPNG_FMT_RGBA16,
            (16, 4): c.SPNG_FMT_RGBA16,
            (16, 6): c.SPNG_FMT_RGBA16,
        }
        pixels = c.spng_decode_image_bytes(data, formats[(bit_depth, color_type)])
    except Exception as e:
        raise RasterResourceError(f"could not decode PNG: {e}") from e
    if pixels.ndim == 2:
        pixels = pixels[:, :, np.newaxis]
    if pixels.shape[:2] != (height, width):
        raise RasterResourceError("decoded PNG dimensions do not match IHDR")
    return width, height, bit_depth, color_type, pixels


def _decoded_to_bgra8(decoded):
    width, height, bit_depth, color_type, pixels = decoded
    if bit_depth != 8:
        raise RasterResourceError(f"BGRA8 conversion requires an 8-bit PNG, got {bit_depth}-bit")

    pixels = np.asarray(pixels, dtype=np.uint8).reshape((-1, pixels.shape[2]))
    out = np.empty((width * height, 4), dtype=np.uint8)
    if color_type == 0:
        out[:, 0:3] = pixels[:, 0:1]
        out[:, 3] = 255
    elif color_type == 2:
        out[:, 0] = pixels[:, 2]
        out[:, 1] = pixels[:, 1]
        out[:, 2] = pixels[:, 0]
        out[:, 3] = 255
    elif color_type == 3:
        if pixels.shape[1] not in (3, 4):
            raise RasterResourceError("palette PNG did not decode to RGB/RGBA")
        out[:, 0] = pixels[:, 2]
        out[:, 1] = pixels[:, 1]
        out[:, 2] = pixels[:, 0]
        out[:, 3] = pixels[:, 3] if pixels.shape[1] == 4 else 255
    elif color_type == 4:
        out[:, 0:3] = pixels[:, 0:1]
        out[:, 3] = pixels[:, -1]
    else:
        out[:, 0] = pixels[:, 2]
        out[:, 1] = pixels[:, 1]
        out[:, 2] = pixels[:, 0]
        out[:, 3] = pixels[:, 3]
    return width, height, out.tobytes()


def png_to_bgra8(data):
    return _decoded_to_bgra8(_decode_png(data))


def png_to_painter_pixels(data):
    """Decode a PNG to the native v8-era Painter image-resource pixel layout."""
    decoded = _decode_png(data)
    width, height, bit_depth, color_type, decoded_pixels = decoded
    if bit_depth == 8:
        width, height, pixels = _decoded_to_bgra8(decoded)
        return width, height, pixels, "BGRA8", width * 4, 0, 2

    samples = np.asarray(decoded_pixels, dtype="<u2").reshape((-1, decoded_pixels.shape[2]))
    if color_type == 0:
        return width, height, samples[:, 0].tobytes(), "LUM16", width * 2, 2, 2

    rgba = np.empty((width * height, 4), dtype="<u2")
    if color_type == 2:
        rgba[:, 0:3] = samples[:, 0:3]
        rgba[:, 3] = 65535
    elif color_type == 4:
        rgba[:, 0:3] = samples[:, 0:1]
        rgba[:, 3] = samples[:, -1]
    else:
        rgba[:, :] = samples[:, 0:4]
    return width, height, rgba.tobytes(), "RGBA16", width * 8, 0, 1


def compress_lz4(data):
    """Return a raw LZ4 block, matching Painter's ``dataSizeRaw`` framing."""
    return lz4.block.compress(
        data,
        mode="high_compression",
        compression=9,
        store_size=False,
    )


def painter_image_from_png(png_bytes):
    width, height, pixels, image_format, pitch, flags, alpha_format = png_to_painter_pixels(png_bytes)
    compressed = compress_lz4(pixels)
    entries = [
        ("width", _qint32(width)),
        ("version", _qstring_value("v2")),
        ("pitch", _qlonglong(pitch)),
        ("height", _qint32(height)),
        ("format", _qstring_value(image_format)),
        ("flags", _qint32(flags)),
        ("dataSizeRaw", _qint64(len(pixels))),
        ("dataSizeLZ4", _qint64(len(compressed))),
        ("compression", _qstring_value("lz4")),
        ("colorSpace", _qvariant(2, struct.pack(">i", 0))),
        ("alphaFormat", _qvariant(2, struct.pack(">i", alpha_format))),
    ]
    if image_format == "BGRA8":
        entries.append(("ICCdataSize", _qint32(0)))
    header = _qmap(entries)
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
