# Author: kaustubh.s.v
# Recruit Desk installer helper: run `uv sync` in one assistant folder so the
# first run is fast. Exit 0 on success, 1 on any failure (the installer then
# skips this step quietly). ASCII only: Windows PowerShell 5.1 reads BOM-less
# files as ANSI.
param([Parameter(Mandatory = $true)][string]$Dir)
$ErrorActionPreference = 'Stop'
try {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
        (Join-Path $env:USERPROFILE '.local\bin') + ';' + (Join-Path $env:USERPROFILE '.cargo\bin')
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { exit 1 }
    Set-Location -LiteralPath $Dir
    & $uv.Source sync --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { exit 1 }
    exit 0
} catch {
    exit 1
}
