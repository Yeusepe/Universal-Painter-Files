import argparse
import contextlib
import errno
import hashlib
import io
import json
import os
import struct
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
from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings
from archive_test_helpers import corrupt_zip_payload


def _test_worker_init():
    import signal
    from cli_with_test_trash import test_recycle
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    uspp_tool._recycle_source = test_recycle


class BatchPackTests(unittest.TestCase):
    method = 'deflate'

    @property
    def extension(self):
        return CompressionSettings(self.method).extension
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # macOS aliases /var to /private/var. Keep fixture paths and the
        # child process's resolved disposal boundary in the same spelling.
        self.root = Path(self.directory.name).resolve()
        self.native_recycle = uspp_tool._recycle_source
        self.trash = self.root / '.test-trash'

        def recycle_fixture(source):
            self.assertIn(self.root, source.absolute().parents)
            self.trash.mkdir(exist_ok=True)
            source.rename(self.trash / source.name)

        self.fixture_recycle = recycle_fixture
        patcher = mock.patch.object(uspp_tool, '_recycle_source', side_effect=recycle_fixture)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(uspp_tool, '_worker_init', _test_worker_init)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, USPP_TEST_TRASH_ROOT=str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for prefix in ('versionAtCreation', 'versionAtLastSave'):
            for key, value in zip(('major', 'minor', 'patch'), (12, 1, 5)):
                name_bytes = f'{prefix}/{key}'.encode('utf-16-be')
                records.append(struct.pack('>I', len(name_bytes)) + name_bytes
                               + b'\x00\x00\x00\x02\x00' + struct.pack('>I', value))
        settings = struct.pack('>I', len(records)) + b''.join(records)
        with h5py.File(path, 'w') as project:
            project.create_dataset('projectsettings.ini', data=np.frombuffer(settings, dtype='u1'))
            project.create_dataset('paint/payload.bin', data=np.frombuffer(b'fixture payload', dtype='u1'))
            project.create_dataset('texture-cache/cache.bin', data=np.arange(16, dtype='u1'))
        return path

    def run_batch(self, *inputs, **kwargs):
        args = argparse.Namespace(inputs=list(map(str, inputs)), recursive=False,
                                  output_dir=None, overwrite=False, delete_source=False,
                                  dry_run=False, verbose=False,
                                  compression_settings=CompressionSettings(self.method))
        for name, value in kwargs.items():
            setattr(args, name, value)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = uspp_tool.cmd_batch_pack(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_real_archives_and_originals_preserved(self):
        sources = [self.project('first project.spp'), self.project('日本語.SPP')]
        before = [hashlib.sha256(path.read_bytes()).digest() for path in sources]
        rc, stdout, stderr = self.run_batch(*sources)
        self.assertEqual((rc, stderr), (0, ''))
        self.assertIn('2 succeeded, 0 failed', stdout)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in sources])
        for path in sources:
            with ArchiveReader(path.with_suffix(self.extension)) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['errors'], [])
                self.assertEqual(manifest['created_version'], {'major': 12, 'minor': 1, 'patch': 5})
                datasets = json.loads(archive.read('datasets.json'))
                self.assertEqual(archive.read(datasets['paint/payload.bin']['data_file']), b'fixture payload')
                self.assertNotIn('texture-cache/cache.bin', datasets)
        self.assert_no_temporaries()

    def test_parallel_cli_continues_after_failure_and_verifies_before_delete(self):
        sources = [self.project('parallel first.spp'), self.project('parallel 日本語.spp')]
        bad = self.root / 'bad.spp'
        bad.write_bytes(b'not HDF5')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tests/cli_with_test_trash.py'),
             'batch-pack', *map(str, [bad, *sources]), '--workers', '2',
             '--delete-source', *(['--compress', '--advanced-config', 'preset=1']
                                   if self.method == 'lzma2' else [])],
            capture_output=True, text=True, encoding='utf-8', timeout=90,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Workers: 2', result.stdout)
        self.assertIn('2 succeeded, 1 failed', result.stdout)
        self.assertTrue(bad.exists())
        for source in sources:
            self.assertFalse(source.exists())
            with ArchiveReader(source.with_suffix(self.extension)) as archive:
                self.assertIsNone(archive.testzip())
        self.assert_no_temporaries()

    def test_parallel_collision_preflight_keeps_both_sources(self):
        first, second = self.project('a/same.spp'), self.project('b/same.spp')
        good = self.project('good.spp')
        rc, stdout, stderr = self.run_batch(first, second, good, workers=2,
            output_dir=str(self.root / 'out'), delete_source=True)
        self.assertEqual(rc, 1, stdout + stderr)
        self.assertIn('1 succeeded, 2 failed', stdout)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse(good.exists())

    def test_invalid_worker_counts_rejected(self):
        for count in ('0', '-1', '62', 'invalid'):
            result = subprocess.run(
                [sys.executable, str(ROOT / 'tests/cli_with_test_trash.py'),
                 'batch-pack', str(self.root), '--workers', count],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_parallel_cancel_finishes_active_files_without_scheduling_more(self):
        sources = [self.project(f'cancel-{index}.spp') for index in range(4)]
        futures = []
        for _ in range(2):
            future = mock.Mock()
            future.cancel.return_value = False
            future.result.return_value = (None, '')
            futures.append(future)
        pool = mock.Mock()
        pool.submit.side_effect = futures
        with mock.patch.object(uspp_tool, 'ProcessPoolExecutor', return_value=pool), \
             mock.patch.object(uspp_tool, 'wait', side_effect=KeyboardInterrupt):
            rc, stdout, stderr = self.run_batch(*sources, workers=2)
        self.assertEqual(rc, 130)
        self.assertEqual(pool.submit.call_count, 2)
        self.assertIn('2 succeeded, 0 failed', stderr)
        pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        for future in futures:
            future.result.assert_called_once()

    def assert_no_temporaries(self):
        self.assertEqual(list(self.root.rglob('*.tmp')), [])

    def test_dry_run_previews_paths_without_opening_or_changing_projects(self):
        first = self.project('projects/sub/first.spp')
        second = first.parents[1] / 'invalid.SPP'
        second.write_bytes(b'contents are not inspected in a dry run')
        output = self.root / 'new outputs'
        before = first.read_bytes()
        with mock.patch.object(uspp_tool, '_batch_pack_one') as pack, \
             mock.patch.object(Path, 'mkdir') as mkdir, \
             mock.patch.object(Path, 'unlink') as unlink:
            rc, stdout, stderr = self.run_batch(first.parents[1], recursive=True,
                output_dir=str(output), dry_run=True, delete_source=True)
        pack.assert_not_called()
        mkdir.assert_not_called()
        unlink.assert_not_called()
        self.assertEqual((rc, stderr), (0, ''))
        self.assertIn(f'WOULD PACK -> {output / f"sub/first{self.extension}"}', stdout)
        self.assertIn(f'WOULD RECYCLE -> {first} (after successful conversion)', stdout)
        self.assertIn('Dry run complete: 2 planned, 0 failed', stdout)
        self.assertNotIn('succeeded', stdout)
        self.assertEqual(first.read_bytes(), before)
        self.assertTrue(second.exists())
        self.assertFalse(output.exists())
        self.assert_no_temporaries()

    def test_dry_run_reports_existing_output_and_continues(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        first.with_suffix(self.extension).write_bytes(b'previous archive')
        rc, stdout, stderr = self.run_batch(first, second, dry_run=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('output exists', stderr)
        self.assertIn('1 planned, 1 failed', stdout)
        self.assertNotIn(f'WOULD RECYCLE -> {first}', stdout)
        self.assertIn(f'WOULD PACK -> {second.with_suffix(self.extension)}', stdout)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse(second.with_suffix(self.extension).exists())

    def test_dry_run_previews_overwrite_and_keeps_existing_files(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        before = source.read_bytes()
        rc, stdout, stderr = self.run_batch(source, dry_run=True, overwrite=True, delete_source=True)
        self.assertEqual((rc, stderr), (0, ''))
        self.assertIn(f'WOULD REPLACE -> {output}', stdout)
        self.assertIn(f'WOULD RECYCLE -> {source}', stdout)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assert_no_temporaries()

    def test_dry_run_reports_missing_inputs_and_collisions_then_continues(self):
        first, second = self.project('a/same.spp'), self.project('b/same.spp')
        good = self.project('good.spp')
        output = self.root / 'out'
        rc, stdout, stderr = self.run_batch(self.root / 'missing.spp', first, second, good,
                                           output_dir=str(output), dry_run=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 planned, 3 failed', stdout)
        self.assertEqual(stderr.count('multiple sources map'), 2)
        self.assertFalse(output.exists())

    def test_dry_run_empty_folder_returns_two(self):
        rc, stdout, _ = self.run_batch(self.root, dry_run=True)
        self.assertEqual(rc, 2)
        self.assertIn('0 planned, 1 failed', stdout)

    def test_real_cli_dry_run_does_not_create_or_delete_files(self):
        source = self.project('project with spaces.spp')
        before = source.read_bytes()
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tests/cli_with_test_trash.py'), 'batch-pack', *(['--compress'] if self.method == 'lzma2' else []),
             str(source), '--dry-run', '--delete-source'],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8',
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('1 planned, 0 failed', result.stdout)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(source.with_suffix(self.extension).exists())
        self.assert_no_temporaries()

    def test_folder_scanning_is_shallow_by_default(self):
        first = self.project('projects/one.spp')
        nested = self.project('projects/nested/two.SPP')
        self.assertEqual(self.run_batch(first.parent)[0], 0)
        self.assertTrue(first.with_suffix(self.extension).exists())
        self.assertFalse(nested.with_suffix(self.extension).exists())

    def test_recursive_output_structure_and_overlapping_inputs(self):
        source = self.project('projects/sub/one.spp')
        output = self.root / 'archives'
        rc, stdout, _ = self.run_batch(source.parents[1], source, recursive=True, output_dir=str(output))
        self.assertEqual(rc, 0)
        self.assertIn('1 succeeded, 0 failed', stdout)
        self.assertTrue((output / f'sub/one{self.extension}').exists())
        self.assertFalse((output / f'one{self.extension}').exists())

    def test_explicit_files_write_directly_to_output_folder(self):
        source = self.project('projects/sub/one.spp')
        output = self.root / 'archives'
        self.assertEqual(self.run_batch(source, output_dir=str(output))[0], 0)
        self.assertTrue((output / f'one{self.extension}').exists())

    def test_existing_output_reports_failure_but_later_file_succeeds(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        first.with_suffix(self.extension).write_bytes(b'previous archive')
        rc, stdout, stderr = self.run_batch(first, second, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertIn('output exists', stderr)
        self.assertTrue(first.exists())
        self.assertEqual(first.with_suffix(self.extension).read_bytes(), b'previous archive')
        self.assertFalse(second.exists())
        self.assertTrue(second.with_suffix(self.extension).exists())

    def test_corrupt_project_reports_failure_but_later_file_succeeds(self):
        bad = self.root / 'bad.spp'
        bad.write_bytes(b'not an HDF5 file')
        good = self.project('good.spp')
        rc, stdout, stderr = self.run_batch(bad, good, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertIn(str(bad), stderr)
        self.assertTrue(bad.exists())
        self.assertFalse(bad.with_suffix(self.extension).exists())
        self.assertFalse(good.exists())
        self.assert_no_temporaries()

    def test_missing_and_wrong_extension_inputs_do_not_stop_batch(self):
        source = self.project('good.spp')
        other = self.root / 'other.txt'
        other.write_text('not a project')
        rc, stdout, _ = self.run_batch(self.root / 'missing', other, source)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 2 failed', stdout)

    def test_empty_folder_returns_two(self):
        self.assertEqual(self.run_batch(self.root)[0], 2)

    def test_collision_fails_both_sources_but_processes_other_files(self):
        first, second = self.project('a/same.spp'), self.project('b/same.spp')
        good = self.project('c/good.spp')
        output = self.root / 'out'
        rc, stdout, stderr = self.run_batch(first.parent, second.parent, good,
                                           output_dir=str(output), delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 2 failed', stdout)
        self.assertEqual(stderr.count('multiple sources map'), 2)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse((output / f'same{self.extension}').exists())
        self.assertTrue((output / f'good{self.extension}').exists())

    def test_overwrite_replaces_existing_archive(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        self.assertEqual(self.run_batch(source, overwrite=True)[0], 0)
        with ArchiveReader(output) as archive:
            self.assertIsNone(archive.testzip())
        self.assertTrue(source.exists())
        self.assertEqual((self.trash / output.name).read_bytes(), b'previous archive')

    def test_overwrite_and_delete_source_recycle_whole_files_in_order(self):
        source = self.project('project.spp')
        original = source.read_bytes()
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        moved = []

        def recycle(path):
            moved.append(path)
            if path == source:
                with ArchiveReader(output) as archive:
                    self.assertIsNone(archive.testzip())
            self.fixture_recycle(path)

        with mock.patch.object(uspp_tool, '_recycle_source', side_effect=recycle):
            rc, stdout, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual((rc, stderr), (0, ''))
        self.assertEqual(moved, [output, source])
        self.assertEqual((self.trash / output.name).read_bytes(), b'previous archive')
        self.assertEqual((self.trash / source.name).read_bytes(), original)
        self.assert_no_temporaries()

    def test_overwrite_recycle_failure_keeps_old_archive_and_source(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        with mock.patch.object(uspp_tool, '_recycle_source', side_effect=PermissionError('Recycle Bin unavailable')):
            rc, _, _ = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertTrue(source.exists())
        self.assert_no_temporaries()

    def test_overwrite_does_not_trust_recycler_that_leaves_original(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        with mock.patch.object(uspp_tool, '_recycle_source', return_value=None):
            rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('previous archive was not moved', stderr)
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertTrue(source.exists())

    def test_overwrite_publish_failure_keeps_source_and_recycled_archive(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        with mock.patch.object(uspp_tool, '_publish_archive', side_effect=PermissionError('Output locked')):
            rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('previous archive is in the Recycle Bin', stderr)
        self.assertEqual((self.trash / output.name).read_bytes(), b'previous archive')
        self.assertTrue(source.exists())
        retained = list(self.root.glob('*.tmp'))
        self.assertEqual(len(retained), 1)
        self.assertIn(str(retained[0]), stderr)
        with ArchiveReader(retained[0]) as archive:
            self.assertIsNone(archive.testzip())

    def test_publication_falls_back_when_links_are_unsupported(self):
        temporary = self.root / 'verified.tmp'
        destination = self.root / 'output.uspp'
        temporary.write_bytes(b'verified archive')
        with mock.patch.object(uspp_tool, '_rename_noreplace', return_value=False), \
             mock.patch.object(uspp_tool.os, 'link', side_effect=OSError(errno.EPERM, 'no links')):
            uspp_tool._publish_posix(temporary, destination)
            self.assertEqual(destination.read_bytes(), b'verified archive')
            with self.assertRaises(FileExistsError):
                uspp_tool._publish_posix(temporary, destination)
        self.assertEqual(destination.read_bytes(), b'verified archive')
        self.assertEqual(temporary.read_bytes(), b'verified archive')

    def test_overwrite_does_not_recycle_output_changed_during_packing(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        pack = uspp_tool.cmd_pack

        def pack_then_change(args):
            rc = pack(args)
            output.write_bytes(b'another application changed the archive')
            return rc

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=pack_then_change):
            rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('output changed', stderr)
        self.assertEqual(output.read_bytes(), b'another application changed the archive')
        self.assertFalse(self.trash.exists())
        self.assertTrue(source.exists())

    def test_failed_overwrite_preserves_previous_archive_and_source(self):
        source = self.root / 'bad.spp'
        source.write_bytes(b'bad')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        self.assertEqual(self.run_batch(source, overwrite=True, delete_source=True)[0], 1)
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertTrue(source.exists())
        self.assert_no_temporaries()

    def test_recorded_extraction_errors_are_failures(self):
        source = self.project('project.spp')
        from spp_extractor import SPPExtractor
        extract = SPPExtractor.extract

        def failed_extract(extractor, **kwargs):
            result = extract(extractor, **kwargs)
            result.errors.append('Failed to read paint/payload.bin')
            return result

        with mock.patch.object(SPPExtractor, 'extract', failed_extract):
            rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('project extraction failed', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())
        self.assert_no_temporaries()

    def test_duplicate_archive_entries_keep_source(self):
        source = self.project('project.spp')
        with h5py.File(source, 'a') as project:
            project.create_dataset('a_b/c', data=np.array([1], dtype='u1'))
            project.create_dataset('a/b_c', data=np.array([2], dtype='u1'))
        rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('duplicate entries', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())
        self.assert_no_temporaries()

    def test_delete_source_only_after_readable_archive_published(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        recycle = uspp_tool._recycle_source

        def observe_recycle(path):
            self.assertEqual(path, source)
            with ArchiveReader(output) as archive:
                self.assertIsNone(archive.testzip())
            return recycle(path)

        with mock.patch.object(uspp_tool, '_recycle_source', observe_recycle):
            rc, stdout, _ = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 0)
        self.assertIn('source moved to Recycle Bin', stdout)
        self.assertFalse(source.exists())
        self.assert_no_temporaries()

    def test_integrity_failure_keeps_source_and_previous_output(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        with mock.patch.object(ArchiveReader, 'testzip', return_value='data/bad.bin'):
            rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('integrity check failed', stderr)
        self.assertTrue(source.exists())
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assert_no_temporaries()

    def test_real_corruption_keeps_source_and_old_output_then_continues(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        output = first.with_suffix(self.extension)
        output.write_bytes(b'previous archive')
        pack = uspp_tool.cmd_pack

        def corrupt_first(args):
            rc = pack(args)
            if Path(args.input) == first:
                if self.method == 'deflate':
                    corrupt_zip_payload(Path(args.output))
                else:
                    data = bytearray(Path(args.output).read_bytes())
                    data[40] ^= 0xFF
                    Path(args.output).write_bytes(data)
            return rc

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=corrupt_first):
            rc, stdout, _ = self.run_batch(first, second, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertTrue(first.exists())
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertFalse(second.exists())
        self.assert_no_temporaries()

    def test_incomplete_archive_never_deletes_source(self):
        source = self.project('source.spp')

        def incomplete_pack(args):
            with ArchiveWriter(args.output, args.compression_settings) as archive:
                for name in ('manifest.json', 'metadata.json', 'structure.json', 'groups.json'):
                    archive.writestr(name, '{}')
                archive.writestr('datasets.json', json.dumps({'payload': {'data_file': 'missing.bin'}}))
            return 0

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=incomplete_pack):
            rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('missing', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())
        self.assert_no_temporaries()

    def test_missing_source_version_keeps_the_project(self):
        source = self.root / 'unknown.spp'
        with h5py.File(source, 'w') as project:
            project.create_dataset('payload.bin', data=np.arange(16, dtype='u1'))
        rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('no source Painter version', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())

    def test_changed_source_is_not_deleted(self):
        source = self.project('project.spp')
        pack = uspp_tool.cmd_pack

        def pack_then_change(args):
            rc = pack(args)
            with source.open('ab') as stream:
                stream.write(b'changed after packing')
            return rc

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=pack_then_change):
            rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('source changed', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())

    def test_delete_failure_reports_saved_archive_and_continues(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        recycle = uspp_tool._recycle_source

        def deny_source(path):
            if path == first:
                raise PermissionError('Recycle Bin unavailable')
            return recycle(path)

        with mock.patch.object(uspp_tool, '_recycle_source', deny_source):
            rc, stdout, stderr = self.run_batch(first, second, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('archive saved', stderr)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertTrue(first.exists())
        self.assertTrue(first.with_suffix(self.extension).exists())
        self.assertFalse(second.exists())
        self.assert_no_temporaries()

    def test_progress_includes_finalization_verification_and_publish(self):
        source = self.project('progress.spp')
        with mock.patch.dict(os.environ, USPP_PROGRESS='1'):
            rc, stdout, stderr = self.run_batch(source)
        self.assertEqual((rc, stderr), (0, ''))
        stages = ['Finishing compression...', 'Verifying archive...', 'Saving archive...', 'OK ->']
        offsets = [stdout.index(stage) for stage in stages]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('__USPP_PROGRESS__\t-1.0000\tVerifying archive...', stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows recycle operation')
    def test_windows_refuses_permanent_delete_when_recycling_is_unavailable(self):
        import pythoncom
        from win32com.shell import shell
        source = self.project('unrecyclable.spp')
        operation = mock.Mock()
        operation.DeleteItem.side_effect = lambda item, sink: sink.PreDeleteItem(0, item)
        with mock.patch.object(pythoncom, 'CoCreateInstance', return_value=operation), \
             mock.patch.object(pythoncom, 'WrapObject', side_effect=lambda obj, iid: obj), \
             mock.patch.object(shell, 'SHCreateItemFromParsingName', return_value=mock.Mock()), \
             mock.patch.object(uspp_tool, '_recycle_source', self.native_recycle):
            rc, stdout, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1, stdout + stderr)
        self.assertTrue(source.exists())
        self.assertTrue(source.with_suffix(self.extension).exists())
        operation.PerformOperations.assert_not_called()
        self.assertIn('archive saved', stderr)

    def test_cancel_removes_partial_archive_and_keeps_source(self):
        source = self.project('project.spp')

        def interrupted(args):
            Path(args.output).write_bytes(b'partial archive')
            raise KeyboardInterrupt

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=interrupted):
            rc, _, _ = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 130)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix(self.extension).exists())
        self.assert_no_temporaries()

    def test_write_failure_does_not_stop_other_projects(self):
        first = self.project('projects/blocked/first.spp')
        second = self.project('projects/second.spp')
        output = self.root / 'out'
        output.mkdir()
        (output / 'blocked').write_bytes(b'not a folder')
        rc, stdout, _ = self.run_batch(first.parents[1], recursive=True,
                                      output_dir=str(output), delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue((output / f'second{self.extension}').exists())

    def test_output_created_during_pack_is_not_overwritten(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        pack = uspp_tool.cmd_pack

        def racing_pack(args):
            rc = pack(args)
            output.write_bytes(b'created by another process')
            return rc

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=racing_pack):
            self.assertEqual(self.run_batch(source, delete_source=True)[0], 1)
        self.assertEqual(output.read_bytes(), b'created by another process')
        self.assertTrue(source.exists())
        self.assertEqual(len(list(self.root.glob('*.tmp'))), 1)

    def test_directory_links_are_not_followed(self):
        source = self.project('projects/first.spp')
        other = self.project('elsewhere/second.spp')
        try:
            (source.parent / 'link').symlink_to(other.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'directory symlinks unavailable: {error}')
        self.assertEqual(self.run_batch(source.parent, recursive=True)[0], 0)
        self.assertFalse(other.with_suffix(self.extension).exists())

    def test_output_link_to_source_cannot_replace_a_project(self):
        source = self.project('project.spp')
        output = source.with_suffix(self.extension)
        before = source.read_bytes()
        try:
            output.symlink_to(source)
        except OSError as error:
            self.skipTest(f'file symlinks unavailable: {error}')
        rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('output points to a source project', stderr)
        self.assertEqual(source.read_bytes(), before)

    def test_source_file_link_is_removed_without_deleting_its_target(self):
        source = self.project('project.spp')
        link = self.root / 'link.spp'
        try:
            link.symlink_to(source)
        except OSError as error:
            self.skipTest(f'file symlinks unavailable: {error}')
        self.assertEqual(self.run_batch(link, delete_source=True)[0], 0)
        self.assertFalse(link.exists())
        self.assertTrue(source.exists())
        self.assertTrue(link.with_suffix(self.extension).exists())

    def test_real_cli_continues_after_error_from_other_working_directory(self):
        source = self.project('project with spaces.spp')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tests/cli_with_test_trash.py'), 'batch-pack', *(['--compress'] if self.method == 'lzma2' else []),
             str(self.root / 'missing.spp'), str(source), '--delete-source'],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('1 succeeded, 1 failed', result.stdout)
        self.assertTrue(source.with_suffix(self.extension).exists())
        self.assertFalse(source.exists())

    def test_real_cli_reports_unicode_paths_with_redirected_output(self):
        first = self.project('日本語.SPP')
        second = self.project('next.spp')
        env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tests/cli_with_test_trash.py'), 'batch-pack', *(['--compress'] if self.method == 'lzma2' else []),
             str(self.root / '不存在.spp'), str(first), str(second)],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8', env=env,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('日本語.SPP', result.stdout)
        self.assertIn('不存在.spp', result.stderr)
        self.assertIn('2 succeeded, 1 failed', result.stdout)
        self.assertTrue(first.with_suffix(self.extension).exists())
        self.assertTrue(second.with_suffix(self.extension).exists())


class Lzma2BatchPackTests(BatchPackTests):
    method = 'lzma2'


if __name__ == '__main__':
    unittest.main()
