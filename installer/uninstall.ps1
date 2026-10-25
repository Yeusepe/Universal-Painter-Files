$ErrorActionPreference = 'Stop'
$target = $PSScriptRoot
$expected = Join-Path $env:LOCALAPPDATA 'Programs\UniversalSPP'
if ([IO.Path]::GetFullPath($target) -ne [IO.Path]::GetFullPath($expected)) {
    throw 'Uninstaller must run from the UniversalSPP install folder.'
}
if (Test-Path -LiteralPath (Join-Path $target '.path-added')) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    try {
        $path = $key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $parts = $path.Split(';') | Where-Object { $_.Trim().TrimEnd('\') -ine $target }
        $key.SetValue('Path', ($parts -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    } finally { $key.Dispose() }
}
$manifest = Join-Path $target 'installed-files.txt'
foreach ($relative in (Get-Content -LiteralPath $manifest)) {
    $file = [IO.Path]::GetFullPath((Join-Path $target $relative))
    if (-not $file.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid installed file path.' }
    if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
}
Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\UniversalSPP' -Force
foreach ($name in @('.path-added', 'installed-files.txt', 'uninstall.ps1')) {
    $file = Join-Path $target $name
    if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
}
Get-ChildItem -LiteralPath $target -Directory -Recurse | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
    if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) { Remove-Item -LiteralPath $_.FullName }
}
if (-not (Get-ChildItem -LiteralPath $target -Force)) { Remove-Item -LiteralPath $target }
