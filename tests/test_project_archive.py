import argparse
import contextlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import h5py
import numpy as np
import py7zr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader'))
import uspp_tool
from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings, verify_project_archive
from lib.raster_manifest import add_capture_dir_to_zip
from test_project_versions import settings_bytes
from archive_test_helpers import corrupt_zip_payload


class ProjectArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def project(self):
        source = self.root / 'source.spp'
        with h5py.File(source, 'w') as project:
            project.create_dataset('projectsettings.ini', data=np.frombuffer(settings_bytes(), dtype='u1'))
            project.create_dataset('paint/payload.bin', data=np.frombuffer(b'payload\x00' * 100, dtype='u1'))
        return source

    def pack(self, method):
        source = self.project()
        path = self.root / (method + '.tmp')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(uspp_tool.cmd_pack(argparse.Namespace(
                input=str(source), output=str(path), verbose=False,
                compression_settings=CompressionSettings(method))), 0)
        return source, path

    def test_both_containers_rebuild_the_same_project_payloads(self):
        for method in ('deflate', 'lzma2'):
            with self.subTest(method=method):
                source, path = self.pack(method)
                with ArchiveReader(path) as reader:
                    manifest = verify_project_archive(reader, full=True)
                    self.assertEqual(manifest['compression']['method'], method)
                    index = json.loads(reader.read('datasets.json'))
                    self.assertEqual(reader.read(index['paint/payload.bin']['data_file']), b'payload\x00' * 100)
                output = self.root / (method + '.spp')
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(uspp_tool.cmd_build(argparse.Namespace(
                        uspp=str(path), output=str(output), target='12.1', verbose=False)), 0)
                with h5py.File(source, 'r') as expected, h5py.File(output, 'r') as actual:
                    for name in ('projectsettings.ini', 'paint/payload.bin'):
                        self.assertEqual(bytes(actual[name][()]), bytes(expected[name][()]))

    def test_openpbr_warning_survives_main_merge_for_both_containers(self):
        from lib.shader_compat import OPENPBR_LOSS
        source = self.project()
        with h5py.File(source, 'a') as project:
            project.create_dataset('resources/material.shader',
                data=np.frombuffer(b'import lib-openpbr-test.glsl\n', dtype='u1'))
        for method in ('deflate', 'lzma2'):
            with self.subTest(method=method):
                path = self.root / ('shader' + CompressionSettings(method).extension)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(uspp_tool.cmd_pack(argparse.Namespace(
                        input=str(source), output=str(path), verbose=False,
                        compression_settings=CompressionSettings(method))), 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(uspp_tool.cmd_plan(argparse.Namespace(
                        uspp=str(path), target='10')), 0)
                plan = json.loads(output.getvalue())
                self.assertIn(OPENPBR_LOSS, plan['lost_features'])
                self.assertTrue(plan['lossy'])

    def test_plan_extracts_only_relevant_seven_zip_payloads(self):
        source = self.project()
        with h5py.File(source, 'a') as project:
            project.create_dataset('resources/material.shader',
                data=np.frombuffer(b'import lib-openpbr-test.glsl\n', dtype='u1'))
        path = self.root / 'selection.usppz'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(uspp_tool.cmd_pack(argparse.Namespace(
                input=str(source), output=str(path), verbose=False,
                compression_settings=CompressionSettings('lzma2'))), 0)
        with ArchiveReader(path, members={'datasets.json'}) as reader:
            index = json.loads(reader.read('datasets.json'))
        calls = []
        original = py7zr.SevenZipFile.extract
        def extract(archive, *args, **kwargs):
            calls.append(set(kwargs['targets']))
            return original(archive, *args, **kwargs)
        with mock.patch.object(py7zr.SevenZipFile, 'extract', extract), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(uspp_tool.cmd_plan(argparse.Namespace(uspp=str(path), target='10')), 0)
        selected = set().union(*calls)
        self.assertIn(index['resources/material.shader']['data_file'], selected)
        self.assertNotIn(index['paint/payload.bin']['data_file'], selected)

    def test_info_extracts_only_metadata_and_reports_actual_container(self):
        _, path = self.pack('lzma2')
        original = py7zr.SevenZipFile.extract
        calls = []

        def extract(archive, *args, **kwargs):
            calls.append(set(kwargs['targets']))
            return original(archive, *args, **kwargs)

        stdout = io.StringIO()
        with mock.patch.object(py7zr.SevenZipFile, 'extract', extract), contextlib.redirect_stdout(stdout):
            self.assertEqual(uspp_tool.cmd_info(argparse.Namespace(uspp=str(path))), 0)
        self.assertEqual(calls, [{'manifest.json', 'metadata.json'}])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result['compression']['container'], '7z')
        self.assertIn('LZMA2', result['compression']['methods'])
        self.assertEqual(result['source_revision'], '12.1.5')

    def test_plan_and_build_each_expand_seven_zip_once(self):
        _, path = self.pack('lzma2')
        for command, args in (
            (uspp_tool.cmd_plan, argparse.Namespace(uspp=str(path), target='10')),
            (uspp_tool.cmd_build, argparse.Namespace(uspp=str(path), target='12.1',
                                                    output=str(self.root / 'rebuilt.spp'), verbose=False)),
        ):
            with self.subTest(command=command.__name__):
                original = py7zr.SevenZipFile.extract
                with mock.patch.object(py7zr.SevenZipFile, 'extract', autospec=True,
                                       side_effect=original) as extract, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(command(args), 0)
                self.assertEqual(extract.call_count, 1)

    def test_cache_uses_numeric_regular_files_and_is_removed(self):
        _, path = self.pack('lzma2')
        with ArchiveReader(path) as reader:
            reader.read('manifest.json')
            cache = reader._cache_root
            self.assertTrue(cache.is_dir())
            self.assertTrue(all(p.name.split('.')[0].isdigit() for p in cache.iterdir()))
            with mock.patch.object(py7zr.SevenZipFile, 'testzip') as repeated_test:
                self.assertIsNone(reader.testzip())
                repeated_test.assert_not_called()
        self.assertFalse(cache.exists())

    def test_cache_is_removed_after_read_failure(self):
        _, path = self.pack('lzma2')
        reader = ArchiveReader(path)
        original = py7zr.SevenZipFile.extract

        def failing_extract(archive, *args, **kwargs):
            original(archive, *args, **kwargs)
            raise OSError('simulated extraction failure')

        with self.assertRaises(OSError), mock.patch.object(py7zr.SevenZipFile, 'extract', failing_extract):
            with reader:
                reader.read('manifest.json')
        self.assertIsNone(reader._cache)

    def test_read_after_full_crc_test_restarts_the_seven_zip_decoder(self):
        _, path = self.pack('lzma2')
        with ArchiveReader(path) as reader:
            self.assertIsNone(reader.testzip())
            self.assertIn('format_version', json.loads(reader.read('manifest.json')))

    def test_normal_pack_validation_caches_indexes_without_payloads(self):
        source = self.project()
        original = py7zr.SevenZipFile.extract
        calls = []

        def extract(archive, *args, **kwargs):
            calls.append(set(kwargs['targets']))
            return original(archive, *args, **kwargs)

        with mock.patch.object(py7zr.SevenZipFile, 'extract', extract):
            uspp_tool._batch_pack_one(source, self.root / 'normal.usppz', argparse.Namespace(
                verbose=False, overwrite=False, delete_source=False,
                compression_settings=CompressionSettings('lzma2')))
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(name.endswith('.json') for name in calls[0]))
        self.assertIn('datasets.json', calls[0])
        self.assertIn('raster/manifest.json', calls[0])
        self.assertTrue(source.exists())

    def test_corrupt_archive_fails_full_verification(self):
        for method in ('deflate', 'lzma2'):
            with self.subTest(method=method):
                _, path = self.pack(method)
                if method == 'deflate':
                    corrupt_zip_payload(path)
                    with ArchiveReader(path, members={'manifest.json', 'metadata.json', 'structure.json',
                                                       'groups.json', 'datasets.json', 'raster/manifest.json'}) as reader:
                        verify_project_archive(reader, full=False)
                else:
                    data = bytearray(path.read_bytes())
                    data[40] ^= 0xFF
                    path.write_bytes(data)
                with self.assertRaises(Exception):
                    with ArchiveReader(path) as reader:
                        verify_project_archive(reader, full=True)

    def test_writer_rejects_duplicate_and_unsafe_names(self):
        for method in ('deflate', 'lzma2'):
            with self.subTest(method=method):
                with ArchiveWriter(self.root / 'archive', CompressionSettings(method)) as writer:
                    writer.writestr('data/a.bin', b'first')
                    for name in ('data/a.bin', '../outside', '/absolute', 'C:/absolute',
                                 'data/../outside', 'data\\outside', 'file:stream', 'a./payload'):
                        with self.assertRaises(ValueError, msg=name):
                            writer.writestr(name, b'bad')

    def test_reader_rejects_duplicate_unsafe_and_link_members(self):
        for name in ('../outside', '/outside', 'C:/outside', 'a\\b', 'a:b'):
            path = self.root / 'unsafe.uspp'
            with zipfile.ZipFile(path, 'w') as writer:
                info = zipfile.ZipInfo('placeholder')
                info.filename = name
                writer.writestr(info, b'bad')
            with self.assertRaises(ValueError, msg=name):
                ArchiveReader(path)
        path = self.root / 'duplicate.uspp'
        with zipfile.ZipFile(path, 'w') as writer:
            writer.writestr('a', b'one')
            with self.assertWarns(UserWarning):
                writer.writestr('a', b'two')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            ArchiveReader(path)
        path = self.root / 'link.uspp'
        with zipfile.ZipFile(path, 'w') as writer:
            info = zipfile.ZipInfo('link')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            writer.writestr(info, 'target')
        with self.assertRaisesRegex(ValueError, 'links'):
            ArchiveReader(path)
        with py7zr.SevenZipFile(self.root / 'duplicate.usppz', 'w') as writer:
            writer.writestr(b'one', 'a')
            writer.writestr(b'two', 'a')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            ArchiveReader(self.root / 'duplicate.usppz')

    def test_case_distinct_dataset_names_survive_both_formats(self):
        source = self.project()
        with h5py.File(source, 'a') as project:
            project.create_dataset('paint/Case', data=np.array([1], dtype='u1'))
            project.create_dataset('paint/case', data=np.array([2], dtype='u1'))
        for method in ('deflate', 'lzma2'):
            path = self.root / method
            with contextlib.redirect_stdout(io.StringIO()):
                uspp_tool.cmd_pack(argparse.Namespace(input=str(source), output=str(path), verbose=False,
                    compression_settings=CompressionSettings(method)))
            with ArchiveReader(path) as reader:
                index = json.loads(reader.read('datasets.json'))
                self.assertEqual(reader.read(index['paint/Case']['data_file']), b'\x01')
                self.assertEqual(reader.read(index['paint/case']['data_file']), b'\x02')

    def test_missing_dataset_or_raster_reference_is_rejected(self):
        for reference in ('data/missing.bin', 'raster/assets/missing.png'):
            for method in ('deflate', 'lzma2'):
                path = self.root / 'missing'
                with ArchiveWriter(path, CompressionSettings(method)) as writer:
                    writer.writestr('manifest.json', '{}')
                    writer.writestr('metadata.json', '{}')
                    writer.writestr('structure.json', '{}')
                    writer.writestr('groups.json', '{}')
                    writer.writestr('datasets.json', json.dumps(
                        {'payload': {'data_file': reference}} if reference.startswith('data/') else {}))
                    writer.writestr('raster/manifest.json', json.dumps(
                        {'assets': [{'archive_path': reference}]} if reference.startswith('raster/') else {}))
                with self.assertRaisesRegex(ValueError, 'missing'):
                    with ArchiveReader(path) as reader:
                        verify_project_archive(reader, full=True)

    def test_raster_files_work_with_both_writer_backends(self):
        captures = self.root / 'captures'
        captures.mkdir()
        (captures / 'one.png').write_bytes(b'fixture png')
        (captures / 'manifest.json').write_text(json.dumps({
            'assets': [{'request_id': 'one', 'path': 'one.png'}]}), encoding='utf-8')
        for method in ('deflate', 'lzma2'):
            path = self.root / method
            with ArchiveWriter(path, CompressionSettings(method)) as writer:
                manifest = add_capture_dir_to_zip(writer, captures)
            with ArchiveReader(path) as reader:
                self.assertEqual(reader.read(manifest['assets'][0]['archive_path']), b'fixture png')
                self.assertEqual(json.loads(reader.read('raster/manifest.json')), manifest)


if __name__ == '__main__':
    unittest.main()
