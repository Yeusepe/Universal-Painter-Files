"""Exercise a built portable archive on its native OS. Uses synthetic fixtures only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from test_batch_pack import BatchPackTests
from lib.project_archive import ArchiveReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--native-trash', action='store_true', help='Test native trash with disposable CI fixtures')
    parser.add_argument('--plugin', type=Path, help='Also test the platform plugin ZIP after updater extraction')
    args = parser.parse_args()
    case = BatchPackTests()
    case.setUp()
    try:
        with tempfile.TemporaryDirectory(prefix='uspp-portable-') as directory:
            root = Path(directory)
            if args.archive.name.endswith('.tar.gz'):
                with tarfile.open(args.archive) as archive:
                    archive.extractall(root, filter='data')
            else:
                with zipfile.ZipFile(args.archive) as archive:
                    archive.extractall(root)
            exe = root / 'uspp_tool' / ('uspp_tool.exe' if os.name == 'nt' else 'uspp_tool')
            env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'USPP_', 'SPP_'))}
            env['PATH'] = str(Path(os.environ['WINDIR']) / 'System32') if os.name == 'nt' else '/usr/bin:/bin'
            if sys.platform.startswith('linux'):
                env['XDG_DATA_HOME'] = str(root / 'trash-data')

            def run(*arguments, expected=0):
                result = subprocess.run([str(exe), *map(str, arguments)], cwd=root, env=env,
                                        capture_output=True, text=True, encoding='utf-8', timeout=120)
                assert result.returncode == expected, result.stdout + result.stderr
                return result.stdout

            for extension, flags, container in (('.uspp', [], 'zip'), ('.usppz', ['--compress'], '7z')):
                source = case.project(f'{case.root.name}-日本語{extension}.spp')
                original = source.read_bytes()
                output = source.with_suffix(extension)
                run('batch-pack', source, '--dry-run', '--delete-source', *flags)
                assert not output.exists() and source.read_bytes() == original
                run('pack', source, *flags)
                info = json.loads(run('info', '--uspp', output))
                assert info['compression']['container'] == container
                run('plan', '--uspp', output, '--target', '12.1')
                rebuilt = root / ('rebuilt' + extension + '.spp')
                run('build', '--uspp', output, '--target', '12.1', '-o', rebuilt)
                import h5py
                with h5py.File(rebuilt) as project:
                    assert bytes(project['paint/payload.bin'][()]) == b'fixture payload'
                second = case.project(f'parallel{extension}.spp')
                run('batch-pack', case.root / 'missing.spp', second, '--workers', '2', *flags, expected=1)
                assert second.with_suffix(extension).exists()
                assert source.read_bytes() == original
                if args.native_trash:
                    previous_hash = hashlib.sha256(output.read_bytes()).digest()
                    run('batch-pack', source, '--overwrite', '--delete-source', *flags)
                    assert not source.exists()
                    with ArchiveReader(output) as archive:
                        assert archive.testzip() is None
                    if sys.platform.startswith('linux'):
                        recycled = list((root / 'trash-data/Trash/files').iterdir())
                        hashes = {hashlib.sha256(path.read_bytes()).digest() for path in recycled if path.is_file()}
                        assert previous_hash in hashes
                        assert hashlib.sha256(original).digest() in hashes
            print('Portable pack, inspect, plan, build, dry-run, parallel error handling, and source preservation passed.')
            if args.native_trash:
                print('Native trash and overwrite checks passed for both formats.')
            if args.plugin:
                from test_updater import updater
                updater.validate_update_zip(args.plugin)
                updater.safe_extract(args.plugin, root / 'plugin-extracted')
                exe = root / 'plugin-extracted/universal_spp_plugin/bin/uspp_tool' / exe.name
                source = case.project('plugin-extracted.spp')
                run('pack', source, '--compress')
                assert json.loads(run('info', '--uspp', source.with_suffix('.usppz')))['compression']['container'] == '7z'
                print('Plugin ZIP extraction, executable permissions, and bundled converter passed.')
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
