"""Exercise plugin handlers without requiring Painter's embedded Qt runtime."""
import ast
import contextlib
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def handler(path, name, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class PluginOpenTests(unittest.TestCase):
    def test_dialog_accepts_compressed_universal_archives(self):
        widgets = mock.Mock()
        widgets.QFileDialog.getOpenFileName.return_value = ('project.usppz', '')
        open_dialog = handler(ROOT / 'universal_spp_plugin/lib/dialogs.py', 'open_uspp',
                              {'_qt': lambda: widgets, '_parent': lambda: None})
        self.assertEqual(open_dialog(), 'project.usppz')
        self.assertIn('*.usppz', widgets.QFileDialog.getOpenFileName.call_args.args[-1])

    def test_launch_accepts_compressed_archive_case_insensitively(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'project.USPPZ'
            path.touch()
            widgets = mock.Mock()
            widgets.QApplication.instance.return_value.arguments.return_value = ['Painter.exe', str(path)]
            launch = handler(ROOT / 'universal_spp_plugin/__init__.py', '_launch_uspp_arg',
                             {'QtWidgets': widgets, 'os': os})
            self.assertEqual(launch(), str(path))

    def test_compressed_open_uses_converter_then_opens_saved_copy(self):
        runner, progress, dialogs, project = (mock.Mock() for _ in range(4))
        runner.plan_args.return_value = (['plan'], {})
        runner.build_args.return_value = (['build'], {})
        progress.run_json_with_progress.return_value = (True, {
            'supported': True, 'lossy': False, 'direction': 'exact',
            'source_file': 'Original.spp',
        }, '')
        progress.run_with_progress.return_value = (True, '')
        namespace = dict(
            runner=runner, progress=progress, dialogs=dialogs, sp_project=project,
            version=types.SimpleNamespace(detect_running=lambda: '12.1', running_binary=lambda: None),
            _busy_operation=contextlib.nullcontext, _parent=lambda: None,
            _representative_source_path=lambda src, archive: src,
            _temp_spp=mock.Mock(return_value='cache.spp'),
            _copy_to_open_destination=mock.Mock(return_value='Saved.spp'),
            _mark_opened_copy_dirty_soon=mock.Mock(), _log=mock.Mock(),
        )
        open_path = handler(ROOT / 'universal_spp_plugin/__init__.py', '_open_path', namespace)
        open_path('Compressed.usppz')
        runner.pack_args.assert_not_called()
        runner.plan_args.assert_called_once_with('Compressed.usppz', '12.1')
        runner.build_args.assert_called_once_with('Compressed.usppz', '12.1', 'cache.spp', target_binary=None)
        namespace['_temp_spp'].assert_called_once_with('Original.spp', '12.1')
        project.open.assert_called_once_with('Saved.spp')
        dialogs.error.assert_not_called()
