# Universal SPP

Universal SPP is an unofficial interoperability tool for project files used by
Adobe Substance 3D Painter software. It helps move `.spp` projects between
installed versions by downgrading a copy of a newer project into a form an older
application version can open.

It ships as two pieces:

| Component | What it is |
| --- | --- |
| [`spp_downgrader/`](spp_downgrader/README.md) | The conversion engine: a standalone Python CLI (`uspp_tool.py`) that converts a `.spp` project down to any target across versions 8.1-12.1. No Adobe Substance 3D Painter installation is required for the CLI conversion step. |
| [`universal_spp_plugin/`](universal_spp_plugin/README.md) | A Python plugin for use with Adobe Substance 3D Painter software. It adds a Universal menu: Save as Universal exports a portable `.uspp`; Open Universal converts one to your installed version on the fly. |

A `.uspp` file is a ZIP archive containing the project data plus a manifest that
records the version it was authored in. Author from your newest installed version;
others can open a downgraded copy in theirs.

> [!IMPORTANT]
> Universal SPP is not affiliated with, endorsed by, sponsored by, or authorized
> by Adobe. It does not include Adobe software, logos, product icons,
> documentation, sample project files, or license/activation bypasses. Use it only
> with project files you own or have permission to process. Conversion is lossy
> and one-way; always keep a backup of the original project. See
> [NOTICE.md](NOTICE.md), [CONTRIBUTING.md](CONTRIBUTING.md), and
> [LICENSE](LICENSE).

## Just want to use the plugin?

1. Download `uspp_tool.exe` from the [Releases](../../releases) page and put it in
   `universal_spp_plugin/bin/`.
2. Follow [`universal_spp_plugin/README.md`](universal_spp_plugin/README.md) to copy
   the plugin into the application's `python/plugins/` folder.

Supported application versions: 8.1-12.1 on Windows.

## Build it yourself

The plugin calls a self-contained `uspp_tool.exe`, so client machines do not need
Python. Build it from source on Windows:

```powershell
git clone <your-fork-url> universal-spp
cd universal-spp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1` runs PyInstaller and stages the result at
`universal_spp_plugin/bin/uspp_tool.exe`, ready to install.

To run the converter directly from source, see
[`spp_downgrader/README.md`](spp_downgrader/README.md). It documents the CLI, the
HBO/HDF5 internals, and how to add support for a new application version.

## Requirements

- Windows and Python 3.11+.
- Runtime: `h5py`, `numpy`, `mmh3`, `PyYAML` (`requirements.txt`).
- Build/maintainer: `pyinstaller`, `py7zr`, `minidump` (`requirements-dev.txt`).

## Layout

```text
universal-spp/
|-- spp_downgrader/        # conversion engine and CLI
|-- universal_spp_plugin/  # Python plugin
|-- build.ps1              # build uspp_tool.exe into plugin bin/
|-- requirements.txt       # runtime dependencies
`-- requirements-dev.txt   # build and maintainer dependencies
```

## License

MIT. See [LICENSE](LICENSE). Third-party trademark and usage notices are in
[NOTICE.md](NOTICE.md). Copyright and contribution rules are in
[CONTRIBUTING.md](CONTRIBUTING.md).
