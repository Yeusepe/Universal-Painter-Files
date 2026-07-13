import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

import h5py


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_builder"))

import raster_resources as rr  # noqa: E402
from spp_builder import SPPBuilder, _raster_request_applies_to_target  # noqa: E402


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
    def test_modified_small_dataset_clamps_preserved_chunk_to_new_shape(self):
        raw = b"source project settings payload"
        rewritten = b"small result"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            zip_path = base / "in.uspp"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("data/projectsettings.ini.bin", raw)

            builder = SPPBuilder(target_major=10)
            builder._prog_done = 0
            builder._prog_total = 1
            tree = {
                "path": "projectsettings.ini",
                "dtype": "uint8",
                "shape": [len(raw)],
            }
            info = {
                "projectsettings.ini": {
                    "creation_props": {
                        "shape": [len(raw)],
                        "maxshape": [None],
                        "chunks": [65536],
                    }
                }
            }
            with (
                zipfile.ZipFile(zip_path) as zf,
                h5py.File(base / "out.spp", "w") as hf,
                mock.patch.object(builder, "_strip_projectsettings_fields", return_value=rewritten),
                mock.patch.object(builder, "_patch_projectsettings_ini", return_value=rewritten),
            ):
                builder._create_dataset(hf, "projectsettings.ini", tree, zf, info)
                dataset = hf["projectsettings.ini"]
                self.assertEqual(bytes(dataset[()]), rewritten)
                self.assertEqual(dataset.chunks, (len(rewritten),))

    def test_preserve_source_keeps_hbo_bytes_and_skips_decoded_payload(self):
        raw = struct.pack("<III", 0x1B7C2FDD, 0, 70) + b"source-hbo"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            zip_path = base / "in.uspp"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("data/paint_document.bin.bin", raw)
                zf.writestr("decoded/paint_document.bin.json", "{not valid json")

            builder = SPPBuilder(preserve_source=True)
            builder._prog_done = 0
            builder._prog_total = 1
            tree = {
                "path": "paint/document.bin",
                "dtype": "uint8",
                "shape": [len(raw)],
            }
            info = {"paint/document.bin": {"is_hbo": True}}
            with zipfile.ZipFile(zip_path) as zf, h5py.File(base / "out.spp", "w") as hf:
                with mock.patch("spp_builder.json.loads") as loads:
                    builder._create_dataset(hf, "document.bin", tree, zf, info)
                loads.assert_not_called()
                self.assertEqual(bytes(hf["document.bin"][()]), raw)

    def test_captured_request_applies_to_its_target_and_older_versions(self):
        request = {"target": "12"}

        self.assertTrue(_raster_request_applies_to_target(request, "12"))
        self.assertTrue(_raster_request_applies_to_target(request, "8.3"))
        self.assertFalse(_raster_request_applies_to_target(request, "12.1"))

    def test_lz4_payload_round_trips_without_stored_size_prefix(self):
        raw = (b"\x10\x20\x30\xff" * 4096) + os.urandom(257)
        compressed = rr.compress_lz4(raw)

        self.assertEqual(rr.lz4.block.decompress(compressed, uncompressed_size=len(raw)), raw)
        self.assertLess(len(compressed), len(raw) // 20)

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

    def test_shared_png_is_injected_once_and_keeps_uv_tile_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "in.uspp"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("raster/assets/shared.png", png_rgba(255, 0, 0))
                zf.writestr("raster/manifest.json", json.dumps({
                    "version": 1,
                    "requests": [{"id": "rf_a"}, {"id": "rf_b"}],
                    "assets": [
                        {"request_id": "rf_a", "archive_path": "raster/assets/shared.png",
                         "mime": "image/png", "uv_tile": 1001},
                        {"request_id": "rf_b", "archive_path": "raster/assets/shared.png",
                         "mime": "image/png", "uv_tile": 1002},
                    ],
                    "warnings": [],
                }))

            builder = SPPBuilder(target_major=8)
            builder._raster_required_ids = {"rf_a", "rf_b"}
            with zipfile.ZipFile(zip_path) as zf:
                builder._prepare_raster_resources(zf)

            self.assertEqual(len(builder._prepared_raster_resources), 1)
            self.assertEqual(builder._raster_replacements["rf_a"][0]["uv_tile"], 1001)
            self.assertEqual(builder._raster_replacements["rf_b"][0]["uv_tile"], 1002)
            self.assertEqual(
                builder._raster_replacements["rf_a"][0]["url"],
                builder._raster_replacements["rf_b"][0]["url"],
            )


if __name__ == "__main__":
    unittest.main()
