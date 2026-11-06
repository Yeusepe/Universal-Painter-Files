import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader/spp_extractor'))
spec = importlib.util.spec_from_file_location('benchmark_pipeline', ROOT / 'scripts/benchmark_pipeline.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings, verify_project_archive


class BenchmarkVerificationTests(unittest.TestCase):
    def production_verify(self, path):
        with ArchiveReader(path, verification_only=True) as reader:
            verify_project_archive(reader, full=True)
            self.assertTrue(reader._verified_all)
            self.assertIsNone(reader._cache)
            with self.assertRaises(KeyError):
                reader.read('payload.bin')

    def test_releasing_written_buffers_keeps_archive_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.usppz'
            members = {'a': os.urandom(65536), 'empty': b'', 'b': b'example' * 10000}
            with ArchiveWriter(path, CompressionSettings('lzma2', threads=1)) as writer:
                for name, payload in members.items():
                    writer.writestr(name, payload)
                    benchmark.release_written_buffer(writer)
            with ArchiveReader(path) as reader:
                for name, payload in members.items():
                    self.assertEqual(reader.read(name), payload)
                self.assertIsNone(reader.testzip())

    def make_archive(self, path, missing_reference=False):
        with ArchiveWriter(path, CompressionSettings('lzma2', threads=1)) as archive:
            archive.writestr('payload.bin', os.urandom(65536))
            members = {
                'manifest.json': {'created_version': {'major': 12}, 'dataset_count': 1, 'group_count': 0},
                'metadata.json': {}, 'structure.json': {}, 'groups.json': {},
                'datasets.json': {'payload': {'data_file': 'missing.bin' if missing_reference else 'payload.bin'}},
            }
            for name, value in members.items():
                archive.writestr(name, json.dumps(value))

    def test_sink_preserves_reference_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.usppz'
            for verify in (benchmark.verify_without_payload_files, self.production_verify):
                self.make_archive(path)
                verify(path)
                self.make_archive(path, missing_reference=True)
                with self.assertRaisesRegex(ValueError, 'missing or reused'):
                    verify(path)

    def test_sink_checks_discarded_payload_crc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.usppz'
            self.make_archive(path)
            with path.open('r+b') as file:
                file.seek(100)
                value = file.read(1)
                file.seek(100)
                file.write(bytes([value[0] ^ 1]))
            for verify in (benchmark.verify_without_payload_files, self.production_verify):
                with self.assertRaises(Exception):
                    verify(path)
