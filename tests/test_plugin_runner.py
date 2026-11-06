import importlib.util
import os
import shutil
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
    @unittest.skipUnless(os.name == 'nt', 'Windows file associations')
    def test_association_adds_usppz_to_existing_uspp_install(self):
        import contextlib
        import types
        runner = load_runner()
        painter = os.path.normpath('C:/Painter/Painter.exe')
        command_key = r'Software\Classes\USPP.Project\shell\open\command'
        values = {command_key: f'"{painter}" "%1"',
                  r'Software\Classes\.uspp': 'USPP.Project'}

        def open_key(root, key):
            if key not in values:
                raise FileNotFoundError(key)
            return contextlib.nullcontext(key)

        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER=1, REG_SZ=1, OpenKey=open_key,
            CreateKey=lambda root, key: contextlib.nullcontext(key),
            QueryValueEx=lambda key, name: (values[key], 1),
            SetValue=lambda key, name, kind, value: values.__setitem__(key, value),
        )
        with mock.patch.dict(sys.modules, winreg=registry), \
             mock.patch.object(runner.os.path, 'exists', return_value=True), \
             mock.patch.object(runner, '_spp_icon', return_value=None):
            self.assertTrue(runner.ensure_association(painter))
            self.assertEqual(values[r'Software\Classes\.usppz'], 'USPP.Project')
            self.assertFalse(runner.ensure_association(painter))

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
        onedir = os.path.join(runner._PLUGIN_ROOT, "bin", "uspp_tool", runner.tool_filename())
        legacy = os.path.join(runner._PLUGIN_ROOT, "bin", runner.tool_filename())

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            runner.os.path, "isfile", side_effect=lambda path: path == onedir
        ):
            self.assertEqual(runner.tool_path(), onedir)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            runner.os.path, "isfile", side_effect=lambda path: path == legacy
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

    def test_python_override_uses_configured_interpreter_inside_painter(self):
        import tempfile
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'converter.py'
            script.touch()
            with mock.patch.dict(os.environ, {'USPP_TOOL': str(script), 'USPP_PYTHON': sys.executable}), \
                 mock.patch.object(runner.sys, 'executable', 'Painter.exe'):
                self.assertTrue(runner.available())
                self.assertEqual(runner._argv('info')[0:2], [shutil.which(os.environ['USPP_PYTHON']), str(script)])
            with mock.patch.dict(os.environ, {'USPP_TOOL': str(script)}, clear=True), \
                 mock.patch.object(runner.sys, 'executable', 'Painter.exe'):
                self.assertFalse(runner.available())
                with self.assertRaisesRegex(RuntimeError, 'USPP_PYTHON'):
                    runner._argv('info')

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
