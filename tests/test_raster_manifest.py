import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib import raster_manifest as rm  # noqa: E402


class RasterManifestTests(unittest.TestCase):
    def test_add_capture_dir_dedupes_assets_by_hash(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "a.png").write_bytes(b"same")
            (base / "b.png").write_bytes(b"same")
            (base / "manifest.json").write_text(json.dumps({
                "requests": [{"id": "r1"}, {"id": "r2"}],
                "assets": [
                    {"request_id": "r1", "path": "a.png"},
                    {"request_id": "r2", "path": "b.png"},
                ],
            }), encoding="utf-8")
            zip_path = base / "out.uspp"
            with zipfile.ZipFile(zip_path, "w") as z:
                manifest = rm.add_capture_dir_to_zip(z, base)
            self.assertEqual(len(manifest["assets"]), 2)
            with zipfile.ZipFile(zip_path) as z:
                stored_assets = [n for n in z.namelist() if n.startswith(rm.ASSET_PREFIX)]
                self.assertEqual(len(stored_assets), 1)

    def test_duplicate_assets_only_consume_budget_once(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "a.png").write_bytes(b"same")
            (base / "b.png").write_bytes(b"same")
            (base / "manifest.json").write_text(json.dumps({
                "requests": [{"id": "r1"}, {"id": "r2"}],
                "assets": [
                    {"request_id": "r1", "path": "a.png"},
                    {"request_id": "r2", "path": "b.png"},
                ],
            }), encoding="utf-8")

            with zipfile.ZipFile(base / "out.uspp", "w") as z:
                manifest = rm.add_capture_dir_to_zip(z, base, budget_bytes=4)

            self.assertEqual(len(manifest["assets"]), 2)
            self.assertEqual(manifest["warnings"], [])

    def test_summary_reports_missing_requests(self):
        manifest = {
            "requests": [{"id": "r1"}, {"id": "r2"}],
            "assets": [{"request_id": "r1", "sha256": "abc"}],
        }
        summary = rm.summarize(manifest)
        self.assertTrue(summary["raster_required"])
        self.assertFalse(summary["raster_available"])
        self.assertEqual([r["id"] for r in summary["missing_raster_fallbacks"]], ["r2"])

    def test_explicit_empty_request_list_does_not_fall_back_to_manifest(self):
        manifest = {
            "requests": [{"id": "r1"}],
            "assets": [{"request_id": "r1", "sha256": "abc"}],
        }

        summary = rm.summarize(manifest, [])

        self.assertFalse(summary["raster_required"])
        self.assertEqual(summary["raster_request_count"], 0)

    def test_unused_texture_set_skips_survive_packing_and_do_not_require_pixels(self):
        requests = [{"id": "old_body"}, {"id": "old_eyes"}, {"id": "body"}]
        skipped = [
            {"request_id": rid, "material_name": name, "reason": "unused_texture_set"}
            for rid, name in (("old_body", "OldBody"), ("old_eyes", "OldEyes"))
        ]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "body.png").write_bytes(b"pixels")
            (base / "manifest.json").write_text(json.dumps({
                "requests": requests, "skipped_requests": skipped,
                "assets": [{"request_id": "body", "path": "body.png"}],
            }), encoding="utf-8")
            with zipfile.ZipFile(base / "out.uspp", "w") as z:
                rm.add_capture_dir_to_zip(z, base)
            with zipfile.ZipFile(base / "out.uspp") as z:
                manifest = rm.load_from_zip(z)

        self.assertEqual(manifest["skipped_requests"], skipped)
        summary = rm.summarize(manifest, requests)
        self.assertEqual(summary["raster_request_count"], 1)
        self.assertTrue(summary["raster_available"])
        self.assertEqual(summary["missing_raster_fallbacks"], [])
        self.assertFalse(rm.summarize(manifest, requests[:2])["raster_required"])

    def test_other_skip_reasons_do_not_hide_missing_captures(self):
        manifest = {
            "requests": [{"id": "active"}],
            "skipped_requests": [{"request_id": "active", "reason": "capture_failed"}],
        }
        self.assertEqual(rm.summarize(manifest)["missing_raster_fallbacks"], [{"id": "active"}])


if __name__ == "__main__":
    unittest.main()
