#!/usr/bin/env python3
"""Generate a migration-profile skeleton for a new version step.

The recurring manual labor in adding a downgrade step is rediscovering the
target version's object schema (which members each type has, in what order) and
its per-dataset data_version numbers. Both are mechanical and live in real files,
so this derives them automatically from one or more target-version `.spp`
files and writes:

  spp_extractor/lib/v<to>_schema.json      target type -> [ordered members]
  profiles/v<from>_to_v<to>.json           profile skeleton wired to that schema

What it CANNOT infer (needs compatibility-testing judgment, left as TODO in the file):
  - type_rename / baking_*_rename  (a type or id was renamed, fields identical)
  - blacklist                      (top-level dict types the target rejects by name;
                                    the "Invalid dict type : X" error names them)
  - baking_schema                  (reuse v10's or extract from a native corpus)

Only inline-format targets (v10 and, presumably, older) are supported -- that is
the format this parser reads. The source format's binary codec is separate code.

Usage:
  python debug/build_profile.py --from 11 --to 10 native_v10_a.spp native_v10_b.spp
"""
import h5py, struct, sys, json, os, glob, collections, argparse

PRIM = {1:4,2:8,3:12,4:16,5:4,6:8,7:12,8:16,9:8,0x0A:1,0x0B:4,0x0C:8,0x0D:36,0x0E:64,0x0F:8,0x15:8,16:32,21:8}
MAGIC = 0x1B7C2FDD

members = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
order_samples = collections.defaultdict(list)


def parse(d):
    p = [12]
    def u8():
        v = d[p[0]]; p[0] += 1; return v
    def u16():
        v = struct.unpack_from('<H', d, p[0])[0]; p[0] += 2; return v
    def u32():
        v = struct.unpack_from('<I', d, p[0])[0]; p[0] += 4; return v
    def obj():
        u8(); u32(); n = u32(); name = d[p[0]:p[0]+n].decode('utf-8', 'replace'); p[0] += n; fc = u16()
        names = []
        for _ in range(fc):
            fl = u32(); fn = d[p[0]:p[0]+fl].decode('utf-8', 'replace'); p[0] += fl
            tag = d[p[0]]
            names.append(fn); members[name][fn][tag] += 1
            val()
        if names:
            order_samples[name].append(tuple(names))
    def val():
        tag = u8()
        if tag == 0x10:
            l = u32(); p[0] += l
        elif tag in (0x12, 0x14):
            if d[p[0]] == 0xFF: p[0] += 1
            else: obj()
        elif tag in (0x13, 0x11):
            u32(); c = u32()
            for _ in range(c):
                e = u8()
                if e in (0x12, 0x14):
                    if d[p[0]] == 0xFF: p[0] += 1
                    else: obj()
                elif e == 0x10:
                    l = u32(); p[0] += l
                else:
                    p[0] += PRIM[e]
        elif tag == 0x00:
            pass
        else:
            p[0] += PRIM[tag]
    assert u8() == 0x12
    obj()


def canonical_order(samples):
    pos = collections.defaultdict(list)
    for s in samples:
        for i, n in enumerate(s):
            pos[n].append(i)
    return sorted(pos, key=lambda n: sorted(pos[n])[len(pos[n]) // 2])


def process(path, version_map):
    try:
        f = h5py.File(path, 'r')
    except Exception:
        return 0
    n = 0
    def vi(name, o):
        nonlocal n
        if not isinstance(o, h5py.Dataset):
            return
        try:
            raw = bytes(o[()])
        except Exception:
            return
        if len(raw) >= 16 and struct.unpack('<I', raw[:4])[0] == MAGIC and struct.unpack('<I', raw[4:8])[0] == 0:
            dv = struct.unpack('<I', raw[8:12])[0]
            version_map.setdefault(name, dv)  # first native file wins
            try:
                parse(raw); n += 1
            except Exception:
                pass
    f.visititems(vi); f.close(); return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='vfrom', type=int, required=True)
    ap.add_argument('--to', dest='vto', type=int, required=True)
    ap.add_argument('files', nargs='+', help='native target (lower-version) .spp files or globs')
    a = ap.parse_args()

    files = []
    for pat in a.files:
        files += glob.glob(pat)
    files = sorted(set(files))
    if not files:
        sys.exit("no input files matched")

    version_map = {}
    total = 0
    for f in files:
        c = process(f, version_map)
        total += c
        print(f"  parsed {c} v{a.vto} streams from {f}", file=sys.stderr)

    schema = {t: canonical_order(order_samples[t]) for t in sorted(members)}
    here = os.path.dirname(__file__)
    lib = os.path.normpath(os.path.join(here, '..', 'spp_extractor', 'lib'))
    profdir = os.path.normpath(os.path.join(here, '..', 'profiles'))
    os.makedirs(profdir, exist_ok=True)

    schema_name = f"v{a.vto}_schema.json"
    json.dump(schema, open(os.path.join(lib, schema_name), 'w'), indent=1)

    max_dv = max(version_map.values()) if version_map else 0
    profile = {
        "from": a.vfrom, "to": a.vto,
        "_comment": "AUTO-GENERATED SKELETON. schema + data_version_map derived from target-version files; fill the TODO maps from compatibility testing.",
        "source_format": "registry" if a.vfrom >= 11 else "inline",
        "target_format": "inline",
        "target_max_data_version": max_dv,
        "data_version_map": dict(sorted(version_map.items())),
        "dataset_renames": {},
        "blacklist": ["TODO: top-level dict types the target rejects by name (see 'Invalid dict type : X')"],
        "type_rename": {},
        "baking_tweak_rename": {},
        "baking_baker_id_rename": {},
        "schema_file": schema_name,
        "baking_schema_file": "TODO: reuse v10_baking_schema.json or extract from a native corpus",
    }
    prof_path = os.path.join(profdir, f"v{a.vfrom}_to_v{a.vto}.json")
    json.dump(profile, open(prof_path, 'w'), indent=2)

    print(f"\n=== {len(schema)} types / {total} streams / {len(version_map)} datasets ===", file=sys.stderr)
    print(f"wrote {os.path.join(lib, schema_name)}", file=sys.stderr)
    print(f"wrote {prof_path}", file=sys.stderr)
    print("Next: fill TODO renames/blacklist/baking_schema, then SPP_PROFILE=v%d_to_v%d" % (a.vfrom, a.vto), file=sys.stderr)


if __name__ == "__main__":
    main()
