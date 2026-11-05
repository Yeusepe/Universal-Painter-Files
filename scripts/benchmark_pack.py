"""Read-only source benchmark for the existing Deflate and LZMA2 implementations."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--formats', nargs='+', choices=('uspp', 'usppz'), default=['uspp', 'usppz'])
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    before = source.stat()
    report = dict(source=str(source), source_bytes=before.st_size,
                  source_sha256_before=digest(source), platform=platform.platform(),
                  python=platform.python_version(), cache_policy='Source hashed first; warm filesystem cache. Runs are sequential.', runs=[])
    summary = directory / 'summary.json'

    def save():
        summary.write_text(json.dumps(report, indent=2), encoding='utf-8')

    save()
    try:
        for extension in dict.fromkeys('.' + value for value in args.formats):
            output = directory / (source.stem + extension)
            run = dict(output=str(output), status='running')
            report['runs'].append(run)
            save()
            print(f'Starting {extension}: {output}', flush=True)
            start = time.perf_counter()
            with (directory / (extension[1:] + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.run([
                    sys.executable, str(ROOT / 'spp_downgrader/uspp_tool.py'),
                    'pack', str(source), '-o', str(output), '--timings', str(directory / 'probes'),
                ], stdout=log, stderr=subprocess.STDOUT)
            run.update(exit_code=process.returncode, process_wall_seconds=time.perf_counter() - start,
                       output_bytes=output.stat().st_size if output.exists() else None,
                       status='ok' if process.returncode == 0 else 'error')
            save()
            print(f'Finished {extension}: {run}', flush=True)
            if process.returncode:
                raise RuntimeError(f'Packing failed; see {directory / (extension[1:] + ".log")}')

            # Separate full verification quantifies the extra work required before source recycling.
            verify_code = '''
import json,sys,time
sys.path.insert(0,sys.argv[1])
from lib.project_archive import open_archive,verify_project_archive
from lib.performance import memory
start=time.perf_counter(); cpu=time.process_time()
with open_archive(sys.argv[2]) as archive: verify_project_archive(archive,full=True)
print(json.dumps(dict(wall_seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu,memory=memory())))
'''
            result = subprocess.run([sys.executable, '-c', verify_code,
                str(ROOT / 'spp_downgrader/spp_extractor'), str(output)],
                capture_output=True, text=True, encoding='utf-8')
            if result.returncode:
                raise RuntimeError(result.stderr)
            run['full_verification'] = json.loads(result.stdout)
            save()
            print(f'Full verification {extension}: {run["full_verification"]}', flush=True)
    finally:
        try:
            after = source.stat()
            report['source_sha256_after'] = digest(source)
            report['source_unchanged'] = (
                report['source_sha256_before'] == report['source_sha256_after'] and
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
        except OSError as error:
            report['source_unchanged'] = None
            report['source_verification_error'] = str(error)
        save()
    if not report['source_unchanged']:
        raise RuntimeError('Source changed or became unavailable during the benchmark. Completed archives and timing reports were kept. See summary.json.')
    print(f'Report: {summary}', flush=True)


if __name__ == '__main__':
    main()
