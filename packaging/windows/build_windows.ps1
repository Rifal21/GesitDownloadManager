<#
.SYNOPSIS
  Build the Gesit Windows installer and portable zip.   Run from anywhere on Windows 10/11:

      powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1

.PARAMETER NoTools     Do not download ffmpeg / yt-dlp (the installer then has no video-site support).
.PARAMETER SkipTests   Skip pytest.
.PARAMETER SkipInstaller  Only build the PyInstaller folder and the portable zip.

Downloads (checksum-verified): yt-dlp.exe from github.com/yt-dlp/yt-dlp releases and an LGPL build of ffmpeg
from github.com/BtbN/FFmpeg-Builds. Requires: Python 3.11+ on PATH; Inno Setup 6 (iscc.exe) for the installer.
#>
param(
    [switch]$NoTools,
    [switch]$SkipTests,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Version = (Select-String -Path "gesit\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "==> Gesit $Version"

$Build = Join-Path $Root "build\windows"
Remove-Item -Recurse -Force $Build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Build | Out-Null
$Venv = Join-Path $Build "venv"

Write-Host "==> virtual environment"
python -m venv $Venv
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet "PySide6-Essentials==6.12.0" httpx pyinstaller pytest pytest-asyncio
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not $SkipTests) {
    Write-Host "==> tests"
    & $Py -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Write-Host "==> browser extensions"
& $Py extension\build.py --store
if ($LASTEXITCODE -ne 0) { throw "extension build failed" }

Write-Host "==> PyInstaller"
& $Py -m PyInstaller --noconfirm --distpath (Join-Path $Build "dist") --workpath (Join-Path $Build "work") packaging\windows\gesit.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$App = Join-Path $Build "dist\Gesit"

function Get-Verified($Url, $Dest, $SumsUrl, $SumName) {
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    $sums = (Invoke-WebRequest -Uri $SumsUrl -UseBasicParsing).Content
    if ($sums -is [byte[]]) { $sums = [System.Text.Encoding]::UTF8.GetString($sums) }  # served as octet-stream
    $line = ($sums -split "`n" | Where-Object { $_ -match [regex]::Escape($SumName) } | Select-Object -First 1)
    if (-not $line) { throw "no checksum for $SumName in $SumsUrl" }
    $expected = ($line.Trim() -split "\s+")[0].ToLower()
    $actual = (Get-FileHash $Dest -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "checksum mismatch for $SumName (expected $expected, got $actual)" }
    Write-Host "    verified $SumName"
}

$Tools = Join-Path $Build "tools"
if (-not $NoTools) {
    Write-Host "==> ffmpeg and yt-dlp"
    New-Item -ItemType Directory -Force $Tools | Out-Null
    Get-Verified "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $Tools "yt-dlp.exe") `
        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS" "yt-dlp.exe"
    $zip = Join-Path $Build "ffmpeg.zip"
    $ff = "ffmpeg-master-latest-win64-lgpl.zip"
    Get-Verified "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$ff" $zip `
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256" $ff
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Build "ffmpeg") -Force
    Copy-Item (Get-ChildItem (Join-Path $Build "ffmpeg") -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName $Tools
}

Write-Host "==> portable zip"
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $Dist | Out-Null
$Stage = Join-Path $Build "portable\Gesit"
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item "$App\*" $Stage -Recurse
Copy-Item "extension\dist\chrome" (Join-Path $Stage "extension\chrome") -Recurse
Copy-Item "extension\dist\firefox" (Join-Path $Stage "extension\firefox") -Recurse
if (Test-Path $Tools) { Copy-Item $Tools (Join-Path $Stage "tools") -Recurse }
Copy-Item LICENSE $Stage
Compress-Archive -Path $Stage -DestinationPath (Join-Path $Dist "Gesit-$Version-windows-x64-portable.zip") -Force

if (-not $SkipInstaller) {
    Write-Host "==> installer (Inno Setup)"
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $p) { $iscc = $p; break }
        }
    }
    if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
    & $iscc "/DAppVersion=$Version" packaging\windows\gesit.iss
    if ($LASTEXITCODE -ne 0) { throw "iscc failed" }
}

Write-Host "==> checksums"
Push-Location $Dist
Get-ChildItem "Gesit-$Version-windows-*" | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
} | Set-Content -Encoding ascii "SHA256SUMS-windows.txt"
Pop-Location
Get-ChildItem $Dist | Where-Object { $_.Name -like "Gesit-$Version-windows-*" -or $_.Name -like "SHA256SUMS-windows*" } | Format-Table Name, Length
