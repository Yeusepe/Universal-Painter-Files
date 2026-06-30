> [!NOTE]
> This is still being tested. If you have a Painter file that does not seem to be working, join our Discord: https://discord.gg/2cEmTJf7Jb

![Universal SPP](https://github.com/user-attachments/assets/bb142dec-652b-4973-943e-3b731e8f1225)
---
Universal SPP is an unofficial compatibility tool for `.spp` project files made
with Adobe Substance 3D Painter software. It helps teams move projects between
installed Painter versions by packaging a project as a portable `.uspp` file,
then rebuilding a copy that the target Painter version can open.

The practical workflow is simple:

1. Save the project normally in Painter as a `.spp`.
2. Export that project as a `.uspp`.
3. Open the `.uspp` on another machine or another Painter version.
4. Universal SPP builds a temporary `.spp` for that installed version and opens
   it.

The important detail is that older Painter versions cannot understand every
field, object type, shader helper, dataset name, or binary layout written by
newer versions. Universal SPP does not pretend those features still exist. When
it has to downgrade a project, it uses explicit migration profiles to rename,
drop, retype, reorder, or rebuild only the parts the target version can read.
The plugin shows a loss report before a lossy downgrade is built.

> [!IMPORTANT]
> Universal SPP is not affiliated with, endorsed by, sponsored by, or authorized
> by Adobe. It does not include Adobe software, logos, product icons,
> documentation, sample project files, or license or activation bypasses. Use it
> only with project files you own or have permission to process. Downgrades are
> lossy and one-way. Always keep the original `.spp`.
>
> Universal SPP does not advocate, encourage, enable, or condone piracy of Adobe
> products, Substance 3D Painter, or any other software. It is intended for
> licensed users who already have legal access to their installed Painter
> version, including Steam buyers whose purchase only provides a fixed version
> and who need to use `.spp` files saved by collaborators in newer versions.

## What Is In This Repo

| Path | Purpose |
| --- | --- |
| [`universal_spp_plugin/`](universal_spp_plugin/README.md) | The Painter plugin. It adds a **Universal** menu for saving and opening `.uspp` files inside Painter. |
| [`spp_downgrader/`](spp_downgrader/README.md) | The conversion engine and command line tool. It can pack, inspect, plan, and build projects without launching Painter. |
| [`build.ps1`](build.ps1) | Builds `uspp_tool.exe` with PyInstaller and stages it into `universal_spp_plugin/bin/`. |

Supported profile chain:

```text
12.1 -> 12 -> 11 -> 10 -> 9 -> 8.1
```

The engine composes adjacent profiles automatically. For example, a request to
build a 12.1 project for version 9 walks `12.1 -> 12 -> 11 -> 10 -> 9` and
collapses those steps into one effective migration.

## Install The Plugin

Use this path if you want the menu inside Painter.

1. Download the latest `universal_spp_plugin.zip` release from the
   [Releases](../../releases) page.
2. Open Adobe Substance 3D Painter.
3. Go to **Python > Plugins Folder**. This opens the correct plugin directory for
   your machine, usually `Documents\Adobe\Adobe Substance 3D Painter\python\plugins`.
4. Extract the release so the plugin root is directly inside `plugins`.
5. Make sure `universal_spp_plugin/bin/uspp_tool.exe` exists.
6. Go to **Python > Reload Plugins Folder**, or restart Painter.
7. Enable `universal_spp_plugin` from the **Python** menu if Painter did not
   enable it automatically.

<img width="635" height="128" alt="How to Install" src="https://github.com/user-attachments/assets/d9db8d07-34d9-45ad-ad56-88a454ab9a4d" />

After reload, a **Universal** menu appears in the menu bar. If it does not,
open the **Python** menu and make sure `universal_spp_plugin` has a check beside
it.

If reloading does not work, restart Painter. Also check that the folder is not
nested one level too deep. The path should look like this:

```text
Documents\Adobe\Adobe Substance 3D Painter\python\plugins\universal_spp_plugin
```

not like this:

```text
Documents\Adobe\Adobe Substance 3D Painter\python\plugins\universal_spp_plugin (1)\universal_spp_plugin
```

Common actions:

- **Universal > Save as Universal...** exports the current saved `.spp` as a
  `.uspp`.
- **Universal > Open Universal...** opens a `.uspp`, plans the conversion, warns
  about lossy downgrades, builds a temporary `.spp`, and opens it.

More plugin detail is in
[`universal_spp_plugin/README.md`](universal_spp_plugin/README.md).

## The Two File Formats

### `.spp`

An `.spp` project is an HDF5 container. Inside it are groups, datasets, dataset
attributes, project settings, embedded binary streams, shaders, baked data, and
checksums. Many of the project datasets contain HBO streams, which are Painter's
binary object graphs for things like the document, layer stack, editor state,
materials, and baking configuration.

Different Painter versions write those HBO streams differently. Versions 11 and
12 use a registry-style binary format. Versions 8.1, 9, and 10 use an older
inline tagged format. The downgrade engine knows how to read and write both
families.

### `.uspp`

A `.uspp` file is a ZIP archive. It stores the original project data plus a
manifest that records the version the project was saved from and which target
versions are reachable through the profile chain.

The archive contains:

- `manifest.json`, with tool version, source file, created version, and supported
  versions.
- `metadata.json`, with parsed Painter version and HDF5 metadata.
- `structure.json`, `groups.json`, and `datasets.json`, which describe the HDF5
  tree and creation properties.
- `data/*.bin`, the raw dataset payloads needed to rebuild a native `.spp`.

That makes `.uspp` the handoff format. It is not a Painter-native project file;
it is the portable package Universal SPP can rebuild for a target version.

## Conversion Directions

Universal SPP handles three cases:

| Case | What happens |
| --- | --- |
| Same version | The project is rebuilt faithfully for the same version. |
| Target is newer | Universal SPP rebuilds the saved version, then Painter performs its normal native upgrade when opening it. |
| Target is older | Universal SPP runs the downgrade profiles, shows any expected losses, and writes a target-version `.spp`. |

Downgrading is the only lossy path. Losses come from things the older version
cannot parse or represent. The report is generated from the same profile data
used by the converter, so the warning and the actual transform stay tied
together.

## Use The CLI Directly

Use this path for automation, testing, or conversion without the Painter plugin.
The CLI does not require Painter to be installed for the conversion step.

```powershell
# Pack a native project into the portable format.
python spp_downgrader\uspp_tool.py pack MyProject.spp -o MyProject.uspp

# Inspect the manifest.
python spp_downgrader\uspp_tool.py info --uspp MyProject.uspp

# Ask what would happen for a target version.
python spp_downgrader\uspp_tool.py plan --uspp MyProject.uspp --target 10

# Build a target-version project copy.
python spp_downgrader\uspp_tool.py build --uspp MyProject.uspp --target 10 -o MyProject_v10.spp
```

Fully close Painter before opening a freshly built file. That avoids confusing a
running session with cached state from a previous copy of the project.

More engine detail is in
[`spp_downgrader/README.md`](spp_downgrader/README.md).

## Build From Source

The plugin calls `universal_spp_plugin/bin/uspp_tool.exe`. Client machines do not
need Python when that executable is present. To build it yourself on Windows:

```powershell
git clone <your-fork-url> universal-spp
cd universal-spp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1` runs PyInstaller from inside `spp_downgrader/`, then copies the
result to:

```text
universal_spp_plugin/bin/uspp_tool.exe
```

## Requirements

- Windows.
- Python 3.11 or newer for source use.
- Runtime Python packages: `h5py`, `numpy`, `mmh3`, `PyYAML`.
- Maintainer and build packages: `pyinstaller`, `py7zr`, `minidump`.

The plugin itself is a normal Painter Python plugin. It does not use admin
rights, DLL injection, or background services.

## Safety Model

Universal SPP writes copies. It does not edit the source `.spp` or the source
`.uspp` in place.

On downgrade, the converter may remove data the target version cannot represent.
Examples include newer post-effect objects, newer baking parameters, channel
masks that use a wider primitive type, shader helpers missing from older
runtimes, or embedded substance graphs too new for the target engine.

The safest habit is:

1. Keep the original project.
2. Export `.uspp` from the newest version you use.
3. Let older versions open downgraded copies.
4. Treat those downgraded copies as delivery artifacts, not as the new source of
   truth.

## Repository Layout

```text
universal-spp/
|-- spp_downgrader/        # conversion engine, CLI, profiles, debug tools
|-- universal_spp_plugin/  # Painter plugin and bundled converter location
|-- build.ps1              # builds and stages uspp_tool.exe
|-- requirements.txt       # runtime dependencies
|-- requirements-dev.txt   # build and maintainer dependencies
|-- NOTICE.md              # trademark and usage notices
|-- CONTRIBUTING.md        # contribution and copyright rules
`-- LICENSE                # MIT license
```

## License

MIT. See [LICENSE](LICENSE). Third-party trademark and usage notices are in
[NOTICE.md](NOTICE.md). Contribution rules are in
[CONTRIBUTING.md](CONTRIBUTING.md).
