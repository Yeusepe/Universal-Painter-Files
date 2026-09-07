"""Patch reference files must exercise the same format profiles as the converter."""
import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "uspp_automap", ROOT / "spp_downgrader" / "debug" / "automap.py"
)
automap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(automap)


class AutomapVersionTests(unittest.TestCase):
    def test_corpus_selects_newest_patch_numerically_without_a_patch_edge(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("v12.1.0.spp", "v12.1.4.spp", "v12.1.10.spp",
                         "v12.0.0.spp", "v9.0.0.spp", "v8.1.0.spp"):
                (Path(td) / name).touch()
            refs = automap.corpus_references(td)
            self.assertEqual(
                [(label, Path(path).name) for label, path in refs],
                [("8.1", "v8.1.0.spp"), ("9", "v9.0.0.spp"),
                 ("12", "v12.0.0.spp"), ("12.1", "v12.1.10.spp")],
            )
            self.assertTrue((Path(td) / "v12.1.0.spp").exists())

    def test_1214_replaces_1210_as_reference_for_existing_profile(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("v12.0.0.spp", "v12.1.0.spp", "v12.1.4.spp"):
                (Path(td) / name).touch()
            refs = automap.corpus_references(td)
            self.assertEqual([label for label, _ in refs], ["12", "12.1"])
            self.assertEqual(Path(refs[-1][1]).name, "v12.1.4.spp")

    def test_explicit_patch_pair_uses_runtime_format_labels(self):
        args = argparse.Namespace(vfrom=None, vto=None)
        summary = dict(applied=0, asked=0, todos=0, decode_fails=[])
        with mock.patch.object(automap, "map_pair", return_value=({}, {}, [], summary)) as mapper, \
             mock.patch.object(automap.H, "iter_hbo_streams", return_value=[]), \
             mock.patch.object(automap, "write_outputs", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            automap.run_pair("v12.1.4.spp", "v12.0.0.spp", args, "2026-09-07")
        self.assertEqual(mapper.call_args.args[2:4], ("12.1", "12"))

    def test_same_format_pair_cannot_write_self_downgrade_profile(self):
        args = argparse.Namespace(vfrom="12.1.4", vto="12.1.0")
        with mock.patch.object(automap, "map_pair") as mapper:
            with self.assertRaisesRegex(ValueError, "Patch releases share a profile"):
                automap.run_pair("new.spp", "old.spp", args, "2026-09-07")
        mapper.assert_not_called()

    def test_empty_or_single_format_corpus_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(automap.cmd_verify(td, argparse.Namespace()))
            (Path(td) / "v12.1.0.spp").touch()
            (Path(td) / "v12.1.4.spp").touch()
            self.assertFalse(automap.cmd_verify(td, argparse.Namespace()))


if __name__ == "__main__":
    unittest.main()
