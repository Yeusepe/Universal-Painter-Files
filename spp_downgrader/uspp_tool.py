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
    """created version + every label reachable downward through adjacent steps.
    A minor-version source (e.g. 11.1) isn't itself a graph node, so start the walk
    from the format node it shares (11) while still listing the created label."""
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
    snapped_from = mp._snap_source(s_label, edges)   # 11.1 -> 11: a minor source shares its major's format
    snapped = mp._snap_target(t_label, edges)        # 8.3 -> 8.1: minors share the format, forward-compat covers the gap
    path = mp._find_path(edges, snapped_from, snapped)
    if path is None and sk[0] == tk[0]:
        return "downgrade", mp._major_baseline_profile(sk[0]) is not None
    return "downgrade", path is not None


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
    ex.save_as_uspp(extraction, args.output, extra_manifest=extra)
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
    if direction == "downgrade" and supported:
        from lib import migration_profile as mp
        from lib.lossiness import build_lossiness_report
        profile = mp.load(f"v{s_label}_to_v{t_label}")
        lost = build_lossiness_report(profile)

    print(json.dumps({
        "direction": direction,
        "supported": supported,
        "lossy": bool(lost),
        "lost_features": lost,
        "source_version": s_label,
        "target_version": t_label,
        "tool_version": TOOL_VERSION,
    }, indent=2))
    return 0


def cmd_build(args):
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
        # exact / native_upgrade: faithful rebuild at the stored version (target_major
        # None disables the downgrade pipeline); Painter upgrades on open if newer.
        os.environ.pop("SPP_PROFILE", None)
        build_spp = _import_builder()
        ok = build_spp(args.uspp, args.output, verbose=args.verbose, target_major=None)

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

    p = sub.add_parser("pack"); p.add_argument("input"); p.add_argument("-o", "--output", required=True); p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("plan"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("build"); p.add_argument("--uspp", required=True); p.add_argument("--target", required=True); p.add_argument("-o", "--output", required=True); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("info"); p.add_argument("--uspp", required=True); p.set_defaults(fn=cmd_info)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
