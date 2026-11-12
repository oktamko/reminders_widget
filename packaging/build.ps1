# Build the app folder, and with -Installer the Inno Setup installer.
#   powershell -ExecutionPolicy Bypass -File packaging/build.ps1 [-Installer]
param([switch]$Installer)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$version = & $python -c "import sys; sys.path.insert(0, 'src'); from reminders_widget.project import VERSION; print(VERSION)"
& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/reminders_widget.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if ($Installer) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
    & $iscc "/DAppVersion=$version" packaging/installer.iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
}
Write-Host "Built Reminders Widget $version"
