import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "universal_spp_plugin" / "lib" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("uspp_plugin_runner_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginRunnerTests(unittest.TestCase):
    def test_bundled_tool_name_is_platform_native(self):
        runner = load_runner()
        self.assertEqual(runner.tool_filename("nt"), "uspp_tool.exe")
        self.assertEqual(runner.tool_filename("posix"), "uspp_tool")

    def test_linux_default_path_has_no_exe_suffix(self):
        runner = load_runner()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(runner.os, "name", "posix"):
            self.assertEqual(os.path.basename(runner.tool_path()), "uspp_tool")

    def test_pack_args_can_include_raster_capture_dir(self):
        runner = load_runner()
        with mock.patch.dict(os.environ, {"USPP_TOOL": "C:/tools/uspp_tool.py"}):
            argv, env = runner.pack_args(
                "in.spp", "out.uspp", raster_capture_dir="capture", raster_budget_mb=256
            )

        self.assertEqual(argv[:2], [sys.executable, "C:/tools/uspp_tool.py"])
        self.assertEqual(argv[2:], [
            "pack", "in.spp", "-o", "out.uspp",
            "--raster-capture-dir", "capture",
            "--raster-budget-mb", "256",
        ])
        self.assertEqual(env, {})

    def test_raster_plan_args_defaults_to_all_lower_targets(self):
        runner = load_runner()
        with mock.patch.dict(os.environ, {"USPP_TOOL": "C:/tools/uspp_tool.exe"}):
            argv, env = runner.raster_plan_args("in.spp", "plan.json")

        self.assertEqual(argv, [
            "C:/tools/uspp_tool.exe",
            "raster-plan", "in.spp", "--targets", "all-lower", "-o", "plan.json",
        ])
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
