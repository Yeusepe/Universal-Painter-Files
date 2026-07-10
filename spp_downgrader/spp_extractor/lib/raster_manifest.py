"""Helpers for raster fallback metadata inside .uspp archives."""
import hashlib
import json
import os
from pathlib import Path


MANIFEST_NAME = "raster/manifest.json"
ASSET_PREFIX = "raster/assets/"
VERSION = 1
DEFAULT_BUDGET_BYTES = 512 * 1024 * 1024


def empty_manifest():
    return {
        "version": VERSION,
        "requests": [],
        "assets": [],
        "warnings": [],
    }


def load_from_zip(zf):
    try:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except KeyError:
        return empty_manifest()
    except Exception as e:
        m = empty_manifest()
        m["warnings"].append(f"could not read raster manifest: {e}")
        return m


def asset_request_ids(manifest):
    out = set()
    for asset in manifest.get("assets") or []:
        rid = asset.get("request_id")
        if rid:
            out.add(rid)
    return out


def summarize(manifest, requests=None):
    if requests is None:
        requests = manifest.get("requests") or []
    requests = list(requests)
    have = asset_request_ids(manifest)
    missing = [r for r in requests if r.get("id") not in have]
    return {
        "raster_required": bool(requests),
        "raster_request_count": len(requests),
        "raster_available": bool(requests) and not missing,
        "missing_raster_fallbacks": missing,
        "editable_loss": sorted({r.get("scope") for r in requests if r.get("scope") in ("group", "full_stack_channel")}),
        "raster_asset_count": len(manifest.get("assets") or []),
        "raster_warnings": list(manifest.get("warnings") or []),
    }


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_capture_manifest(capture_dir):
    base = Path(capture_dir)
    for rel in ("raster/manifest.json", "manifest.json"):
        p = base / rel
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f), p.parent
    return None, base


def add_capture_dir_to_zip(zf, capture_dir, budget_bytes=DEFAULT_BUDGET_BYTES):
    """Store a capture directory into `zf` as raster/manifest.json + assets.

    Capture manifests may list:
      requests: plan requests emitted by uspp_tool raster-plan
      assets: {request_id, path, kind?, mime?}
    Asset paths are relative to the capture manifest directory unless absolute.
    """
    out = empty_manifest()
    if not capture_dir:
        zf.writestr(MANIFEST_NAME, json.dumps(out, indent=2))
        return out
    cap, cap_base = _load_capture_manifest(capture_dir)
    if not cap:
        out["warnings"].append("raster capture directory had no manifest.json")
        zf.writestr(MANIFEST_NAME, json.dumps(out, indent=2))
        return out
    out["requests"] = list(cap.get("requests") or [])
    seen_hashes = set()
    total = 0
    for asset in cap.get("assets") or []:
        rel = asset.get("path") or asset.get("file")
        if not rel:
            out["warnings"].append(f"asset for {asset.get('request_id')} had no path")
            continue
        p = Path(rel)
        if not p.is_absolute():
            p = cap_base / p
        if not p.exists():
            out["warnings"].append(f"missing raster asset: {p}")
            continue
        sha = _sha256_file(p)
        size = os.path.getsize(p)
        if sha not in seen_hashes and total + size > budget_bytes:
            out["warnings"].append(f"raster budget exceeded; skipped {p.name}")
            continue
        ext = p.suffix.lower() or ".png"
        arc = f"{ASSET_PREFIX}{sha}{ext}"
        if sha not in seen_hashes:
            zf.write(str(p), arc)
            seen_hashes.add(sha)
            total += size
        item = dict(asset)
        item.update({
            "sha256": sha,
            "size": size,
            "archive_path": arc,
            "mime": asset.get("mime") or ("image/png" if ext == ".png" else "application/octet-stream"),
        })
        item.pop("path", None)
        item.pop("file", None)
        out["assets"].append(item)
    zf.writestr(MANIFEST_NAME, json.dumps(out, indent=2))
    return out
