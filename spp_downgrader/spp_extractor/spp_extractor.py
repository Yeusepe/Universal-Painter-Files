#!/usr/bin/env python3
"""
SPP Extractor - Extract and decode Substance Painter project files.

This is a Python implementation of the SPP Extractor utility that allows
testing the HBO decoding logic without needing to run Substance Painter.

The script:
1. Reads .spp files (HDF5 containers)
2. Decodes HBO binary streams (v10 tagged / v11 registry-based)
3. Extracts all data to a USPP (Universal SPP) format
4. Supports full traversal, version detection, and human-readable output

Usage:
    python spp_extractor.py <input.spp> [options]

Options:
    --output, -o          Output directory or USPP file path
    --format, -f          Output format: uspp (zip), json, raw
    --decode-hbo          Decode HBO streams to JSON (default: raw bytes)
    --verbose, -v         Verbose output
    --dry-run             Preview extraction without writing files
    --help, -h            Show this help message

Examples:
    python spp_extractor.py Textures.spp
    python spp_extractor.py Textures.spp -o extracted/
    python spp_extractor.py Textures.spp --format json --decode-hbo
    python spp_extractor.py Textures.spp --dry-run -v
"""

import sys
import os
import json
import struct
import argparse
import zipfile
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path


def emit_progress(frac, msg):
    """Emit a parseable progress line for the plugin UI (only when USPP_PROGRESS is set,
    so the CLI stays clean). frac<0 means indeterminate/busy."""
    if os.environ.get("USPP_PROGRESS"):
        try:
            sys.stdout.write("__USPP_PROGRESS__\t%.4f\t%s\n" % (frac, msg))
            sys.stdout.flush()
        except Exception:
            pass

# Add local lib directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    import h5py
    import numpy as np
except ImportError:
    print("Error: h5py and numpy are required. Install with: pip install h5py numpy")
    sys.exit(1)

from spp_ext_models import (
    BINARY_MAGIC_V11,
    HBOHeader, HBOV11BinaryHeader, ExtractedDataset, ExtractedGroup, SPPExtraction,
)
from spp_ext_decoder import HBODecoder


# ============================================================================
# HBO Decoder (Standalone Implementation)
# ============================================================================



# ============================================================================
# SPP Extractor Class
# ============================================================================

class SPPExtractor:
    """Extract data from Substance Painter .spp files."""

    def __init__(self, spp_path: str, verbose: bool = False):
        self.spp_path = Path(spp_path)
        self.verbose = verbose
        self.errors: List[str] = []

        if not self.spp_path.exists():
            raise FileNotFoundError(f"File not found: {spp_path}")

    def log(self, message: str):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(f"  {message}")

    def extract(self, decode_hbo: bool = False, skip_texture_cache: bool = False) -> SPPExtraction:
        """
        Extract all data from the SPP file.

        Args:
            decode_hbo: If True, attempt to decode HBO streams to JSON
            skip_texture_cache: If True, don't read the SVT texture-cache datasets.
                They are the bulk of a big project's bytes and are stripped on every
                rebuild anyway (Painter recomputes them on open), so packing them into
                the .uspp is pure wasted I/O/CPU/size.

        Returns:
            SPPExtraction containing all extracted data
        """
        groups: Dict[str, ExtractedGroup] = {}
        datasets: Dict[str, ExtractedDataset] = {}
        hdf5_structure: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {}

        print(f"Opening: {self.spp_path}")

        with h5py.File(str(self.spp_path), 'r') as f:
            # Collect file-level metadata
            metadata['hdf5_version'] = h5py.version.hdf5_version
            metadata['h5py_version'] = h5py.version.version

            # Detect file version from projectsettings.ini if present
            if 'projectsettings.ini' in f:
                try:
                    settings_data = bytes(f['projectsettings.ini'][()])
                    version_info = _parse_project_settings(settings_data)
                    if version_info:
                        metadata['version_info'] = version_info
                        # Use 'is not None' (not 'or'): a legit minor of 0 must NOT fall
                        # back to versionAtCreation -- that mislabels e.g. v12.0 as v12.1
                        # and applies the wrong version's transforms.
                        major = version_info.get('.versionAtLastSave/major')
                        if major is None:
                            major = version_info.get('.versionAtCreation/major')
                        minor = version_info.get('.versionAtLastSave/minor')
                        if minor is None:
                            minor = version_info.get('.versionAtCreation/minor')
                        if major is not None:
                            metadata['painter_version'] = {
                                'major': int(major),
                                'minor': int(minor) if minor is not None else 0
                            }
                except Exception:
                    pass

            # Recursively traverse the file
            hdf5_structure = self._build_structure_tree(f)

            # Extract all items
            def visitor(name: str, obj):
                if isinstance(obj, h5py.Group):
                    self._extract_group(name, obj, groups)
                elif isinstance(obj, h5py.Dataset):
                    if skip_texture_cache and name.startswith('texture-cache'):
                        return  # stripped on rebuild anyway -- don't read it into RAM
                    self._extract_dataset(name, obj, datasets, decode_hbo)

            # Handle root attributes
            root_attrs, root_attr_dtypes, root_attr_orders = self._extract_attributes_with_dtypes(f)
            if root_attrs:
                metadata['root_attributes'] = root_attrs
            if root_attr_dtypes:
                metadata['root_attributes_dtypes'] = root_attr_dtypes
            if root_attr_orders:
                metadata['root_attributes_orders'] = root_attr_orders

            # Visit all items
            f.visititems(visitor)

        # Build extraction result
        extraction = SPPExtraction(
            source_file=str(self.spp_path),
            extraction_time=datetime.now().isoformat(),
            hdf5_structure=hdf5_structure,
            groups=groups,
            datasets=datasets,
            metadata=metadata,
            errors=self.errors
        )

        return extraction

    def _build_structure_tree(self, group: h5py.Group, path: str = "") -> Dict[str, Any]:
        """Build a tree representation of the HDF5 structure."""
        tree = {
            'type': 'group',
            'path': path or '/',
            'attributes': list(group.attrs.keys()),
            'children': {}
        }

        for name, item in group.items():
            child_path = f"{path}/{name}" if path else name

            if isinstance(item, h5py.Group):
                tree['children'][name] = self._build_structure_tree(item, child_path)
            elif isinstance(item, h5py.Dataset):
                tree['children'][name] = {
                    'type': 'dataset',
                    'path': child_path,
                    'shape': list(item.shape),
                    'dtype': str(item.dtype),
                    'size': item.size,
                    'attributes': list(item.attrs.keys())
                }

        return tree

    def _extract_attributes(self, obj) -> Dict[str, Any]:
        """Extract attributes from an HDF5 object."""
        attrs = {}
        for key in obj.attrs.keys():
            value = obj.attrs[key]
            # Convert numpy types to Python types for JSON serialization
            if hasattr(value, 'tolist'):
                value = value.tolist()
            elif hasattr(value, 'item'):
                value = value.item()
            elif isinstance(value, bytes):
                try:
                    value = value.decode('utf-8')
                except:
                    value = value.hex()
            attrs[key] = value
        return attrs

    def _extract_attributes_with_dtypes(self, obj) -> tuple[Dict[str, Any], Dict[str, str], Dict[str, int]]:
        """Extract attributes and capture their numpy dtype strings and byte orders."""
        attrs: Dict[str, Any] = {}
        dtypes: Dict[str, str] = {}
        orders: Dict[str, int] = {}
        for key in obj.attrs.keys():
            value = obj.attrs[key]
            if hasattr(value, "dtype"):
                try:
                    dtypes[key] = str(value.dtype)
                except Exception:
                    pass
            try:
                attr_id = obj.attrs.get_id(key)
                orders[key] = int(attr_id.get_type().get_order())
            except Exception:
                pass
            # Convert numpy types to Python types for JSON serialization
            if hasattr(value, 'tolist'):
                value = value.tolist()
            elif hasattr(value, 'item'):
                value = value.item()
            elif isinstance(value, bytes):
                try:
                    value = value.decode('utf-8')
                except Exception:
                    value = value.hex()
            attrs[key] = value
        return attrs, dtypes, orders

    def _extract_group(self, path: str, obj: h5py.Group, groups: Dict[str, ExtractedGroup]):
        """Extract a group and its metadata."""
        self.log(f"Group: {path}")

        creation_props = {}
        try:
            plist = obj.id.get_create_plist()
            try:
                creation_props['track_times'] = bool(plist.get_obj_track_times())
            except Exception:
                pass
            try:
                creation_props['link_creation_order'] = plist.get_link_creation_order()
            except Exception:
                pass
            try:
                creation_props['attr_creation_order'] = plist.get_attr_creation_order()
            except Exception:
                pass
            try:
                creation_props['attr_phase_change'] = plist.get_attr_phase_change()
            except Exception:
                pass
        except Exception:
            creation_props = {}

        if creation_props:
            creation_props = {k: self._json_safe(v) for k, v in creation_props.items()}

        groups[path] = ExtractedGroup(
            path=path,
            creation_props=creation_props,
            attributes=self._extract_attributes(obj),
            datasets=[name for name, item in obj.items() if isinstance(item, h5py.Dataset)],
            subgroups=[name for name, item in obj.items() if isinstance(item, h5py.Group)]
        )

    def _extract_dataset(self, path: str, obj: h5py.Dataset,
                         datasets: Dict[str, ExtractedDataset],
                         decode_hbo: bool):
        """Extract a dataset and its data."""
        self.log(f"Dataset: {path} ({obj.dtype}, {obj.size} elements)")

        # Read raw data
        try:
            raw_data = bytes(obj[()])
        except Exception as e:
            self.errors.append(f"Failed to read {path}: {e}")
            raw_data = b''

        # Check if this is an HBO stream
        hbo_header = None
        is_hbo = False
        decoded = None

        if len(raw_data) >= 4:
            magic = struct.unpack('<I', raw_data[:4])[0]
            if magic == BINARY_MAGIC_V11:
                obj_count = 0
                if len(raw_data) >= 8:
                    obj_count = struct.unpack('<I', raw_data[4:8])[0]
                hbo_header = HBOV11BinaryHeader(magic=magic, object_count=obj_count)
                is_hbo = True
                self.log("  -> HBO stream (v11_binary)")
            elif len(raw_data) >= 12:
                hbo_header = HBOHeader.from_bytes(raw_data)
                if hbo_header:
                    is_hbo = True
                    self.log(f"  -> HBO stream ({hbo_header.format_version}, data_ver={hbo_header.data_version})")

                    # Optionally decode HBO
                    if decode_hbo:
                        try:
                            decoder = HBODecoder(raw_data)
                            decoded = decoder.decode()
                            # Mark as lossy unless fully decoded
                            decoded['_lossy'] = True
                            self.log("  -> Decoded successfully")
                        except Exception as e:
                            self.errors.append(f"Failed to decode HBO in {path}: {e}")
                            decoded = {'error': str(e)}

        plist = None
        try:
            plist = obj.id.get_create_plist()
        except Exception:
            plist = None

        creation_props = {
            'shape': list(obj.shape),
            'maxshape': [None if d is None else int(d) for d in obj.maxshape],
            'chunks': list(obj.chunks) if obj.chunks else None,
            'compression': obj.compression,
            'compression_opts': self._json_safe(obj.compression_opts),
            'shuffle': obj.shuffle,
            'fletcher32': obj.fletcher32,
            'scaleoffset': self._json_safe(obj.scaleoffset),
            'fillvalue': self._json_safe(obj.fillvalue),
        }
        if plist is not None:
            try:
                creation_props['alloc_time'] = plist.get_alloc_time()
            except Exception:
                pass
            try:
                creation_props['fill_time'] = plist.get_fill_time()
            except Exception:
                pass
            try:
                creation_props['fill_value_defined'] = int(plist.fill_value_defined())
            except Exception:
                pass
            try:
                creation_props['track_times'] = bool(plist.get_obj_track_times())
            except Exception:
                pass
            try:
                creation_props['attr_creation_order'] = plist.get_attr_creation_order()
            except Exception:
                pass
            try:
                creation_props['attr_phase_change'] = self._json_safe(plist.get_attr_phase_change())
            except Exception:
                pass
            try:
                creation_props['layout'] = plist.get_layout()
            except Exception:
                pass
        try:
            creation_props['dtype_order'] = int(obj.id.get_type().get_order())
        except Exception:
            pass

        attrs, attrs_dtypes, attrs_orders = self._extract_attributes_with_dtypes(obj)

        datasets[path] = ExtractedDataset(
            path=path,
            size=len(raw_data),
            dtype=str(obj.dtype),
            is_hbo=is_hbo,
            hbo_header=hbo_header,
            attributes=attrs,
            attributes_dtypes=attrs_dtypes,
            attributes_orders=attrs_orders,
            data=raw_data,
            decoded=decoded,
            creation_props=creation_props
        )

    def _json_safe(self, value):
        """Convert h5py/numpy values to JSON-serializable types."""
        if isinstance(value, tuple):
            return [self._json_safe(v) for v in value]
        if isinstance(value, list):
            return [self._json_safe(v) for v in value]
        if hasattr(value, 'tolist'):
            return value.tolist()
        if hasattr(value, 'item'):
            try:
                return value.item()
            except Exception:
                return value
        return value

    def save_as_uspp(self, extraction: SPPExtraction, output_path: str,
                     extra_manifest: dict = None, raster_capture_dir: str = None,
                     raster_budget_bytes: int = None):
        """Save extraction result as a USPP (ZIP) archive. extra_manifest merges extra
        top-level fields into manifest.json (e.g. created_version, supported_versions)."""
        output_path = Path(output_path)

        print(f"Saving to: {output_path}")

        # Level 1: the payload is mostly already-compressed binary, so level-6 deflate
        # burns CPU for little gain. Level 1 is several times faster at near-identical size.
        with zipfile.ZipFile(str(output_path), 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            # Write metadata
            manifest = {
                'version': '1.0',
                'source_file': extraction.source_file,
                'extraction_time': extraction.extraction_time,
                'dataset_count': len(extraction.datasets),
                'group_count': len(extraction.groups),
                'errors': extraction.errors
            }
            if extra_manifest:
                manifest.update(extra_manifest)
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))

            # Write HDF5 structure
            zf.writestr('structure.json', json.dumps(extraction.hdf5_structure, indent=2))

            # Write metadata
            zf.writestr('metadata.json', json.dumps(extraction.metadata, indent=2))

            # Write group info
            groups_info = {path: {
                'path': g.path,
                'attributes': g.attributes,
                'creation_props': g.creation_props,
                'datasets': g.datasets,
                'subgroups': g.subgroups
            } for path, g in extraction.groups.items()}
            zf.writestr('groups.json', json.dumps(groups_info, indent=2))

            # Write dataset info and data
            datasets_info = {}
            _n = max(1, len(extraction.datasets))
            for _i, (path, ds) in enumerate(extraction.datasets.items()):
                emit_progress(0.10 + 0.88 * (_i / _n), "Compressing  %s" % path.rsplit('/', 1)[-1])
                # Create safe filename
                safe_path = path.replace('/', '_').replace('\\', '_')

                datasets_info[path] = {
                    'path': ds.path,
                    'size': ds.size,
                    'dtype': ds.dtype,
                    'is_hbo': ds.is_hbo,
                    'hbo_header': ds.hbo_header.to_dict() if hasattr(ds.hbo_header, 'to_dict') else ds.hbo_header,
                    'attributes': ds.attributes,
                    'attributes_dtypes': ds.attributes_dtypes,
                    'attributes_orders': ds.attributes_orders,
                    'creation_props': ds.creation_props,
                    'data_file': f'data/{safe_path}.bin',
                    'decoded_file': f'decoded/{safe_path}.json' if ds.decoded else None
                }

                # Write raw data
                zf.writestr(f'data/{safe_path}.bin', ds.data)

                # Write decoded data if available
                if ds.decoded:
                    zf.writestr(f'decoded/{safe_path}.json',
                               json.dumps(ds.decoded, indent=2, default=str))

            zf.writestr('datasets.json', json.dumps(datasets_info, indent=2))
            try:
                from lib.raster_manifest import add_capture_dir_to_zip
                kwargs = {}
                if raster_budget_bytes is not None:
                    kwargs["budget_bytes"] = int(raster_budget_bytes)
                add_capture_dir_to_zip(zf, raster_capture_dir, **kwargs)
            except Exception as e:
                zf.writestr('raster/manifest.json', json.dumps({
                    "version": 1,
                    "requests": [],
                    "assets": [],
                    "warnings": [f"could not attach raster capture: {e}"],
                }, indent=2))

        print(f"  Created USPP archive: {output_path}")
        print(f"  Datasets: {len(extraction.datasets)}")
        print(f"  Groups: {len(extraction.groups)}")
        if extraction.errors:
            print(f"  Errors: {len(extraction.errors)}")

    def save_as_json(self, extraction: SPPExtraction, output_path: str):
        """Save extraction result as JSON (structure only, no binary data)."""
        output_path = Path(output_path)

        result = {
            'source_file': extraction.source_file,
            'extraction_time': extraction.extraction_time,
            'metadata': extraction.metadata,
            'hdf5_structure': extraction.hdf5_structure,
            'groups': {path: {
                'path': g.path,
                'attributes': g.attributes,
                'creation_props': g.creation_props,
                'datasets': g.datasets,
                'subgroups': g.subgroups
            } for path, g in extraction.groups.items()},
            'datasets': {path: {
                'path': ds.path,
                'size': ds.size,
                'dtype': ds.dtype,
                'is_hbo': ds.is_hbo,
                'hbo_header': ds.hbo_header.to_dict() if ds.hbo_header else None,
                'attributes': ds.attributes,
                'attributes_dtypes': ds.attributes_dtypes,
                'attributes_orders': ds.attributes_orders,
                'decoded': ds.decoded
            } for path, ds in extraction.datasets.items()},
            'errors': extraction.errors
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, default=str)

        print(f"Saved JSON to: {output_path}")

    def save_raw(self, extraction: SPPExtraction, output_dir: str):
        """Save extraction as raw files in a directory."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"Saving to directory: {output_dir}")

        # Create directory structure matching HDF5
        for path, group in extraction.groups.items():
            group_dir = output_dir / path
            group_dir.mkdir(parents=True, exist_ok=True)

            # Write group attributes
            if group.attributes:
                attrs_file = group_dir / '_attributes.json'
                with open(attrs_file, 'w', encoding='utf-8') as f:
                    json.dump(group.attributes, f, indent=2)

        # Write datasets
        for path, ds in extraction.datasets.items():
            ds_path = output_dir / path
            ds_path.parent.mkdir(parents=True, exist_ok=True)

            # Write raw data
            with open(ds_path, 'wb') as f:
                f.write(ds.data)

            # Write metadata
            meta_path = Path(str(ds_path) + '.meta.json')
            meta = {
                'size': ds.size,
                'dtype': ds.dtype,
                'is_hbo': ds.is_hbo,
                'hbo_header': ds.hbo_header.to_dict() if ds.hbo_header else None,
                'attributes': ds.attributes
            }
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)

            # Write decoded data
            if ds.decoded:
                decoded_path = Path(str(ds_path) + '.decoded.json')
                with open(decoded_path, 'w', encoding='utf-8') as f:
                    json.dump(ds.decoded, f, indent=2, default=str)

        # Write manifest
        manifest = {
            'source_file': extraction.source_file,
            'extraction_time': extraction.extraction_time,
            'metadata': extraction.metadata,
            'errors': extraction.errors
        }
        with open(output_dir / 'manifest.json', 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)

        print(f"  Datasets: {len(extraction.datasets)}")
        print(f"  Groups: {len(extraction.groups)}")


# ============================================================================
# Analysis Functions
# ============================================================================

def analyze_spp(spp_path: str, verbose: bool = False) -> Dict[str, Any]:
    """
    Analyze an SPP file and return a summary.

    This is useful for quick inspection without full extraction.
    """
    analysis = {
        'file': spp_path,
        'size_mb': os.path.getsize(spp_path) / (1024 * 1024),
        'structure': {},
        'hbo_datasets': [],
        'version_info': {}
    }

    with h5py.File(spp_path, 'r') as f:
        # Check for projectsettings.ini
        if 'projectsettings.ini' in f:
            settings_data = bytes(f['projectsettings.ini'][()])
            analysis['version_info'] = _parse_project_settings(settings_data)

        # Analyze all datasets
        def visitor(name: str, obj):
            if isinstance(obj, h5py.Dataset):
                if obj.dtype == np.uint8:
                    data = bytes(obj[()])
                    if len(data) >= 4:
                        magic = struct.unpack('<I', data[:4])[0]
                        if magic == BINARY_MAGIC_V11:
                            analysis['hbo_datasets'].append({
                                'path': name,
                                'size': len(data),
                                'format': 'v11_binary',
                                'data_version': None
                            })
                        elif len(data) >= 12:
                            header = HBOHeader.from_bytes(data)
                            if header:
                                analysis['hbo_datasets'].append({
                                    'path': name,
                                    'size': len(data),
                                    'format': header.format_version,
                                    'data_version': header.data_version
                                })

        f.visititems(visitor)

    return analysis


def _parse_project_settings(data: bytes) -> Dict[str, Any]:
    """Parse version info from projectsettings.ini dataset."""
    result = {}

    # Look for version fields in UTF-16 encoded data
    fields = [
        '.versionAtCreation/major',
        '.versionAtCreation/minor',
        '.versionAtLastSave/major',
        '.versionAtLastSave/minor',
    ]

    for field_name in fields:
        encoded = field_name.encode('utf-16-le')
        idx = data.find(encoded)
        if idx >= 0:
            value_offset = idx + len(encoded) + 2 + 5
            if value_offset < len(data):
                result[field_name] = data[value_offset]

    return result


# ============================================================================
# CLI Interface
# ============================================================================

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Extract data from Substance Painter .spp files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s Textures.spp
  %(prog)s Textures.spp -o extracted/
  %(prog)s Textures.spp --format json --decode-hbo
  %(prog)s Textures.spp --analyze
  %(prog)s Textures.spp --dry-run -v
        '''
    )

    parser.add_argument('input_file', help='Input .spp file')
    parser.add_argument('-o', '--output', dest='output_path',
                       help='Output path (directory for raw, file for uspp/json)')
    parser.add_argument('-f', '--format', dest='output_format',
                       choices=['uspp', 'json', 'raw'],
                       default='uspp',
                       help='Output format (default: uspp)')
    parser.add_argument('--decode-hbo', action='store_true',
                       help='Decode HBO streams to JSON')
    parser.add_argument('--analyze', action='store_true',
                       help='Only analyze file, do not extract')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview extraction without writing files')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Verbose output')

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Check input file exists
    if not os.path.exists(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found")
        sys.exit(1)

    # Analysis mode
    if args.analyze:
        print(f"\n{'=' * 60}")
        print(f"Analyzing: {args.input_file}")
        print(f"{'=' * 60}\n")

        analysis = analyze_spp(args.input_file, verbose=args.verbose)

        print(f"File size: {analysis['size_mb']:.2f} MB")
        print(f"HBO datasets: {len(analysis['hbo_datasets'])}")

        if analysis['version_info']:
            print("\nVersion info:")
            for key, value in analysis['version_info'].items():
                print(f"  {key}: {value}")

        if args.verbose:
            print("\nHBO datasets:")
            for ds in analysis['hbo_datasets']:
                print(f"  {ds['path']}: {ds['format']}, data_ver={ds['data_version']}, size={ds['size']}")

        sys.exit(0)

    # Extraction mode
    print(f"\n{'=' * 60}")
    print("SPP Extractor")
    print(f"{'=' * 60}\n")

    extractor = SPPExtractor(args.input_file, verbose=args.verbose)

    # Perform extraction
    extraction = extractor.extract(decode_hbo=args.decode_hbo)

    # Print summary
    print("\nExtraction summary:")
    print(f"  Datasets: {len(extraction.datasets)}")
    print(f"  Groups: {len(extraction.groups)}")
    print(f"  HBO streams: {sum(1 for ds in extraction.datasets.values() if ds.is_hbo)}")

    if extraction.errors:
        print(f"  Errors: {len(extraction.errors)}")
        for err in extraction.errors[:5]:
            print(f"    - {err}")

    # Handle dry run
    if args.dry_run:
        print("\n[DRY RUN] No files written")
        sys.exit(0)

    # Determine output path
    if args.output_path:
        output_path = args.output_path
    else:
        base_name = Path(args.input_file).stem
        if args.output_format == 'uspp':
            output_path = f"{base_name}.uspp"
        elif args.output_format == 'json':
            output_path = f"{base_name}_extraction.json"
        else:
            output_path = f"{base_name}_extracted"

    # Save output
    print()
    if args.output_format == 'uspp':
        extractor.save_as_uspp(extraction, output_path)
    elif args.output_format == 'json':
        extractor.save_as_json(extraction, output_path)
    else:
        extractor.save_raw(extraction, output_path)

    print(f"\n{'=' * 60}")
    print("Extraction complete!")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
