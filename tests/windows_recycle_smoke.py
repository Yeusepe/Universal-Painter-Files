"""Opt-in native test: python tests/windows_recycle_smoke.py <uspp_tool.exe>.

Creates private fixtures, verifies complete recycled files by hash, and removes
only its own verified Recycle Bin entries. Never uses an existing user project.
"""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys

from test_batch_pack import BatchPackTests
from lib.project_archive import ArchiveReader


def digest(path):
    return hashlib.sha256(path.read_bytes()).digest()


def original_path(metadata):
    raw = metadata.read_bytes()
    version = struct.unpack_from('<Q', raw)[0]
    if version == 2:
        length = struct.unpack_from('<I', raw, 24)[0]
        return Path(raw[28:28 + length * 2].decode('utf-16-le').rstrip('\0'))
    if version == 1:
        return Path(raw[24:].decode('utf-16-le').rstrip('\0'))
    raise AssertionError('Unknown Recycle Bin metadata version')


def main():
    from win32com.client import Dispatch
    sys.stdout.reconfigure(encoding='utf-8')
    exe = str(Path(sys.argv[1]).resolve(strict=True))
    case = BatchPackTests()
    case.setUp()
    expected = {}
    verified_originals = set()
    try:
        def run(*args):
            result = subprocess.run([exe, *map(str, args)], capture_output=True,
                                    text=True, encoding='utf-8', timeout=90)
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout

        for compressed, extension in ((False, '.uspp'), (True, '.usppz')):
            sources = [case.project(f'{compressed}-{index}-日本語.spp') for index in range(2)]
            for source in sources:
                run('pack', source, *(['--compress'] if compressed else []))
                output = source.with_suffix(extension)
                expected[source] = digest(source)
                expected[output] = digest(output)
            print(run('batch-pack', *sources, '--workers', '2', '--overwrite', '--delete-source',
                      *(['--compress'] if compressed else [])))
            for source in sources:
                assert not source.exists()
                with ArchiveReader(source.with_suffix(extension)) as archive:
                    assert archive.testzip() is None

        if len(sys.argv) > 2:
            gui = str(Path(sys.argv[2]).resolve(strict=True))
            for compressed, extension in ((False, '.uspp'), (True, '.usppz')):
                source = case.project(f'context-{compressed}-日本語.spp')
                run('pack', source, *(['--compress'] if compressed else []))
                output = source.with_suffix(extension)
                expected[source] = digest(source)
                expected[output] = digest(output)
                screenshot = Path(exe).parent.parent / f'context-replace-{compressed}.png'
                result = subprocess.run([gui, str(source), str(compressed), str(screenshot), '0', 'True'], timeout=90)
                assert result.returncode == 0, str(screenshot)
                assert not source.exists()
                with ArchiveReader(output) as archive:
                    assert archive.testzip() is None
            print('Both context replacement actions passed with existing outputs.')

        print('Verified new archives after recycling both old outputs and sources.')
    finally:
        verified = []
        for item in Dispatch('Shell.Application').NameSpace(10).Items():
            if item.ExtendedProperty('System.Recycle.DeletedFrom') != str(case.root):
                continue
            payload = Path(item.Path)
            assert payload.name.startswith('$R')
            assert payload.parent.resolve().parent.name.lower() == '$recycle.bin'
            metadata = payload.with_name('$I' + payload.name[2:])
            original = original_path(metadata)
            assert original.parent == case.root
            assert original in expected
            assert digest(payload) == expected[original], str(original)
            verified.append((payload, metadata))
            verified_originals.add(original)
        # All exact paths and bytes are verified before removing test-only entries.
        for payload, metadata in verified:
            payload.unlink()
            metadata.unlink()
        print(f'Verified {len(verified)} complete recycled files; removed all native-test debris.')
        case.doCleanups()
    assert verified_originals == set(expected), f'Missing recycled files: {set(expected) - verified_originals}'


if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('This opt-in test requires Windows.')
    main()
