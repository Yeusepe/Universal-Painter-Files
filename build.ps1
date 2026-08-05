# Builds the onedir converter and stages it into the plugin's bin/.
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
    $dist = Join-Path $root "spp_downgrader\dist\uspp_tool"
    $exe = Join-Path $dist "uspp_tool.exe"
    if (-not (Test-Path -LiteralPath $exe)) { throw "build failed: $exe not found" }
    $bin = Join-Path $root "universal_spp_plugin\bin"
    $target = Join-Path $bin "uspp_tool"
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    if (Test-Path -LiteralPath $target) {
        $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
        $resolvedBin = (Resolve-Path -LiteralPath $bin).Path
        if (-not $resolvedTarget.StartsWith($resolvedBin + [IO.Path]::DirectorySeparatorChar)) {
            throw "refusing to replace converter outside plugin bin: $resolvedTarget"
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
    Copy-Item -LiteralPath $dist -Destination $target -Recurse
    $legacy = Join-Path $bin "uspp_tool.exe"
    if (Test-Path -LiteralPath $legacy) {
        $resolvedLegacy = (Resolve-Path -LiteralPath $legacy).Path
        $resolvedBin = (Resolve-Path -LiteralPath $bin).Path
        if (-not $resolvedLegacy.StartsWith($resolvedBin + [IO.Path]::DirectorySeparatorChar)) {
            throw "refusing to remove legacy converter outside plugin bin: $resolvedLegacy"
        }
        Remove-Item -LiteralPath $resolvedLegacy -Force
    }
    Write-Host "OK -> universal_spp_plugin\bin\uspp_tool\uspp_tool.exe"
} finally {
    Pop-Location
}
