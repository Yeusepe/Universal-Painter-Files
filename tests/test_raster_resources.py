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


class RasterResourceTests(unittest.TestCase):
    def test_png_to_bgra8(self):
        png = png_rgba(1, 1, bytes([10, 20, 30, 40]))
        width, height, bgra = rr.png_to_bgra8(png)
        self.assertEqual((width, height), (1, 1))
        self.assertEqual(bgra, bytes([30, 20, 10, 40]))

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
