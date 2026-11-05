"""Opt-in wall/CPU timing probes; no archive or compression behavior changes."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import platform
import time
import uuid

_active = ContextVar('uspp_performance', default=None)


def memory():
    if os.name != 'nt':
        return {}
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak_rss', 'rss', 'peak_paged', 'paged',
                                               'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {'rss_bytes': counters.rss, 'peak_rss_bytes': counters.peak_rss}
    return {}


class Profile:
    def __init__(self, directory, source, destination, settings):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (Path(source).stem + '-' + uuid.uuid4().hex[:12] + '.json')
        self.events = self.path.with_suffix('.events.jsonl').open('x', encoding='utf-8')
        self.start = time.perf_counter()
        self.cpu_start = time.process_time()
        self.spans = []
        self.info = dict(schema_version=1, source=str(source), destination=str(destination),
                         source_bytes=Path(source).stat().st_size, pid=os.getpid(),
                         python=platform.python_version(), platform=platform.platform(),
                         compression=settings.metadata() if settings else None)

    def event(self, event, name, **values):
        self.events.write(json.dumps(dict(event=event, name=name,
                                         elapsed_seconds=time.perf_counter() - self.start, **values)) + '\n')
        self.events.flush()

    def finish(self, error):
        groups = defaultdict(lambda: dict(count=0, wall_seconds=0., cpu_seconds=0., bytes=0))
        for item in self.spans:
            group = groups[item['name']]
            group['count'] += 1
            group['wall_seconds'] += item['wall_seconds']
            group['cpu_seconds'] += item['cpu_seconds']
            group['bytes'] += item.get('bytes', 0)
        report = dict(self.info, elapsed_seconds=time.perf_counter() - self.start,
                      cpu_seconds=time.process_time() - self.cpu_start, status='error' if error else 'ok',
                      error=str(error) if error else None, memory=memory(),
                      # Spans are nested. Their times must not be summed across groups.
                      aggregates=dict(groups), spans=self.spans)
        self.event('finished', 'pack', status=report['status'])
        self.events.close()
        self.path.write_text(json.dumps(report, indent=2), encoding='utf-8')


@contextmanager
def span(name, **tags):
    profile = _active.get()
    if profile is None:
        yield
        return
    wall, cpu = time.perf_counter(), time.process_time()
    live = name.startswith('pack.') or name in ('archive.finalize', 'archive.read_cache') or tags.get('bytes', 0) >= 16 * 1024**2
    if live:
        profile.event('started', name, **tags)
    try:
        yield
    finally:
        item = dict(name=name, started_seconds=wall - profile.start,
                    wall_seconds=time.perf_counter() - wall,
                    cpu_seconds=time.process_time() - cpu, **tags)
        profile.spans.append(item)
        if live:
            profile.event('completed', name, wall_seconds=item['wall_seconds'], cpu_seconds=item['cpu_seconds'], **tags)


def profiled_pack(function):
    @wraps(function)
    def wrapped(source, destination, args):
        directory = getattr(args, 'timings', None)
        if not directory or _active.get() is not None:
            return function(source, destination, args)
        profile = Profile(directory, source, destination, getattr(args, 'compression_settings', None))
        token = _active.set(profile)
        error = None
        try:
            return function(source, destination, args)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _active.reset(token)
            profile.finish(error)
    return wrapped
