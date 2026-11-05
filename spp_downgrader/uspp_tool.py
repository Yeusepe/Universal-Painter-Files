#!/usr/bin/env python3
"""uspp_tool - client-facing universal .uspp converter (the bundled exe the Painter
plugin calls). One CLI over the extractor + builder + profile/lossiness engine.

Subcommands:
  pack    <in.spp> -o <out.uspp>          extract + write the universal manifest
  plan    --uspp <f> --target <maj.min>   -> JSON {direction, supported, lossy, lost_features, ...}
  build   --uspp <f> --target <maj.min> -o <out.spp>   produce a native .spp for the target
  convert <in.spp> --target <maj.min> -o <out.spp>     one-shot pack + build (.spp -> .spp)
  info    --uspp <f>                      print the manifest

Direction (S = .uspp created version, T = target/running version):
  T == S            exact          rebuild at S, no transform
  T  > S            native_upgrade rebuild at S, Painter upgrades on open
  T  < S            downgrade      compose profile v{S}_to_v{T} (lossy)
"""
import os
import sys
import json
import argparse
import struct
import tempfile
import io
import multiprocessing
import signal
from dataclasses import replace
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from contextlib import nullcontext, redirect_stdout, redirect_stderr

TOOL_VERSION = "1.0.0"
FORMAT_VERSION = 2

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "spp_extractor"))
sys.path.insert(0, os.path.join(_HERE, "spp_builder"))

from lib.project_archive import CompressionSettings, PROJECT_METADATA_MEMBERS, open_archive, verify_project_archive
from archive_options import describe_compression, resolve_compression
from lib.performance import profiled_pack, span

# When frozen by PyInstaller, point the engine at the bundled profiles dir.
if getattr(sys, "frozen", False):
    os.environ.setdefault("SPP_PROFILE_DIR", os.path.join(sys._MEIPASS, "profiles"))


# --------------------------------------------------------------- version helpers

def parse_ver(s):
    """'12.1' / '12' / '12.1.0' -> (major, minor)."""
    parts = [int(x) for x in str(s).split(".") if x != ""]
    return (parts[0], parts[1] if len(parts) > 1 else 0)


def ver_label(major, minor):
    """(8,1)->'8.1'  (9,0)->'9'  -- matches profile file naming."""
    return f"{major}.{minor}" if minor else str(major)


def _vkey(label):
    return tuple(int(x) for x in label.split("."))


# ---------------------------------------------------------------------- manifest

def load_manifest(uspp_path):
    with open_archive(uspp_path, members=('manifest.json', 'metadata.json')) as z:
        manifest = json.loads(z.read("manifest.json"))
        try:
            meta = json.loads(z.read("metadata.json"))
        except KeyError:
            meta = {}
    return manifest, meta


def load_raster_manifest(uspp_path):
    with open_archive(uspp_path) as z:
        from lib.raster_manifest import load_from_zip
        return load_from_zip(z)


def created_version_of(manifest, meta):
    """(major, minor) the .uspp was authored in. Prefer manifest.created_version,
    fall back to metadata.painter_version (older .uspp without the universal manifest)."""
    cv = manifest.get("created_version") or meta.get("painter_version") or {}
    if cv.get("major") is not None:
        return (int(cv["major"]), int(cv.get("minor") or 0))
    return None


def source_revision_of(manifest, meta):
    """Keep the saved patch for display while profiles use major/minor boundaries."""
    version = created_version_of(manifest, meta)
    if version is None:
        return None
    label = ver_label(*version)
    for info in (manifest.get('created_version'), meta.get('painter_version')):
        if not info or info.get('patch') is None:
            continue
        if created_version_of({}, {'painter_version': info}) == version:
            return f"{version[0]}.{version[1]}.{int(info['patch'])}"
    return label


# --------------------------------------------------------- profile graph helpers

def _edges():
    from lib import migration_profile as mp
    pdir = os.environ.get("SPP_PROFILE_DIR", mp._DEFAULT_PROFILE_DIR)
    return mp._discover_edges(pdir)


def compute_supported_versions(created_label):
    """created version + every label reachable downward through adjacent steps."""
    from lib import migration_profile as mp
    created_label = ver_label(*parse_ver(created_label))
    edges = _edges()
    start = mp._snap_source(created_label, edges)
    seen = {created_label, start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in edges.get(cur, {}):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return sorted(seen, key=_vkey, reverse=True)


def resolve_direction(s_label, t_label):
    """-> (direction, supported)."""
    s_label, t_label = (ver_label(*parse_ver(v)) for v in (s_label, t_label))
    sk, tk = _vkey(s_label), _vkey(t_label)
    if tk == sk:
        return "exact", True
    if tk > sk:
        return "native_upgrade", True
    from lib import migration_profile as mp
    edges = _edges()
    snapped_from = mp._snap_source(s_label, edges)
    snapped = mp._snap_target(t_label, edges)
    path = mp._find_path(edges, snapped_from, snapped)
    if path is None and sk[0] == tk[0]:
        return "downgrade", mp._major_baseline_profile(sk[0]) is not None
    return "downgrade", path is not None


def _reload_profile_engine():
    import importlib
    for name in ("lib.migration_profile", "lib.hbo_reserializer"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from lib import hbo_reserializer as hr
    return hr


def _bind_raster_target(s_label, t_label):
    os.environ["SPP_PROFILE"] = f"v{s_label}_to_v{t_label}"
    os.environ["SPP_TARGET_VERSION"] = t_label
    hr = _reload_profile_engine()
    try:
        hr.runtime.TARGET_MEMBERS = hr.runtime.load_members(t_label)
    except Exception:
        hr.runtime.TARGET_MEMBERS = None
    return hr


def _hbo_raster_requests(raw, dataset_path, s_label, t_label):
    if dataset_path not in ("paint/document.bin", "paint/default_material.bin"):
        return []
    if len(raw) < 12 or struct.unpack("<I", raw[:4])[0] != 0x1B7C2FDD:
        return []
    hr = _bind_raster_target(s_label, t_label)
    try:
        return hr.HBOSerializer(raw).raster_plan(dataset_path=dataset_path, target_label=t_label)
    except Exception:
        return []


def _raster_requests_from_uspp(uspp_path, s_label, t_label):
    out = []
    with open_archive(uspp_path) as z:
        try:
            datasets = json.loads(z.read("datasets.json").decode("utf-8"))
        except Exception:
            return out
        for path, info in datasets.items():
            if not info.get("is_hbo"):
                continue
            data_file = info.get("data_file")
            if not data_file:
                continue
            try:
                raw = z.read(data_file)
            except Exception:
                continue
            out.extend(_hbo_raster_requests(raw, path, s_label, t_label))
    return out


def _read_native_version_and_hbo(spp_path):
    import h5py
    from spp_extractor import _parse_project_settings, _painter_version
    version = None
    items = []
    with h5py.File(spp_path, "r") as f:
        if "projectsettings.ini" in f:
            try:
                settings = bytes(f["projectsettings.ini"][()])
                parsed = _painter_version(_parse_project_settings(settings))
                if parsed:
                    version = ver_label(parsed['major'], parsed['minor'])
            except Exception:
                pass

        def visit(name, obj):
            try:
                import h5py as _h5py
                if not isinstance(obj, _h5py.Dataset):
                    return
                raw = bytes(obj[()])
                if len(raw) >= 12 and struct.unpack("<I", raw[:4])[0] == 0x1B7C2FDD:
                    items.append((name, raw))
            except Exception:
                pass

        f.visititems(visit)
    return version, items


def _targets_for_raster_plan(source_label, targets_arg):
    if targets_arg == "all-lower":
        return [v for v in compute_supported_versions(source_label)
                if _vkey(v) < _vkey(source_label)]
    return [ver_label(*parse_ver(v.strip())) for v in str(targets_arg).split(",") if v.strip()]


# ------------------------------------------------------------------- subcommands

def _batch_inputs(args):
    """Discover before writing; the first input owns each duplicate source path."""
    jobs = {}
    errors = []
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    settings = getattr(args, 'compression_settings', None) or CompressionSettings()

    def add(source, relative):
        destination = (output_dir / relative if output_dir else source).with_suffix(settings.extension)
        jobs.setdefault(source.resolve(), (source, destination))

    def walk_error(error):
        errors.append((error.filename or 'folder', str(error)))

    for value in args.inputs:
        try:
            path = Path(os.path.abspath(value))
            path.stat()  # Report inaccessible or missing inputs instead of silently ignoring them.
            if path.is_file():
                if path.suffix.lower() != '.spp':
                    raise ValueError('input file must have the .spp extension')
                add(path, Path(path.name))
            elif path.is_dir():
                found = False
                for folder, directories, files in os.walk(path, onerror=walk_error, followlinks=False):
                    directories[:] = sorted(
                        (name for name in directories
                         if not os.path.islink(os.path.join(folder, name))
                         and not getattr(os.path, 'isjunction', lambda p: False)(os.path.join(folder, name))),
                        key=str.casefold,
                    ) if args.recursive else []
                    for name in sorted(files, key=str.casefold):
                        if Path(name).suffix.lower() == '.spp':
                            source = Path(folder) / name
                            try:
                                add(source, source.relative_to(path))
                                found = True
                            except (OSError, RuntimeError) as error:
                                errors.append((str(source), str(error)))
                if not found:
                    errors.append((value, 'no .spp files found'))
            else:
                raise ValueError('input must be a file or folder')
        except (OSError, ValueError, RuntimeError) as error:
            errors.append((value, str(error)))
    return list(jobs.values()), errors


def _source_identity(source):
    info = source.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _check_batch_output(destination, overwrite):
    if os.path.lexists(destination) and destination.is_dir():
        raise IsADirectoryError(f'output is a directory: {destination}')
    if not overwrite and os.path.lexists(destination):
        raise FileExistsError(f'output exists: {destination}. Use --overwrite to replace it')


def _recycle_source(source):
    """Use the native trash backend. Never fall back to permanent deletion."""
    if os.name == 'nt':
        # Refuse any operation that Windows cannot send to the Recycle Bin.
        import pythoncom
        from win32com.shell import shell, shellcon
        from win32com.server.exception import COMException
        from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink

        class RecycleOnlySink(FileOperationProgressSink):
            def PreDeleteItem(self, flags, item):
                if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                    raise COMException('Windows cannot recycle this source', scode=-2147467260)

        pythoncom.CoInitialize()
        try:
            operation = pythoncom.CoCreateInstance(
                shell.CLSID_FileOperation, None, pythoncom.CLSCTX_ALL, shell.IID_IFileOperation)
            operation.SetOperationFlags(
                shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT
                | shellcon.FOFX_EARLYFAILURE | 0x00080000 | 0x20000000)
            # FOFX_RECYCLEONDELETE | FOFX_ADDUNDORECORD (Windows 8+).
            sink = pythoncom.WrapObject(RecycleOnlySink(), shell.IID_IFileOperationProgressSink)
            item = shell.SHCreateItemFromParsingName(str(source.absolute()), None, shell.IID_IShellItem)
            operation.DeleteItem(item, sink)
            result = operation.PerformOperations()
            if result or operation.GetAnyOperationsAborted():
                raise OSError('Windows did not move the source to the Recycle Bin')
        except (pythoncom.com_error, COMException) as error:
            raise OSError(f'Could not move the source to the Recycle Bin: {error}') from error
        finally:
            pythoncom.CoUninitialize()
        return
    else:
        from send2trash import send2trash
    send2trash(str(source.absolute()))


@profiled_pack
def _batch_pack_one(source, destination, args):
    """Publish complete archives, then optionally remove the unchanged source."""
    temporary = None
    try:
        if source.resolve() == destination.resolve():
            raise ValueError(f'output points to a source project: {destination}')
        _check_batch_output(destination, args.overwrite)
        previous_output = destination.lstat() if args.overwrite and os.path.lexists(destination) else None
        identity = _source_identity(source) if args.delete_source else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f'.{destination.name}.', suffix='.tmp', delete=False,
        ) as handle:
            temporary = Path(handle.name)
        pack_args = argparse.Namespace(
            input=str(source), output=str(temporary), verbose=args.verbose,
            compression_settings=getattr(args, 'compression_settings', None),
            raster_capture_dir=getattr(args, 'raster_capture_dir', None),
            raster_budget_mb=getattr(args, 'raster_budget_mb', None),
        )
        with open(os.devnull, 'w', encoding='utf-8') as quiet:
            with nullcontext() if args.verbose or os.environ.get('USPP_PROGRESS') else redirect_stdout(quiet):
                rc = cmd_pack(pack_args)
        if rc:
            raise RuntimeError(f'packer returned exit code {rc}')
        from spp_extractor import emit_progress
        emit_progress(-1, 'Verifying archive...')
        members = None if args.delete_source or previous_output is not None else PROJECT_METADATA_MEMBERS
        with span('pack.verify', full=args.delete_source or previous_output is not None):
            with open_archive(temporary, members=members, verification_only=True) as archive:
                verify_project_archive(archive, full=args.delete_source)
                if previous_output is not None and not args.delete_source:
                    bad_member = archive.testzip()
                    if bad_member:
                        raise ValueError(f'archive integrity check failed: {bad_member}')
        if args.delete_source or previous_output is not None:
            if args.delete_source and _source_identity(source) != identity:
                raise RuntimeError('source changed during conversion; source was kept')
            with temporary.open('r+b') as archive_file:
                os.fsync(archive_file.fileno())
        emit_progress(-1, 'Saving archive...')
        if previous_output is not None:
            current = destination.lstat()
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
            if any(getattr(current, field) != getattr(previous_output, field) for field in fields):
                raise RuntimeError('output changed during conversion; previous archive was kept')
            _recycle_source(destination)
            if os.path.lexists(destination):
                raise RuntimeError('previous archive was not moved to the Recycle Bin; source was kept')
            print(f'RECYCLED previous archive -> {destination}', flush=True)
        try:
            with span('pack.publish'):
                _publish_archive(temporary, destination)
        except OSError as error:
            if previous_output is not None:
                raise RuntimeError(f'could not save the new archive; previous archive is in the Recycle Bin and source was kept: {error}') from error
            raise
        if args.delete_source:
            try:
                if _source_identity(source) != identity:
                    raise RuntimeError('source changed during conversion')
                _recycle_source(source)
                if os.path.lexists(source):
                    raise RuntimeError('source still exists after recycling')
            except (OSError, RuntimeError) as error:
                raise RuntimeError(f'archive saved to {destination}, but source was not moved to the Recycle Bin: {error}') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _publish_archive(temporary, destination):
    if os.name == 'nt':
        # rename() cannot overwrite a file created after recycling the previous output.
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


def _worker_init():
    # The parent handles cancellation; active archives finish safely.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _batch_worker(source, destination, args):
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            _batch_pack_one(source, destination, args)
        return None, output.getvalue()
    except Exception as error:
        if args.verbose:
            import traceback
            traceback.print_exc(file=output)
        return str(error), output.getvalue()


def _positive_workers(value):
    number = int(value)
    if number < 1 or number > 61:
        raise argparse.ArgumentTypeError('workers must be between 1 and 61')
    return number


def _parallel_batch(jobs, args, report):
    """Bound queued work to the worker count; parent owns all console output."""
    succeeded = 0
    iterator = iter(jobs)
    pending = {}
    pool = ProcessPoolExecutor(max_workers=min(args.workers, len(jobs)),
                               mp_context=multiprocessing.get_context('spawn'),
                               initializer=_worker_init)

    def submit():
        job = next(iterator, None)
        if job is not None:
            index, source, destination = job
            print(f'[{index}] START {source}', flush=True)
            pending[pool.submit(_batch_worker, source, destination, args)] = (source, destination)

    try:
        for _ in range(min(args.workers, len(jobs))):
            submit()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                source, destination = pending.pop(future)
                try:
                    error, output = future.result()
                    if output and args.verbose:
                        print(f'--- {source} ---\n{output}', flush=True)
                    if error is not None:
                        report(source, error)
                    else:
                        succeeded += 1
                        print(f'OK -> {destination}' + (' (source moved to Recycle Bin)' if args.delete_source else ''), flush=True)
                except Exception as error:
                    report(source, error)
                submit()
    except KeyboardInterrupt:
        print('Cancellation requested. Waiting for active files to finish.', file=sys.stderr, flush=True)
        # Do not start more files. Include completed in-flight results in totals.
        for future, (source, destination) in pending.items():
            if future.cancel():
                continue
            try:
                error, _ = future.result()
                if error is None:
                    succeeded += 1
                    print(f'OK -> {destination}' + (' (source moved to Recycle Bin)' if args.delete_source else ''), flush=True)
                else:
                    report(source, error)
            except Exception as error:
                report(source, error)
        return succeeded, True
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return succeeded, False


def cmd_batch_pack(args):
    succeeded = planned = failed = 0
    label = 'Dry run' if args.dry_run else 'Batch'

    def totals():
        count = f'{planned} planned' if args.dry_run else f'{succeeded} succeeded'
        return f'{count}, {failed} failed'

    def report(source, error):
        nonlocal failed
        failed += 1
        print(f'FAILED {source}: {error}', file=sys.stderr, flush=True)

    try:
        jobs, errors = _batch_inputs(args)
        for source, error in errors:
            report(source, error)
        if not jobs:
            print(f'{label} complete: {totals()}. No matching .spp files.')
            return 2

        destinations = {}
        invalid = {}
        sources = set()
        for source, _ in jobs:
            try:
                sources.add(source.resolve())
            except (OSError, RuntimeError) as error:
                invalid[source] = str(error)
        for source, destination in jobs:
            try:
                key = destination.resolve()
                if key in sources:
                    invalid[source] = f'output points to a source project: {destination}'
                destinations.setdefault(key, []).append(source)
            except (OSError, RuntimeError) as error:
                invalid[source] = str(error)
        for destination, owners in destinations.items():
            if len(owners) > 1:
                for source in owners:
                    invalid[source] = f'multiple sources map to output: {destination}'

        if not args.dry_run and getattr(args, 'workers', 1) > 1:
            ready = []
            for index, (source, destination) in enumerate(jobs, 1):
                if source in invalid:
                    report(source, invalid[source])
                else:
                    ready.append((index, source, destination))
            if ready:
                settings = getattr(args, 'compression_settings', None)
                if settings is not None and settings.method == 'lzma2' and not settings.threads:
                    budget = max(1, (os.cpu_count() or 1) // min(args.workers, len(ready)))
                    args.compression_settings = replace(settings, threads=min(24, budget))
                print(f'Workers: {min(args.workers, len(ready))}', flush=True)
                succeeded, cancelled = _parallel_batch(ready, args, report)
                if cancelled:
                    print(f'{label} cancelled: {totals()}.', file=sys.stderr)
                    return 130
            print(f'{label} complete: {totals()}.')
            return 1 if failed else 0

        for index, (source, destination) in enumerate(jobs, 1):
            print(f'[{index}/{len(jobs)}] {source}', flush=True)
            try:
                if source in invalid:
                    raise ValueError(invalid[source])
                if args.dry_run:
                    _check_batch_output(destination, args.overwrite)
                    action = 'REPLACE' if os.path.lexists(destination) else 'PACK'
                    print(f'WOULD {action} -> {destination}', flush=True)
                    if action == 'REPLACE':
                        print(f'WOULD RECYCLE -> {destination} (existing archive, after successful conversion)', flush=True)
                    if args.delete_source:
                        print(f'WOULD RECYCLE -> {source} (after successful conversion)', flush=True)
                    planned += 1
                    continue
                _batch_pack_one(source, destination, args)
                succeeded += 1
                print(f'OK -> {destination}' + (' (source moved to Recycle Bin)' if args.delete_source else ''), flush=True)
            except Exception as error:
                report(source, error)
                if args.verbose:
                    import traceback
                    traceback.print_exc()
    except KeyboardInterrupt:
        print(f'{label} cancelled: {totals()}.', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        report('batch inputs', error)
        print(f'{label} complete: {totals()}.')
        return 2
    print(f'{label} complete: {totals()}.')
    return 1 if failed else 0


def cmd_pack(args):
    if getattr(args, 'publish_output', False):
        publish_args = argparse.Namespace(**vars(args))
        publish_args.overwrite = True  # Retain the existing single-file pack replacement behavior.
        publish_args.delete_source = False
        _batch_pack_one(Path(args.input), Path(args.output), publish_args)
        print(f'packed -> {args.output}', flush=True)
        return 0
    from spp_extractor import SPPExtractor, emit_progress
    emit_progress(-1, "Reading project...")
    ex = SPPExtractor(args.input, verbose=args.verbose)
    with span('pack.extract'):
        extraction = ex.extract(decode_hbo=False, skip_texture_cache=True)
    emit_progress(0.10, "Compressing project...")
    cv = extraction.metadata.get("painter_version") or {}
    created = dict(cv) if cv.get("major") is not None else None
    extra = {
        "format_version": FORMAT_VERSION,
        "tool_version": TOOL_VERSION,
        "created_version": created,
        "supports_native_upgrade": True,
    }
    if created:
        extra["supported_versions"] = compute_supported_versions(ver_label(created["major"], created["minor"]))
        print(f"Detected Painter v{source_revision_of(extra, extraction.metadata)}")
    with span('pack.write'):
        _save_pack_archive(ex, extraction, args, extra)
    print(f"packed -> {args.output}")
    return 0


def _save_pack_archive(ex, extraction, args, extra):
    ex.save_as_uspp(
        extraction,
        args.output,
        extra_manifest=extra,
        raster_capture_dir=getattr(args, "raster_capture_dir", None),
        raster_budget_bytes=(
            int(args.raster_budget_mb) * 1024 * 1024
            if getattr(args, "raster_budget_mb", None) is not None else None
        ),
        compression=(getattr(args, 'compression_settings', None) or
                     CompressionSettings('lzma2' if str(args.output).lower().endswith('.usppz') else 'deflate')),
    )


def cmd_convert(args):
    """One-shot .spp -> .spp: pack to an intermediate .uspp, then build for the target."""
    import tempfile

    keep = getattr(args, "keep_uspp", None)
    if keep:
        uspp_path = keep
        tmp = None
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".uspp", delete=False)
        tmp.close()
        uspp_path = tmp.name

    try:
        pack_ns = argparse.Namespace(
            input=args.input,
            output=uspp_path,
            raster_capture_dir=getattr(args, "raster_capture_dir", None),
            raster_budget_mb=getattr(args, "raster_budget_mb", None),
            verbose=args.verbose,
            compression_settings=(resolve_compression(output=keep) if keep else CompressionSettings()),
            publish_output=bool(keep),
        )
        rc = cmd_pack(pack_ns)
        if rc != 0:
            return rc

        build_ns = argparse.Namespace(
            uspp=uspp_path,
            target=args.target,
            output=args.output,
            verbose=args.verbose,
        )
        return cmd_build(build_ns)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def cmd_info(args):
    with open_archive(args.uspp, members=('manifest.json', 'metadata.json')) as archive:
        manifest, meta = load_manifest(archive)
        compression = archive.compression_info()
    compression['configuration'] = manifest.get('compression')
    cv = created_version_of(manifest, meta)
    print(json.dumps({
        "created_version": ver_label(*cv) if cv else None,
        "source_revision": source_revision_of(manifest, meta),
        "source_file": manifest.get("source_file"),
        "supported_versions": manifest.get("supported_versions"),
        "format_version": manifest.get("format_version"),
        "tool_version": manifest.get("tool_version"),
        "source_file": manifest.get("source_file"),
        "compression": compression,
    }, indent=2))
    return 0


def cmd_plan(args):
    with open_archive(args.uspp) as archive:
        return _plan_from_archive(args, archive)


def _plan_from_archive(args, archive):
    manifest, meta = load_manifest(archive)
    cv = created_version_of(manifest, meta)
    if not cv:
        print(json.dumps({"error": "no created_version in .uspp"}))
        return 2
    s_label = ver_label(*cv)
    t_label = ver_label(*parse_ver(args.target))
    direction, supported = resolve_direction(s_label, t_label)

    lost = []
    raster_requests = []
    raster_summary = {}
    if direction == "downgrade" and supported:
        from lib import migration_profile as mp
        from lib.lossiness import build_lossiness_report
        from lib.raster_manifest import summarize
        profile = mp.load(f"v{s_label}_to_v{t_label}")
        lost = build_lossiness_report(profile)
        raster_requests = _raster_requests_from_uspp(archive, s_label, t_label)
        raster_summary = summarize(load_raster_manifest(archive), raster_requests)

    result = {
        "direction": direction,
        "supported": supported,
        "lossy": bool(lost) or bool(raster_requests),
        "lost_features": lost,
        "source_version": s_label,
        "source_revision": source_revision_of(manifest, meta),
        "source_file": manifest.get("source_file"),
        "target_version": t_label,
        "tool_version": TOOL_VERSION,
    }
    if raster_summary:
        result.update(raster_summary)
    else:
        result.update({
            "raster_required": False,
            "raster_available": False,
            "missing_raster_fallbacks": [],
            "editable_loss": [],
        })
    print(json.dumps(result, indent=2))
    return 0


def cmd_raster_plan(args):
    s_label, items = _read_native_version_and_hbo(args.input)
    if not s_label:
        print(json.dumps({"error": "could not detect source Painter version"}))
        return 2
    targets = _targets_for_raster_plan(s_label, args.targets)
    result = {
        "version": 1,
        "source": args.input,
        "source_version": s_label,
        "targets": targets,
        "requests": [],
        "assets": [],
        "warnings": [],
    }
    for t_label in targets:
        direction, supported = resolve_direction(s_label, t_label)
        if direction != "downgrade" or not supported:
            result["warnings"].append(f"no downgrade path from v{s_label} to v{t_label}")
            continue
        for dataset_path, raw in items:
            result["requests"].extend(_hbo_raster_requests(raw, dataset_path, s_label, t_label))
    # De-dupe identical request ids across datasets/targets after all targets are scanned.
    seen = {}
    deduped = []
    for req in result["requests"]:
        rid = req.get("id")
        previous = seen.get(rid)
        if previous is not None:
            for key in ("reason", "object_type"):
                incoming = req.get(key)
                current = previous.get(key)
                if incoming and incoming not in str(current or "").split(","):
                    previous[key] = ",".join(v for v in (current, incoming) if v)
            previous_capture = previous.setdefault("capture", {})
            incoming_capture = req.get("capture") or {}
            old_mask = previous_capture.get("channel_mask")
            new_mask = incoming_capture.get("channel_mask")
            if old_mask is None:
                previous_capture["channel_mask"] = new_mask
            elif new_mask is not None:
                previous_capture["channel_mask"] = old_mask | new_mask
            continue
        seen[rid] = req
        deduped.append(req)
    result["requests"] = deduped
    text = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0


def cmd_build(args):
    with open_archive(args.uspp) as archive:
        return _build_from_archive(args, archive)


def _build_from_archive(args, archive):
    manifest, meta = load_manifest(archive)
    cv = created_version_of(manifest, meta)
    if not cv:
        print("error: no created_version in .uspp", file=sys.stderr)
        return 2
    s_label = ver_label(*cv)
    t_major, t_minor = parse_ver(args.target)
    t_label = ver_label(t_major, t_minor)
    direction, supported = resolve_direction(s_label, t_label)
    if not supported:
        print(f"error: no conversion path from v{s_label} to v{t_label}", file=sys.stderr)
        return 3

    if direction == "downgrade":
        from lib import migration_profile as mp
        snapped = mp._snap_target(t_label, _edges())
        if snapped != t_label:
            print(f"v{t_label} requested -> building for v{snapped} (shares the v{snapped.split('.')[0]}.x format; opens in v{t_label} and newer)")
        os.environ["SPP_PROFILE"] = f"v{s_label}_to_v{t_label}"
        # The member-allowlist filter uses the version the file will OPEN in (the requested
        # target, incl. minor), not the snapped format boundary.
        os.environ["SPP_TARGET_VERSION"] = t_label
        build_spp = _import_builder()
        ok = build_spp(args.uspp, args.output, verbose=args.verbose, target_major=t_major, archive=archive)
    else:
        # Exact / native-upgrade builds copy compatible streams as-is. Painter upgrades
        # on open if needed; do not infer the source major as a downgrade target.
        os.environ.pop("SPP_PROFILE", None)
        build_spp = _import_builder()
        ok = build_spp(
            args.uspp,
            args.output,
            verbose=args.verbose,
            target_major=None,
            preserve_source=True,
            archive=archive,
        )

    if ok:
        print(f"built -> {args.output}")
        return 0
    print("error: build failed", file=sys.stderr)
    return 1


def _import_builder():
    """Import the builder AFTER SPP_PROFILE is set so the import-time profile binding
    is correct. Reload the profile/engine chain if already imported (in-process reuse)."""
    import importlib
    for name in ("lib.migration_profile", "lib.hbo_reserializer", "spp_builder"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    from spp_builder import build_spp
    return build_spp


def _add_compression_options(parser):
    parser.add_argument('--timings', metavar='DIR',
                        help='Write per-file JSON timing reports and live probe events to DIR')
    parser.add_argument('--compress', action='store_true',
                        help='Use LZMA2 compression (.usppz); default: Deflate (.uspp)')
    parser.add_argument('--advanced-config', metavar='KEY=VALUE,...',
                        help='LZMA2 options: preset, extreme, dict_size, threads, mode, nice_len, mf, depth, lc, lp, pb')


def main():
    # The plugin also decodes converter progress as UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    ap = argparse.ArgumentParser(description="Universal .uspp converter")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pack"); p.add_argument("input"); p.add_argument("-o", "--output"); p.add_argument("--raster-capture-dir"); p.add_argument("--raster-budget-mb", type=int); p.set_defaults(fn=cmd_pack)
    _add_compression_options(p)
    p = sub.add_parser('batch-pack', help='Pack .spp files and folders; report errors and continue')
    p.add_argument('inputs', nargs='+', help='One or more .spp files or folders')
    p.add_argument('-r', '--recursive', action='store_true', help='Include subfolders without following directory links')
    p.add_argument('-o', '--output-dir', help='Write beneath this folder instead of beside each source')
    p.add_argument('--overwrite', action='store_true', help='Recycle existing outputs and replace them only after packing and verification succeed')
    p.add_argument('--delete-source', action='store_true', help='Move each source .spp to the Recycle Bin only after its archive passes integrity checks')
    p.add_argument('--workers', type=_positive_workers, default=1, metavar='N',
                   help='Pack and compress up to N files in separate processes (1-61; default: 1)')
    p.add_argument('--dry-run', action='store_true', help='Preview output paths and source deletions without changing files')
    p.add_argument('-v', '--verbose', action='store_true', default=argparse.SUPPRESS, help='Show packer details and tracebacks')
    _add_compression_options(p)
    p.set_defaults(fn=cmd_batch_pack)
    p = sub.add_parser("raster-plan"); p.add_argument("input"); p.add_argument("--targets", default="all-lower"); p.add_argument("-o", "--output"); p.set_defaults(fn=cmd_raster_plan)
    p = sub.add_parser("plan"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("build"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.add_argument("-o", "--output", required=True); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("convert"); p.add_argument("input"); p.add_argument("--target", required=True); p.add_argument("-o", "--output", required=True); p.add_argument("--raster-capture-dir"); p.add_argument("--raster-budget-mb", type=int); p.add_argument("--keep-uspp"); p.set_defaults(fn=cmd_convert)
    p = sub.add_parser("info"); p.add_argument("--uspp", required=True); p.set_defaults(fn=cmd_info)

    args = ap.parse_args()
    if args.cmd == 'convert' and args.keep_uspp:
        try:
            resolve_compression(output=args.keep_uspp)
        except ValueError as error:
            ap.error(str(error))
    if args.cmd in ('pack', 'batch-pack'):
        try:
            args.compression_settings = resolve_compression(
                'lzma2' if args.compress else None, getattr(args, 'output', None), args.advanced_config)
        except ValueError as error:
            ap.error(str(error))
        if args.cmd == 'pack':
            args.output = args.output or str(Path(args.input).with_suffix(args.compression_settings.extension))
            args.publish_output = True
        print(describe_compression(args.compression_settings), flush=True)
    try:
        rc = args.fn(args)
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr)
        rc = 130
    except Exception as error:
        print(f'error: {error}', file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
