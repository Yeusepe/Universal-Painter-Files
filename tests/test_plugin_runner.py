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
    def test_plan_args_support_event_driven_execution(self):
        runner = load_runner()
        with mock.patch.dict(os.environ, {"USPP_TOOL": "C:/tools/uspp_tool.exe"}):
            argv, env = runner.plan_args("in.uspp", "10")

        self.assertEqual(argv, [
            "C:/tools/uspp_tool.exe",
            "plan", "--uspp", "in.uspp", "--target", "10",
        ])
        self.assertEqual(env, {})

    def test_tool_path_prefers_onedir_and_falls_back_to_legacy_onefile(self):
        runner = load_runner()
        onedir = os.path.join(runner._PLUGIN_ROOT, "bin", "uspp_tool", "uspp_tool.exe")
        legacy = os.path.join(runner._PLUGIN_ROOT, "bin", "uspp_tool.exe")

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            runner.os.path, "exists", side_effect=lambda path: path == onedir
        ):
            self.assertEqual(runner.tool_path(), onedir)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            runner.os.path, "exists", side_effect=lambda path: path == legacy
        ):
            self.assertEqual(runner.tool_path(), legacy)

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
