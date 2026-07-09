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

    def test_summary_reports_missing_requests(self):
        manifest = {
            "requests": [{"id": "r1"}, {"id": "r2"}],
            "assets": [{"request_id": "r1", "sha256": "abc"}],
        }
        summary = rm.summarize(manifest)
        self.assertTrue(summary["raster_required"])
        self.assertFalse(summary["raster_available"])
        self.assertEqual([r["id"] for r in summary["missing_raster_fallbacks"]], ["r2"])


if __name__ == "__main__":
    unittest.main()
