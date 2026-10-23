# Author: kaustubh.s.v
# Recruit Desk installer helper. The NSIS prerequisite pages run it to check
# and install what the app needs before any app file is copied.
#
#   rd-prereqs.ps1 check   <node|claude|login|uv>   hidden; prints one line, exit 0 = ready, 1 = not ready
#   rd-prereqs.ps1 install <node|claude|uv>         visible console; does the install, then re-checks
#   rd-prereqs.ps1 login                            visible console; runs `claude` so the user can sign in
#
# The installer creates "<step>.running" next to this script before it starts
# an install or login window; this script deletes it when it ends and writes
# "<step>.failed" when the install did not work. Keep this file ASCII only:
# Windows PowerShell 5.1 reads files without a BOM as ANSI.
param([string]$Action, [string]$Step)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$Manual = @{
    node   = 'Download the LTS installer from https://nodejs.org and run it (winget is not needed)'
    claude = 'npm install -g @anthropic-ai/claude-code   (or: irm https://claude.ai/install.ps1 | iex)'
    login  = 'claude   (then sign in in the browser)'
    uv     = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
}

# Rebuild PATH from the registry (Machine + User) plus the usual install
# folders, so tools installed a moment ago are found without a new login.
function Update-PathFromRegistry {
    $pf = if ($env:ProgramW6432) { $env:ProgramW6432 } else { $env:ProgramFiles }
    $parts = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        (Join-Path $pf 'nodejs'),
        (Join-Path $env:APPDATA 'npm'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs'),
        (Join-Path $env:USERPROFILE '.local\bin'),
        (Join-Path $env:USERPROFILE '.cargo\bin'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps')
    )
    $env:Path = (($parts | Where-Object { $_ }) -join ';')
}

# Run a program found on PATH and return its trimmed output, or $null when it
# is missing or fails. Only real programs (.exe/.cmd), never .ps1 shims.
function Invoke-Tool([string]$Exe, [string[]]$ToolArgs) {
    $cmd = Get-Command $Exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    try { $out = & $cmd.Source @ToolArgs 2>$null | Out-String } catch { return $null }
    if ($LASTEXITCODE -ne 0) { return $null }
    $out = $out.Trim()
    if ($out) { return $out } else { return $null }
}

# Returns a short "ready" description, or $null when the step is not ready.
function Test-Step([string]$s) {
    switch ($s) {
        'node' {
            $n = Invoke-Tool 'node' @('-v'); $m = Invoke-Tool 'npm' @('-v')
            if ($n -and $m) { return "Node.js $n, npm $m" }
        }
        'claude' {
            $v = Invoke-Tool 'claude' @('--version')
            if ($v) { return "Claude Code $v" }
        }
        'login' {
            $o = Invoke-Tool 'claude' @('auth', 'status')
            if ($o) {
                try { if (($o | ConvertFrom-Json).loggedIn -eq $true) { return 'Signed in to Claude' } } catch {}
            }
        }
        'uv' {
            $v = Invoke-Tool 'uv' @('--version')
            if ($v) { return $v }
        }
    }
    return $null
}

function Invoke-Official([string]$Url) {
    # Same as the documented `irm <url> | iex`, in a child PowerShell so the
    # installer script cannot end this one.
    & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm $Url | iex"
}

function Install-Node {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing Node.js LTS with winget...'
        & winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
        Update-PathFromRegistry
        if (Test-Step 'node') { return }
        Write-Host 'winget did not finish; trying the official installer from nodejs.org.'
    }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'x64' }
    $lts = Invoke-RestMethod 'https://nodejs.org/dist/index.json' | Where-Object { $_.lts } | Select-Object -First 1
    $ver = $lts.version
    $msi = Join-Path $env:TEMP "node-$ver-$arch.msi"
    Write-Host "Downloading Node.js $ver ($arch) from nodejs.org..."
    Invoke-WebRequest "https://nodejs.org/dist/$ver/node-$ver-$arch.msi" -OutFile $msi -UseBasicParsing
    Write-Host 'Running the Node.js installer. Windows may ask for permission.'
    Start-Process msiexec.exe -ArgumentList @('/i', "`"$msi`"", '/passive', '/norestart') -Wait
}

function Install-Claude {
    if (Test-Step 'node') {
        Write-Host 'Installing Claude Code with npm...'
        & npm install -g '@anthropic-ai/claude-code'
        Update-PathFromRegistry
        if (Test-Step 'claude') { return }
        Write-Host 'npm did not finish; trying the official installer.'
    }
    Invoke-Official 'https://claude.ai/install.ps1'
}

$Base = Join-Path $PSScriptRoot $Step
Update-PathFromRegistry

switch ($Action) {
    'check' {
        $ok = Test-Step $Step
        if ($ok) { Write-Output $ok; exit 0 }
        exit 1
    }
    'install' {
        Remove-Item "$Base.failed" -ErrorAction SilentlyContinue
        try {
            switch ($Step) {
                'node'   { Install-Node }
                'claude' { Install-Claude }
                'uv'     { Invoke-Official 'https://astral.sh/uv/install.ps1' }
            }
        } catch { Write-Host $_ }
        Update-PathFromRegistry
        $ok = Test-Step $Step
        if (-not $ok) { Set-Content "$Base.failed" 'install did not finish' }
        Remove-Item "$Base.running" -ErrorAction SilentlyContinue
        if ($ok) { Write-Host "Done: $ok. You can go back to the installer."; Start-Sleep -Seconds 3; exit 0 }
        Write-Host ''
        Write-Host 'The install did not finish. Run this by hand in PowerShell, then press Check again:'
        Write-Host "  $($Manual[$Step])"
        Read-Host 'Press Enter to close this window'
        exit 1
    }
    'login' {
        $Base = Join-Path $PSScriptRoot 'login'
        Write-Host 'Claude Code will start now. Sign in in the browser when it asks.'
        Write-Host 'When you are signed in, type /exit here and go back to the installer.'
        try { & claude } finally { Remove-Item "$Base.running" -ErrorAction SilentlyContinue }
        exit 0
    }
}
exit 1
