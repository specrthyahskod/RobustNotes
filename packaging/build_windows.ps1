$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$appVersionMatch = [regex]::Match((Get-Content (Join-Path $projectRoot "canvas_app.py") -Raw), '(?m)^APP_VERSION\s*=\s*"([^"]+)"\s*$')
$installerVersionMatch = [regex]::Match((Get-Content (Join-Path $PSScriptRoot "RobustNotes.iss") -Raw), '(?m)^#define AppVersion "([^"]+)"\s*$')
if (-not $appVersionMatch.Success -or -not $installerVersionMatch.Success) {
    throw "Could not read the app and installer version values."
}
$appVersion = $appVersionMatch.Groups[1].Value
$installerVersion = $installerVersionMatch.Groups[1].Value
if ($appVersion -ne $installerVersion) {
    throw "Version mismatch: canvas_app.py is $appVersion but RobustNotes.iss is $installerVersion."
}
$legacyOnedirPath = Join-Path $projectRoot "dist\RobustNotes"
$singleFilePath = Join-Path $projectRoot "dist\RobustNotes.exe"

python -m pip install -r (Join-Path $PSScriptRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install the Windows packaging dependencies." }

# This build is intentionally a single-file executable, so a launcher copied
# out of an old onedir folder can never be picked up by the installer.
if (Test-Path -LiteralPath $legacyOnedirPath) {
    Remove-Item -LiteralPath $legacyOnedirPath -Recurse -Force
}
if (Test-Path -LiteralPath $singleFilePath) {
    Remove-Item -LiteralPath $singleFilePath -Force
}

python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "RobustNotes.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not build RobustNotes." }
if (-not (Test-Path -LiteralPath $singleFilePath -PathType Leaf)) {
    throw "PyInstaller did not create dist\RobustNotes.exe."
}

python (Join-Path $PSScriptRoot "verify_windows_bundle.py") $singleFilePath
if ($LASTEXITCODE -ne 0) { throw "The Windows executable is missing required bundled runtime files." }

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
    throw "The executable is in dist\RobustNotes.exe. Install Inno Setup 6 or 7 to create the Windows installer."
}
& $compilerPath (Join-Path $PSScriptRoot "RobustNotes.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup could not create the installer." }
Write-Host "Installer created in packaging\output."
