import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_batch_pack as batch_fixture

ROOT = batch_fixture.ROOT


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = batch_fixture.BatchPackTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def test_pack_reports_stage_and_member_timings_for_both_formats(self):
        source = self.fixture.project('timing.spp')
        for extension in ('.uspp', '.usppz'):
            directory = self.root / extension[1:]
            result = subprocess.run([
                sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
                'pack', str(source), '-o', str(source.with_suffix(extension)),
                '--timings', str(directory),
            ], capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reports = list(directory.glob('*.json'))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'ok')
            for name in ('pack.extract', 'pack.write', 'pack.verify', 'pack.publish',
                         'extract.read_dataset', 'archive.write_member', 'archive.finalize'):
                self.assertIn(name, report['aggregates'])
                self.assertGreaterEqual(report['aggregates'][name]['wall_seconds'], 0)
            events = [json.loads(line) for line in reports[0].with_suffix('.events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1]['status'], 'ok')
            self.assertTrue(source.exists())

    def test_failed_pack_still_records_error(self):
        source = self.root / 'invalid.spp'
        source.write_bytes(b'not HDF5')
        result = subprocess.run([
            sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
            'pack', str(source), '--timings', str(self.root / 'timings'),
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        report = json.loads(next((self.root / 'timings').glob('*.json')).read_text())
        self.assertEqual(report['status'], 'error')
        self.assertTrue(report['error'])

    def test_workers_write_independent_reports_for_equal_source_names(self):
        sources = [self.fixture.project('a/same.spp'), self.fixture.project('b/same.spp')]
        directory = self.root / 'timings'
        result = subprocess.run([
            sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
            'batch-pack', *map(str, sources), '--workers', '2', '--timings', str(directory),
        ], capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reports = [json.loads(path.read_text(encoding='utf-8')) for path in directory.glob('*.json')]
        self.assertEqual(len(reports), 2)
        self.assertEqual({item['source'] for item in reports}, set(map(str, sources)))
        self.assertTrue(all(item['status'] == 'ok' for item in reports))
