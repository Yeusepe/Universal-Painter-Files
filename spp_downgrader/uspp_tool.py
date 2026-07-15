#!/usr/bin/env python3
"""uspp_tool - client-facing universal .uspp converter (the bundled exe the Painter
plugin calls). One CLI over the extractor + builder + profile/lossiness engine.

Subcommands:
  pack  <in.spp> -o <out.uspp>            extract + write the universal manifest
  plan  --uspp <f> --target <maj.min>     -> JSON {direction, supported, lossy, lost_features, ...}
  build --uspp <f> --target <maj.min> -o <out.spp>   produce a native .spp for the target
  info  --uspp <f>                        print the manifest

Direction (S = .uspp created version, T = target/running version):
  T == S            exact          rebuild at S, no transform
  T  > S            native_upgrade rebuild at S, Painter upgrades on open
  T  < S            downgrade      compose profile v{S}_to_v{T} (lossy)
"""
import os
import sys
import json
import zipfile
import argparse
import struct

TOOL_VERSION = "1.0.0"
FORMAT_VERSION = 2

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "spp_extractor"))
sys.path.insert(0, os.path.join(_HERE, "spp_builder"))

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
    with zipfile.ZipFile(uspp_path) as z:
        manifest = json.loads(z.read("manifest.json"))
        try:
            meta = json.loads(z.read("metadata.json"))
        except KeyError:
            meta = {}
    return manifest, meta


def load_raster_manifest(uspp_path):
    with zipfile.ZipFile(uspp_path) as z:
        from lib.raster_manifest import load_from_zip
        return load_from_zip(z)


def created_version_of(manifest, meta):
    """(major, minor) the .uspp was authored in. Prefer manifest.created_version,
    fall back to metadata.painter_version (older .uspp without the universal manifest)."""
    cv = manifest.get("created_version") or meta.get("painter_version") or {}
    if cv.get("major") is not None:
        return (int(cv["major"]), int(cv.get("minor") or 0))
    return None


# --------------------------------------------------------- profile graph helpers

def _edges():
    from lib import migration_profile as mp
    pdir = os.environ.get("SPP_PROFILE_DIR", mp._DEFAULT_PROFILE_DIR)
    return mp._discover_edges(pdir)


def compute_supported_versions(created_label):
    """created version + every label reachable downward through adjacent steps."""
    from lib import migration_profile as mp
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
    with zipfile.ZipFile(uspp_path) as z:
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
    from spp_extractor import _parse_project_settings
    version = None
    items = []
    with h5py.File(spp_path, "r") as f:
        if "projectsettings.ini" in f:
            try:
                settings = bytes(f["projectsettings.ini"][()])
                parsed = _parse_project_settings(settings) or {}
                major = parsed.get(".versionAtLastSave/major")
                if major is None:
                    major = parsed.get(".versionAtCreation/major")
                minor = parsed.get(".versionAtLastSave/minor")
                if minor is None:
                    minor = parsed.get(".versionAtCreation/minor")
                if major is not None:
                    version = ver_label(int(major), int(minor or 0))
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

def cmd_pack(args):
    from spp_extractor import SPPExtractor, emit_progress
    emit_progress(-1, "Reading project…")
    ex = SPPExtractor(args.input, verbose=args.verbose)
    extraction = ex.extract(decode_hbo=False, skip_texture_cache=True)
    emit_progress(0.10, "Compressing project…")
    cv = extraction.metadata.get("painter_version") or {}
    created = {"major": int(cv.get("major")), "minor": int(cv.get("minor") or 0)} if cv.get("major") is not None else None
    extra = {
        "format_version": FORMAT_VERSION,
        "tool_version": TOOL_VERSION,
        "created_version": created,
        "supports_native_upgrade": True,
    }
    if created:
        extra["supported_versions"] = compute_supported_versions(ver_label(created["major"], created["minor"]))
    ex.save_as_uspp(
        extraction,
        args.output,
        extra_manifest=extra,
        raster_capture_dir=getattr(args, "raster_capture_dir", None),
        raster_budget_bytes=(
            int(args.raster_budget_mb) * 1024 * 1024
            if getattr(args, "raster_budget_mb", None) is not None else None
        ),
    )
    print(f"packed -> {args.output}")
    return 0


def cmd_info(args):
    manifest, meta = load_manifest(args.uspp)
    cv = created_version_of(manifest, meta)
    print(json.dumps({
        "created_version": ver_label(*cv) if cv else None,
        "supported_versions": manifest.get("supported_versions"),
        "format_version": manifest.get("format_version"),
        "tool_version": manifest.get("tool_version"),
        "source_file": manifest.get("source_file"),
    }, indent=2))
    return 0


def cmd_plan(args):
    manifest, meta = load_manifest(args.uspp)
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
        raster_requests = _raster_requests_from_uspp(args.uspp, s_label, t_label)
        raster_summary = summarize(load_raster_manifest(args.uspp), raster_requests)

    result = {
        "direction": direction,
        "supported": supported,
        "lossy": bool(lost) or bool(raster_requests),
        "lost_features": lost,
        "source_version": s_label,
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
    if args.target_binary:
        target_binary = os.path.abspath(args.target_binary)
        if not os.path.isfile(target_binary):
            print(f"error: target Painter binary not found: {target_binary}", file=sys.stderr)
            return 2
        os.environ["SPP_TARGET_BINARY"] = target_binary
    manifest, meta = load_manifest(args.uspp)
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
        ok = build_spp(args.uspp, args.output, verbose=args.verbose, target_major=t_major)
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


def main():
    ap = argparse.ArgumentParser(description="Universal .uspp converter")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pack"); p.add_argument("input"); p.add_argument("-o", "--output", required=True); p.add_argument("--raster-capture-dir"); p.add_argument("--raster-budget-mb", type=int); p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("raster-plan"); p.add_argument("input"); p.add_argument("--targets", default="all-lower"); p.add_argument("-o", "--output"); p.set_defaults(fn=cmd_raster_plan)
    p = sub.add_parser("plan"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("build"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.add_argument("-o", "--output", required=True); p.add_argument("--target-binary", help="exact Painter executable for the target-member compatibility filter"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("info"); p.add_argument("--uspp", required=True); p.set_defaults(fn=cmd_info)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
