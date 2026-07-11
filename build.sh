#!/usr/bin/env bash
set -euo pipefail

# Build the native Linux converter and stage it beside the Painter plugin.
# PyInstaller produces native binaries, so run this script with a Linux Python.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${PYTHON:-python3}"

if ! command -v objdump >/dev/null 2>&1; then
    printf 'error: objdump is required; install your distribution binutils package\n' >&2
    exit 2
fi

cd "$root/spp_downgrader"
"$python_bin" -m PyInstaller uspp_tool.spec --noconfirm
install -Dm755 dist/uspp_tool "$root/universal_spp_plugin/bin/uspp_tool"
printf 'OK -> universal_spp_plugin/bin/uspp_tool\n'
