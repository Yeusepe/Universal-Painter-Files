import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import struct
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
from spp_extractor import _parse_project_settings, _painter_version
from spp_builder import SPPBuilder
from lib import migration_profile


def settings_bytes(created=(11, 1, 0), saved=(12, 1, 5)):
    records = []
    for prefix, version in [('versionAtCreation', created), ('versionAtLastSave', saved)]:
        for key, value in zip(('major', 'minor', 'patch'), version):
            name = f'{prefix}/{key}'.encode('utf-16-be')
            records.append(struct.pack('>I', len(name)) + name + b'\x00\x00\x00\x02\x00'
                           + struct.pack('>I', value))
    return struct.pack('>I', len(records)) + b''.join(records)


class ProjectVersionTests(unittest.TestCase):
    def test_last_save_revision_wins_over_creation(self):
        self.assertEqual(_painter_version(_parse_project_settings(settings_bytes())),
                         dict(major=12, minor=1, patch=5))

    def test_zero_and_missing_save_components_never_mix_with_creation(self):
        for saved, expected in [((12, 0, 0), dict(major=12, minor=0, patch=0)),
                                ((12,), dict(major=12, minor=0))]:
            with self.subTest(saved=saved):
                self.assertEqual(_painter_version(_parse_project_settings(
                    settings_bytes(created=(11, 1, 8), saved=saved))), expected)

    def test_creation_fallback_and_full_width_patch(self):
        self.assertEqual(_painter_version(_parse_project_settings(
            settings_bytes(created=(12, 1, 300), saved=()))), dict(major=12, minor=1, patch=300))

    def test_truncated_integer_is_not_a_revision(self):
        parsed = _parse_project_settings(settings_bytes()[:-1])
        self.assertNotIn('.versionAtLastSave/patch', parsed)

    def test_downgrade_resets_both_patches_and_preserves_record_sizes(self):
        source = settings_bytes(created=(11, 1, 300))
        output = SPPBuilder()._patch_projectsettings_ini(source, 8, 1)
        self.assertEqual(len(source), len(output))
        parsed = _parse_project_settings(output)
        for prefix in ('.versionAtCreation/', '.versionAtLastSave/'):
            self.assertEqual([parsed[prefix + k] for k in ('major', 'minor', 'patch')], [8, 1, 0])

    def test_patch_releases_use_existing_profiles(self):
        with mock.patch.dict(os.environ, {'SPP_PROFILE_DIR': migration_profile._DEFAULT_PROFILE_DIR}):
            for target in ('12', '11.1', '10', '9', '8.1'):
                with self.subTest(target=target):
                    self.assertEqual(uspp_tool.resolve_direction('12.1.5', target), ('downgrade', True))
            self.assertEqual(uspp_tool.resolve_direction('12.1.5', '12.1.0'), ('exact', True))
            self.assertEqual(uspp_tool.resolve_direction('11.1.0', '12.1.5'), ('native_upgrade', True))
            self.assertEqual(uspp_tool.resolve_direction('13.0.0', '12'), ('downgrade', False))
            self.assertEqual(uspp_tool.compute_supported_versions('12.1.5'),
                             ['12.1', '12', '11', '10', '9', '8.1'])

    def test_older_manifest_can_report_metadata_patch(self):
        manifest = {'created_version': dict(major=12, minor=1)}
        self.assertEqual(uspp_tool.source_revision_of(manifest, {}), '12.1')
        self.assertEqual(uspp_tool.source_revision_of(manifest, {
            'painter_version': dict(major=12, minor=1, patch=5)}), '12.1.5')
        self.assertEqual(uspp_tool.source_revision_of(manifest, {
            'painter_version': dict(major=11, minor=1, patch=5)}), '12.1')

    def test_resource_version_cap_survives_composition_without_upgrading_old_presets(self):
        for target in ('12', '11', '10', '9', '8.1'):
            with self.subTest(target=target):
                profile = migration_profile.load(f'v12.1_to_v{target}')
                with mock.patch('spp_builder.PROFILE', profile):
                    builder = SPPBuilder()
                    self.assertEqual(builder._get_target_data_version('resources/123.spexp',
                        {'data_version': 5}), 4)
                    self.assertEqual(builder._get_target_data_version('resources/456.spexp',
                        {'data_version': 3}), 3)
                    self.assertEqual(builder._get_target_data_version('paint/document.bin',
                        {'data_version': 91}), profile.data_version_map['paint/document.bin'])

    def test_pack_plan_and_build_detect_content_and_stamp_actual_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'arbitrary project name.spp'
            archive = Path(td) / 'project.uspp'
            raw = settings_bytes()
            with h5py.File(source, 'w') as f:
                f.create_dataset('projectsettings.ini', data=np.frombuffer(raw, dtype='u1'))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(uspp_tool.cmd_pack(argparse.Namespace(
                    input=str(source), output=str(archive), verbose=False)), 0)
            manifest, meta = uspp_tool.load_manifest(archive)
            self.assertEqual(manifest['created_version'], dict(major=12, minor=1, patch=5))
            self.assertEqual(meta['painter_version'], manifest['created_version'])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(uspp_tool.cmd_plan(argparse.Namespace(uspp=archive, target='11.1')), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(plan['source_revision'], '12.1.5')
            self.assertEqual(plan['direction'], 'downgrade')
            self.assertTrue(plan['supported'])

            for target, saved in [('12.1.0', (12, 1, 5)), ('13', (12, 1, 5)),
                                  ('12', (12, 0, 0)), ('11.1', (11, 0, 0)), ('8.3', (8, 1, 0))]:
                with self.subTest(target=target):
                    output = Path(td) / f'output-{target}.spp'
                    env = {k: v for k, v in os.environ.items() if not k.startswith('SPP_')}
                    env['PYTHONIOENCODING'] = 'utf-8'
                    result = subprocess.run([sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
                        'build', '--uspp', str(archive), '--target', target, '-o', str(output)],
                        env=env, capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    with h5py.File(output, 'r') as f:
                        actual = bytes(f['projectsettings.ini'][()])
                    version = _painter_version(_parse_project_settings(actual))
                    self.assertEqual(version, dict(zip(('major', 'minor', 'patch'), saved)))
                    if target in ('12.1.0', '13'):
                        self.assertEqual(actual, raw)
            with h5py.File(source, 'r') as f:
                self.assertEqual(bytes(f['projectsettings.ini'][()]), raw)


if __name__ == '__main__':
    unittest.main()
