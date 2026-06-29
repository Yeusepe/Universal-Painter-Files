# Builds spp_downgrader/uspp_tool.exe and stages it into the plugin's bin/.
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
#
# The PyInstaller spec resolves its data paths from the current directory, so the
# build MUST run from inside spp_downgrader/ — the Push-Location below guarantees it.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Push-Location (Join-Path $root "spp_downgrader")
try {
    # `python -m PyInstaller` works whether or not the console script is on PATH.
    python -m PyInstaller uspp_tool.spec --noconfirm
    $exe = Join-Path $root "spp_downgrader\dist\uspp_tool.exe"
    if (-not (Test-Path $exe)) { throw "build failed: $exe not found" }
    $bin = Join-Path $root "universal_spp_plugin\bin"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    Copy-Item $exe (Join-Path $bin "uspp_tool.exe") -Force
    Write-Host "OK -> universal_spp_plugin\bin\uspp_tool.exe"
} finally {
    Pop-Location
}
