param([string]$Compiler = 'C:\msys64\ucrt64\bin\gcc.exe')
$ErrorActionPreference = 'Stop'
$compilerPath = (Get-Command $Compiler -CommandType Application -ErrorAction Stop).Source
$outputDir = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$output = Join-Path $outputDir 'fast-lzma2.dll'
$sources = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'fast-lzma2') -Filter '*.c' | ForEach-Object FullName)
& $compilerPath -O3 -DNDEBUG -DFL2_DLL_EXPORT=1 -shared -static-libgcc @sources -o $output
if ($LASTEXITCODE -ne 0) { throw 'Fast LZMA2 native build failed.' }
Write-Output $output
