"""Compare existing and native verification on an existing archive without modifying it."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CODE = '''
import json,sys,time
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from lib.project_archive import ArchiveReader,verify_project_archive,PROJECT_METADATA_MEMBERS
from lib.performance import memory
full=sys.argv[4]=='full'
start=time.perf_counter(); cpu=time.process_time()
from contextlib import nullcontext
with (patch('lib.fast_lzma2.install_decoders',return_value=[]) if sys.argv[3]=='liblzma' else nullcontext()):
 with ArchiveReader(sys.argv[2],members=None if full else PROJECT_METADATA_MEMBERS) as archive:
  verify_project_archive(archive,full=full)
print(json.dumps(dict(wall_seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu,memory=memory())))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    before = archive.stat()
    report = dict(archive=str(archive), archive_bytes=before.st_size,
                  cache_policy='Sequential single runs on the same archive; not repeated averages.', runs=[])
    for mode in ('metadata', 'full'):
        for decoder in ('liblzma', 'fast-lzma2'):
            result = subprocess.run([sys.executable, '-c', CODE,
                str(ROOT / 'spp_downgrader/spp_extractor'), str(archive), decoder, mode],
                capture_output=True, text=True, check=True)
            run = dict(mode=mode, decoder=decoder, **json.loads(result.stdout))
            report['runs'].append(run)
            print(json.dumps(run), flush=True)
    after = archive.stat()
    report['archive_stat_unchanged'] = (before.st_size, before.st_mtime_ns, before.st_ino) == (
        after.st_size, after.st_mtime_ns, after.st_ino)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
