$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

python -m pip install -r (Join-Path $PSScriptRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install the Windows packaging dependencies." }

python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "RobustNotes.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not build RobustNotes." }

$compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $compiler) {
    throw "The app bundle is in dist\RobustNotes. Install Inno Setup 6 and add ISCC.exe to PATH to create the installer."
}
& $compiler.Source (Join-Path $PSScriptRoot "RobustNotes.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup could not create the installer." }
Write-Host "Installer created in packaging\output."
