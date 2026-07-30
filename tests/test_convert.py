import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader"))

import uspp_tool  # noqa: E402


def make_args(**overrides):
    defaults = dict(
        input="in.spp",
        target="9",
        output="out.spp",
        raster_capture_dir=None,
        raster_budget_mb=None,
        keep_uspp=None,
        verbose=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ConvertTests(unittest.TestCase):
    def test_pack_then_build_in_order_with_temp_uspp(self):
        calls = []

        def fake_pack(ns):
            calls.append(("pack", ns))
            Path(ns.output).write_bytes(b"uspp")
            return 0

        def fake_build(ns):
            calls.append(("build", ns))
            return 0

        with mock.patch.object(uspp_tool, "cmd_pack", side_effect=fake_pack), \
             mock.patch.object(uspp_tool, "cmd_build", side_effect=fake_build):
            rc = uspp_tool.cmd_convert(make_args(
                raster_capture_dir="caps", raster_budget_mb=64, verbose=True))

        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in calls], ["pack", "build"])
        pack_ns, build_ns = calls[0][1], calls[1][1]
        self.assertEqual(pack_ns.input, "in.spp")
        self.assertEqual(pack_ns.raster_capture_dir, "caps")
        self.assertEqual(pack_ns.raster_budget_mb, 64)
        self.assertTrue(pack_ns.verbose)
        self.assertTrue(pack_ns.output.endswith(".uspp"))
        self.assertEqual(build_ns.uspp, pack_ns.output)
        self.assertEqual(build_ns.target, "9")
        self.assertEqual(build_ns.output, "out.spp")

    def test_temp_uspp_deleted_on_success(self):
        seen = []

        with mock.patch.object(uspp_tool, "cmd_pack", return_value=0), \
             mock.patch.object(uspp_tool, "cmd_build",
                               side_effect=lambda ns: seen.append(ns.uspp) or 0):
            rc = uspp_tool.cmd_convert(make_args())

        self.assertEqual(rc, 0)
        self.assertEqual(len(seen), 1)
        self.assertFalse(os.path.exists(seen[0]))

    def test_temp_uspp_deleted_on_pack_failure(self):
        seen = []

        def fake_pack(ns):
            seen.append(ns.output)
            return 1

        with mock.patch.object(uspp_tool, "cmd_pack", side_effect=fake_pack), \
             mock.patch.object(uspp_tool, "cmd_build") as build:
            rc = uspp_tool.cmd_convert(make_args())

        self.assertEqual(rc, 1)
        build.assert_not_called()
        self.assertFalse(os.path.exists(seen[0]))

    def test_build_return_code_propagated(self):
        with mock.patch.object(uspp_tool, "cmd_pack", return_value=0), \
             mock.patch.object(uspp_tool, "cmd_build", return_value=3):
            rc = uspp_tool.cmd_convert(make_args())

        self.assertEqual(rc, 3)

    def test_keep_uspp_used_as_intermediate_and_not_deleted(self):
        keep = Path(ROOT / "tests" / "_tmp_keep.uspp")
        try:
            calls = []

            def fake_pack(ns):
                calls.append(ns.output)
                Path(ns.output).write_bytes(b"uspp")
                return 0

            with mock.patch.object(uspp_tool, "cmd_pack", side_effect=fake_pack), \
                 mock.patch.object(uspp_tool, "cmd_build", return_value=0):
                rc = uspp_tool.cmd_convert(make_args(keep_uspp=str(keep)))

            self.assertEqual(rc, 0)
            self.assertEqual(calls, [str(keep)])
            self.assertTrue(keep.exists())
        finally:
            keep.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
