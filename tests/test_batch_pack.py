import argparse
import contextlib
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
import zipfile

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader'))
import uspp_tool


class BatchPackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

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
                                  output_dir=None, overwrite=False, delete_source=False, verbose=False)
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
            with zipfile.ZipFile(path.with_suffix('.uspp')) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['errors'], [])
                self.assertEqual(manifest['created_version'], {'major': 12, 'minor': 1, 'patch': 5})
                datasets = json.loads(archive.read('datasets.json'))
                self.assertEqual(archive.read(datasets['paint/payload.bin']['data_file']), b'fixture payload')
                self.assertNotIn('texture-cache/cache.bin', datasets)
        self.assert_no_temporaries()

    def assert_no_temporaries(self):
        self.assertEqual(list(self.root.rglob('*.tmp')), [])

    def test_folder_scanning_is_shallow_by_default(self):
        first = self.project('projects/one.spp')
        nested = self.project('projects/nested/two.SPP')
        self.assertEqual(self.run_batch(first.parent)[0], 0)
        self.assertTrue(first.with_suffix('.uspp').exists())
        self.assertFalse(nested.with_suffix('.uspp').exists())

    def test_recursive_output_structure_and_overlapping_inputs(self):
        source = self.project('projects/sub/one.spp')
        output = self.root / 'archives'
        rc, stdout, _ = self.run_batch(source.parents[1], source, recursive=True, output_dir=str(output))
        self.assertEqual(rc, 0)
        self.assertIn('1 succeeded, 0 failed', stdout)
        self.assertTrue((output / 'sub/one.uspp').exists())
        self.assertFalse((output / 'one.uspp').exists())

    def test_explicit_files_write_directly_to_output_folder(self):
        source = self.project('projects/sub/one.spp')
        output = self.root / 'archives'
        self.assertEqual(self.run_batch(source, output_dir=str(output))[0], 0)
        self.assertTrue((output / 'one.uspp').exists())

    def test_existing_output_reports_failure_but_later_file_succeeds(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        first.with_suffix('.uspp').write_bytes(b'previous archive')
        rc, stdout, stderr = self.run_batch(first, second, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertIn('output exists', stderr)
        self.assertTrue(first.exists())
        self.assertEqual(first.with_suffix('.uspp').read_bytes(), b'previous archive')
        self.assertFalse(second.exists())
        self.assertTrue(second.with_suffix('.uspp').exists())

    def test_corrupt_project_reports_failure_but_later_file_succeeds(self):
        bad = self.root / 'bad.spp'
        bad.write_bytes(b'not an HDF5 file')
        good = self.project('good.spp')
        rc, stdout, stderr = self.run_batch(bad, good, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertIn(str(bad), stderr)
        self.assertTrue(bad.exists())
        self.assertFalse(bad.with_suffix('.uspp').exists())
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
        self.assertFalse((output / 'same.uspp').exists())
        self.assertTrue((output / 'good.uspp').exists())

    def test_overwrite_replaces_existing_archive(self):
        source = self.project('project.spp')
        output = source.with_suffix('.uspp')
        output.write_bytes(b'previous archive')
        self.assertEqual(self.run_batch(source, overwrite=True)[0], 0)
        self.assertTrue(zipfile.is_zipfile(output))
        self.assertTrue(source.exists())

    def test_failed_overwrite_preserves_previous_archive_and_source(self):
        source = self.root / 'bad.spp'
        source.write_bytes(b'bad')
        output = source.with_suffix('.uspp')
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
        self.assertFalse(source.with_suffix('.uspp').exists())
        self.assert_no_temporaries()

    def test_duplicate_archive_entries_keep_source(self):
        source = self.project('project.spp')
        with h5py.File(source, 'a') as project:
            project.create_dataset('a_b/c', data=np.array([1], dtype='u1'))
            project.create_dataset('a/b_c', data=np.array([2], dtype='u1'))
        with self.assertWarns(UserWarning):
            rc, _, stderr = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('duplicate entries', stderr)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix('.uspp').exists())
        self.assert_no_temporaries()

    def test_delete_source_only_after_readable_archive_published(self):
        source = self.project('project.spp')
        output = source.with_suffix('.uspp')
        unlink = Path.unlink

        def observe_unlink(path, *args, **kwargs):
            if path == source:
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
            return unlink(path, *args, **kwargs)

        with mock.patch.object(Path, 'unlink', observe_unlink):
            rc, stdout, _ = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 0)
        self.assertIn('source deleted', stdout)
        self.assertFalse(source.exists())
        self.assert_no_temporaries()

    def test_integrity_failure_keeps_source_and_previous_output(self):
        source = self.project('project.spp')
        output = source.with_suffix('.uspp')
        output.write_bytes(b'previous archive')
        with mock.patch.object(zipfile.ZipFile, 'testzip', return_value='data/bad.bin'):
            rc, _, stderr = self.run_batch(source, overwrite=True, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('integrity check failed', stderr)
        self.assertTrue(source.exists())
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assert_no_temporaries()

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
        self.assertFalse(source.with_suffix('.uspp').exists())

    def test_delete_failure_reports_saved_archive_and_continues(self):
        first, second = self.project('first.spp'), self.project('second.spp')
        unlink = Path.unlink

        def deny_source(path, *args, **kwargs):
            if path == first:
                raise PermissionError('source is locked')
            return unlink(path, *args, **kwargs)

        with mock.patch.object(Path, 'unlink', deny_source):
            rc, stdout, stderr = self.run_batch(first, second, delete_source=True)
        self.assertEqual(rc, 1)
        self.assertIn('archive saved', stderr)
        self.assertIn('1 succeeded, 1 failed', stdout)
        self.assertTrue(first.exists())
        self.assertTrue(first.with_suffix('.uspp').exists())
        self.assertFalse(second.exists())
        self.assert_no_temporaries()

    def test_cancel_removes_partial_archive_and_keeps_source(self):
        source = self.project('project.spp')

        def interrupted(args):
            Path(args.output).write_bytes(b'partial archive')
            raise KeyboardInterrupt

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=interrupted):
            rc, _, _ = self.run_batch(source, delete_source=True)
        self.assertEqual(rc, 130)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix('.uspp').exists())
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
        self.assertTrue((output / 'second.uspp').exists())

    def test_output_created_during_pack_is_not_overwritten(self):
        source = self.project('project.spp')
        output = source.with_suffix('.uspp')
        pack = uspp_tool.cmd_pack

        def racing_pack(args):
            rc = pack(args)
            output.write_bytes(b'created by another process')
            return rc

        with mock.patch.object(uspp_tool, 'cmd_pack', side_effect=racing_pack):
            self.assertEqual(self.run_batch(source, delete_source=True)[0], 1)
        self.assertEqual(output.read_bytes(), b'created by another process')
        self.assertTrue(source.exists())
        self.assert_no_temporaries()

    def test_directory_links_are_not_followed(self):
        source = self.project('projects/first.spp')
        other = self.project('elsewhere/second.spp')
        try:
            (source.parent / 'link').symlink_to(other.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'directory symlinks unavailable: {error}')
        self.assertEqual(self.run_batch(source.parent, recursive=True)[0], 0)
        self.assertFalse(other.with_suffix('.uspp').exists())

    def test_output_link_to_source_cannot_replace_a_project(self):
        source = self.project('project.spp')
        output = source.with_suffix('.uspp')
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
        self.assertTrue(link.with_suffix('.uspp').exists())

    def test_real_cli_continues_after_error_from_other_working_directory(self):
        source = self.project('project with spaces.spp')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'), 'batch-pack',
             str(self.root / 'missing.spp'), str(source), '--delete-source'],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('1 succeeded, 1 failed', result.stdout)
        self.assertTrue(source.with_suffix('.uspp').exists())
        self.assertFalse(source.exists())

    def test_real_cli_reports_unicode_paths_with_redirected_output(self):
        first = self.project('日本語.SPP')
        second = self.project('next.spp')
        env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
        result = subprocess.run(
            [sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'), 'batch-pack',
             str(self.root / '不存在.spp'), str(first), str(second)],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8', env=env,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('日本語.SPP', result.stdout)
        self.assertIn('不存在.spp', result.stderr)
        self.assertIn('2 succeeded, 1 failed', result.stdout)
        self.assertTrue(first.with_suffix('.uspp').exists())
        self.assertTrue(second.with_suffix('.uspp').exists())


if __name__ == '__main__':
    unittest.main()
