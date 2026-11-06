import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'spp_downgrader'))
import uspp_tool

# Importing the test CLI installs its worker hook; restore it after import.
with mock.patch.object(uspp_tool, '_recycle_source'):
    from cli_with_test_trash import test_recycle


class TrashBoundaryTests(unittest.TestCase):
    def test_parent_symlink_cannot_escape_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'fixture'
            outside = base / 'outside'
            root.mkdir()
            outside.mkdir()
            target = outside / 'project.spp'
            target.write_bytes(b'outside')
            try:
                (root / 'link').symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f'symlinks unavailable: {error}')
            with mock.patch.dict(os.environ, USPP_TEST_TRASH_ROOT=str(root)):
                with self.assertRaisesRegex(RuntimeError, 'outside'):
                    test_recycle(root / 'link' / target.name)
            self.assertEqual(target.read_bytes(), b'outside')

    def test_final_symlink_moves_the_link_not_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'fixture'
            root.mkdir()
            target = Path(directory) / 'outside.spp'
            target.write_bytes(b'outside')
            link = root / 'project.spp'
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f'symlinks unavailable: {error}')
            with mock.patch.dict(os.environ, USPP_TEST_TRASH_ROOT=str(root)):
                test_recycle(link)
            self.assertFalse(link.exists())
            self.assertTrue(next((root / '.test-trash').iterdir()).is_symlink())
            self.assertEqual(target.read_bytes(), b'outside')
