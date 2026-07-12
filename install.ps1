<#
    Marksman installer (Windows).

    - installs the `marksman` command for the current user (pipx if available, else pip --user)
    - generates the logo/icon
    - creates a Start Menu launch shortcut (and a Desktop one with -Desktop)

    Usage:
        powershell -ExecutionPolicy Bypass -File .\install.ps1
        powershell -ExecutionPolicy Bypass -File .\install.ps1 -Desktop
#>
param([switch]$Desktop)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Installing marksman..." -ForegroundColor Cyan
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if ($pipx) {
    pipx install --force "$root"
} else {
    python -m pip install --user "$root"
}

# Resolve the installed command; fall back to `python -m marksman`.
$exe = (Get-Command marksman -ErrorAction SilentlyContinue).Source
if ($exe) { $launch = "& `"$exe`"" } else { $launch = "python -m marksman" }

# A stable home for the data file (marksman_data.json) and generated assets.
$dataDir = Join-Path $env:USERPROFILE "Marksman"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# Generate the icon into the data dir (so the shortcut always finds it).
Write-Host "Generating logo/icon..." -ForegroundColor Cyan
Push-Location $dataDir
try { Invoke-Expression "$launch --no-color logo --out `"$dataDir\assets`"" } finally { Pop-Location }
$ico = Join-Path $dataDir "assets\marksman.ico"

function New-MarksmanShortcut($path) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($path)
    $sc.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $sc.Arguments = "-NoExit -Command `"$launch --help`""
    $sc.WorkingDirectory = $dataDir
    $sc.Description = "Marksman - airsoft marksmanship tracker"
    if (Test-Path $ico) { $sc.IconLocation = $ico }
    $sc.Save()
    Write-Host "  shortcut: $path" -ForegroundColor Green
}

$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-MarksmanShortcut (Join-Path $startDir "Marksman.lnk")
if ($Desktop) {
    New-MarksmanShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Marksman.lnk")
}

Write-Host ""
Write-Host "Done. Launch from the Start Menu (search 'Marksman'), or run 'marksman --help'." -ForegroundColor Cyan
Write-Host "Your data lives in $dataDir" -ForegroundColor DarkGray
