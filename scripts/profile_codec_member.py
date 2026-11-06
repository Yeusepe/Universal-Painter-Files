"""Profile the existing writer on one source dataset without changing its settings."""
import argparse
import cProfile
import json
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'spp_downgrader/spp_extractor'))


def main():
    import h5py
    import py7zr  # Exclude dependency import time from the codec measurement.
    from lib.project_archive import ArchiveReader, ArchiveWriter, CompressionSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with h5py.File(args.source, 'r') as project:
        payload = bytes(project[args.dataset][()])
    results = dict(source=str(args.source), dataset=args.dataset, bytes=len(payload),
                   py7zr=py7zr.__version__, runs=[])
    for method in ('deflate', 'lzma2'):
        settings = CompressionSettings(method)
        output = args.output_dir / ('member' + settings.extension)
        profiler = cProfile.Profile()
        start, cpu = time.perf_counter(), time.process_time()
        profiler.enable()
        try:
            with ArchiveWriter(output, settings) as archive:
                archive.writestr('payload.bin', payload)
        finally:
            profiler.disable()
        wall, used_cpu = time.perf_counter() - start, time.process_time() - cpu
        stats_path = args.output_dir / (method + '.pstats')
        profiler.dump_stats(stats_path)
        stats = pstats.Stats(profiler)
        functions = [dict(file=key[0], line=key[1], function=key[2], calls=value[1],
                          self_seconds=value[2], cumulative_seconds=value[3])
                     for key, value in stats.stats.items()]
        functions.sort(key=lambda item: item['self_seconds'], reverse=True)
        with ArchiveReader(output) as archive:
            assert archive.read('payload.bin') == payload
        run = dict(method=method, compression=settings.metadata(), wall_seconds=wall,
                   cpu_seconds=used_cpu, output_bytes=output.stat().st_size,
                   top_functions=functions[:20])
        results['runs'].append(run)
        (args.output_dir / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(run, indent=2), flush=True)


if __name__ == '__main__':
    main()
