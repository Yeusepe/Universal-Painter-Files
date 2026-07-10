#!/usr/bin/env python3
"""
SPP Builder - Create Substance Painter project files from extracted data.

This module recreates HDF5-based SPP files from extracted USPP data,
including proper HBO encoding and HDF5 structure.

Usage:
    python spp_builder.py input.uspp output.spp

Requires: h5py, numpy
"""

import sys
import os
import re
import json
import zipfile
import tempfile
import struct
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

try:
    import h5py
    import numpy as np
except ImportError:
    print("Error: h5py and numpy are required. Install with: pip install h5py numpy")
    sys.exit(1)

# Add parent directories to path
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'spp_extractor'))

from hbo_encoder import encode_to_hbo
from lib.config_manager import load_config
from lib.hbo_reserializer import HBOSerializer


def _emit_progress(frac, msg):
    """Parseable progress line for the plugin UI (only when USPP_PROGRESS is set)."""
    if os.environ.get("USPP_PROGRESS"):
        try:
            sys.stdout.write("__USPP_PROGRESS__\t%.4f\t%s\n" % (frac, msg))
            sys.stdout.flush()
        except Exception:
            pass
from lib.migration_profile import ACTIVE as PROFILE

BINARY_MAGIC = 0x1B7C2FDD
BINARY_MAGIC_V11 = 0x69000B11


def _version_key(label):
    parts = re.findall(r"\d+", str(label or ""))
    return tuple(int(part) for part in (parts + ["0"])[:2]) if parts else None


def _raster_request_applies_to_target(request, target_label):
    """A capture planned for vN is also required by older additive targets."""
    request_key = _version_key(request.get("target"))
    target_key = _version_key(target_label)
    return request_key is None or target_key is None or target_key <= request_key


class SPPBuilder:
    """Build SPP (HDF5) files from extracted USPP data."""

    V10_DATASET_RENAMES = {
        'editor/iraysettings2.ini': 'editor/iraysettings.ini',
        'editor/layersstackstate2.bin': 'editor/layersstackstate.bin',
        'editor/viewersettings2.ini': 'editor/viewersettings.ini',
    }

    V10_DATA_VERSION_MAP = {
        'baking/baking.ini': 20,
        'editor/camera.ini': 2,
        'editor/colorprofile.ini': 0,
        'editor/iraysettings.ini': 1,
        'editor/layersstackstate.bin': 2,
        'editor/posteffects.ini': 7,
        'editor/viewersettings.ini': 26,
        'paint/default_material.bin': 81,
        'paint/document.bin': 81,
    }

    def __init__(self, verbose: bool = False, target_major: Optional[int] = None,
                 preserve_source: bool = False):
        self.verbose = verbose
        self.errors: List[str] = []
        self.target_major = target_major
        # Exact builds and forward-compatible opens must not infer a target from
        # source metadata. Doing so turns an already-v8 project into a second
        # v8 downgrade and can restamp its HBO streams with a newer data version.
        self.preserve_source = bool(preserve_source)
        self._downgrade_config = None
        # Fast mode: minimal output compression for throwaway temp files (plugin "Open").
        self.fast = bool(os.environ.get("SPP_FAST"))
        self._prepared_raster_resources = []
        self._raster_replacements = {}
        self._raster_required_ids = set()
        self._raster_requests = []
        # Apply migration profile overrides for dataset renames and data versions.
        self.V10_DATASET_RENAMES = PROFILE.dataset_renames or self.V10_DATASET_RENAMES
        self.V10_DATA_VERSION_MAP = PROFILE.data_version_map or self.V10_DATA_VERSION_MAP

    def log(self, message: str):
        if self.verbose:
            print(f"  {message}")

    # Max SBAM (cooked substance assembly) format each target's substance engine reads.
    # v8.1 ships engine 8.5.2 which reads format <=6; v9+ engines read >=9. Only v8.1
    # needs graphs dropped/swapped.
    SUBSTANCE_MAX_FORMAT = {8: 6}

    def _compute_drop_substance_graphs(self, zf):
        """Find embedded substance graphs whose cooked .sbsasm format exceeds what the
        target's substance engine can read, and (when no lower-format copy is shippable)
        mark them for dropping so the engine never tries to load them and crash."""
        from lib import hbo_reserializer as _hr
        _hr.runtime.DROP_SUBSTANCE_GRAPHS = set()
        maxfmt = self.SUBSTANCE_MAX_FORMAT.get(self.target_major)
        if maxfmt is None:
            return
        try:
            import py7zr, io
        except Exception:
            self.log("  (py7zr unavailable; cannot check substance graph formats)")
            return
        drop = set()
        for n in zf.namelist():
            if not (n.endswith('.sbsar.bin') and 'alg_meta' not in n):
                continue
            try:
                with py7zr.SevenZipFile(io.BytesIO(zf.read(n))) as a:
                    if not any(x.endswith('.sbsasm') for x in a.getnames()):
                        continue
                    with tempfile.TemporaryDirectory() as td:
                        a.extractall(path=td)
                        for root, _, files in os.walk(td):
                            for fl in files:
                                if not fl.endswith('.sbsasm'):
                                    continue
                                h = open(os.path.join(root, fl), 'rb').read(8)
                                if h[:4] == b'SBAM' and h[6] > maxfmt:
                                    gname = fl[:-len('.sbsasm')].rsplit('.sbsar', 1)[0]
                                    gname = re.sub(r'\.v\d+$', '', gname)
                                    drop.add(gname.lower())
            except Exception:
                continue
        if drop:
            _hr.runtime.DROP_SUBSTANCE_GRAPHS = drop
            self.log(f"  Substance graphs too new for the v{self.target_major} engine "
                     f"(format > {maxfmt}); dropping their layers: {sorted(drop)}")

    def build_from_uspp(self, uspp_path: str, output_spp_path: str,
                        target_major: Optional[int] = None) -> bool:
        """
        Build an SPP file from a USPP archive.

        Args:
            uspp_path: Path to input .uspp file (ZIP archive)
            output_spp_path: Path for output .spp file

        Returns:
            True on success, False on failure
        """
        uspp_path = Path(uspp_path)
        output_spp_path = Path(output_spp_path)
        if target_major is not None:
            self.target_major = target_major

        if not uspp_path.exists():
            self.errors.append(f"USPP file not found: {uspp_path}")
            return False

        print(f"Building SPP from: {uspp_path}")

        try:
            with zipfile.ZipFile(str(uspp_path), 'r') as zf:
                # Read manifest
                manifest = json.loads(zf.read('manifest.json'))
                self.log(f"Source: {manifest.get('source_file', 'unknown')}")

                # Read structure
                structure = json.loads(zf.read('structure.json'))

                # Read groups info
                groups_info = json.loads(zf.read('groups.json'))

                # Read datasets info
                datasets_info = json.loads(zf.read('datasets.json'))

                # Read metadata (for version inference)
                metadata = json.loads(zf.read('metadata.json'))
                source_major = None
                try:
                    source_major = metadata.get('painter_version', {}).get('major')
                except Exception:
                    source_major = None
                if self.target_major is None and not self.preserve_source:
                    self.target_major = source_major
                if self.target_major:
                    self.log(f"Target version: {self.target_major}")

                # Drop substance graphs whose cooked format the target engine can't read.
                self._compute_drop_substance_graphs(zf)

                # Load the target version's member allowlist and drop identifiers its reader
                # does not recognize.
                from lib import hbo_reserializer as _hr
                if self.target_major:
                    label = os.environ.get("SPP_TARGET_VERSION") or str(self.target_major)
                    _hr.runtime.TARGET_MEMBERS = _hr.runtime.load_members(label)
                    if _hr.runtime.TARGET_MEMBERS:
                        self.log(f"  target member allowlist: v{label} "
                                 f"({len(_hr.runtime.TARGET_MEMBERS)} identifiers)")
                    else:
                        self.log(f"  WARNING: no member allowlist for v{label}")
                else:
                    _hr.runtime.TARGET_MEMBERS = None

                if self.target_major:
                    self._report_raster_fallbacks(zf, datasets_info)

                self._prepare_raster_resources(zf)

                # Progress accounting for the plugin UI (one tick per dataset written).
                self._prog_total = max(1, len(datasets_info))
                self._prog_done = 0
                _emit_progress(-1, "Converting to target version…")

                # Create HDF5 file
                print(f"Creating: {output_spp_path}")
                # Keep HDF5 format within v1.10 bounds to match Painter's reader.
                with h5py.File(str(output_spp_path), 'w', libver=('earliest', 'v110')) as hf:
                    # Recreate structure from tree
                    self._create_structure(hf, structure, zf, groups_info, datasets_info)
                    self._inject_raster_resources(hf)

        except Exception as e:
            self.errors.append(f"Build failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        stats = os.path.getsize(output_spp_path)
        print(f"Created: {output_spp_path} ({stats / 1024 / 1024:.2f} MB)")
        return True

    def _report_raster_fallbacks(self, zf: zipfile.ZipFile, datasets_info: Dict):
        """Non-mutating report for raster fallbacks. Until bitmap-resource injection is
        verified, missing assets intentionally do not change build output."""
        try:
            from lib.raster_manifest import load_from_zip, summarize
        except Exception:
            return
        target_label = os.environ.get("SPP_TARGET_VERSION") or (str(self.target_major) if self.target_major else None)
        live_requests = []
        for path, info in datasets_info.items():
            if not info.get("is_hbo"):
                continue
            if path not in ("paint/document.bin", "paint/default_material.bin"):
                continue
            data_file = info.get("data_file")
            if not data_file:
                continue
            try:
                raw = zf.read(data_file)
                live_requests.extend(HBOSerializer(raw).raster_plan(dataset_path=path, target_label=target_label))
            except Exception as e:
                self.log(f"  Raster planning failed for {path}: {e}")
                continue
        raster_manifest = load_from_zip(zf)
        captured_requests = [
            request for request in (raster_manifest.get("requests") or [])
            if _raster_request_applies_to_target(request, target_label)
        ]
        requests_by_id = {
            request.get("id"): request for request in captured_requests if request.get("id")
        }
        requests_by_id.update({
            request.get("id"): request for request in live_requests if request.get("id")
        })
        requests = list(requests_by_id.values())
        self._raster_requests = requests
        self._raster_required_ids = {r.get("id") for r in requests if r.get("id")}
        self.log(
            f"  Raster planner identified {len(requests)} required fallback request(s) "
            f"({len(live_requests)} live, {len(captured_requests)} captured)"
        )
        summary = summarize(raster_manifest, requests)
        if summary.get("raster_required"):
            missing = summary.get("missing_raster_fallbacks") or []
            if missing:
                self.log(f"  Raster fallbacks needed but missing: {len(missing)} request(s); "
                         "continuing with current lossy downgrade behavior")
            else:
                self.log(f"  Raster fallback assets available: {summary.get('raster_asset_count', 0)}; "
                         "will apply matching graph rewrites during HBO conversion")

    def _prepare_raster_resources(self, zf: zipfile.ZipFile):
        try:
            from lib.raster_manifest import load_from_zip
            from raster_resources import RasterResourceError, prepare_png_resource
        except Exception as e:
            self.log(f"  Raster resource encoder unavailable: {e}")
            return
        self._prepared_raster_resources = []
        self._raster_replacements = {}
        required_ids = set(getattr(self, "_raster_required_ids", set()) or set())
        manifest = load_from_zip(zf)
        assets = list(manifest.get("assets") or [])
        if self.target_major and not required_ids:
            return
        if required_ids:
            assets = [a for a in assets if a.get("request_id") in required_ids]
        if not assets:
            return
        skipped = 0
        prepared_by_archive = {}
        for asset in assets:
            arc = asset.get("archive_path")
            if not arc:
                skipped += 1
                continue
            mime = (asset.get("mime") or "").lower()
            if mime and mime != "image/png":
                skipped += 1
                self.log(f"  Raster asset skipped (not PNG): {arc}")
                continue
            try:
                request_id = asset.get("request_id") or asset.get("sha256") or f"asset_{len(self._prepared_raster_resources)}"
                prepared = prepared_by_archive.get(arc)
                if prepared is None:
                    png = zf.read(arc)
                    resource_id = asset.get("sha256") or request_id
                    prepared = prepare_png_resource(png, resource_id)
                    prepared["asset"] = dict(asset)
                    prepared["request_id"] = request_id
                    prepared_by_archive[arc] = prepared
                    self._prepared_raster_resources.append(prepared)
                key = asset.get("request_id") or arc
                self._raster_replacements.setdefault(key, []).append({
                    "url": prepared["url"],
                    "channel": asset.get("channel"),
                    "channel_index": asset.get("channel_index"),
                    "channel_type": asset.get("channel_type"),
                    "material": asset.get("material"),
                    "material_index": asset.get("material_index"),
                    "stack": asset.get("stack"),
                    "stack_index": asset.get("stack_index"),
                    "kind": asset.get("kind"),
                    "uv_tile": asset.get("uv_tile"),
                    "archive_path": arc,
                })
            except RasterResourceError as e:
                skipped += 1
                self.log(f"  Raster asset skipped: {arc} ({e})")
            except Exception as e:
                skipped += 1
                self.log(f"  Raster asset injection failed: {arc} ({e})")
        if self._prepared_raster_resources:
            self.log(f"  Prepared {len(self._prepared_raster_resources)} raster bitmap resource(s)")
        if skipped:
            self.log(f"  Skipped {skipped} raster asset(s)")

    def _inject_raster_resources(self, hf: h5py.File):
        try:
            from raster_resources import write_prepared_resource
        except Exception:
            return
        prepared_resources = list(getattr(self, "_prepared_raster_resources", []) or [])
        if not prepared_resources:
            return
        injected = 0
        skipped = 0
        for prepared in prepared_resources:
            try:
                write_prepared_resource(hf, prepared, _m3_x64_128)
                injected += 1
            except Exception as e:
                skipped += 1
                self.log(f"  Raster asset injection failed: {prepared.get('token')} ({e})")
        if injected:
            self.log(f"  Injected {injected} raster bitmap resource(s)")
        if skipped:
            self.log(f"  Skipped {skipped} raster asset(s)")

    def _create_structure(self, hf: h5py.File, tree: Dict,
                          zf: zipfile.ZipFile, groups_info: Dict, datasets_info: Dict):
        """Recursively create HDF5 structure from tree."""

        # Handle root attributes
        if 'attributes' in tree:
            for attr_name in tree['attributes']:
                try:
                    # Try to read attribute value from metadata
                    metadata = json.loads(zf.read('metadata.json'))
                    if 'root_attributes' in metadata and attr_name in metadata['root_attributes']:
                        value = metadata['root_attributes'][attr_name]
                        dtype_hint = None
                        if 'root_attributes_dtypes' in metadata:
                            dtype_hint = metadata['root_attributes_dtypes'].get(attr_name)
                        order_hint = None
                        if 'root_attributes_orders' in metadata:
                            order_hint = metadata['root_attributes_orders'].get(attr_name)
                        if isinstance(value, list):
                            if order_hint == 1 and (not dtype_hint or np.dtype(dtype_hint) == np.dtype('uint8')):
                                try:
                                    from h5py import h5a, h5s, h5t
                                    arr = np.array(value, dtype=np.uint8)
                                    space = h5s.create_simple(arr.shape)
                                    attr_id = h5a.create(hf.id, attr_name.encode('utf-8'), h5t.STD_U8BE, space)
                                    attr_id.write(arr)
                                except Exception:
                                    hf.attrs[attr_name] = np.array(value, dtype=np.uint8)
                            elif dtype_hint:
                                hf.attrs[attr_name] = np.array(value, dtype=np.dtype(dtype_hint))
                            else:
                                hf.attrs[attr_name] = np.array(value, dtype=np.uint8)
                        else:
                            hf.attrs[attr_name] = value
                except:
                    pass

        # Create children
        if 'children' in tree:
            for name, child in tree['children'].items():
                if child['type'] == 'group':
                    self._create_group(hf, name, child, zf, groups_info, datasets_info)
                elif child['type'] == 'dataset':
                    self._create_dataset(hf, name, child, zf, datasets_info)

    def _create_group(self, parent: h5py.Group, name: str, tree: Dict,
                      zf: zipfile.ZipFile, groups_info: Dict, datasets_info: Dict):
        """Create an HDF5 group."""
        self.log(f"Group: {tree['path']}")

        group_info = groups_info.get(tree['path'], {})
        creation_props = group_info.get('creation_props') or {}
        group_kwargs = {}
        if 'link_creation_order' in creation_props:
            group_kwargs['track_order'] = bool(creation_props.get('link_creation_order'))
        if 'track_times' in creation_props:
            group_kwargs['track_times'] = bool(creation_props.get('track_times'))

        try:
            group = parent.create_group(name, **group_kwargs)
        except TypeError:
            # Some h5py builds don't support track_times for groups.
            group_kwargs.pop('track_times', None)
            group = parent.create_group(name, **group_kwargs)

        # Set attributes if available
        attrs = group_info.get('attributes', {})
        for attr_name, value in attrs.items():
            try:
                if isinstance(value, list):
                    if all(isinstance(v, int) and 0 <= v <= 255 for v in value):
                        group.attrs[attr_name] = np.array(value, dtype=np.uint8)
                    else:
                        group.attrs[attr_name] = np.array(value)
                else:
                    group.attrs[attr_name] = value
            except Exception:
                continue

        # Create children
        if 'children' in tree:
            for child_name, child in tree['children'].items():
                if child['type'] == 'group':
                    self._create_group(group, child_name, child, zf, groups_info, datasets_info)
                elif child['type'] == 'dataset':
                    self._create_dataset(group, child_name, child, zf, datasets_info)

    def _infer_hbo_header(self, data: bytes) -> Optional[Dict[str, Any]]:
        if len(data) < 4:
            return None
        magic = struct.unpack('<I', data[:4])[0]
        if magic == BINARY_MAGIC_V11:
            return {'format': 'v11_binary', 'data_version': None}
        if len(data) < 12:
            return None
        magic, version_check, data_version = struct.unpack('<III', data[:12])
        if magic != BINARY_MAGIC:
            return None
        if version_check == 0:
            fmt = 'v10'
        elif version_check == 1:
            fmt = 'v11'
        else:
            fmt = f'unknown({version_check})'
        return {'format': fmt, 'data_version': data_version}

    # Shader-helper functions present in v11/v12 shader libs (lib-sss.glsl etc.) but
    # absent in v10-and-below. name -> GLSL stub giving the v10-equivalent behaviour
    # (no per-channel SSS scattering color -> use the uniform path, i.e. return false).
    SHADER_COMPAT_STUBS = {
        'usesSSSScatteringColorChannel': 'bool usesSSSScatteringColorChannel() { return false; }',
    }

    def _patch_shader_compat(self, raw: bytes):
        """Inject stubs for v11/v12-only shader-helper functions that an embedded
        shader CALLS but the v10- runtime does not DEFINE. Returns patched bytes, or
        None if nothing to do / not text."""
        try:
            text = raw.decode('utf-8')
        except Exception:
            return None
        inject = []
        for fn, stub in self.SHADER_COMPAT_STUBS.items():
            called = (fn + '(') in text
            defined = ('bool ' + fn) in text or ('void ' + fn) in text or ('float ' + fn) in text
            if called and not defined:
                inject.append(stub)
        if not inject:
            return None
        lines = text.split('\n')
        # insert after the last `import ...` line so the libs load first
        last_import = max((i for i, l in enumerate(lines) if l.strip().startswith('import ')), default=-1)
        block = ['', '//- v10 downgrade compatibility stubs (injected by SPP downgrader)'] + inject + ['']
        lines[last_import + 1:last_import + 1] = block
        self.log(f"  Shader compat: injected {len(inject)} stub(s)")
        return '\n'.join(lines).encode('utf-8')

    def _create_dataset(self, parent: h5py.Group, name: str, tree: Dict,
                        zf: zipfile.ZipFile, datasets_info: Dict):
        """Create an HDF5 dataset."""
        path = tree['path']
        self._prog_done = getattr(self, '_prog_done', 0) + 1
        _emit_progress(0.05 + 0.92 * (self._prog_done / getattr(self, '_prog_total', 1)),
                       "Writing  %s" % str(path).rsplit('/', 1)[-1])
        # Stale texture cache: the SVT cache was computed by the SOURCE version's engine
        # for the ORIGINAL document. We always transform the document on downgrade (retype
        # channelTypes, rebitmask channelType, rename members, drop layers...), so the
        # cached results no longer match. When the structure is unchanged (e.g. v12.1->v12)
        # the engine trusts the cache by hash and faults on the mismatch; for bigger jumps
        # it just shows blank until toggled. Either way the cache is invalid -> strip it so
        # Painter recomputes fresh on open (a freshly-made project has no computed cache).
        if str(path).startswith('texture-cache'):
            return
        output_path = path
        output_name = name
        if self.target_major and self.target_major <= 10:
            renamed = self.V10_DATASET_RENAMES.get(path)
            if renamed:
                output_path = renamed
                output_name = renamed.split('/')[-1]
                self.log(f"Dataset: {path} -> {output_path}")
            else:
                self.log(f"Dataset: {path}")
        else:
            self.log(f"Dataset: {path}")

        # Get dataset info
        ds_info = datasets_info.get(path, {})

        # Create safe filename
        safe_path = path.replace('/', '_').replace('\\', '_')
        data_file = f'data/{safe_path}.bin'
        decoded_file = f'decoded/{safe_path}.json'

        # Read raw data
        data = None
        raw_data = None
        data_modified = False
        if data_file in zf.namelist():
            raw_data = zf.read(data_file)

        # On ANY downgrade, restamp projectsettings.ini's version: the opening Painter must
        # never be told the project was saved by a NEWER build than itself -- it loads, then
        # crashes (e.g. v12.0 opening a 12.1-stamped project). The projectUUID strip is a
        # separate v10-and-below concern.
        if raw_data is not None and self.target_major and path == 'projectsettings.ini':
            if self.target_major <= 10:
                # Strip v11-only settings records (e.g. projectUUID) that v10's
                # ProjectManagement loader does not expect.
                raw_data = self._strip_projectsettings_fields(raw_data, ('projectUUID',))
            raw_data = self._patch_projectsettings_ini(raw_data, self.target_major, 0)
            data_modified = True

        # Patch embedded GLSL shaders for v10-and-below: inject compatibility stubs for
        # shader-helper functions that exist in v11/v12's libs but not the older runtime
        # (e.g. usesSSSScatteringColorChannel from lib-sss.glsl). Painter embeds the
        # shader it was saved with, so a v11/v12 shader otherwise fails to compile in v10
        # ("undefined variable") and renders purple. Only call-but-undefined functions
        # are stubbed; the rest of the project's real shader is untouched.
        if raw_data is not None and self.target_major and self.target_major <= 10 and path.endswith('.shader'):
            patched = self._patch_shader_compat(raw_data)
            if patched is not None:
                raw_data = patched
                data_modified = True

        # Check if we have decoded data that needs re-encoding
        if (ds_info.get('is_hbo') and decoded_file in zf.namelist()
                and (not self.preserve_source or raw_data is None)):
            try:
                decoded = json.loads(zf.read(decoded_file))

                # Re-encode HBO
                hbo_header = ds_info.get('hbo_header', {}) or {}
                if not hbo_header and raw_data is not None:
                    hbo_header = self._infer_hbo_header(raw_data) or {}
                format_version = hbo_header.get('format', 'v10')
                data_version = hbo_header.get('data_version', 20)
                if self.target_major and self.target_major <= 10 and format_version != 'v11_binary':
                    format_version = 'v10'
                    data_version = self._get_target_data_version(output_path, hbo_header)

                # Get the data section from decoded
                if 'data' in decoded:
                    if decoded.get('_lossy'):
                        self.log("  Skipping re-encode (lossy decode)")
                        data = None
                    elif self.target_major and self.target_major <= 10 and output_path in (
                        'paint/document.bin', 'paint/default_material.bin'
                    ):
                        try:
                            self._downgrade_paint_v11_to_v10(decoded['data'])
                            data_modified = True
                        except Exception as e:
                            self.log(f"  Warning: paint downgrade failed: {e}")
                    elif format_version == 'v11_binary':
                        # Only re-encode if binary payloads are present
                        if isinstance(decoded['data'], dict) and (
                            'objects' in decoded['data'] or 'text_entries' in decoded['data']
                        ):
                            self.log("  Re-encoding HBO (v11_binary)")
                            data = encode_to_hbo(
                                decoded['data'],
                                version='v11_binary',
                                data_version=data_version
                            )
                            data_modified = True
                    else:
                        self.log(f"  Re-encoding HBO ({format_version})")
                        data = encode_to_hbo(
                            decoded['data'],
                            version=format_version,
                            data_version=data_version
                        )
                        data_modified = True

            except Exception as e:
                self.log(f"  Warning: Failed to re-encode HBO: {e}")
                data = None

        # Downgrade registry source to the target format (v10 inline, or v11/v12
        # registry when the active profile's target_format is "registry").
        _target_fmt = PROFILE.data.get("target_format")
        _downgrade = bool(self.target_major and self.target_major <= 10) or _target_fmt == "registry"
        if data is None and raw_data is not None and _downgrade:
            if len(raw_data) >= 12 and struct.unpack('<I', raw_data[:4])[0] == BINARY_MAGIC:
                _, version_check, data_version = struct.unpack('<III', raw_data[:12])
                # version_check 1 = registry source (v11/v12); 0 = inline source (v8/v9/v10).
                if version_check in (0, 1):
                    try:
                        serializer = HBOSerializer(raw_data)
                        blacklist, max_data_version = self._get_downgrade_rules()
                        target_data_version = self._get_target_data_version(
                            output_path,
                            {'data_version': data_version},
                            max_data_version=max_data_version
                        )
                        overrides = None
                        if output_path in ('paint/document.bin', 'paint/default_material.bin'):
                            overrides = {
                                'DataSymmetry': {
                                    'enabled': 0,
                                    'count': 0,
                                    'matrix': [
                                        1.0, 0.0, 0.0, 0.0,
                                        0.0, 1.0, 0.0, 0.0,
                                        0.0, 0.0, 1.0, 0.0,
                                        0.0, 0.0, 0.0, 1.0,
                                    ],
                                },
                                'DataBrushStamp': {
                                    'resolutionOverride': [1024, 1024],
                                },
                                'DataBrushRibbon': {
                                    'changeFlags': 0,
                                    'tilingMode': 0,
                                    'omitEndsWhenClosed': 0,
                                    'alphaBlendingMode': 2,
                                    'perChannelBlending': [],
                                    '__omit__': ['overlappingMode'],
                                },
                                'DataActionFill': {
                                    'type': 0,
                                    'typeMetadata': "",
                                },
                            }
                        elif output_path == 'editor/posteffects.ini':
                            overrides = {
                                'DataPostEffectBase': {
                                    'enable': 1,
                                },
                            }
                        elif output_path == 'editor/colorprofile.ini':
                            overrides = {
                                'DataColorProfileParameters': {
                                    'enable': 1,
                                },
                            }
                        data = serializer.prune_and_reserialize(
                            blacklist,
                            target_data_version,
                            overrides=(None if _target_fmt == "registry" else overrides),
                            raster_replacements=getattr(self, "_raster_replacements", None),
                            raster_requests=getattr(self, "_raster_requests", None),
                            dataset_path=path,
                            target_label=os.environ.get("SPP_TARGET_VERSION") or (str(self.target_major) if self.target_major else None),
                        )
                        data_modified = True
                        raster_stats = getattr(serializer, "raster_stats", {}) or {}
                        if raster_stats.get("error"):
                            self.log(f"  Raster replacement failed: {raster_stats['error']}")
                        if any(raster_stats.get(k) for k in (
                            "mask_stacks_replaced",
                            "sources_replaced",
                            "content_actions_replaced",
                            "layers_replaced",
                            "full_stacks_replaced",
                        )):
                            parts = []
                            if raster_stats.get("mask_stacks_replaced"):
                                parts.append(f"masks={raster_stats['mask_stacks_replaced']}")
                            if raster_stats.get("sources_replaced"):
                                parts.append(f"sources={raster_stats['sources_replaced']}")
                            if raster_stats.get("content_actions_replaced"):
                                parts.append(f"actions={raster_stats['content_actions_replaced']}")
                            if raster_stats.get("layers_replaced"):
                                parts.append(f"layers={raster_stats['layers_replaced']}")
                            if raster_stats.get("full_stacks_replaced"):
                                parts.append(f"full_stacks={raster_stats['full_stacks_replaced']}")
                            self.log("  Raster replacements applied: " + ", ".join(parts))
                        self.log("  Transcoded HBO (v11 -> v10)")
                    except Exception as e:
                        self.log(f"  Warning: Failed to transcode HBO: {e}")

        # Fall back to raw binary data
        if data is None and raw_data is not None:
            data = raw_data

        if data is None:
            self.log(f"  Warning: No data for {path}")
            data = b''

        # Create dataset
        dtype = tree.get('dtype', 'uint8')
        try:
            np_dtype = np.dtype(dtype)
        except Exception:
            np_dtype = np.uint8

        arr = np.frombuffer(data, dtype=np_dtype)
        creation_props = ds_info.get('creation_props') or {}
        shape = creation_props.get('shape') or tree.get('shape')
        if shape:
            try:
                if int(np.prod(shape)) == arr.size:
                    arr = arr.reshape(shape)
                elif data_modified:
                    # When transcoding/downgrading, allow the new payload size to define shape.
                    shape = None
            except Exception:
                pass

        chunks = None
        if creation_props.get('chunks'):
            chunks = tuple(creation_props['chunks'])
        elif arr.dtype == np.uint8 and arr.ndim == 1 and arr.nbytes > 0x8000:
            chunk_len = min(arr.shape[0], 0x10000)
            if chunk_len > 0:
                chunks = (chunk_len,)

        maxshape = creation_props.get('maxshape')
        if shape is None:
            maxshape = None
        if maxshape:
            maxshape = tuple(maxshape)
            if shape and not any(m is None for m in maxshape) and list(maxshape) == list(shape):
                maxshape = None

        if chunks and shape:
            # Clamp chunk dims when maxshape would make them invalid
            if not maxshape or any(m is not None and c > m for c, m in zip(chunks, maxshape)):
                chunks = tuple(min(c, s) for c, s in zip(chunks, shape))

        ds_kwargs = {}
        if chunks:
            ds_kwargs['chunks'] = chunks
        if creation_props.get('compression') is not None:
            ds_kwargs['compression'] = creation_props.get('compression')
        if creation_props.get('compression_opts') is not None:
            ds_kwargs['compression_opts'] = creation_props.get('compression_opts')
        # Fast mode (plugin "Open" path -> throwaway temp file): re-deflating ~hundreds of
        # MB at the original gzip level dominates build time. Cap to level 1 -- same data,
        # Painter reads it identically, just a bigger temp file that's deleted after open.
        if self.fast and ds_kwargs.get('compression') in ('gzip', 'deflate'):
            ds_kwargs['compression_opts'] = 1
        if creation_props.get('shuffle') is not None:
            ds_kwargs['shuffle'] = creation_props.get('shuffle')
        if creation_props.get('fletcher32') is not None:
            ds_kwargs['fletcher32'] = creation_props.get('fletcher32')
        if creation_props.get('scaleoffset') is not None:
            ds_kwargs['scaleoffset'] = creation_props.get('scaleoffset')
        fill_defined = creation_props.get('fill_value_defined')
        if fill_defined is None:
            if creation_props.get('fillvalue') is not None:
                ds_kwargs['fillvalue'] = creation_props.get('fillvalue')
        elif fill_defined == 2:
            # User-defined fill value.
            if creation_props.get('fillvalue') is not None:
                ds_kwargs['fillvalue'] = creation_props.get('fillvalue')
        if creation_props.get('fill_time') is not None:
            fill_time_map = {0: 'alloc', 1: 'never', 2: 'ifset'}
            fill_time = fill_time_map.get(creation_props.get('fill_time'))
            if fill_time:
                ds_kwargs['fill_time'] = fill_time
        # Avoid writing mtime_new object header messages; match original files.
        if creation_props.get('track_times') is not None:
            ds_kwargs['track_times'] = False
        if maxshape:
            ds_kwargs['maxshape'] = maxshape

        dtype_order = creation_props.get('dtype_order')
        use_low_level = dtype_order is not None and (shape is not None)
        low_level_ok = False

        if use_low_level:
            try:
                from h5py import h5d, h5s, h5t, h5p

                # HDF5 dataspace
                if shape:
                    space_shape = tuple(int(s) for s in shape)
                else:
                    space_shape = arr.shape
                if maxshape:
                    maxshape_tuple = tuple(h5s.UNLIMITED if m is None else int(m) for m in maxshape)
                else:
                    maxshape_tuple = None
                space = h5s.create_simple(space_shape, maxshape_tuple)

                # HDF5 datatype with explicit byte order
                if np_dtype == np.dtype('uint8'):
                    if dtype_order == h5t.ORDER_BE:
                        dtype_id = h5t.STD_U8BE
                    else:
                        dtype_id = h5t.STD_U8LE
                elif np_dtype == np.dtype('uint64'):
                    if dtype_order == h5t.ORDER_BE:
                        dtype_id = h5t.STD_U64BE
                    else:
                        dtype_id = h5t.STD_U64LE
                else:
                    dtype_id = h5t.py_create(np_dtype)
                    try:
                        if dtype_order in (h5t.ORDER_BE, h5t.ORDER_LE):
                            dtype_id.set_order(dtype_order)
                    except Exception:
                        pass

                # Dataset creation property list
                dcpl = h5p.create(h5p.DATASET_CREATE)
                if chunks:
                    dcpl.set_chunk(chunks)
                if ds_kwargs.get('compression') in ('gzip', 'deflate'):
                    level = ds_kwargs.get('compression_opts')
                    if level is None:
                        level = 4
                    try:
                        dcpl.set_deflate(int(level))
                    except Exception:
                        pass
                if ds_kwargs.get('shuffle'):
                    dcpl.set_shuffle()
                if ds_kwargs.get('fletcher32'):
                    dcpl.set_fletcher32()
                if ds_kwargs.get('scaleoffset') is not None:
                    scaleoffset = ds_kwargs.get('scaleoffset')
                    try:
                        if isinstance(scaleoffset, (list, tuple)) and len(scaleoffset) == 2:
                            dcpl.set_scaleoffset(scaleoffset[1], scaleoffset[0])
                        else:
                            dcpl.set_scaleoffset(scaleoffset, 0)
                    except Exception:
                        pass
                if ds_kwargs.get('fillvalue') is not None:
                    try:
                        dcpl.set_fill_value(np.array(ds_kwargs['fillvalue'], dtype=np_dtype))
                    except Exception:
                        pass
                if creation_props.get('fill_time') is not None:
                    try:
                        dcpl.set_fill_time(int(creation_props.get('fill_time')))
                    except Exception:
                        pass
                if creation_props.get('alloc_time') is not None:
                    try:
                        dcpl.set_alloc_time(int(creation_props.get('alloc_time')))
                    except Exception:
                        pass
                if creation_props.get('track_times') is not None:
                    try:
                        dcpl.set_obj_track_times(False)
                    except Exception:
                        pass

                # Create dataset and write data
                dset_id = h5d.create(parent.id, output_name.encode('utf-8'), dtype_id, space, dcpl=dcpl)
                dset_id.write(h5s.ALL, h5s.ALL, arr)
                ds = parent[output_name]
                low_level_ok = True
            except Exception:
                low_level_ok = False

        if not low_level_ok:
            try:
                ds = parent.create_dataset(output_name, data=arr, **ds_kwargs)
            except TypeError:
                # Older h5py builds may not support all dataset kwargs.
                ds_kwargs.pop('track_times', None)
                ds_kwargs.pop('fill_time', None)
                ds = parent.create_dataset(output_name, data=arr, **ds_kwargs)

        # Set attributes from original info
        attrs = ds_info.get('attributes', {})
        attrs_dtypes = ds_info.get('attributes_dtypes', {})
        attrs_orders = ds_info.get('attributes_orders', {})
        for attr_name, value in attrs.items():
            try:
                dtype_hint = attrs_dtypes.get(attr_name)
                order_hint = attrs_orders.get(attr_name)
                if isinstance(value, list):
                    if order_hint == 1 and (not dtype_hint or np.dtype(dtype_hint) == np.dtype('uint8')):
                        try:
                            from h5py import h5a, h5s, h5t
                            arr = np.array(value, dtype=np.uint8)
                            space = h5s.create_simple(arr.shape)
                            attr_id = h5a.create(ds.id, attr_name.encode('utf-8'), h5t.STD_U8BE, space)
                            attr_id.write(arr)
                            continue
                        except Exception:
                            pass
                    if attr_name == 'm3_x64_128' and len(value) == 2:
                        if dtype_hint:
                            ds.attrs[attr_name] = np.array(value, dtype=np.dtype(dtype_hint))
                        else:
                            ds.attrs[attr_name] = np.array(value, dtype=np.uint64)
                    elif all(isinstance(v, int) and 0 <= v <= 255 for v in value):
                        if dtype_hint:
                            ds.attrs[attr_name] = np.array(value, dtype=np.dtype(dtype_hint))
                        else:
                            ds.attrs[attr_name] = np.array(value, dtype=np.uint8)
                    else:
                        if dtype_hint:
                            ds.attrs[attr_name] = np.array(value, dtype=np.dtype(dtype_hint))
                        else:
                            ds.attrs[attr_name] = np.array(value)
                else:
                    ds.attrs[attr_name] = value
            except Exception:
                continue

        # Compute m3_x64_128 only if it existed in the source or data was modified
        if 'm3_x64_128' in attrs and (data_modified or 'm3_x64_128' not in ds.attrs):
            try:
                h0, h1 = _m3_x64_128(bytes(data))
                dtype_hint = attrs_dtypes.get('m3_x64_128')
                if dtype_hint:
                    ds.attrs['m3_x64_128'] = np.array([h0, h1], dtype=np.dtype(dtype_hint))
                else:
                    ds.attrs['m3_x64_128'] = np.array([h0, h1], dtype=np.uint64)
            except Exception:
                pass

        # If we renamed the dataset, mirror the metadata under the v10 name
        if output_path != path:
            self.log(f"  Created as: {output_path}")

    def _strip_projectsettings_fields(self, data: bytes, names_to_remove: Tuple[str, ...]) -> bytes:
        """Remove named records from projectsettings.ini.

        Format: [u32 BE count][record]*, record = [u32 BE namelen][name UTF-16BE]
        [value]. Names are big-endian; the low byte of the namelen often coincides
        with a leading-'.' so we parse records by signature rather than by string
        search. Used to drop v11-only settings (projectUUID) on downgrade.
        """
        buf = bytes(data)
        n = len(buf)
        if n < 4:
            return buf

        def record_starts():
            starts = []
            p = 4
            while p + 4 <= n:
                L = struct.unpack('>I', buf[p:p + 4])[0]
                if 4 <= L <= 80 and L % 2 == 0 and p + 4 + L <= n:
                    name = buf[p + 4:p + 4 + L]
                    if all(name[k] == 0 for k in range(0, L, 2)) and all(32 <= name[k] < 127 for k in range(1, L, 2)):
                        starts.append((p, name.decode('utf-16-be')))
                        # skip past this record's value to the next prefix signature
                        q = p + 4 + L
                        while q + 4 <= n:
                            L2 = struct.unpack('>I', buf[q:q + 4])[0]
                            if 4 <= L2 <= 80 and L2 % 2 == 0 and q + 4 + L2 <= n:
                                nm2 = buf[q + 4:q + 4 + L2]
                                if all(nm2[k] == 0 for k in range(0, L2, 2)) and all(32 <= nm2[k] < 127 for k in range(1, L2, 2)):
                                    break
                            q += 1
                        p = q
                        continue
                p += 1
            return starts

        starts = record_starts()
        remove = set(names_to_remove)
        removed = 0
        out = bytearray(buf)
        # remove from the end so earlier offsets stay valid
        for i in range(len(starts) - 1, -1, -1):
            pos, nm = starts[i]
            if nm in remove:
                end = starts[i + 1][0] if i + 1 < len(starts) else len(out)
                del out[pos:end]
                removed += 1
        if removed:
            try:
                cnt = struct.unpack('>I', out[0:4])[0]
                out[0:4] = struct.pack('>I', max(0, cnt - removed))
            except Exception:
                pass
        return bytes(out)

    def _patch_projectsettings_ini(self, data: bytes, target_major: int, target_minor: int) -> bytes:
        data = bytearray(data)
        fields = [
            ('.versionAtCreation/major', target_major),
            ('.versionAtCreation/minor', target_minor),
            ('.versionAtLastSave/major', target_major),
            ('.versionAtLastSave/minor', target_minor),
        ]
        for field_name, target_value in fields:
            offset = self._find_version_offset(data, field_name)
            if offset >= 0 and offset < len(data):
                data[offset] = target_value
        return bytes(data)

    def _find_version_offset(self, data: bytes, field_name: str) -> int:
        encoded = field_name.encode('utf-16-le')
        idx = data.find(encoded)
        if idx < 0:
            return -1
        value_offset = idx + len(encoded) + 2
        return value_offset + 5

    def _get_downgrade_rules(self) -> Tuple[List[str], int]:
        # Prefer migration profile rules; use downgrade_config.yaml when no profile is loaded.
        if PROFILE.blacklist or PROFILE.target_max_data_version:
            return PROFILE.blacklist, (PROFILE.target_max_data_version or 81)
        if self._downgrade_config is None:
            try:
                config = load_config()
            except Exception:
                config = None
            self._downgrade_config = config
        blacklist = []
        max_data_version = 81
        if self._downgrade_config is not None:
            try:
                dr = self._downgrade_config.dict_removal
                if dr and dr.blacklist:
                    blacklist = list(dr.blacklist)
            except Exception:
                blacklist = []
            try:
                max_data_version = int(self._downgrade_config.target_version.max_data_version)
            except Exception:
                max_data_version = 81
        return blacklist, max_data_version

    def _get_target_data_version(self, dataset_path: str, hbo_header: Optional[Dict[str, Any]],
                                 max_data_version: Optional[int] = None) -> int:
        if max_data_version is None:
            _, max_data_version = self._get_downgrade_rules()
        # Apply the profile's per-dataset data-version map (inline and registry targets).
        if self.V10_DATA_VERSION_MAP:
            mapped = self.V10_DATA_VERSION_MAP.get(dataset_path)
            if mapped is not None:
                return mapped
        if hbo_header:
            data_version = hbo_header.get('data_version')
            if isinstance(data_version, int):
                if max_data_version is not None:
                    return min(data_version, max_data_version)
                return data_version
        return max_data_version if max_data_version is not None else 20

    def _get_type_name(self, obj: Any) -> Optional[str]:
        if not isinstance(obj, dict):
            return None
        for key in ('_type', 'type', 'type_name', 'typeName', '__type'):
            value = obj.get(key)
            if isinstance(value, str):
                return value
        return None

    def _iter_typed_objects(self, data: Any):
        stack = [(None, data)]
        while stack:
            inherited_type, obj = stack.pop()
            if isinstance(obj, dict):
                obj_type = self._get_type_name(obj) or inherited_type
                if obj_type:
                    yield obj, obj_type
                for key, value in obj.items():
                    if isinstance(value, dict):
                        child_type = self._get_type_name(value)
                        if not child_type and isinstance(key, str) and key[:1].isupper() and key.isalnum():
                            child_type = key
                        stack.append((child_type, value))
                    elif isinstance(value, list):
                        for item in value:
                            stack.append((None, item))
            elif isinstance(obj, list):
                for item in obj:
                    stack.append((None, item))

    def _iter_points(self, strokes: Any):
        if isinstance(strokes, list):
            for stroke in strokes:
                if isinstance(stroke, dict):
                    points = stroke.get('points')
                    if isinstance(points, list):
                        yield from points
        elif isinstance(strokes, dict):
            points = strokes.get('points')
            if isinstance(points, list):
                yield from points

    def _downgrade_paint_v11_to_v10(self, data: Any) -> None:
        if not isinstance(data, dict):
            return

        strokes = data.get('strokes3D')
        if strokes is None:
            return

        for point in self._iter_points(strokes):
            if not isinstance(point, dict):
                continue
            # v10 stores stroke size/opacity on the layer, not per point.
            point['size'] = 0.0
            point['opacity'] = 0.0
            point.pop('pressure', None)

        identity_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]

        for obj, obj_type in self._iter_typed_objects(data):
            if obj_type == 'DataSymmetry':
                obj['enabled'] = 0
                obj['count'] = 0
                obj['matrix'] = identity_matrix
            elif obj_type == 'DataBrushStamp':
                obj['resolutionOverride'] = [1024, 1024]
            elif obj_type == 'DataBrushRibbon':
                obj['changeFlags'] = 0
                use_count = obj.get('useCount')
                tiling_mode = 1 if isinstance(use_count, int) and use_count > 0 else 0
                obj['tilingMode'] = tiling_mode
                obj['omitEndsWhenClosed'] = 0
                obj['alphaBlendingMode'] = 2
                obj['perChannelBlending'] = []
                obj.pop('overlappingMode', None)
            elif obj_type == 'DataActionFill':
                obj['type'] = 0
                obj['typeMetadata'] = ""

            brush = obj.get('brush') if isinstance(obj, dict) else None
            brush_type = self._get_type_name(brush)
            if brush_type == 'DataBrushFill':
                obj.pop('sourceTransparent', None)


def _m3_x64_128(data: bytes, seed: int = 0xF13A0239) -> tuple[int, int]:
    """Compute Painter's m3_x64_128 dataset checksum (MurmurHash3 x64 128, seed 0xF13A0239)."""
    import mmh3
    h0, h1 = mmh3.hash64(data, seed, x64arch=True, signed=False)
    return h0 & 0xFFFFFFFFFFFFFFFF, h1 & 0xFFFFFFFFFFFFFFFF


def build_spp(uspp_path: str, output_path: str, verbose: bool = False,
              target_major: Optional[int] = None, preserve_source: bool = False) -> bool:
    """
    Convenience function to build SPP from USPP.

    Args:
        uspp_path: Path to input .uspp file
        output_path: Path for output .spp file
        verbose: Enable verbose logging

    Returns:
        True on success
    """
    # Large document trees stay live for the whole build; disable GC so cyclic collection
    # does not rescan them on every pass.
    import gc
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        builder = SPPBuilder(
            verbose=verbose,
            target_major=target_major,
            preserve_source=preserve_source,
        )
        return builder.build_from_uspp(uspp_path, output_path)
    finally:
        if gc_was_enabled:
            gc.enable()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Build SPP file from USPP archive',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('input_uspp', help='Input .uspp file')
    parser.add_argument('output_spp', help='Output .spp file')
    parser.add_argument('--target-version', type=int, default=None,
                       help='Target major version (e.g., 10). Defaults to source version.')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Verbose output')

    args = parser.parse_args()

    success = build_spp(args.input_uspp, args.output_spp, args.verbose, args.target_version)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
