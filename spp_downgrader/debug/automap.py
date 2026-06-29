#!/usr/bin/env python3
"""automap - automatically map the differences between two .spp versions into a
migration profile, asking the user (and learning) only when it can't infer.

The version-difference knowledge for a downgrade step used to be hand-authored from
compatibility testing. Given the SAME project saved in two adjacent versions, this
derives it: it decodes every HBO stream in both files, diffs the object trees (lib/hbo_diff),
auto-applies high-confidence inferences (type renames, dropped types, dataset/version
maps, schema), and for ambiguous ones asks once and persists the answer
(lib/decisions_store) so re-runs never re-ask.

Usage:
  python debug/automap.py SRC.spp TGT.spp [--from V --to V] [options]
  python debug/automap.py --corpus DIR [options]          # all adjacent pairs

Options:
  --non-interactive     never prompt; unresolved diffs become TODO entries
  --overwrite           replace an existing profile/schema (default: skip + report)
  --out-profiles DIR    where to write v{from}_to_v{to}.json  (default: profiles/)
  --out-schema DIR      where to write v{to}_schema.json      (default: spp_extractor/lib/)
  --register-primitive CODE=SIZE   add a primitive size to profiles/primitive_sizes.json
  --print-diffs         dump the full classified diff list
  --dry-run             compute + print, write nothing
  --today YYYY-MM-DD    date stamped into decisions (default: 1970-01-01 placeholder)
"""
import sys
import os
import re
import json
import glob
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spp_extractor"))

from lib import hbo_decode as H
from lib import hbo_diff as D
from lib import decisions_store as DS

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
DEF_PROFILES = os.path.join(ROOT, "profiles")
DEF_SCHEMA = os.path.join(ROOT, "spp_extractor", "lib")


# --------------------------------------------------------------- version handling

def parse_version(path):
    """v8.1.0 -> '8.1', v9.0.0 -> '9', v12.1.0 -> '12.1' (drop trailing .0 groups)."""
    m = re.search(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", os.path.basename(path))
    if not m:
        return None
    parts = [int(x) for x in m.groups() if x is not None]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(p) for p in parts)


def vkey(label):
    return tuple(int(x) for x in label.split("."))


# --------------------------------------------------------------- schema / versions

def _canonical_order(samples):
    import collections
    pos = collections.defaultdict(list)
    for s in samples:
        for i, n in enumerate(s):
            pos[n].append(i)
    return sorted(pos, key=lambda n: sorted(pos[n])[len(pos[n]) // 2])


def _ser_val(v, depth=0):
    """Serialize a canonical Value to a JSON-safe form (for stored defaults)."""
    if depth > D.MAX_DEPTH:
        return ["null"]
    k = v[0]
    if k == "primitive":
        return ["p", v[1], v[2].hex()]
    if k == "string":
        return ["s", v[1].hex()]
    if k == "object":
        if v[1] is None:
            return ["o", None]
        nm, fields = v[1]
        return ["o", nm, [[fn, _ser_val(fv, depth + 1)] for fn, fv in fields]]
    if k == "array":
        return ["a", [_ser_val(e, depth + 1) for e in v[1]]]
    return ["null"]


def derive_schema(node):
    """From a decoded target tree, return (schema, defaults):
      schema   = type -> [ordered members]
      defaults = type -> {member: <serialized representative value>}
    Defaults let the engine SYNTHESIZE a required member the source lacks (older
    readers error on a missing member, e.g. DataChannel.userIsColorManaged, or whole
    object members like DataPostEffectsParameters.colorCorrection). Scalars take the
    most-common value; objects/arrays take the first representative seen."""
    import collections
    samples = collections.defaultdict(list)
    scalar = collections.defaultdict(collections.Counter)
    subtree = {}

    def walk(n, depth=0):
        if n is None or depth > D.MAX_DEPTH:
            return
        t, fields = n
        if fields:
            samples[t].append(tuple(fn for fn, _ in fields))
        for fn, val in fields:
            if val[0] in ("primitive", "string"):
                scalar[(t, fn)][tuple(_flat(_ser_val(val)))] += 1
            else:
                subtree.setdefault((t, fn), _ser_val(val))
            if val[0] == "object" and val[1]:
                walk(val[1], depth + 1)
            elif val[0] == "array":
                for e in val[1]:
                    if e[0] == "object" and e[1]:
                        walk(e[1], depth + 1)
    walk(node)
    schema = {t: _canonical_order(s) for t, s in sorted(samples.items())}
    defaults = {}
    for (t, m), cnt in scalar.items():
        defaults.setdefault(t, {})[m] = list(cnt.most_common(1)[0][0])
    for (t, m), sv in subtree.items():
        defaults.setdefault(t, {}).setdefault(m, sv)
    return schema, defaults


def _flat(x):
    """Hashable form of a serialized scalar (['p',code,hex] / ['s',hex])."""
    return tuple(x)


def derive_dataset_renames(src_names, tgt_names):
    """Pair a src-only dataset with a tgt-only one after stripping trailing digits
    from the basename stem (v11 'iraysettings2.ini' -> v10 'iraysettings.ini')."""
    src_only = sorted(set(src_names) - set(tgt_names))
    tgt_only = set(tgt_names) - set(src_names)
    out = {}
    for s in src_only:
        d, base = os.path.split(s)
        stem, ext = os.path.splitext(base)
        cand = os.path.join(d, re.sub(r"\d+$", "", stem) + ext).replace("\\", "/")
        if cand in tgt_only:
            out[s] = cand
    return out


# --------------------------------------------------------------- core mapping

def map_pair(src_spp, tgt_spp, from_v, to_v, args, today):
    sstreams = {n: r for n, r, h in H.iter_hbo_streams(src_spp)}
    tstreams = {n: (r, h) for n, r, h in H.iter_hbo_streams(tgt_spp)}

    # schema + versions from target (decode target streams once)
    schema, version_map, tgt_format = {}, {}, "inline"
    for name, (raw, hdr) in tstreams.items():
        version_map[name] = hdr[2]
        try:
            node, fmt, _ = H.decode(raw, name)
            tgt_format = fmt
            for t, members in derive_schema(node)[0].items():
                if t not in schema:          # first occurrence wins (target ordering)
                    schema[t] = members
        except Exception as e:
            print(f"  ! target decode failed {name}: {e}", file=sys.stderr)
    tgt_types = set(schema)

    # diff every shared dataset
    diffs, src_format, decode_fails = [], "registry", []
    for name in sorted(set(sstreams) & set(tstreams)):
        try:
            snode, src_format, _ = H.decode(sstreams[name], name)
            tnode = H.decode(tstreams[name][0], name)[0]
        except H.UnknownPrimitive as e:
            decode_fails.append(f"{name}: unknown primitive {e.code} (register with --register-primitive {e.code}=SIZE)")
            continue
        except Exception as e:
            decode_fails.append(f"{name}: {e}")
            continue
        diffs += D.diff(snode, tnode, tgt_types)

    if args.print_diffs:
        for d in diffs:
            print("   ", {k: v for k, v in d.items() if k in ("action", "confidence", "src_type", "field_name", "tgt_type", "tgt_field", "mapping")})

    # assemble profile, applying confidence + learning
    store = DS.load(from_v, to_v)
    prof = {
        "from": from_v, "to": to_v,
        "_comment": f"AUTO-MAPPED from {os.path.basename(src_spp)} vs {os.path.basename(tgt_spp)}. "
                    "Review TODO entries; edit freely (this file is the source of truth).",
        "source_format": src_format, "target_format": tgt_format,
        "target_max_data_version": max(version_map.values()) if version_map else 0,
        "data_version_map": dict(sorted(version_map.items())),
        "dataset_renames": derive_dataset_renames(sstreams.keys(), tstreams.keys()),
        "blacklist": [], "type_rename": {}, "field_rename": {}, "field_retype": {}, "field_rekind": {},
        "field_value_transform": {},
        "baking_tweak_rename": {}, "baking_baker_id_rename": {},
        "schema_file": f"v{to_v}_schema.json",
        "defaults_file": f"v{to_v}_defaults.json",
    }
    todos, asked, applied = [], 0, 0
    seen = set()
    for d in diffs:
        key = (d["action"], d.get("src_type"), d.get("field_name"), d.get("tgt_type"), str(d.get("mapping")))
        if key in seen:
            continue
        seen.add(key)
        res = _apply(d, prof, store, args, today, todos)
        if res == "asked":
            asked += 1
        elif res == "applied":
            applied += 1

    summary = {"pair": f"v{from_v}->v{to_v}", "applied": applied, "asked": asked,
               "todos": len(todos), "decode_fails": decode_fails}
    return prof, store, todos, summary


def _apply(d, prof, store, args, today, todos):
    """Route one classified diff into the profile per confidence + learned decisions."""
    act = d["action"]

    # HIGH auto-applied, no questions
    if act == "type_rename":
        prof["type_rename"][d["src_type"]] = d["tgt_type"]; return "applied"
    if act == "blacklist_type":
        if d["src_type"] not in prof["blacklist"]:
            prof["blacklist"].append(d["src_type"]); return "applied"
        return None
    if act == "field_retype":
        prof["field_retype"][f"{d['src_type']}.{d['field_name']}"] = d["to_code"]; return "applied"
    if act == "field_rekind":
        prof["field_rekind"][f"{d['src_type']}.{d['field_name']}"] = d["to_kind"]; return "applied"
    if act == "field_value_transform":
        prof["field_value_transform"][f"{d['src_type']}.{d['field_name']}"] = {"op": d["op"], "code": d["to_code"]}; return "applied"
    if act == "noop_projection":
        return None  # schema projection already drops it

    # MEDIUM / LOW: a real (human) decision wins; else infer.
    stored = DS.resolve(store, d)
    if stored is not None and stored["action"] not in ("todo", "skip", "keep"):
        return _commit(d, prof, stored["action"], stored.get("value"))
    if stored is not None and stored["action"] in ("skip", "keep"):
        return None                       # user previously declined this one

    inferred = _infer(d)
    if args.non_interactive:
        # apply the best guess so the profile is usable, and flag it for review;
        # do NOT persist (an unconfirmed guess must not silence a later interactive run)
        todos.append(_todo_text(d, inferred))
        if inferred:
            _commit(d, prof, *inferred)
        return None

    choice = _prompt(d, inferred)
    DS.record(store, d, choice[0], choice[1], "interactive", today)
    if choice[0] in ("skip", "keep"):
        return "asked"
    _commit(d, prof, *choice)
    return "asked"


def _infer(d):
    """Best-guess (action, value) for a MEDIUM/LOW diff, or None."""
    act = d["action"]
    if act == "field_rename":
        return ("field_rename", {"type": d["src_type"], "from": d["field_name"], "to": d["tgt_field"]})
    if act == "blacklist_field":
        return ("drop", f"{d['src_type']}.{d['field_name']}")
    if act == "baker_rename":
        return ("baker_rename", d["mapping"])
    if act == "tweak_rename":
        return ("tweak_rename", d["mapping"])
    return None


def _commit(d, prof, action, value):
    if action == "field_rename" and value:
        prof["field_rename"][f"{value['type']}.{value['from']}"] = value["to"]
    elif action == "drop" and value:
        if value not in prof["blacklist"]:
            prof["blacklist"].append(value)
    elif action == "baker_rename" and value:
        prof["baking_baker_id_rename"].update(value)
    elif action == "tweak_rename" and value:
        prof["baking_tweak_rename"].update(value)
    elif action == "rename" and value:           # generic type rename from a stored decision
        prof["type_rename"][d["src_type"]] = value
    else:
        return None
    return "applied"


def _todo_text(d, inferred):
    base = DS._describe(d)
    return f"{base}  [inferred: {inferred[0]}={inferred[1] if inferred else '?'}]"


def _prompt(d, inferred):
    print("\n  ? " + DS._describe(d) + f"   (confidence {d['confidence']})")
    if inferred:
        print(f"      inferred: {inferred[0]} -> {inferred[1]}")
    print("      [a]ccept inferred  [d]rop  [k]eep/skip  [c]ustom rename target")
    try:
        ans = input("      > ").strip().lower()
    except EOFError:
        ans = "k"
    if ans == "a" and inferred:
        return inferred
    if ans == "d":
        return ("drop", f"{d['src_type']}.{d['field_name']}" if d.get("field_name") else d["src_type"])
    if ans == "c":
        tgt = input("        rename target: ").strip()
        return ("rename", tgt) if not d.get("field_name") else ("field_rename", {"type": d["src_type"], "from": d["field_name"], "to": tgt})
    return ("skip", None)


# --------------------------------------------------------------- output

def write_outputs(prof, store, from_v, to_v, defaults, schema, args):
    prof_dir = args.out_profiles or DEF_PROFILES
    schema_dir = args.out_schema or DEF_SCHEMA
    os.makedirs(prof_dir, exist_ok=True)
    os.makedirs(schema_dir, exist_ok=True)
    prof_path = os.path.join(prof_dir, f"v{from_v}_to_v{to_v}.json")
    schema_path = os.path.join(schema_dir, f"v{to_v}_schema.json")
    defaults_path = os.path.join(schema_dir, f"v{to_v}_defaults.json")

    wrote = []
    for path, data in [(schema_path, schema), (defaults_path, defaults), (prof_path, prof)]:
        if os.path.exists(path) and not args.overwrite:
            print(f"  = exists, not overwriting (use --overwrite): {path}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"  (dry-run) would write {path}", file=sys.stderr)
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2 if path == prof_path else 1, sort_keys=(path == schema_path))
        wrote.append(path)
    if not args.dry_run:
        DS.save(from_v, to_v, store)
    return wrote


def run_pair(src, tgt, args, today):
    fv = args.vfrom or parse_version(src)
    tv = args.vto or parse_version(tgt)
    print(f"\n=== mapping v{fv} (src {os.path.basename(src)}) -> v{tv} (tgt {os.path.basename(tgt)}) ===")
    prof, store, todos, summary = map_pair(src, tgt, fv, tv, args, today)
    # recompute target schema + per-member defaults for writing
    schema, defaults = {}, {}
    for name, (raw, hdr) in {n: (r, h) for n, r, h in H.iter_hbo_streams(tgt)}.items():
        try:
            sc, df = derive_schema(H.decode(raw, name)[0])
            for t, m in sc.items():
                schema.setdefault(t, m)
            for t, dd in df.items():
                defaults.setdefault(t, {}).update({k: v for k, v in dd.items() if k not in defaults.get(t, {})})
        except Exception:
            pass
    wrote = write_outputs(prof, store, fv, tv, defaults, schema, args)
    print(f"  applied={summary['applied']} asked={summary['asked']} todos={summary['todos']} "
          f"decode_fails={len(summary['decode_fails'])} wrote={len(wrote)}")
    for t in todos:
        print(f"    TODO: {t}")
    for e in summary["decode_fails"]:
        print(f"    DECODE-FAIL: {e}", file=sys.stderr)
    return summary


def _reload_with_profile(name):
    """Reload the engine with a specific profile active (profiles bind at import)."""
    import importlib
    os.environ["SPP_PROFILE"] = name
    from lib import migration_profile, hbo_reserializer
    importlib.reload(migration_profile)
    importlib.reload(hbo_reserializer)
    return hbo_reserializer, migration_profile.ACTIVE


def cmd_verify(corpus, args):
    """Parity oracle: for each adjacent pair, downgrade the higher file and assert the
    result has no foreign types, no unknown primitives, matching versions, and every
    field conforms to the native lower file's schema. Ground truth = the native lower
    file (same content). No Painter needed."""
    files = sorted(glob.glob(os.path.join(corpus, "v*.spp")), key=lambda p: vkey(parse_version(p)))
    labels = [(parse_version(f), f) for f in files]
    allpass = True
    print("corpus:", [l for l, _ in labels])
    for (lo_l, lo_f), (hi_l, hi_f) in zip(labels, labels[1:]):
        pname = f"v{hi_l}_to_v{lo_l}"
        R, prof = _reload_with_profile(pname)
        nat = {n: (r, h) for n, r, h in H.iter_hbo_streams(lo_f)}
        src = {n: r for n, r, h in H.iter_hbo_streams(hi_f)}
        foreign, extra, missing, decfail, dvmism = set(), [], [], [], []
        for sname, raw in src.items():
            tname = prof.dataset_renames.get(sname, sname)
            if tname not in nat:
                continue
            tdv = prof.data_version_map.get(tname)   # may be 0 -> don't fall through
            if tdv is None:
                tdv = prof.target_max_data_version or None
            out = R.HBOSerializer(raw).prune_and_reserialize(list(prof.blacklist), tdv)
            if not out:
                decfail.append(f"{sname}: produced nothing"); continue
            try:
                btree, _, bdv = H.decode(out, sname)
            except Exception as e:
                decfail.append(f"{sname}: {e}"); continue
            bt = D.collect(btree)[0]
            nt = D.collect(H.decode(nat[tname][0])[0])[0]
            foreign |= set(bt) - set(nt)
            for ty in set(bt) & set(nt):
                for f in sorted(bt[ty] - nt[ty]):
                    extra.append(f"{tname}:{ty}.{f}")
                for f in sorted(nt[ty] - bt[ty]):    # native requires, built lacks -> load error
                    missing.append(f"{tname}:{ty}.{f}")
            if bdv != nat[tname][1][2]:
                dvmism.append(f"{tname}: built={bdv} native={nat[tname][1][2]}")
        ok = not (foreign or extra or missing or decfail or dvmism)
        allpass = allpass and ok
        print(f"\n{'PASS' if ok else 'FAIL'}  {pname}")
        if foreign:
            print("   foreign types:", sorted(foreign))
        if missing:
            print(f"   MISSING required members ({len(missing)}):", missing[:8], "..." if len(missing) > 8 else "")
        if extra:
            print(f"   non-conforming fields ({len(extra)}):", extra[:8], "..." if len(extra) > 8 else "")
        if decfail:
            print("   decode failures:", decfail[:5])
        if dvmism:
            print("   data_version mismatches:", dvmism[:5])
    print(f"\n==== {'ALL PAIRS PASS' if allpass else 'FAILURES PRESENT'} ====")
    return allpass


def cmd_explain(name):
    """Print what a profile will do (transforms + cumulative loss), composing chains."""
    _, prof = _reload_with_profile(name)
    print(f"profile {name}: {prof.data.get('from')} -> {prof.data.get('to')}  "
          f"({prof.data.get('source_format')} -> {prof.data.get('target_format')})")
    print(f"  schema types         : {len(prof.schema)}")
    print(f"  type_rename          : {len(prof.type_rename)}  {dict(list(prof.type_rename.items())[:4])}")
    print(f"  field_rename         : {len(prof.field_rename)}  {dict(list(prof.field_rename.items())[:4])}")
    print(f"  field_retype (resize): {len(prof.field_retype)}  {dict(list(prof.field_retype.items())[:4])}")
    print(f"  baking_baker_id_rename: {len(prof.baking_baker_id_rename)}")
    print(f"  baking_tweak_rename  : {len(prof.baking_tweak_rename)}")
    print(f"  DROPPED (blacklist)  : {len(prof.blacklist)}  {prof.blacklist[:8]}{'...' if len(prof.blacklist) > 8 else ''}")
    print(f"  dataset_renames      : {len(prof.dataset_renames)}")
    print(f"  target_max_data_version: {prof.target_max_data_version}")


def main():
    ap = argparse.ArgumentParser(description="Auto-map .spp version differences into a migration profile.")
    ap.add_argument("src", nargs="?", help="source (higher version) .spp")
    ap.add_argument("tgt", nargs="?", help="target (lower version) .spp")
    ap.add_argument("--corpus", help="directory of v*.spp; map every adjacent pair")
    ap.add_argument("--from", dest="vfrom", help="override source version label")
    ap.add_argument("--to", dest="vto", help="override target version label")
    ap.add_argument("--non-interactive", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--out-profiles")
    ap.add_argument("--out-schema")
    ap.add_argument("--register-primitive")
    ap.add_argument("--verify", action="store_true", help="parity-check downgrades against native lower files")
    ap.add_argument("--explain", help="print what a profile (e.g. v12.1_to_v8.1) will do, then exit")
    ap.add_argument("--print-diffs", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default="1970-01-01")
    a = ap.parse_args()

    if a.register_primitive:
        code, size = a.register_primitive.split("=")
        path = os.path.join(DEF_PROFILES, "primitive_sizes.json")
        data = json.load(open(path))
        data.setdefault("registry", {})[code.strip()] = int(size)
        json.dump(data, open(path, "w"), indent=2)
        print(f"registered registry primitive {code}={size} in {path}")
        return

    if a.explain:
        cmd_explain(a.explain)
        return

    if a.verify:
        if not a.corpus:
            ap.error("--verify requires --corpus DIR")
        import sys as _s
        _s.exit(0 if cmd_verify(a.corpus, a) else 1)

    if a.corpus:
        files = sorted(glob.glob(os.path.join(a.corpus, "v*.spp")), key=parse_version and (lambda p: vkey(parse_version(p))))
        labels = [(parse_version(f), f) for f in files]
        print("corpus versions:", [l for l, _ in labels])
        summaries = []
        for (lo_l, lo_f), (hi_l, hi_f) in zip(labels, labels[1:]):
            a.vfrom, a.vto = hi_l, lo_l        # downgrade: higher -> lower
            summaries.append(run_pair(hi_f, lo_f, a, a.today))
        print("\n==== corpus summary ====")
        for s in summaries:
            print(f"  {s['pair']:14} applied={s['applied']:3} asked={s['asked']:3} "
                  f"todos={s['todos']:3} decode_fails={len(s['decode_fails'])}")
        return

    if not (a.src and a.tgt):
        ap.error("provide SRC.spp TGT.spp, or --corpus DIR")
    run_pair(a.src, a.tgt, a, a.today)


if __name__ == "__main__":
    main()
