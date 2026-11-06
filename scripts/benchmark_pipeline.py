"""Benchmark compatible codec and I/O candidates on an existing project's payloads.

All candidate changes are confined to child benchmark processes. Production defaults
are untouched. Each encoded archive receives full reference and CRC verification.
"""
import argparse
import cProfile
import hashlib
import io
import json
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader/spp_extractor'))


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def release_written_buffer(writer):
    """Benchmark-only py7zr 1.1.x candidate; completed members aren't read again."""
    writer._archive.header.files_info.files[-1]['data'].close()


def prepare(source, directory):
    from lib.project_archive import ArchiveReader, verify_project_archive
    corpus = directory / 'corpus'
    corpus.mkdir()
    members = []
    with ArchiveReader(source) as archive:
        verify_project_archive(archive, full=True)
        for index, name in enumerate(archive.namelist()):
            if name not in archive._files:
                continue
            path = corpus / f'{index}.bin'
            data = archive.read(name)
            path.write_bytes(data)
            members.append(dict(name=name, file=path.name, bytes=len(data), sha256=sha(path)))
    (corpus / 'index.json').write_text(json.dumps(members, indent=2), encoding='utf-8')


def verify_without_payload_files(path):
    """Experimental full verifier: py7zr checks every CRC, retains only metadata."""
    from py7zr.io import Py7zIO, WriterFactory
    from lib.project_archive import ArchiveReader, PROJECT_METADATA_MEMBERS, verify_project_archive

    class Sink(Py7zIO):
        def __init__(self, keep):
            self.buffer = io.BytesIO() if keep else None
            self.length = 0
        def write(self, data):
            self.length += len(data)
            return self.buffer.write(data) if self.buffer else len(data)
        def read(self, size=None):
            return self.buffer.read(-1 if size is None else size) if self.buffer else b''
        def seek(self, offset, whence=0):
            if self.buffer:
                return self.buffer.seek(offset, whence)
            if offset == 0 and whence == 0:
                return 0
            raise ValueError('Unexpected seek in verification sink')
        def flush(self):
            pass
        def size(self):
            return self.length
        def read_bytes(self):
            return self.buffer.getvalue()

    class Factory(WriterFactory):
        def __init__(self):
            self.outputs = {}
        def create(self, filename):
            output = Sink(filename in PROJECT_METADATA_MEMBERS)
            self.outputs[filename] = output
            return output

    with ArchiveReader(path) as archive:
        if archive.container != '7z' or not archive._all_crcs:
            raise ValueError('Prototype requires a 7z archive with all member CRCs')
        factory = Factory()
        archive._reset_decoder()
        archive._archive.extract(factory=factory)
        if set(factory.outputs) != archive._files:
            raise ValueError('Not every archive member was verified')
        archive._cached_files = factory.outputs
        verify_project_archive(archive, full=True)


def child(args):
    from lib import fast_lzma2 as fl2
    from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings, verify_project_archive
    from lib.performance import memory
    import py7zr
    case = json.loads(args.case)
    native = fl2.library()
    if case['kind'] == 'encode':
        original = native.FL2_createCStreamMt
        native.FL2_createCStreamMt = lambda threads, dual: original(threads, int(case.get('dual', False)))
    if case['kind'] == 'verify':
        original = fl2.FastLzma2Decoder
        class Decoder(original):
            def __init__(self, properties, threads=8):
                super().__init__(properties, threads=case['threads'])
                native.FL2_setDStreamMemoryLimitMt(self._stream, case['memory_mib'] * 1024**2)
        fl2.FastLzma2Decoder = Decoder
        original_install = fl2.install_decoders
        def install(archive):
            decoders = original_install(archive)
            for folder in archive.header.main_streams.unpackinfo.folders:
                if folder.decompressor is not None:
                    folder.decompressor.block_size = case.get('read_kib', 4096) * 1024
            return decoders
        fl2.install_decoders = install
    profile = cProfile.Profile()
    start, cpu = time.perf_counter(), time.process_time()
    profile.enable()
    if case['kind'] == 'encode':
        members = json.loads((args.directory / 'corpus/index.json').read_text())
        method = case.get('method', 'lzma2')
        settings = CompressionSettings(method, threads=case.get('threads', 8))
        output = args.directory / (args.name + settings.extension)
        with ArchiveWriter(output, settings) as archive:
            for member in members:
                path = args.directory / 'corpus' / member['file']
                if case.get('file_io'):
                    archive.write(path, member['name'])
                else:
                    if method == 'lzma2':
                        # Keep the historical retaining-buffer baseline reproducible
                        # even after production starts releasing completed buffers.
                        archive._claim(member['name'])
                        archive._archive.writestr(path.read_bytes(), member['name'])
                    else:
                        archive.writestr(member['name'], path.read_bytes())
                    if case.get('release_buffers') and method == 'lzma2':
                        release_written_buffer(archive)
    else:
        if case.get('sink'):
            verify_without_payload_files(args.source)
        else:
            with ArchiveReader(args.source) as archive:
                verify_project_archive(archive, full=True)
    profile.disable()
    result = dict(case=case, wall_seconds=time.perf_counter()-start,
                  cpu_seconds=time.process_time()-cpu, memory=memory(), py7zr=py7zr.__version__)
    profile.dump_stats(str(args.directory / (args.name + '.pstats')))
    functions = [dict(file=key[0], line=key[1], function=key[2], calls=value[1],
                      self_seconds=value[2], cumulative_seconds=value[3])
                 for key, value in pstats.Stats(profile).stats.items()]
    result['top_functions'] = sorted(functions, key=lambda item: item['self_seconds'], reverse=True)[:25]
    if case['kind'] == 'encode':
        result['compression'] = archive.compression_metadata()
        result['output_bytes'] = output.stat().st_size
        start = time.perf_counter()
        with ArchiveReader(output) as archive:
            verify_project_archive(archive, full=True)
            for member in members:
                if hashlib.sha256(archive.read(member['name'])).hexdigest() != member['sha256']:
                    raise ValueError('Member content changed: ' + member['name'])
        result['validation_seconds'] = time.perf_counter()-start
        result['all_payload_hashes_match'] = True
    result['validation'] = 'passed'
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--follow-up', action='store_true', help='Reuse corpus and test combined encoder and decoder read buffers')
    parser.add_argument('--case')
    parser.add_argument('--name')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.source = args.source.resolve(strict=True)
    args.directory = args.directory.resolve()
    if args.case:
        child(args)
        return
    if not args.follow_up:
        args.directory.mkdir(parents=True, exist_ok=False)
    elif not (args.directory / 'corpus/index.json').is_file():
        parser.error('--follow-up requires a prepared corpus in --directory')
    summary_path = args.directory / ('follow-up-summary.json' if args.follow_up else 'summary.json')
    before = sha(args.source)
    if not args.follow_up:
        prepare(args.source, args.directory)
        print('Verified payload corpus prepared', flush=True)
    cases = [
        dict(name='encode-8', kind='encode', threads=8),
        dict(name='encode-12', kind='encode', threads=12),
        dict(name='encode-24', kind='encode', threads=24),
        dict(name='encode-8-dual', kind='encode', threads=8, dual=True),
        dict(name='encode-8-file', kind='encode', threads=8, file_io=True),
        dict(name='deflate', kind='encode', method='deflate'),
        dict(name='verify-8', kind='verify', threads=8, memory_mib=1024),
        dict(name='verify-4', kind='verify', threads=4, memory_mib=512),
        dict(name='verify-2', kind='verify', threads=2, memory_mib=256),
        dict(name='verify-8-sink', kind='verify', threads=8, memory_mib=1024, sink=True),
    ]
    if args.follow_up:
        cases = [
            dict(name='encode-24-dual-release', kind='encode', threads=24, dual=True, release_buffers=True),
            dict(name='verify-8-read1M', kind='verify', threads=8, memory_mib=1024, read_kib=1024),
            dict(name='verify-8-read256K', kind='verify', threads=8, memory_mib=1024, read_kib=256),
            dict(name='verify-4-roomy', kind='verify', threads=4, memory_mib=1024),
            dict(name='verify-2-roomy', kind='verify', threads=2, memory_mib=1024),
            dict(name='verify-8-sink256K', kind='verify', threads=8, memory_mib=1024, read_kib=256, sink=True),
        ]
    report = dict(source=str(args.source), source_sha256_before=before,
                  methodology='Sequential fresh child processes; profiled warm-cache payload repacks; unchanged level 6 and dictionary. Reverse case order on alternate repetitions. Includes profiler overhead. No source-extraction or batch-scaling measurement.', runs=[])
    for repetition in range(args.repeats):
        for case in (cases if repetition % 2 == 0 else list(reversed(cases))):
            name = f'{case["name"]}-{repetition+1}'
            print('Starting ' + name, flush=True)
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(args.source),
                '--directory', str(args.directory), '--case', json.dumps(case), '--name', name],
                capture_output=True, text=True)
            if completed.returncode:
                report['runs'].append(dict(name=name, status='failed', stderr=completed.stderr))
            else:
                run = json.loads(completed.stdout)
                run.update(name=name, repetition=repetition+1)
                report['runs'].append(run)
                print(f'{name}: {run["wall_seconds"]:.3f}s, peak {run["memory"].get("peak_rss_bytes",0)/1024**2:.0f} MiB', flush=True)
            summary_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    report['medians'] = {case['name']: statistics.median(run['wall_seconds'] for run in report['runs']
        if run.get('case', {}).get('name') == case['name']) for case in cases
        if any(run.get('case', {}).get('name') == case['name'] for run in report['runs'])}
    report['source_sha256_after'] = sha(args.source)
    report['source_unchanged'] = before == report['source_sha256_after']
    summary_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['medians'], indent=2), flush=True)
    if not report['source_unchanged'] or any(run.get('status') == 'failed' for run in report['runs']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
