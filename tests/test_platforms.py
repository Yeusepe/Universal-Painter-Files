import importlib.util
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader/spp_extractor'))

from test_plugin_runner import load_runner
from test_updater import updater
from lib.fast_lzma2 import library_filename


def load_version():
    spec = importlib.util.spec_from_file_location('platform_version_test', ROOT / 'universal_spp_plugin/lib/version.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformTests(unittest.TestCase):
    def test_native_library_names(self):
        for platform, filename in (('win32', 'fast-lzma2.dll'), ('linux', 'libfast-lzma2.so'),
                                   ('darwin', 'libfast-lzma2.dylib')):
            self.assertEqual(library_filename(platform), filename)
        with self.assertRaises(RuntimeError):
            library_filename('unknown')

    def test_plugin_chooses_native_onedir_executable_on_every_platform(self):
        runner = load_runner()
        for platform, filename in (('win32', 'uspp_tool.exe'), ('linux', 'uspp_tool'), ('darwin', 'uspp_tool')):
            with tempfile.TemporaryDirectory() as directory:
                bundle = Path(directory) / 'bin/uspp_tool'
                bundle.mkdir(parents=True)
                executable = bundle / filename
                executable.touch()
                with mock.patch.object(runner, '_PLUGIN_ROOT', directory), \
                     mock.patch.object(runner.sys, 'platform', platform), \
                     mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(runner.tool_path(), str(executable))

    @unittest.skipIf(os.name == 'nt', 'POSIX executable permissions')
    def test_plugin_rejects_native_file_without_execute_permission(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'uspp_tool'
            executable.touch()
            executable.chmod(0o644)
            with mock.patch.dict(os.environ, USPP_TOOL=str(executable)):
                self.assertFalse(runner.available())
                executable.chmod(0o755)
                self.assertTrue(runner.available())

    def test_update_asset_cannot_cross_platform_or_architecture(self):
        for system, machine, suffix in (('Windows', 'AMD64', ''), ('Linux', 'x86_64', '-linux-x64'),
                                        ('Darwin', 'x86_64', '-macos-x64'), ('Darwin', 'arm64', '-macos-arm64')):
            with mock.patch.object(updater.platform, 'system', return_value=system), \
                 mock.patch.object(updater.platform, 'machine', return_value=machine):
                name = updater.asset_name_for_version('1.0')
                self.assertEqual(name, f'universal_spp_plugin-1.0{suffix}.zip')
                release = {'tag_name': 'v1.0', 'assets': [{'name': name, 'browser_download_url': 'native'}]}
                self.assertEqual(updater.update_info_from_release(release).download_url, 'native')
                release['assets'][0]['name'] = 'wrong-platform.zip'
                with self.assertRaises(updater.UpdateError):
                    updater.update_info_from_release(release)

    def test_multi_asset_checksum_uses_selected_asset_digest(self):
        with mock.patch.object(updater.platform, 'system', return_value='Darwin'), \
             mock.patch.object(updater.platform, 'machine', return_value='arm64'):
            name = updater.asset_name_for_version('1.0')
            release = {'tag_name': 'v1.0', 'body': 'a' * 64, 'assets': [
                {'name': name, 'browser_download_url': 'native', 'digest': 'sha256:' + 'b' * 64},
                {'name': 'universal_spp_plugin-1.0.zip', 'browser_download_url': 'windows'}]}
            self.assertEqual(updater.update_info_from_release(release).sha256, 'b' * 64)
            release['assets'][0].pop('digest')
            self.assertEqual(updater.update_info_from_release(release).sha256, '')

    def test_checksum_file_selects_the_matching_platform(self):
        text = 'a' * 64 + '  windows.zip\n' + 'b' * 64 + '  macos.zip\n'
        self.assertEqual(updater.checksum_for_asset(text, 'macos.zip'), 'b' * 64)
        self.assertEqual(updater.checksum_for_asset(text, 'linux.zip'), '')
        self.assertEqual(updater.checksum_for_asset('c' * 64 + '\n', 'linux.zip'), 'c' * 64)
        self.assertEqual(updater.checksum_for_asset('d' * 64 + '  *macos.zip\n', 'macos.zip'), 'd' * 64)
        self.assertEqual(updater.checksum_for_asset('SHA256 (macos.zip) = ' + 'e' * 64, 'macos.zip'), 'e' * 64)
        self.assertEqual(updater.checksum_for_asset('f' * 64 + '  not-macos.zip\n', 'macos.zip'), '')

    def test_posix_update_restores_converter_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'plugin.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('universal_spp_plugin/bin/uspp_tool/uspp_tool', 'native binary')
            with mock.patch.object(updater.sys, 'platform', 'linux'), mock.patch.object(updater.os, 'chmod') as chmod:
                updater.safe_extract(archive, directory)
                chmod.assert_called_once_with(os.path.join(directory, 'universal_spp_plugin', 'bin', 'uspp_tool', 'uspp_tool'), 0o755)

    def test_posix_settings_paths(self):
        with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/uspp-config'}, clear=True), \
             mock.patch.object(updater.sys, 'platform', 'linux'):
            self.assertEqual(updater.settings_dir(), os.path.join('/tmp/uspp-config', 'universal-spp'))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(updater.sys, 'platform', 'darwin'):
            self.assertEqual(updater.settings_dir(), os.path.join(os.path.expanduser('~/Library/Application Support'), 'UniversalSPP'))

    def test_macos_bundle_binary_and_version_discovery(self):
        version = load_version()
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / 'Adobe Substance 3D Painter.app/Contents'
            executable = contents / 'MacOS/Adobe Substance 3D Painter'
            executable.parent.mkdir(parents=True)
            executable.touch()
            interpreter = contents / 'Resources/python/bin/python3'
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            with (contents / 'Info.plist').open('wb') as file:
                plistlib.dump({'CFBundleShortVersionString': '12.1.5'}, file)
            with mock.patch.object(version, 'painter_binary_names', return_value=(executable.name,)), \
                 mock.patch.object(version.sys, 'executable', str(interpreter)), \
                 mock.patch.object(version.sys, 'platform', 'darwin'):
                self.assertEqual(version.running_binary(), os.path.realpath(executable))
                self.assertEqual(version.bundle_version(), '12.1')

    def test_linux_binary_discovery(self):
        version = load_version()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'Adobe Substance 3D Painter'
            executable.touch()
            interpreter = Path(directory) / 'resources/python/bin/python3'
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            with mock.patch.object(version, 'painter_binary_names', return_value=(executable.name,)), \
                 mock.patch.object(version.sys, 'executable', str(interpreter)), \
                 mock.patch.object(version.sys, 'platform', 'linux'):
                self.assertEqual(version.running_binary(), os.path.realpath(executable))
