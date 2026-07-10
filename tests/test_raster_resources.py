import struct
import sys
from pathlib import Path
import tempfile
import unittest
import zlib

import h5py


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_builder"))

import raster_resources as rr  # noqa: E402


def _chunk(kind, payload):
    import binascii
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)


def png_rgba(width, height, pixels):
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        start = y * stride
        rows += pixels[start:start + stride]
    return (
        rr.PNG_SIG
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _chunk(b"IEND", b"")
    )


def png_16(width, height, color_type, samples):
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    packed = struct.pack(">" + "H" * (width * height * channels), *samples)
    rows = bytearray()
    stride = width * channels * 2
    for y in range(height):
        rows.append(0)
        rows += packed[y * stride:(y + 1) * stride]
    return (
        rr.PNG_SIG
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, color_type, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _chunk(b"IEND", b"")
    )


class RasterResourceTests(unittest.TestCase):
    def test_png_to_bgra8(self):
        png = png_rgba(1, 1, bytes([10, 20, 30, 40]))
        width, height, bgra = rr.png_to_bgra8(png)
        self.assertEqual((width, height), (1, 1))
        self.assertEqual(bgra, bytes([30, 20, 10, 40]))

    def test_16_bit_grayscale_uses_lum16_little_endian(self):
        png = png_16(2, 1, 0, [0x1234, 0xABCD])
        width, height, pixels, fmt, pitch, flags, alpha = rr.png_to_painter_pixels(png)

        self.assertEqual((width, height, fmt, pitch, flags, alpha), (2, 1, "LUM16", 4, 2, 2))
        self.assertEqual(pixels, b"\x34\x12\xcd\xab")

    def test_16_bit_rgb_uses_rgba16_and_adds_opaque_alpha(self):
        png = png_16(1, 1, 2, [0x1234, 0x5678, 0x9ABC])
        width, height, pixels, fmt, pitch, flags, alpha = rr.png_to_painter_pixels(png)

        self.assertEqual((width, height, fmt, pitch, flags, alpha), (1, 1, "RGBA16", 8, 0, 1))
        self.assertEqual(pixels, b"\x34\x12\x78\x56\xbc\x9a\xff\xff")

    def test_resource_toc_round_trip(self):
        data = rr.build_resource_toc({"Alg::ResourceImage": ["/one?version=a.image"]})
        parsed = rr.parse_resource_toc(data)
        self.assertEqual(parsed["Alg::ResourceImage"], ["/one?version=a.image"])

    def test_inject_png_resource(self):
        png = png_rgba(1, 1, bytes([255, 0, 0, 255]))
        with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
            path = tmp.name
        try:
            with h5py.File(path, "w") as hf:
                info = rr.inject_png_resource(hf, png, "rf_test", lambda b: (1, 2))
                self.assertIn("resources", hf)
                self.assertIn(info["token"], hf["resources"])
                self.assertIn(info["token"], hf["resources/.alg_meta"])
                toc = rr.parse_resource_toc(bytes(hf["resources.toc"][()]))
                self.assertIn(info["url"], toc["Alg::ResourceImage"])
                self.assertEqual(list(hf[f"resources/{info['token']}"].attrs["m3_x64_128"]), [1, 2])
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
