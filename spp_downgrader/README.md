# SPP Downgrader for Adobe Substance 3D Painter software

Converts a `.spp` project file down to a version an older installed application
can open, across versions 8.1-12.1. The default step is v11 to v10; any other pair
is composed automatically from the adjacent migration profiles (see *Migration
profiles* below). This is the self-contained production set of scripts; everything
here is used by the pipeline.

> [!IMPORTANT]
> Universal SPP is unofficial and is not affiliated with, endorsed by, sponsored
> by, or authorized by Adobe. It does not include Adobe software, logos, product
> icons, documentation, sample project files, or license/activation bypasses. Use
> it only with project files you own or have permission to process. See
> [../NOTICE.md](../NOTICE.md).

A `.spp` is an **HDF5** container holding **HBO** binary streams (the document,
layer stack, editor settings, baking config, etc.). v11 writes these in a
registry-based format and with v11-era schemas; v10 needs the older tagged format
and the v10 schemas. This tool rewrites them.

---

## Requirements

```
python 3.11+        (3.13/3.14 fine)
pip install h5py numpy mmh3 PyYAML
```

## Usage

Two steps: extract the v11 project to an intermediate `.uspp` (a zip), then
rebuild it as a v10 `.spp`.

```bash
# 1) extract v11 .spp -> .uspp
python spp_extractor/spp_extractor.py  MyProject.spp  -f uspp  -o MyProject.uspp

# 2) rebuild as v10
python spp_builder/spp_builder.py  MyProject.uspp  MyProject_v10.spp  --target-version 10
```

Open `MyProject_v10.spp` in your installed version 10 application.

> Fully close the application before opening a freshly built file.

---

## Layout

```
spp_downgrader/
├── spp_extractor/
│   ├── spp_extractor.py            # v11 .spp  -> .uspp (HDF5 read, no installed app needed)
│   ├── config/
│   │   └── downgrade_config.yaml   # blacklist of v11-only fields to drop
│   └── lib/
│       ├── hbo_reserializer.py     # ★ the heart: v11 registry HBO -> v10 tagged HBO
│       ├── hbo_parser.py           # HBO stream/dict-entry parsing
│       ├── type_code_mapper.py     # v11 -> v10 type-code mapping
│       ├── config_manager.py       # loads downgrade_config.yaml
│       ├── dict_remover.py         # dict-entry removal helpers
│       ├── v10_schema.json         # v10 object schema (type -> ordered members)
│       └── v10_baking_schema.json  # v10 baking schema (bakerId -> tweaks)
├── spp_builder/
│   ├── spp_builder.py              # .uspp -> v10 .spp (HDF5 write, version/hash patch, transcode)
│   └── hbo_encoder.py             # HBO encoding helpers
└── debug/                          # diagnostics (not needed to run the tool)
    ├── painter_crash.py            # parse newest Crashpad minidump (fault/regs/backtrace)
    ├── doc_treediff.py             # object-graph diff of a built file vs a reference v10
    └── extract_v10_schema.py       # (re)generate v10_schema.json from target-version files
```

The builder adds `../spp_extractor` to `sys.path`, so keep the two folders as
siblings (as laid out above).

---

## What the downgrade actually does

Each item below was a distinct incompatibility that blocked v10 from opening a
v11 file. They are applied generically (data-driven), not hardcoded per file.

### Container / file level (`spp_builder.py`)
- **Version stamp** in `projectsettings.ini` → set major to 10.
- **`projectUUID`** record (v11-only) is **stripped** from `projectsettings.ini`
  (v10's `ProjectManagement` doesn't expect it).
- **MurmurHash3 checksums** (`m3_x64_128` attribute) recomputed for every changed
  dataset. The application checksum seed is `0xF13A0239` (verified against native
  files). A wrong checksum makes the application report "Archive appears to be
  corrupted".
- **Dataset renames**: v11 `editor/*2.{ini,bin}` → v10 names.
- **`data_version`** in HBO headers capped to the v10 values. Critically,
  `paint/document.bin` and `paint/default_material.bin` must be **81** (the v10
  current). Stamping them lower makes the application run its update handlers on
  already-v10 data → crash.

### HBO stream level (`hbo_reserializer.py`)
- **Registry → tagged**: decode the v11 registry-based object graph, re-encode in
  v10 tagged format (`version_check=0`).
- **Object tag 0x12 vs 0x14**: v10 tags entity objects (those with a `uid`) as
  `0x12` and value-struct objects (no `uid`, e.g. `DataBlending`,
  `DataLayerState`) as `0x14`. Derived from the object's own fields.
- **Schema projection**: each object is projected onto `v10_schema.json` — keep
  only members v10 defines for that type, in v10 order; drop v11-only fields.
- **Empty/null objects**: a v11 null-object decodes to an empty nameless object;
  written as v10 null (`0xFF`).
- **UIDs pass through** unchanged (they are referenced elsewhere; rewriting a
  definition's uid without updating its references creates a null reference →
  "Null object access").
- **Field-value pass-through**: transforms only fill *missing* required v10 fields;
  they never overwrite values present in the v11 source.
- **Baking migration** (`v10_baking_schema.json` + rename maps): v11 uses short
  baker ids (`Normal`, `Color`, `Curvature`…) and renamed some tweak parameters;
  v10 expects fully-qualified ids (`GLMapBakerManager.NormalFromDetail`…) and the
  v10 tweak set/order. The tool renames baker ids, renames/retypes tweaks
  (e.g. `Detail.CageMode` int → `Detail.UseCage` bool), and reorders to the v10
  baker schema, preserving your baking *values*.

---

## Migration profiles (how versions differ)

You author **only adjacent steps** — `v11_to_v10.json`, `v10_to_v9.json`,
`v9_to_v8.json`. There is **never a file for a non-adjacent pair**: a request for
`v11_to_v9` is *composed* from the links between (`v11→v10` then `v10→v9`) on the
fly. Transforms run on the decoded object tree, so a chain collapses into one
effective profile — renames compose transitively (`A→B→C` ⇒ `A→C`), blacklists
union, and objects project straight to the final target's schema/versions.

Each adjacent step lives in **one data file**, `profiles/<v>_to_<v-1>.json` — not
in the Python. The engine is version-agnostic and reads the *active* profile (env
`SPP_PROFILE`, default `v11_to_v10`; set e.g. `SPP_PROFILE=v11_to_v9` to compose).
A step holds:

| key | meaning |
|-----|---------|
| `schema_file` | target type → ordered members (drop/reorder source objects) |
| `baking_schema_file` | target bakerId → tweak list (reorder/rename/retype baking) |
| `type_rename` | a dict type renamed between versions (fields identical) |
| `field_rename` | `"Type.oldfield": "newfield"` — a member renamed within a type |
| `baking_tweak_rename` / `baking_baker_id_rename` | renamed baking params / baker ids |
| `blacklist` | top-level dict types the target rejects by name |
| `data_version_map` / `target_max_data_version` | per-dataset version stamps |
| `source_format` / `target_format` | which binary codec to use (see below) |

To widen coverage of the current step, edit `profiles/v11_to_v10.json` directly —
e.g. add a baker to `baking_baker_id_rename` or a field to `blacklist`.

### Adding a new step — `automap.py` (auto-maps the whole profile)

Given the **same project saved in two adjacent versions**, `automap.py` derives the
entire profile by diffing the decoded object trees: type renames, dropped types,
field renames, baking id/param renames, schema, dataset renames, and version stamps.
What it can't infer with confidence it **asks about once and remembers**
(`profiles/decisions/`), so re-runs never re-ask.

```bash
# one pair:
python debug/automap.py NEWER.spp OLDER.spp
# every adjacent pair in a folder of v*.spp (the experiment workflow):
python debug/automap.py --corpus path/to/Old_to_new --non-interactive
```

It writes `profiles/v<from>_to_v<to>.json` + `spp_extractor/lib/v<to>_schema.json`.
Confidence policy: high-confidence inferences (type renames, dropped types, schema,
versions) auto-apply; medium ones (field/baking renames) are applied as a best guess
and flagged `TODO` (non-interactive) or confirmed by you (interactive). It only
emits **adjacent** steps — non-adjacent pairs compose (above). Flags: `--overwrite`
(replace an existing profile), `--non-interactive`, `--print-diffs`, `--dry-run`,
`--out-profiles/--out-schema DIR`, `--register-primitive CODE=SIZE`.

Then run it — any direction, same engine:
```bash
SPP_PROFILE=v10_to_v9   python spp_builder/spp_builder.py …   # adjacent
SPP_PROFILE=v12_to_v8.1 python spp_builder/spp_builder.py …   # composed across the chain
```

`build_profile.py` (schema + versions only, no rename inference) remains as a
lower-level fallback.

> **Codecs vs data.** A profile is pure data; the *binary* parse/write is code,
> selected by `source_format`/`target_format`. Both are implemented:
> `inline` (v8/v9/v10) and `registry` (v11/v12) **readers and writers**, so all four
> directions rebuild (inline→inline, registry→inline, registry→registry). A new
> primitive type code is data too — `profiles/primitive_sizes.json` (e.g. v12.1's
> code 22 = 16 bytes); add one with `automap.py --register-primitive`.

### Keeping it maintainable (no painpoints later)

The **reference corpus + decisions are the source of truth; profiles are a derived
cache.** The whole loop for supporting a new version is: save the *same project* in
that version, drop it in the corpus, `automap --corpus`, answer any new TODO once
(saved forever in `profiles/decisions/`).

- **One rich reference project beats many thin ones.** Coverage scales with scene
  richness — multiple layers/groups, every baker, all post-effects, fill/paint/
  procedural/decal, symmetry. Thin scenes miss cases (the included corpus has no
  `CurvatureFromMap` baker, no populated post-effects).
- **Verify instead of opening the application for regression** — the parity oracle downgrades
  each adjacent pair and asserts the result matches the native lower file (no foreign
  types, no unknown primitives, conforming fields, matching versions):
  ```bash
  python debug/automap.py --corpus Old_to_new --verify   # exits non-zero on any FAIL
  ```
- **See what a downgrade costs** (cumulative across a composed chain):
  ```bash
  python debug/automap.py --explain v12.1_to_v8.1
  ```
- **Hand knowledge goes in an overrides file, never by editing a generated profile.**
  A sibling `profiles/<pair>.overrides.json` deep-merges over the auto-generated base
  (dicts merge, lists union), so the base stays freely regenerable and your manual
  additions are never clobbered. Regenerate a base only from a corpus at least as rich
  as what produced the current schema, or you'll shrink coverage.

---

## Diagnosing a crash

If a built file crashes the application, the Crashpad minidump is more reliable than the
(often truncated) `log.txt`:

```bash
pip install minidump                 # one-time, only for this script
python debug/painter_crash.py        # parses the newest Crashpad dump
```

It prints the exception, faulting module+offset, registers, and a backtrace.
`python debug/doc_treediff.py <ref.spp> <built.spp> [dataset]` compares a built
dataset's object graph against a known-good lower-version reference (`dataset`
defaults to `paint/document.bin`).

---

## Known limitations

- Schema/baking coverage is bounded by the v10 files used to build the JSON
  schemas (see "Updating the schemas").
- Baking parameter values for renamed+retyped params are best-effort converted
  (e.g. int→bool); other baking config is preserved.
- Tested on version 10.0 project files.
