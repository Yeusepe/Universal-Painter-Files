# Batch conversion

Use `--timings DIR` with `pack` or `batch-pack` to measure where time is spent.
Each file produces a JSON report and a live `.events.jsonl` file in that directory.
Reports include wall time, CPU time, dataset reads, archive writes, finalization, verification, publishing, and peak memory on Windows.
Timing spans are nested. Do not add parent and child times together.
The probes do not change compression settings or archive contents.

```powershell
uspp_tool pack "A:\Projects\Body.spp" --compress --timings "A:\Profiles"
```

For implementation comparisons, run the developer benchmark from the repository root:

```powershell
.venv\Scripts\python.exe scripts/benchmark_pipeline.py "A:\Projects\Body.usppz" --directory "A:\Profiles\pipeline" --repeats 2
.venv\Scripts\python.exe scripts/benchmark_pipeline.py "A:\Projects\Body.usppz" --directory "A:\Profiles\pipeline" --follow-up --repeats 2
```

The first command requires a new output directory. It prepares a verified payload corpus, then compares encoder threads,
buffering, file input, Deflate, decoder threads, and full-verification output handling.
The second command reuses that corpus to test combined candidates and decoder read buffers.
Each run uses a fresh process. Repacked archives receive full verification and a SHA-256 check of every member.
Results include JSON summaries, CPU profiles, wall time, and peak memory. Candidate changes apply only to benchmark processes.
These warm-cache payload tests exclude source `.spp` extraction and batch-worker throughput. Benchmark archives retain the original
metadata for member-by-member comparison; their actual encoder settings are recorded in the results.

The Windows installer adds two actions to the `.spp` right-click menu:
`Convert to Universal Substance Painter` creates a `.uspp` archive.
`Convert to Universal Substance Painter Compressed` creates a `.usppz` archive with LZMA2 compression.
Both actions keep the source and save the archive beside it. Existing archives are kept and reported as errors.
Two additional actions, **Replace original with Universal Substance Painter** and **Replace original with Universal Substance Painter Compressed**,
fully verify the new archive, then move the original `.spp` to the Recycle Bin.
If a destination archive already exists, these replacement actions move it to the Recycle Bin as well.
A progress window shows the current stage and elapsed time.
The window shows error details if conversion fails. Select **Open folder** after a successful conversion to locate the archive.
On Windows 11, select **Show more options** to find these actions.
The uninstaller removes both actions.

A worker processes one project at a time in a separate process.
Use `--workers N` to pack and compress several files at once:

```powershell
uspp_tool batch-pack "A:\Projects" -r --compress --workers 2
```

The default is one worker. The allowed range is 1 to 61.
Each worker needs its own memory for the project and compression.
Start with two workers. Reduce the count if memory or disk use is too high.
Workers keep the selected compression configuration and verify each archive before optional source deletion.
The command checks output collisions before it starts workers.
Results appear as files finish. With `--verbose`, each file's log appears after that file finishes.
Dry runs remain sequential and do not start workers.
Press Ctrl+C to stop scheduling files. Active files finish before the command exits with code 130.

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

## Archive formats

The CLI writes `.uspp` by default. This format uses ZIP with Deflate level 1.
Use `--compress` to write `.usppz` archives in a 7z container with LZMA2 compression.
The recommended configuration uses preset 6 with extreme mode off.
Fast LZMA2 now encodes the project data. The 7z container and existing archive readers remain compatible.
Plain LZMA2 data also uses the native multithreaded decoder during reading and verification.
py7zr still checks member checksums and validates project references. Other codec chains use their existing readers.
The decoder uses up to eight threads and caps its parallel buffers at 1 GiB per active archive;
dictionary, cache, and Python buffers use additional memory. It falls back to one thread if the parallel buffer limit is reached.
The portable executable includes the native library and its license.
Automatic compression uses up to 24 threads and double buffering. Batch workers share the available CPU-thread budget.
Completed in-memory member buffers are released as soon as they have been written.
Full verification retains metadata and discards checked payload bytes, while still checking every member CRC.
Each project gets its own solid stream, which shares compression history between archive members.
The executable includes the Python library for 7z. You do not need to install 7-Zip.

To use LZMA2 in a batch:

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --compress
```

For a single project, `pack` can choose the output name automatically:

```powershell
.\uspp_tool.exe pack "A:\Projects\Body.spp"
```

This command writes `A:\Projects\Body.uspp`.
If you specify an output filename, its extension selects the format.
The command rejects `--compress` with an explicit `.uspp` output.

```powershell
.\uspp_tool.exe pack "A:\Projects\Body.spp" -o "A:\Universal\Body.uspp"
```

The Painter plugin continues to request `.uspp` files, so its existing save workflow keeps the ZIP format.
The plugin opens `.usppz` files through its file dialog and Windows file associations.
The existing `--uspp` argument accepts both formats:

```powershell
.\uspp_tool.exe info --uspp "A:\Universal\Body.usppz"
.\uspp_tool.exe plan --uspp "A:\Universal\Body.usppz" --target 10
.\uspp_tool.exe build --uspp "A:\Universal\Body.usppz" --target 10 -o "A:\Universal\Body_v10.spp"
```

Compression preserves the same archive members and dataset payloads.
Both formats omit Painter's texture cache, which Painter rebuilds when needed.
Neither format captures raster fallbacks through Painter during a batch.

## Output files

By default, the batch command writes `name.uspp` beside `name.spp`.
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
The single-file `pack` command retains its existing replacement behavior.
It replaces an existing output only after the new archive passes validation.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --overwrite
```

## Advanced LZMA2 configuration

Select LZMA2 with `--compress` or an explicit `.usppz` output filename.
Use `--advanced-config` with one quoted, comma-separated string.
The selected preset supplies the initial configuration. Individual properties override that preset.
The CLI prints the selected preset and overrides before it processes files.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --compress `
  --advanced-config "preset=9,extreme=true,dict_size=256MiB,nice_len=273,mf=bt4"
```

The CLI reports the selected encoder. Fast LZMA2 archive metadata records its version, effective dictionary size, and thread count.
Fast LZMA2 uses its own preset table. Preset `0` selects its fastest level, `1`; presets `1` through `9` keep their level numbers.
Extreme mode selects its high-compression table. Preset numbers do not promise identical output between different encoder implementations.
`mode`, `mf`, and `depth` select the original liblzma encoder because Fast LZMA2 cannot preserve those controls.
Dictionaries below 1 MiB or above 1 GiB, and `nice_len` below `6`, also use liblzma.
This fallback is printed explicitly and keeps existing advanced configurations valid.
Explicit multi-threading is rejected for configurations that require liblzma.
Larger dictionaries and extreme mode can increase memory use and compression time.
Compression gains depend on the project data. Start with the recommended preset before you compare custom configurations.

| Key | Accepted values | Default |
| --- | --- | --- |
| `preset` | Integer `0` through `9` | `6` |
| `extreme` | `true` or `false` | `false` |
| `threads` | Integer `0` through `64`; `0` selects automatic thread budgeting | `0` |
| `dict_size` | Integer bytes, or an integer with `KiB`, `MiB`, or `GiB`; 4 KiB through 1536 MiB | From the preset |
| `mode` | `fast`, `normal` | From the preset |
| `mf` | `hc3`, `hc4`, `bt2`, `bt3`, `bt4` | From the preset |
| `nice_len` | Integer `2` through `273`; the target match length | From the preset |
| `depth` | Integer `0` through `4294967295`; `0` selects the search depth automatically | From the preset |
| `lc` | Integer `0` through `4`; literal context bits | `3` |
| `lp` | Integer `0` through `4`; literal position bits | `0` |
| `pb` | Integer `0` through `4`; position bits | `2` |

The sum of `lc` and `lp` must be at most `4`.
For example, if you set `lp=2`, also set `lc=2` or lower.
Size units accept either letter case. Fractional sizes, such as `0.5GiB`, are not accepted. Use `512MiB` instead.

Unknown keys, duplicate keys, and invalid values stop the command before it processes any project.
Advanced configuration requires LZMA2 output. Select `--compress` or an explicit `.usppz` output to use it.
Thread counts, solid-block sizes, and I/O buffer sizes are not exposed by this interface.

The archive records the preset and overrides for later inspection with `info`.
The reported container and compression methods come from the archive itself.
Older archives can omit the saved configuration.

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
The preview also prints the compression configuration without allocating an encoder.
Remove `--dry-run` to perform the conversion.

## Delete sources after conversion

`--overwrite` and `--delete-source` both use the Recycle Bin for disposal.
With `--overwrite`, the command verifies the new archive, recycles the old output archive, and saves the new archive at the same path.
With `--delete-source`, the command recycles the original `.spp` only after the new archive is verified and saved.
Use both flags to recycle both old files. The new archive stays beside the source or in the selected output folder.
If the old output cannot be recycled, the command keeps both old files and reports an error.
If saving fails after the old output was recycled, the source stays in place and the old output remains recoverable from the Recycle Bin.

The command keeps all source files by default.
If you want to delete each source after conversion, add `--delete-source`.
This option moves the source `.spp` to the Recycle Bin. It never falls back to permanent deletion.
If recycling fails, the command keeps the source and archive, reports the error, and continues.
Disk space is reclaimed only after you empty the Recycle Bin.

```powershell
.\uspp_tool.exe batch-pack "A:\Projects" -r --delete-source
```

The command deletes a source only after all archive members pass integrity checks and the output is saved.
It also validates dataset references, raster references, and the recorded source Painter version.
It keeps sources when conversion fails, an output already exists, the source version is missing, or a source changes during conversion.
If deletion fails, the command keeps the archive, reports the error, and continues.

Reading `.usppz` for planning, rebuilding, or full verification uses a temporary cache of expanded members on disk.
Allow disk space for the expanded project data. The tool removes the cache when the operation ends.
The `info` command extracts only the manifest and version metadata.

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
