import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader'))
import uspp_tool
from archive_options import parse_advanced_config, resolve_compression
from lib.project_archive import ArchiveReader
from test_project_versions import settings_bytes


class ArchiveOptionsTests(unittest.TestCase):
    def test_format_selection_and_recommended_default(self):
        default = resolve_compression()
        self.assertEqual(default.method, 'deflate')
        self.assertEqual(default.extension, '.uspp')
        self.assertEqual((default.preset, default.extreme, default.overrides), (6, False, ()))
        for extension, method in (('.uspp', 'deflate'), ('.usppz', 'lzma2'), ('.USPP', 'deflate')):
            with self.subTest(extension=extension):
                self.assertEqual(resolve_compression(output='name' + extension).method, method)
        self.assertEqual(resolve_compression(method='deflate').extension, '.uspp')

    def test_conflicting_or_unrecognized_extensions_are_errors(self):
        for method, output in (('lzma2', 'name.uspp'), ('deflate', 'name.usppz'),
                               (None, 'name.zip'), (None, 'name'), ('bad', None)):
            with self.subTest(method=method, output=output), self.assertRaises(ValueError):
                resolve_compression(method=method, output=output)

    def test_all_advanced_settings_and_size_units(self):
        result = parse_advanced_config('preset=9,extreme=true,dict_size=256MiB,mode=normal,'
            'nice_len=273,mf=bt4,depth=128,lc=2,lp=1,pb=2')
        self.assertEqual(result['dict_size'], 256 * 1024 ** 2)
        self.assertEqual(result['preset'], 9)
        self.assertTrue(result['extreme'])
        for text, expected in (('4096', 4096), ('4KiB', 4096), ('64 mib', 64 * 1024 ** 2),
                               ('1GiB', 1024 ** 3), ('1536MiB', 1536 * 1024 ** 2)):
            self.assertEqual(parse_advanced_config('dict_size=' + text)['dict_size'], expected)
        settings = resolve_compression(method='lzma2', advanced_config='preset=9,dict_size=64KiB')
        self.assertEqual(settings.preset, 9)
        self.assertEqual(dict(settings.overrides), {'dict_size': 65536})

    def test_invalid_settings_are_rejected_without_allocating_an_encoder(self):
        invalid = ('', 'preset', 'preset=', '=6', 'preset=6,', 'preset=9,preset=6',
                   'preset=-1', 'preset=10', 'preset=1.5', 'dict_size=3KiB',
                   'dict_size=2GiB', 'dict_size=1GB', 'dict_size=0.5GiB',
                   'mode=ultra', 'mf=wrong', 'nice_len=1', 'nice_len=274',
                   'depth=-1', 'depth=4294967296', 'lc=5', 'lp=5', 'pb=5',
                   'lc=3,lp=2', 'lp=2', 'extreme=yes', 'threads=8', 'solid=256MiB', 'blocksize=4096')
        with mock.patch('lzma.LZMACompressor') as compressor:
            for text in invalid:
                with self.subTest(text=text), self.assertRaises(ValueError):
                    resolve_compression(method='lzma2', advanced_config=text)
            compressor.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'requires LZMA2'):
            resolve_compression(method='deflate', advanced_config='preset=6')


class ArchiveCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'project 日本語.spp'
        with h5py.File(self.source, 'w') as project:
            project.create_dataset('projectsettings.ini', data=np.frombuffer(settings_bytes(), dtype='u1'))
            project.create_dataset('payload.bin', data=np.frombuffer(b'CLI payload' * 100, dtype='u1'))

    def cli(self, *args, expected=0):
        env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
        result = subprocess.run([sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
                                 *map(str, args)], cwd=self.root, env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_single_and_batch_defaults_create_uspp(self):
        result = self.cli('pack', self.source)
        self.assertIn('Deflate', result.stdout)
        self.assertTrue(self.source.with_suffix('.uspp').exists())
        with ArchiveReader(self.source.with_suffix('.uspp')) as archive:
            self.assertEqual(archive.container, 'zip')
        output = self.root / 'batch'
        self.cli('batch-pack', self.source, '-o', output)
        self.assertTrue((output / self.source.with_suffix('.uspp').name).exists())

    def test_explicit_uspp_output_keeps_plugin_calls_compatible(self):
        output = self.root / 'plugin-style.uspp'
        self.cli('pack', self.source, '-o', output)
        with ArchiveReader(output) as archive:
            self.assertEqual(archive.container, 'zip')
            self.assertEqual(json.loads(archive.read('manifest.json'))['compression']['method'], 'deflate')

    def test_batch_deflate_option_and_explicit_lzma2_output(self):
        output = self.root / 'old format'
        self.cli('batch-pack', self.source, '-o', output, '--compression', 'deflate')
        self.assertTrue((output / self.source.with_suffix('.uspp').name).exists())
        output = self.root / 'lzma batch'
        result = self.cli('batch-pack', self.source, '-o', output, '--compression', 'lzma2')
        self.assertIn('preset=6', result.stdout)
        with ArchiveReader(output / self.source.with_suffix('.usppz').name) as archive:
            self.assertEqual(archive.container, '7z')
        output = self.root / 'explicit.usppz'
        self.cli('pack', self.source, '-o', output)
        with ArchiveReader(output) as archive:
            self.assertEqual(archive.container, '7z')

    def test_advanced_filter_round_trip_and_info(self):
        output = self.root / 'advanced 日本語.usppz'
        self.cli('pack', self.source, '-o', output, '--advanced-config',
                 'preset=9,extreme=true,dict_size=64KiB,mode=normal,nice_len=273,mf=bt4,depth=128,lc=2,lp=1,pb=2')
        info = json.loads(self.cli('info', '--uspp', output).stdout)
        configuration = info['compression']['configuration']
        self.assertEqual(configuration['preset'], 9)
        self.assertTrue(configuration['extreme'])
        self.assertEqual(configuration['overrides']['dict_size'], 65536)
        rebuilt = self.root / 'rebuilt 日本語.spp'
        self.cli('build', '--uspp', output, '--target', '12.1', '-o', rebuilt)
        with h5py.File(rebuilt, 'r') as project:
            self.assertEqual(bytes(project['payload.bin'][()]), b'CLI payload' * 100)

    def test_plugin_progress_and_raster_arguments_survive_atomic_packing(self):
        captures = self.root / 'captures'
        captures.mkdir()
        (captures / 'one.png').write_bytes(b'fixture png')
        (captures / 'manifest.json').write_text(json.dumps({
            'assets': [{'request_id': 'one', 'path': 'one.png'}]}), encoding='utf-8')
        env = {**os.environ, 'USPP_PROGRESS': '1'}
        output = self.root / 'plugin-style.uspp'
        result = subprocess.run([sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
            'pack', str(self.source), '-o', str(output), '--raster-capture-dir', str(captures),
            '--raster-budget-mb', '1'], env=env, cwd=self.root, capture_output=True,
            text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('__USPP_PROGRESS__', result.stdout)
        with ArchiveReader(output) as archive:
            manifest = json.loads(archive.read('raster/manifest.json'))
            self.assertEqual(archive.read(manifest['assets'][0]['archive_path']), b'fixture png')

    def test_invalid_config_returns_two_before_processing_and_creates_nothing(self):
        before = set(self.root.iterdir())
        cases = (
            ['pack', self.source, '-o', self.root / 'bad.uspp', '--compression', 'lzma2'],
            ['pack', self.source, '-o', self.root / 'bad.zip'],
            ['batch-pack', self.source, '-o', self.root / 'not created', '--advanced-config', 'threads=8'],
            ['batch-pack', self.source, '--compression', 'deflate', '--advanced-config', 'preset=6'],
            ['batch-pack', self.source, '--dry-run', '--advanced-config', 'dict_size=2GiB'],
            ['convert', self.source, '--target', '12.1', '-o', self.root / 'rebuilt.spp',
             '--keep-uspp', self.root / 'bad.zip'],
        )
        for args in cases:
            with self.subTest(args=args):
                result = self.cli(*args, expected=2)
                self.assertNotIn('[1/', result.stdout)
                self.assertEqual(set(self.root.iterdir()), before)

    def test_dry_run_accepts_large_dictionary_without_creating_an_archive(self):
        output = self.root / 'not created'
        before = self.source.read_bytes()
        result = self.cli('batch-pack', self.source, '-o', output, '--dry-run', '--delete-source', '--compression', 'lzma2',
                          '--advanced-config', 'preset=9,dict_size=1536MiB')
        self.assertIn('.usppz', result.stdout)
        self.assertIn('1 planned, 0 failed', result.stdout)
        self.assertIn('dict_size=1610612736', result.stdout)
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_failed_single_pack_keeps_previous_archive(self):
        self.source.write_bytes(b'not an SPP')
        output = self.root / 'existing.usppz'
        output.write_bytes(b'previous archive')
        self.cli('pack', self.source, '-o', output, expected=1)
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_convert_keeps_the_requested_handoff_container(self):
        for extension, container in (('.uspp', 'zip'), ('.usppz', '7z')):
            with self.subTest(extension=extension):
                handoff = self.root / ('handoff' + extension)
                output = self.root / ('rebuilt' + extension + '.spp')
                self.cli('convert', self.source, '--target', '12.1', '-o', output, '--keep-uspp', handoff)
                with ArchiveReader(handoff) as archive:
                    self.assertEqual(archive.container, container)
                self.assertTrue(output.exists())


if __name__ == '__main__':
    unittest.main()
