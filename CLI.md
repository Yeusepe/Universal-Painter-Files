# Batch conversion

Extract the entire portable ZIP before you run `uspp_tool.exe`.
Keep the executable beside its `_internal` folder. You do not need Python or Painter to run the executable.

To convert every `.spp` in a folder, run:

```powershell
.\uspp_tool.exe batch-pack "A:\Projects"
```

To include subfolders, add `--recursive`:

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" --recursive
```

You can supply several files or folders. Quote paths that contain spaces.
The command accepts literal paths, not wildcard patterns.
It finds `.spp` files without regard to letter case.
It does not follow directory links inside a scanned folder.
If inputs overlap, the command processes each source once. The first input determines its output path.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects\First.spp" "A:\Other projects"
```

## Output files

By default, the command writes `name.uspp` beside `name.spp`.
Use `--output-dir` to write the archives elsewhere.
Each folder input keeps its relative subfolder paths beneath the output folder.
An explicit file input goes directly into the output folder.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r -o "A:\Universal"
```

For example, `A:\Projects\Character\Body.spp` becomes `A:\Universal\Character\Body.uspp`.
If different sources map to the same output, the command reports those files as failures.
It continues with the remaining files.

If an output already exists, the command reports a failure and continues.
Use `--overwrite` to replace existing outputs after the new conversion succeeds.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --overwrite
```

## Preview a batch

Add `--dry-run` to preview output paths without changing files.
The preview also lists replacements and source deletions when you supply `--overwrite` or `--delete-source`.
It reports detected errors and continues with the remaining files.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --dry-run --delete-source
```

A dry run does not create folders, write archives, or delete sources.
It finds inputs and detects output collisions and existing outputs.
It does not inspect project contents or test write permissions.
The summary counts planned conversions, not successful conversions.
Remove `--dry-run` to perform the conversion.

## Delete sources after conversion

The command keeps all source files by default.
If you want to delete each source after conversion, add `--delete-source`.
This option permanently deletes the source `.spp`. It does not use the Recycle Bin.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --delete-source
```

The command deletes a source only after its archive passes an integrity check and the output is saved.
It keeps sources when conversion fails, an output already exists, or a source changes during conversion.
If deletion fails, the command keeps the archive, reports the error, and continues.

The archive uses the existing Universal SPP format. It excludes Painter's texture cache, which Painter rebuilds when needed.
Batch conversion does not capture raster fallbacks through Painter.

## Results and exit codes

The command reports each file and prints final success and failure counts.
File errors do not stop the batch. Press Ctrl+C to cancel.
Add `--verbose` to see conversion details and Python error traces.

| Exit code | Meaning |
| --- | --- |
| `0` | Every project succeeded, or the dry run detected no errors. |
| `1` | At least one project or input failed. Other projects were still processed. |
| `2` | The command syntax was invalid, or no matching projects were found. |
| `130` | You cancelled the batch. |

## Run or build from source

From the repository root, install dependencies in a Windows Python 3.11 or newer virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe spp_downgrader\uspp_tool.py batch-pack "A:\Projects" -r
.\build.ps1 -CliOnly -Python .\.venv\Scripts\python.exe
```

The portable ZIP is `spp_downgrader\dist\uspp_tool-windows-x64.zip`.
Omit `-CliOnly` to also stage the executable into the Painter plugin.
