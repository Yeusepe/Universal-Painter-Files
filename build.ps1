# Builds the portable CLI ZIP and optionally stages the converter into the plugin.
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
# CLI only: .\build.ps1 -CliOnly -Python .\.venv\Scripts\python.exe
#
# The PyInstaller spec resolves its data paths from the current directory, so the
# build MUST run from inside spp_downgrader/ — the Push-Location below guarantees it.
param(
    [switch]$CliOnly,
    [string]$Python = "python",
    [string]$NativeCompiler = 'C:\msys64\ucrt64\bin\gcc.exe'
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pythonExe = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
& (Join-Path $root 'native\build.ps1') -Compiler $NativeCompiler
Push-Location (Join-Path $root "spp_downgrader")
try {
    # `python -m PyInstaller` works whether or not the console script is on PATH.
    & $pythonExe -m PyInstaller uspp_tool.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    $dist = Join-Path $root "spp_downgrader\dist\uspp_tool"
    $exe = Join-Path $dist "uspp_tool.exe"
    if (-not (Test-Path -LiteralPath $exe)) { throw "build failed: $exe not found" }
    Copy-Item -LiteralPath (Join-Path $root "LICENSE"), (Join-Path $root "NOTICE.md"), (Join-Path $root "CLI.md") -Destination $dist -Force
    Copy-Item -LiteralPath (Join-Path $root 'native\THIRD_PARTY_NOTICES.txt') -Destination (Join-Path $dist 'Fast-LZMA2-LICENSE.txt') -Force
    $archive = Join-Path $root "spp_downgrader\dist\uspp_tool-windows-x64.zip"
    Compress-Archive -LiteralPath $dist -DestinationPath $archive -Force
    Write-Host "CLI -> $archive"
    if ($CliOnly) { return }
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
