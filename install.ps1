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

# The shortcut opens the GUI. Prefer pythonw.exe so no console window flashes
# up behind it: first the one beside the installed command (pipx's venv), then
# whatever pythonw is on PATH. If there is none, fall back to a console launch.
$scripts = if ($exe) { Split-Path $exe } else { $null }
$pythonw = $null
if ($scripts -and (Test-Path (Join-Path $scripts "pythonw.exe"))) {
    $pythonw = Join-Path $scripts "pythonw.exe"
} else {
    $pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
}

function New-MarksmanShortcut($path) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($path)
    if ($pythonw) {
        $sc.TargetPath = $pythonw
        $sc.Arguments = "-m marksman gui"
    } else {
        $sc.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $sc.Arguments = "-Command `"$launch gui`""
    }
    $sc.WorkingDirectory = $dataDir
    $sc.Description = "Marksman - airsoft marksmanship drills and progress"
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
Write-Host "Done. Launch from the Start Menu (search 'Marksman') to open the app," -ForegroundColor Cyan
Write-Host "or run 'marksman --help' for the command line." -ForegroundColor Cyan
Write-Host "Your data lives in $dataDir" -ForegroundColor DarkGray
