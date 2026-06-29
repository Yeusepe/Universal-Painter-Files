# Universal SPP plugin for Adobe Substance 3D Painter software

Open and save `.uspp` universal project files across installed versions of Adobe
Substance 3D Painter software. A `.uspp` stores the project plus the version it
was authored in; when you open it, the plugin converts it to your installed
version automatically.

> [!IMPORTANT]
> Universal SPP is unofficial and is not affiliated with, endorsed by, sponsored
> by, or authorized by Adobe. It does not include Adobe software, logos, product
> icons, documentation, sample project files, or license/activation bypasses. Use
> it only with project files you own or have permission to process. See
> [../NOTICE.md](../NOTICE.md).

## Install (clients)

1. **Get the converter (`bin/uspp_tool.exe`).** It is not shipped inside the repo.
   Either download it from the project's **GitHub Releases** page and drop it into
   `universal_spp_plugin/bin/`, or build it from source with `build.ps1` (see the
   repo's top-level README). The plugin won't run without it.
2. In the application, open the **Python** menu, then **Plugins Folder**. This opens
   the correct folder for your machine; the path varies, and OneDrive may redirect
   Documents. Go into the `plugins` subfolder. Python plugins must live in
   `python/plugins/` (typically `.../Documents/Adobe/Adobe Substance 3D Painter/python/plugins/`).
3. Copy the whole `universal_spp_plugin` folder (including `bin/uspp_tool.exe`) in, so you
   have `python/plugins/universal_spp_plugin/__init__.py`.
4. **Python** menu, then **Reload Plugins Folder** (or restart the application).
5. In the **Python** menu, make sure `universal_spp_plugin` is enabled. A
   **Universal** menu appears in the menu bar.

(Optional) To install elsewhere, set the `SUBSTANCE_PAINTER_PLUGINS_PATH` env var to a root
folder containing the `plugins`, `modules`, `assets` subfolders.

> Supported application versions: 8.1-12.1. No admin rights and no DLL injection;
> this is a regular Python plugin that calls a bundled converter.

## Use

- **Universal > Open Universal...** - pick a `.uspp`. The plugin detects your installed
  version and:
  - If the file is from a newer version, it converts it down to yours. A popup
    first lists what will be lost or changed. Conversion is lossy and one-way.
    Your `.uspp` is never modified.
  - If the file is from your version or older, it opens directly (the application
    upgrades older projects on open).
- **Universal > Save as Universal...** - save your current, already-saved project as a
  `.uspp` to distribute. Author from your highest installed version so the file can be
  downgraded for everyone else.

## How it works

`.uspp` is a lossless ZIP of the full project plus a manifest carrying the
authoring version and the list of versions it can convert to. The bundled
`bin/uspp_tool.exe` is self-contained, so client machines do not need Python. The
plugin itself is a thin UI layer over the schema-driven downgrade engine.

## For maintainers

- The converter source is `spp_downgrader/uspp_tool.py` (subcommands `pack`/`plan`/
  `build`/`info`). Rebuild the exe with: `cd spp_downgrader && pyinstaller uspp_tool.spec
  --noconfirm`, then copy `dist/uspp_tool.exe` into `universal_spp_plugin/bin/`.
- Plain-English loss messages live in `spp_downgrader/lossiness_messages.json` (edit
  freely; uncurated entries still produce readable text).
- Adding a new application version = author one adjacent migration profile (see
  `spp_downgrader/README.md`), rebuild the exe. Non-adjacent jumps compose automatically.
- Dev/testing: set env `USPP_TOOL` to the `.py` CLI (e.g.
  `H:/path/to/spp_downgrader/uspp_tool.py`) to run the plugin against source instead of the exe.
