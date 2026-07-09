import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_builder"))

import raster_resources as rr  # noqa: E402
from spp_builder import SPPBuilder  # noqa: E402


def _chunk(kind, payload):
    import binascii
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)


def png_rgba(r, g, b, a=255):
    row = b"\x00" + bytes([r, g, b, a])
    return (
        rr.PNG_SIG
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row))
        + _chunk(b"IEND", b"")
    )


class SPPBuilderRasterTests(unittest.TestCase):
    def test_prepare_raster_resources_filters_to_target_requests(self):
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "in.uspp"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("raster/assets/one.png", png_rgba(255, 0, 0))
                zf.writestr("raster/assets/two.png", png_rgba(0, 255, 0))
                zf.writestr("raster/manifest.json", json.dumps({
                    "version": 1,
                    "requests": [{"id": "rf_needed"}, {"id": "rf_other"}],
                    "assets": [
                        {"request_id": "rf_needed", "archive_path": "raster/assets/one.png", "mime": "image/png"},
                        {"request_id": "rf_other", "archive_path": "raster/assets/two.png", "mime": "image/png"},
                    ],
                    "warnings": [],
                }))

            builder = SPPBuilder(target_major=8)
            builder._raster_required_ids = {"rf_needed"}
            with zipfile.ZipFile(zip_path) as zf:
                builder._prepare_raster_resources(zf)

            self.assertEqual(len(builder._prepared_raster_resources), 1)
            self.assertEqual(set(builder._raster_replacements), {"rf_needed"})


if __name__ == "__main__":
    unittest.main()
