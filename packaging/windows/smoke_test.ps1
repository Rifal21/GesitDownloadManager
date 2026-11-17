<#
.SYNOPSIS
  End-to-end test of the Windows installer: silent install, native messaging registration, browser-style
  native host call that launches the app and downloads a file, quit, silent uninstall.
  Run after build_windows.ps1 (it finds the newest Gesit-*-setup.exe in dist\).
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Setup = Get-ChildItem (Join-Path $Root "dist") -Filter "Gesit-*-windows-x64-setup.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $Setup) { throw "no installer found in dist\" }
$Dir = Join-Path $env:RUNNER_TEMP "gesit-smoke"
if (-not $env:RUNNER_TEMP) { $Dir = Join-Path $env:TEMP "gesit-smoke" }
Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue

function Step($msg) { Write-Host "==> $msg" }
function Fail($msg) { Write-Host "FAILED: $msg"; exit 1 }

Step "silent install $($Setup.Name)"
$p = Start-Process -FilePath $Setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$Dir`"", "/TASKS=integration" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "installer exit code $($p.ExitCode)" }
foreach ($f in "gesit.exe", "gesit-native-host.exe", "extension\chrome\manifest.json", "extension\firefox\manifest.json") {
    if (-not (Test-Path (Join-Path $Dir $f))) { Fail "missing $f" }
}
$Gesit = Join-Path $Dir "gesit.exe"
$HostExe = Join-Path $Dir "gesit-native-host.exe"

Step "native messaging registration"
$regs = @{
    "Chrome"  = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\app.gesit.host"
    "Edge"    = "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\app.gesit.host"
    "Firefox" = "HKCU:\Software\Mozilla\NativeMessagingHosts\app.gesit.host"
}
foreach ($k in $regs.Keys) {
    $manifest = (Get-ItemProperty $regs[$k] -ErrorAction Stop).'(default)'
    if (-not (Test-Path $manifest)) { Fail "$k manifest file $manifest does not exist" }
    $json = Get-Content $manifest -Raw | ConvertFrom-Json
    if ($json.path -ne $HostExe) { Fail "$k manifest points at $($json.path), expected $HostExe" }
    Write-Host "    $k -> $manifest"
}

function Invoke-Host($Message) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo $HostExe
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $body = [System.Text.Encoding]::UTF8.GetBytes(($Message | ConvertTo-Json -Compress))
    $in = $proc.StandardInput.BaseStream
    $in.Write([BitConverter]::GetBytes([int]$body.Length), 0, 4)
    $in.Write($body, 0, $body.Length)
    $in.Flush()
    $in.Close()
    $out = $proc.StandardOutput.BaseStream
    $hdr = New-Object byte[] 4
    if ($out.Read($hdr, 0, 4) -ne 4) { Fail "native host returned nothing" }
    $len = [BitConverter]::ToInt32($hdr, 0)
    $buf = New-Object byte[] $len
    $got = 0
    while ($got -lt $len) { $got += $out.Read($buf, $got, $len - $got) }
    $proc.WaitForExit(15000) | Out-Null
    return ([System.Text.Encoding]::UTF8.GetString($buf) | ConvertFrom-Json)
}

Step "ping while Gesit is not running (must not start it)"
$r = Invoke-Host @{ cmd = "ping" }
if ($r.ok) { Fail "Gesit was already running" }

Step "local web server with a test file"
$Www = Join-Path $Dir "..\gesit-www"
New-Item -ItemType Directory -Force $Www | Out-Null
$bytes = New-Object byte[] (3MB)
(New-Object Random 42).NextBytes($bytes)
# include CR/LF bytes: a text-mode file handle would corrupt these
for ($i = 0; $i -lt $bytes.Length; $i += 4096) { $bytes[$i] = 13; $bytes[$i + 1] = 10 }
[IO.File]::WriteAllBytes((Join-Path $Www "smoke.zip"), $bytes)
$want = (Get-FileHash (Join-Path $Www "smoke.zip") -Algorithm SHA256).Hash
$srv = Start-Process python -ArgumentList "-m", "http.server", "8765", "--bind", "127.0.0.1", "--directory", $Www -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2

Step "add a download through the native host (it must launch Gesit)"
$r = Invoke-Host @{ cmd = "add"; url = "http://127.0.0.1:8765/smoke.zip"; dialog = $false }
if (-not $r.ok) { Fail "add failed: $($r.error)" }
$target = Join-Path $env:USERPROFILE "Downloads\Gesit\Compressed\smoke.zip"
for ($i = 0; $i -lt 60 -and -not (Test-Path $target); $i++) { Start-Sleep -Milliseconds 500 }
if (-not (Test-Path $target)) { Fail "download did not arrive at $target" }
Start-Sleep -Seconds 1
$have = (Get-FileHash $target -Algorithm SHA256).Hash
if ($have -ne $want) { Fail "downloaded file differs (binary mode problem?): $have vs $want" }
Write-Host "    downloaded and byte-identical"

Step "ping while running"
$r = Invoke-Host @{ cmd = "ping" }
if (-not $r.ok) { Fail "ping failed with Gesit running" }

Step "quit and uninstall"
Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
& $Gesit --quit
Start-Sleep -Seconds 2
$un = Get-ChildItem $Dir -Filter "unins*.exe" | Select-Object -First 1
$p = Start-Process -FilePath $un.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "uninstaller exit code $($p.ExitCode)" }
if (Test-Path $regs["Chrome"]) { Fail "registry entry still present after uninstall" }
Remove-Item $target -ErrorAction SilentlyContinue
Write-Host "SMOKE TEST PASSED"
