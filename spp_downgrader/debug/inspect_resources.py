#!/usr/bin/env python3
"""Phase-0 spike helper: figure out how a bitmap referenced by `urlToBitmapRes` is
stored/resolved inside a .spp, so the builder can INJECT a baked texture that Painter
resolves (Phase 3 of the rasterize-on-downgrade plan).

READ-ONLY. Run on a .spp that contains at least one imported-bitmap fill layer:

    python inspect_resources.py "C:/path/to/project.spp"

It prints:
  1. the HDF5 group/dataset tree (shape, dtype, compression, chunks, attrs) so we can
     see where resource pixel data lives (a resources/shelf/mesh group vs texture/*),
  2. every `urlToBitmapRes` / `urlToSbsRes` string found in HBO datasets, i.e. the URL
     scheme a source uses to point at a resource,
  3. the datasets whose path or name matches those URLs -> the embed target + its exact
     creation props (what an injected dataset must replicate: chunks/compression/dtype,
     and whether an `m3_x64_128` content hash attr is present).

Nothing is modified. Use the output to fill in the Phase-0 exit criterion.
"""
import os
import re
import sys

try:
    import h5py
except Exception as e:  # pragma: no cover - environment dependent
    print(f"h5py is required: {e}", file=sys.stderr)
    sys.exit(2)


_URL_KEYS = (b"urlToBitmapRes", b"urlToSbsRes")
# Printable-string scanner (UTF-8-ish and UTF-16LE), long enough to catch resource URLs.
_ASCII = re.compile(rb"[\x20-\x7e]{6,}")
_UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")


def _fmt_ds(ds):
    props = []
    props.append(f"shape={tuple(ds.shape)}")
    props.append(f"dtype={ds.dtype}")
    if ds.chunks:
        props.append(f"chunks={ds.chunks}")
    if ds.compression:
        props.append(f"compression={ds.compression}:{ds.compression_opts}")
    attrs = list(ds.attrs.keys())
    if attrs:
        props.append(f"attrs={attrs}")
    return "  ".join(props)


def dump_tree(f):
    print("=" * 70)
    print("HDF5 TREE")
    print("=" * 70)

    def visit(name, obj):
        depth = name.count("/")
        indent = "  " * depth
        base = name.rsplit("/", 1)[-1]
        if isinstance(obj, h5py.Group):
            print(f"{indent}[G] {base or '/'}")
        else:
            print(f"{indent}[D] {base}  ({_fmt_ds(obj)})")

    f.visititems(visit)


def _strings(buf):
    out = set()
    for m in _ASCII.finditer(buf):
        out.add(m.group(0).decode("latin1"))
    for m in _UTF16.finditer(buf):
        out.add(m.group(0).decode("utf-16-le", "replace"))
    return out


def scan_resource_urls(f):
    print("\n" + "=" * 70)
    print("RESOURCE URL STRINGS (near urlToBitmapRes / urlToSbsRes)")
    print("=" * 70)
    urls = set()

    def visit(name, obj):
        if not isinstance(obj, h5py.Dataset):
            return
        try:
            raw = bytes(obj[()])
        except Exception:
            return
        if not any(k in raw for k in _URL_KEYS):
            return
        # Print strings from this dataset that look like resource URLs/paths.
        found = [s for s in _strings(raw)
                 if ("://" in s) or s.lower().endswith((".png", ".tga", ".exr", ".jpg", ".sbsar"))
                 or "resource" in s.lower() or "shelf" in s.lower()]
        if found:
            print(f"\n{name}:")
            for s in sorted(found):
                print(f"    {s!r}")
                urls.add(s)

    f.visititems(visit)
    if not urls:
        print("\n(no obvious URL strings found -- the source may be a bare bitmap fill; "
              "try a project that imports an external image as a fill/mask source)")
    return urls


def match_datasets(f, urls):
    print("\n" + "=" * 70)
    print("CANDIDATE EMBED TARGETS (datasets whose path matches a resource URL)")
    print("=" * 70)
    # Extract the trailing id/guid/name token of each URL and look for datasets containing it.
    tokens = set()
    for u in urls:
        for tok in re.split(r"[\\/:?#]+", u):
            tok = tok.strip()
            if len(tok) >= 6:
                tokens.add(tok)
                tokens.add(os.path.splitext(tok)[0])

    hits = []

    def visit(name, obj):
        if not isinstance(obj, h5py.Dataset):
            return
        if any(tok and tok in name for tok in tokens):
            hits.append((name, obj))

    f.visititems(visit)
    if not hits:
        print("\n(no dataset path matched a URL token -- resources may live under a group "
              "named by GUID; inspect the tree above for resources/shelf/mesh groups)")
    for name, obj in hits:
        print(f"\n{name}:\n    {_fmt_ds(obj)}")
        print(f"    has m3_x64_128 hash attr: {'m3_x64_128' in obj.attrs}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"not found: {path}", file=sys.stderr)
        return 2
    with h5py.File(path, "r") as f:
        dump_tree(f)
        urls = scan_resource_urls(f)
        match_datasets(f, urls)
    print("\nDone. For Phase 0, confirm: (a) do bitmap resources live as HDF5 datasets in "
          "this file, or as external shelf paths? (b) what is the URL->dataset mapping? "
          "(c) which creation props + attrs must an injected dataset replicate?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
