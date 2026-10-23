# Author: kaustubh.s.v
# Recruit Desk installer helper: run `uv sync` in one assistant folder so the
# first run is fast, then create that assistant's empty Excel file in its results
# folder (-WorkbookDir) when it does not exist yet. Exit 0 on success, 1 when the
# sync fails (the installer then skips this step quietly). A failure to create the
# Excel file does not fail the step: the assistant creates it at the start of its
# first run. ASCII only: Windows PowerShell 5.1 reads BOM-less files as ANSI.
param([Parameter(Mandatory = $true)][string]$Dir, [string]$WorkbookDir = '')
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
    if ($WorkbookDir) {
        try {
            $env:RECRUIT_DESK_WORKBOOK_DIR = $WorkbookDir
            & $uv.Source run --quiet python scripts/init_workbook.py 2>&1 | Out-Null
        } catch { }
    }
    exit 0
} catch {
    exit 1
}
