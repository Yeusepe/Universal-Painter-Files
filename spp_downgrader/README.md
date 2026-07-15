# SPP Downgrader And Universal SPP CLI

This folder contains the conversion engine behind Universal SPP. It can package
a native `.spp` project as `.uspp`, inspect that package, explain whether a
target conversion is supported, and build a target-version `.spp` copy.

Use `uspp_tool.py` first. The older `spp_extractor/` and `spp_builder/` scripts
are still useful for debugging, but `uspp_tool.py` is the client-facing command
that the Painter plugin calls.

> [!IMPORTANT]
> Universal SPP is unofficial and is not affiliated with, endorsed by, sponsored
> by, or authorized by Adobe. It does not include Adobe software, logos, product
> icons, documentation, sample project files, or license or activation bypasses.
> Use it only with project files you own or have permission to process. See
> [../NOTICE.md](../NOTICE.md).

## Requirements

```powershell
python --version          # 3.11 or newer
pip install h5py numpy mmh3 PyYAML
```

Maintainer tools:

```powershell
pip install pyinstaller py7zr minidump
```

`py7zr` is only needed for paths that inspect embedded substance graph archives.
`minidump` is only needed for Crashpad diagnostics.

## Quick Start

Pack a native project into the portable `.uspp` format:

```powershell
python spp_downgrader\uspp_tool.py pack MyProject.spp -o MyProject.uspp
```

Inspect the `.uspp` manifest:

```powershell
python spp_downgrader\uspp_tool.py info --uspp MyProject.uspp
```

Ask what would happen if the project were opened by a target Painter version:

```powershell
python spp_downgrader\uspp_tool.py plan --uspp MyProject.uspp --target 10
```

Build a target-version `.spp`:

```powershell
python spp_downgrader\uspp_tool.py build --uspp MyProject.uspp --target 10 -o MyProject_v10.spp
```

For standalone Linux use, pass the exact target Painter executable when it is
available so the target-member compatibility filter can inspect it:

```bash
python spp_downgrader/uspp_tool.py build \
  --uspp MyProject.uspp --target 10 -o MyProject_v10.spp \
  --target-binary "/path/to/Adobe Substance 3D Painter"
```

The Painter plugin supplies this path automatically. Without it, standalone
Linux conversion still runs but cannot apply the binary-derived member filter.

Close Painter before opening a freshly built file. The converter writes a new
copy, but Painter can keep runtime state and caches alive inside the running
process.

## CLI Commands

| Command | Purpose |
| --- | --- |
| `pack <in.spp> -o <out.uspp>` | Reads the `.spp`, captures the HDF5 structure and datasets, writes a portable `.uspp`, and records the source Painter version. |
| `info --uspp <file.uspp>` | Prints manifest fields such as created version, supported versions, format version, tool version, and source file. |
| `plan --uspp <file.uspp> --target <version>` | Resolves the conversion direction and prints JSON showing whether the target is supported and what lossy changes are expected. |
| `build --uspp <file.uspp> --target <version> -o <out.spp>` | Rebuilds a native `.spp` for the target version or for the stored version when the target can upgrade natively. |

Version labels can be major-only or major-minor, such as `10`, `12`, or `12.1`.

## How Versions Are Resolved

The `.uspp` manifest records the version that created the project. The build
target is the version you want to open the output in.

| Direction | Condition | Result |
| --- | --- | --- |
| `exact` | Target equals source | Rebuild at the same version with no downgrade profile. |
| `native_upgrade` | Target is newer than source | Rebuild the stored source version and let Painter upgrade normally on open. |
| `downgrade` | Target is older than source | Compose the needed adjacent profiles and transform the project to the target format. |

Profiles are adjacent only:

```text
12.1 -> 12 -> 11 -> 10 -> 9 -> 8.1
```

A non-adjacent request is composed from the graph. For example, `v12.1_to_v9`
walks the links from 12.1 down to 9. There is no separate `v12.1_to_v9.json`
file because that would duplicate the adjacent steps and drift over time.

When a requested minor version shares the same format boundary as a supported
lower version, the resolver snaps down to the nearest available profile target.
For example, an 8.x target can be built at the 8.1 boundary and opened by newer
8.x installs.

## What A `.uspp` Contains

`.uspp` is a ZIP archive with enough information to reconstruct a native `.spp`
later.

| File | Meaning |
| --- | --- |
| `manifest.json` | Tool version, source file, source version, supported versions, counts, and extraction errors. |
| `metadata.json` | Parsed Painter version plus HDF5 and root-attribute metadata. |
| `structure.json` | The HDF5 group and dataset tree. |
| `groups.json` | Group attributes and group creation properties. |
| `datasets.json` | Dataset paths, dtypes, creation properties, attributes, HBO headers, and the names of stored payload files. |
| `data/*.bin` | Raw dataset bytes used to rebuild the project. |
| `decoded/*.json` | Optional decoded HBO JSON when using the low-level extractor with HBO decoding enabled. |

The normal `pack` path keeps the archive focused on rebuild data. It skips the
source texture cache because the cache is invalid after a downgrade and Painter
can recompute it on open.

## Pipeline

### 1. Pack

`pack` opens the `.spp` as HDF5, walks every group and dataset, records creation
properties, stores dataset attributes, and parses `projectsettings.ini` to find
the source Painter version.

For datasets that look like HBO streams, the extractor records the HBO header.
The normal pack path stores raw bytes instead of fully decoded object JSON
because the builder can decode transformable HBO streams from those raw bytes
during the downgrade step.

### 2. Plan

`plan` reads `manifest.json` and decides which direction applies. For downgrades,
it loads the effective migration profile and builds a human-readable loss report.

Only genuinely lossy categories are reported:

- `blacklist`, where a type or field is dropped.
- `field_retype`, where a primitive shrinks and precision or high bits may be
  lost.

Faithful transforms are not reported as losses. Renaming a type, renaming a
field, wrapping an object into an array, or translating an enum into a bitmask
preserves the represented value, so those changes stay silent.

### 3. Build

`build` recreates the HDF5 file from the `.uspp` archive. It writes groups,
datasets, creation properties, attributes, and transformed payloads into a new
`.spp`.

When the target is older, the command sets the active profile before importing
the builder. That matters because the HBO serializer and profile loader bind
profile data at import time.

When the target is the same or newer, `build` skips the downgrade profile and
rebuilds the stored version. Newer Painter versions then use their own native
upgrade path when opening the file.

## What A Downgrade Changes

The converter handles incompatibilities at two levels: the HDF5 container and
the HBO object streams inside some datasets.

### Container And Dataset Level

- **Version stamps:** `projectsettings.ini` is patched so the target version sees
  the file as one of its own.
- **Unsupported project settings:** v10-and-older builds strip v11-only records
  such as `projectUUID`, which older `ProjectManagement` code does not expect.
- **Dataset renames:** profile data maps dataset names that changed between
  versions, such as `editor/viewersettings2.ini` to `editor/viewersettings.ini`
  for v10.
- **HBO `data_version`:** HBO headers are capped or mapped to the target version.
  For v10, `paint/document.bin` and `paint/default_material.bin` must use data
  version `81`; lower values can make Painter run update handlers on data that
  has already been projected to v10.
- **Checksums:** changed datasets get fresh `m3_x64_128` attributes. The seed is
  `0xF13A0239`, matching Painter's MurmurHash3 x64 128 checksum.
- **Texture cache:** `texture-cache/*` is stripped because it belongs to the
  source version's engine and source document state. Painter rebuilds it.
- **Shader compatibility:** for v10-and-below targets, the builder can inject
  small GLSL stubs for shader helper functions that newer embedded shaders call
  but older runtimes do not define.
- **Substance graph compatibility:** for v8.1 targets, embedded substance graph
  formats newer than the target engine can read are detected and dropped when no
  compatible graph can be used.
- **HDF5 fidelity:** group and dataset creation properties, byte order, fill
  values, compression settings, root attributes, and dataset attributes are
  copied where h5py allows it.

### HBO Stream Level

HBO streams contain Painter object graphs. The serializer decodes the source
format, applies profile-driven transforms to the object tree, then writes the
target binary format.

Key behaviors:

- **Codec conversion:** registry-format streams and inline tagged streams are
  both readable and writable. The active profile chooses `source_format` and
  `target_format`.
- **Object tags:** inline targets distinguish entity objects with `uid` from
  value-struct objects without `uid`. The writer emits the target tag based on
  the actual object fields.
- **Schema projection:** each object is projected to the target schema. Members
  unknown to the target are dropped, members are ordered the way the target
  expects, and missing required members can be synthesized from target defaults.
- **Null objects:** source null or empty nameless objects are written as target
  null objects instead of malformed empty values.
- **UID preservation:** object IDs are not rewritten. Other streams can refer to
  them, so changing IDs without updating every reference would create broken
  links.
- **Type and field renames:** profile maps rename object types and fields without
  losing values.
- **Field kind changes:** profile maps can wrap or unwrap fields when a target
  expects an array instead of an object, or the reverse.
- **Field value transforms:** profile maps can translate value semantics, such as
  enum indexes to bitmasks.
- **Primitive retyping:** known primitive width changes can be applied per field
  or globally by primitive code.
- **Target member allowlist:** the builder can load target-version member names
  from the installed Painter binary path or generated member data, then drop
  members the target reader rejects.
- **Baking migration:** baker IDs, tweak names, tweak order, and tweak primitive
  types are projected to the target baking schema while preserving compatible
  values.

## Migration Profiles

Profiles live in `profiles/`. They are data files, not code patches.

Each adjacent step has a base profile:

```text
profiles/v12.1_to_v12.json
profiles/v12_to_v11.json
profiles/v11_to_v10.json
profiles/v10_to_v9.json
profiles/v9_to_v8.1.json
```

A sibling `*.overrides.json` file deep-merges over the generated base. Dicts
merge key by key, lists are unioned, and scalar values override. Put hand-written
knowledge in the override file when you want the generated base to stay
regenerable.

Common profile keys:

| Key | Meaning |
| --- | --- |
| `from` / `to` | Adjacent version labels for the step. |
| `source_format` / `target_format` | HBO binary codec names, such as `registry` or `inline`. |
| `schema_file` | Target object schema, type name to ordered member list. |
| `defaults_file` | Target default values used when a required member is missing from the source. |
| `baking_schema_file` | Target baker ID to ordered tweak list. |
| `target_max_data_version` | Fallback cap for HBO data versions. |
| `data_version_map` | Per-dataset HBO data version values for the target. |
| `dataset_renames` | Dataset path renames between source and target. |
| `blacklist` | Types or fields the target rejects and the converter must remove. |
| `type_rename` | Object type renames where the data still means the same thing. |
| `field_rename` | Field renames, either global or scoped as `Type.oldField`. |
| `field_retype` | Field-specific primitive code changes. |
| `field_rekind` | Field container changes, such as object to array. |
| `field_value_transform` | Semantic value transforms, such as enum to bitmask. |
| `primitive_retype` | Global primitive code changes for every matching primitive. |
| `baking_tweak_rename` | Baking tweak name changes. |
| `baking_baker_id_rename` | Baking baker ID changes. |
| `schema_add` / `defaults_add` | Hand-added target schema/default entries for cases missing from the corpus. |

Profile composition is transitive. If one step says `A -> B` and the next says
`B -> C`, a composed downgrade treats it as `A -> C`. Blacklists union. The final
target schema and defaults win because the output only needs to satisfy the final
Painter version.

## Adding Support For A New Version

The fastest path is to compare the same rich project saved in two adjacent
Painter versions.

```powershell
# One adjacent pair.
python spp_downgrader\debug\automap.py NEWER.spp OLDER.spp

# A folder of adjacent reference files.
python spp_downgrader\debug\automap.py --corpus path\to\Old_to_new --non-interactive
```

`automap.py` decodes both object trees and derives as much profile data as it
can: type renames, field renames, dropped types, schema, dataset renames, version
stamps, baking ID renames, and baking tweak renames.

When it cannot infer something confidently, interactive mode asks once and saves
the answer under `profiles/decisions/`. Non-interactive mode writes a best guess
where possible and marks uncertain entries for review.

A useful reference project should exercise the features you care about:

- Multiple layers, folders, masks, fill layers, paint layers, generators, filters,
  decals, and procedural sources.
- All baked map types you expect to preserve.
- Post effects, display settings, symmetry, channels, and shader variants.
- At least one project saved natively by each adjacent version in the chain.

Thin reference projects produce thin schemas. If a type never appears in the
corpus, the generated profile cannot learn it.

## Verify A Profile

Run the corpus verifier after generating or editing profiles:

```powershell
python spp_downgrader\debug\automap.py --corpus Old_to_new --verify
```

The verifier downgrades each adjacent pair and checks the result against the
native lower-version reference. It looks for foreign types, unknown primitive
codes, field mismatches, version stamp problems, and other issues that would
make the target reader unhappy.

To explain the cumulative cost of a composed downgrade:

```powershell
python spp_downgrader\debug\automap.py --explain v12.1_to_v8.1
```

`debug/build_profile.py` remains as a lower-level fallback for schema and version
data only. Prefer `automap.py` for real profile work because it learns more of
the migration surface.

## Low-Level Tools

The CLI wrapper is the normal entry point, but these scripts are still useful
when debugging the file format.

Extract directly:

```powershell
python spp_downgrader\spp_extractor\spp_extractor.py MyProject.spp -f uspp -o MyProject.uspp
python spp_downgrader\spp_extractor\spp_extractor.py MyProject.spp --format json --decode-hbo
python spp_downgrader\spp_extractor\spp_extractor.py MyProject.spp --analyze -v
```

Build directly:

```powershell
python spp_downgrader\spp_builder\spp_builder.py MyProject.uspp MyProject_v10.spp --target-version 10
```

The direct builder reads the active profile from `SPP_PROFILE`:

```powershell
$env:SPP_PROFILE = "v12.1_to_v10"
python spp_downgrader\spp_builder\spp_builder.py MyProject.uspp MyProject_v10.spp --target-version 10
```

For the main `uspp_tool.py build` command, you normally do not set
`SPP_PROFILE` yourself. The wrapper derives it from the `.uspp` source version
and the requested target version.

## Diagnosing Crashes

If a built file crashes Painter, the Crashpad minidump is usually more useful
than `log.txt`.

```powershell
pip install minidump
python spp_downgrader\debug\painter_crash.py
```

It prints the exception, faulting module and offset, registers, and a backtrace.

To compare a built dataset against a known-good lower-version file:

```powershell
python spp_downgrader\debug\doc_treediff.py reference_v10.spp built_v10.spp paint/document.bin
```

If you omit the dataset path, `doc_treediff.py` defaults to
`paint/document.bin`.

## Known Limits

- Downgrades are lossy when the target version lacks a feature or binary shape
  used by the source project.
- Schema and baking coverage are bounded by the reference projects used to
  generate the profiles.
- Some profile entries are best-effort when a value changed meaning across
  versions.
- A project that opens successfully can still look different if the target
  Painter version lacks the rendering, shader, bake, or procedural feature used
  by the source.
- Windows is the established runtime target. Native Linux converter and plugin
  support is experimental; the Win32-only legacy UV-tile guard bypass is not
  available there.
