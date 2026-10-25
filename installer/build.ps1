param()
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$payload = Join-Path $root 'spp_downgrader\dist\uspp_tool-windows-x64.zip'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $payload)) { throw 'Run build.ps1 -CliOnly first.' }
$output = Join-Path $root 'spp_downgrader\dist\UniversalSPP-Setup.exe'
& $compiler /nologo /target:winexe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/resource:$payload,payload.zip" "/resource:$PSScriptRoot\uninstall.ps1,uninstall.ps1" "/out:$output" "$PSScriptRoot\Setup.cs"
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
Write-Output $output
