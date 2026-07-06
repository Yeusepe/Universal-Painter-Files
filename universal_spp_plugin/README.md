# Universal SPP Plugin

This folder is the Painter-side part of Universal SPP. It adds a **Universal**
menu to Adobe Substance 3D Painter software and connects that menu to the bundled
converter executable.

The plugin keeps the UI small on purpose:

- **Save as Universal...** packs the current saved `.spp` into a `.uspp`.
- **Open Universal...** opens a `.uspp` or a regular `.spp`. If you choose a
  `.spp`, the plugin automatically packs it into a temporary `.uspp` before it
  plans the conversion for the running Painter version, shows any lossy
  downgrade warning, builds a temporary `.spp`, and opens that copy.
- **Check for Updates...** checks GitHub Releases for a newer plugin build.
- **Update Settings...** controls daily update checks, prerelease checks, and
  skipped update reminders.

The plugin does not contain the conversion logic itself. It is a thin Qt/Painter
integration layer over `bin/uspp_tool.exe`.

> [!IMPORTANT]
> Universal SPP is unofficial and is not affiliated with, endorsed by, sponsored
> by, or authorized by Adobe. It does not include Adobe software, logos, product
> icons, documentation, sample project files, or license or activation bypasses.
> Use it only with project files you own or have permission to process. See
> [../NOTICE.md](../NOTICE.md).

## Install

1. Get `bin/uspp_tool.exe`.
   - From a release: download the release build and keep the included
     `universal_spp_plugin/bin/uspp_tool.exe`.
   - From source: run `build.ps1` from the repository root. It builds the
     converter and copies it into this plugin folder.
2. In Painter, open **Python > Plugins Folder**.
3. Open the `plugins` subfolder.
4. Copy the whole `universal_spp_plugin` folder into that `plugins` folder.
5. Confirm this file exists:

   ```text
   python/plugins/universal_spp_plugin/__init__.py
   ```

6. Confirm the converter exists:

   ```text
   python/plugins/universal_spp_plugin/bin/uspp_tool.exe
   ```

7. In Painter, choose **Python > Reload Plugins Folder**, or restart Painter.
8. In the **Python** menu, make sure `universal_spp_plugin` is enabled.

After the plugin starts, Painter shows a **Universal** menu in the menu bar. The
plugin also tries to add **Save as Universal (.uspp)...** to Painter's **File**
menu.

Optional: if you use a custom plugin root, set
`SUBSTANCE_PAINTER_PLUGINS_PATH` to a folder that contains Painter's expected
`plugins`, `modules`, and `assets` subfolders.

## Use

### Save a `.uspp`

1. Open a project in Painter.
2. Save it normally as a `.spp` first.
3. Choose **Universal > Save as Universal...**.
4. Pick the output `.uspp` path.

The plugin packs from the `.spp` file on disk. If Painter reports unsaved
changes, the plugin tries to save first, but the hard requirement is still that
the project already has a real file path.

### Open a `.uspp` or `.spp`

1. Choose **Universal > Open Universal...**.
2. Pick a `.uspp` or a regular `.spp`.
3. Review the warning if the target conversion is lossy.
4. Confirm the conversion.

When you pick a regular `.spp`, the plugin first packs it into a temporary
`.uspp`. The plugin then detects the running Painter version, asks the converter
for a plan, and builds a temporary `.spp` in the system temp directory under
`USPPCache`. The source `.uspp` or `.spp` is not modified.

If the `.uspp` was authored in an older version than the one you are running,
the converter rebuilds the stored project and lets Painter perform its normal
native upgrade when opening it.

### Check for updates

Choose **Universal > Check for Updates...** to check the public GitHub Releases
page manually. If a newer release is available, the plugin asks before
downloading or installing anything.

The plugin also checks automatically once per day after startup. Automatic
checks only show a prompt; Universal SPP never installs silently. The prompt can
install the update, skip that specific version, remind you later, or turn off
automatic update checks.

Choose **Universal > Update Settings...** to:

- Enable or disable daily update checks.
- Include or exclude prerelease versions.
- Clear a skipped version.
- See when updates were last checked.
- Open the releases page.

When an update is installed, restart Substance 3D Painter to finish using the
new plugin files. Reloading the plugins folder may work, but a restart is the
cleanest path because Painter can cache plugin code.

## How The Plugin Is Written

The plugin is split so the Painter and Qt code stays away from the converter
process code.

| File | Role |
| --- | --- |
| [`__init__.py`](__init__.py) | Painter plugin entry point. Creates menus, actions, event hooks, open/save handlers, temp file paths, and cache cleanup. |
| [`lib/runner.py`](lib/runner.py) | Finds and runs `uspp_tool.exe` or `USPP_TOOL`. Streams progress, captures errors, and never uses shell splitting. |
| [`lib/version.py`](lib/version.py) | Detects the running Painter version from the Painter API when available, then falls back to parsing the install path. |
| [`lib/dialogs.py`](lib/dialogs.py) | Qt dialogs for file picking, errors, info messages, and lossy conversion confirmation. |
| [`lib/progress.py`](lib/progress.py) | Progress dialog wrapper around the long-running converter calls. |
| [`lib/updater.py`](lib/updater.py) | Headless updater logic for settings, GitHub release parsing, ZIP validation, checksum checks, installation, and rollback. |

### Open Flow

`on_open()` asks for a `.uspp` or `.spp`, then `_open_path()` does the real
work:

1. Check that the converter is available.
2. If the selected file is a `.spp`, pack it into a temporary `.uspp`.
3. Detect the running Painter version, such as `10`, `12`, or `12.1`.
4. Run:

   ```text
   uspp_tool.exe plan --uspp <file.uspp> --target <running-version>
   ```

5. Stop if no profile path exists.
6. Show the lossy conversion warning if the plan says the downgrade removes
   unsupported data.
7. Run:

   ```text
   uspp_tool.exe build --uspp <file.uspp> --target <running-version> -o <temp.spp>
   ```

8. Open the temporary `.spp` with `substance_painter.project.open()`.

If Painter refuses to open because another dirty project is loaded, the plugin
asks before closing the current project and retrying.

### Save Flow

`on_save()` is intentionally conservative:

1. Check that a project is open.
2. Check that the project already has a saved `.spp` path.
3. Best-effort save the current project if Painter reports unsaved changes.
4. Ask where to write the `.uspp`.
5. Run:

   ```text
   uspp_tool.exe pack <project.spp> -o <output.uspp>
   ```

Because packing reads the `.spp` file directly, an unsaved temporary Painter
scene cannot be exported until it has been saved once.

### Double-Click Support

On Windows, the plugin tries to register a per-user `.uspp` file association in
`HKCU\Software\Classes`. The command is the currently running Painter executable
with the `.uspp` path as its first argument.

When Painter starts from a double-click, the plugin waits until the GUI is ready,
reads `QApplication.arguments()`, finds the `.uspp` path, and opens it through
the same `_open_path()` flow used by the menu.

This is best effort. It does not require admin rights. If registration is
blocked, the menu still works.

## Developer Mode

Set `USPP_TOOL` to run the plugin against a source checkout instead of the
bundled executable:

```powershell
$env:USPP_TOOL = "C:\path\to\universal-spp\spp_downgrader\uspp_tool.py"
```

If `USPP_TOOL` ends in `.py`, `runner.py` launches it with the current Python
interpreter. If it points to an `.exe`, it runs that executable directly.

Useful rebuild command from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

That command builds `spp_downgrader/dist/uspp_tool.exe` and stages it into
`universal_spp_plugin/bin/uspp_tool.exe`.

## Troubleshooting

| Problem | Check |
| --- | --- |
| **Universal menu does not appear** | Confirm the folder is installed as `python/plugins/universal_spp_plugin/`, then reload plugins and enable it in the **Python** menu. |
| **Converter not found** | Confirm `bin/uspp_tool.exe` exists inside the plugin folder, or set `USPP_TOOL` during development. |
| **Project must be saved first** | Save the Painter project as a `.spp`, then run **Save as Universal...** again. |
| **Conversion is reported as unsupported** | The source version cannot reach the target version through the profiles currently shipped in `spp_downgrader/profiles/`. |
| **Loss warning appears** | The target version is older and cannot represent some source data. The warning is generated from the active downgrade profile. |
| **Double-click does not open `.uspp`** | Open from the **Universal** menu. File association is best effort and can be blocked by system policy. |
| **Update check fails** | Check your internet connection and try **Universal > Check for Updates...** again. Automatic check failures are logged silently. |
| **Updated version does not appear** | Restart Substance 3D Painter. Some Painter sessions keep old plugin code cached after files are replaced. |

Supported conversion profile chain:

```text
12.1 -> 12 -> 11 -> 10 -> 9 -> 8.1
```
