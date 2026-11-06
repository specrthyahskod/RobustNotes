$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

python -m pip install -r (Join-Path $PSScriptRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install the Windows packaging dependencies." }

python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "RobustNotes.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not build RobustNotes." }

$compilerCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$compilerPath = if ($compilerCommand) { $compilerCommand.Source } else { $null }
if (-not $compilerPath) {
    $programFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
    $compilerCandidates = @(
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $programFilesX86 "Inno Setup 7\ISCC.exe"),
        (Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe")
    )
    $compilerPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $compilerPath) {
    throw "The app bundle is in dist\RobustNotes. Install Inno Setup 6 or 7 to create the Windows installer."
}
& $compilerPath (Join-Path $PSScriptRoot "RobustNotes.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup could not create the installer." }
Write-Host "Installer created in packaging\output."
