<#
  End-to-end check of a real Windows install, run by CI after Setup.exe.
  Signs in with the setup code, waits for the launcher to install every
  component and start every service by itself, reinstalls one component
  through the Control Center API, and checks the security settings the
  installer applies. With -Desktop, the install was made by the desktop
  app's installer: it also checks the app's shortcuts, that the app trusts
  the local certificate and loads the case manager, and takes a screenshot.
#>
param([switch]$Desktop, [string]$Screenshot = "")
$ErrorActionPreference = "Stop"
$root = "C:\CaseFiles"
$base = "https://127.0.0.1:8443"
$failures = @()
function Check($ok, $what) {
  if ($ok) { Write-Host "ok   $what" -ForegroundColor Green } else { Write-Host "FAIL $what" -ForegroundColor Red; $script:failures += $what }
}

$code = (Get-Content "$root\launcher\setup-code.txt" | Select-String -Pattern "^[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$").Line
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
function Post($path, $body, $csrf) {
  $h = @{ Origin = $base }
  if ($csrf) { $h["X-CSRF-Token"] = $csrf }
  Invoke-RestMethod -Method Post -Uri "$base$path" -Body ($body | ConvertTo-Json) -ContentType "application/json" `
    -Headers $h -WebSession $session -SkipCertificateCheck
}
function Status { Invoke-RestMethod -Uri "$base/api/status" -WebSession $session -SkipCertificateCheck }
function WaitOp($timeoutSec) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  do {
    Start-Sleep -Seconds 3
    $snap = Status
    $op = $snap.operations | Select-Object -First 1
  } while ($op -and $op.state -eq "running" -and (Get-Date) -lt $deadline)
  return $snap
}
function Show($snap) {
  $snap.services | Format-Table name, state, detail, version | Out-String | Write-Host
  $snap.checks | Format-Table label, level, detail | Out-String | Write-Host
}

$csrf = (Post "/api/session/setup-code" @{ code = $code }).csrf
Check ([bool]$csrf) "signed in with the setup code"

# The launcher installs Python, Tesseract, Ollama and the embedding model by
# itself on first start, then starts every service. Nobody presses a button.
$snap = WaitOp 2700
Show $snap
$snap.components | Format-Table name, state, version, detail | Out-String | Write-Host
$op = $snap.operations | Select-Object -First 1
Check ($op.action -eq "components.install_missing" -and $op.actor -eq "launcher") "the launcher started the install by itself ($($op.action) by $($op.actor))"
Check ($op.state -eq "succeeded") "first install succeeded ($($op.message))"
foreach ($c in $snap.components) { Check ($c.state -eq "installed") "$($c.name) is installed ($($c.detail))" }
foreach ($s in $snap.services) { Check ($s.state -eq "running") "$($s.name) is running" }

# Reinstalling from the Control Center stops the service using it and starts
# it again. The verified download is reused.
Post "/api/actions" @{ action = "component.install"; component = "ollama" } $csrf | Out-Null
$snap = WaitOp 600
$op = $snap.operations | Select-Object -First 1
Check ($op.action -eq "component.install" -and $op.state -eq "succeeded") "reinstalling Ollama from the Control Center worked ($($op.message))"
Check (($snap.services | Where-Object id -eq "models").state -eq "running") "Local AI models is running again after the reinstall"
# Downloads are kept where only Administrators and SYSTEM can reach them.
$acl = (Get-Acl "$root\launcher\downloads").Access | ForEach-Object { $_.IdentityReference.Value }
Check (-not ($acl | Where-Object { $_ -like "NT SERVICE\*" -or $_ -like "*Users*" })) "downloads folder is Administrators and SYSTEM only ($($acl -join ', '))"
Start-Sleep -Seconds 65   # tool checks refresh once a minute
$snap = Status
foreach ($id in @("runtime", "ocr", "pst", "cert", "audit")) {
  $c = $snap.checks | Where-Object id -eq $id
  Check ($c.level -eq "pass") "check '$($c.label)' passes: $($c.detail)"
}

# Each app service runs under its own restricted account, never SYSTEM.
foreach ($id in @("api", "worker", "models")) {
  $svc = Get-CimInstance Win32_Service -Filter "Name='CaseFiles-$id'"
  Check ($svc.StartName -eq "NT SERVICE\CaseFiles-$id") "CaseFiles-$id runs as $($svc.StartName)"
  $status = Get-Content -Raw "$root\logs\$id\status.json" | ConvertFrom-Json
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($status.pid)"
  $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwner
  Check ($owner.User -eq "CaseFiles-$id") "$id process owner is $($owner.Domain)\$($owner.User)"
}

# The web app answers on its port.
$health = Invoke-RestMethod -Uri "https://127.0.0.1/health" -SkipCertificateCheck
Check ($health.status -eq "ok") "web app answers on port 443 (version $($health.version))"
$page = Invoke-WebRequest -Uri "https://127.0.0.1/" -SkipCertificateCheck -UseBasicParsing
Check ($page.Content -match 'id="root"') "web app serves its screens"

# The Admin created in the setup wizard signs in to the web app with the same
# password, creates a case and uploads a file as NT SERVICE\CaseFiles-api.
Post "/api/session/admin" @{ username = "ciadmin"; password = "ci admin password 2026" } $csrf | Out-Null
$app = "https://127.0.0.1"
$appSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
function AppCall($method, $path, $body, $contentType = "application/json") {
  $h = @{ Origin = $app }
  if ($script:appCsrf) { $h["X-CSRF-Token"] = $script:appCsrf }
  Invoke-RestMethod -Method $method -Uri "$app$path" -Body $body -ContentType $contentType -Headers $h -WebSession $appSession -SkipCertificateCheck
}
$appCsrf = $null
$s = AppCall Post "/api/session" (@{ username = "ciadmin"; password = "ci admin password 2026" } | ConvertTo-Json)
$appCsrf = $s.csrf
Check ($s.user.role -eq "admin") "the wizard's Admin signs in to the web app"
$case = AppCall Post "/api/cases" (@{ name = "CI case" } | ConvertTo-Json)
Check ([bool]$case.id) "Admin creates a case"
$up = AppCall Put "/api/cases/$($case.id)/files?path=Letters%2Fhello.txt" ([Text.Encoding]::UTF8.GetBytes("hello from CI")) "application/octet-stream"
Check ($up.result -eq "stored" -and $up.file.folder -eq "Letters") "a file uploads into the case ($($up.result))"
$stored = Get-Item "$root\data\cases\$($case.id)\original\$($up.file.id).txt" -ErrorAction SilentlyContinue
Check ($stored -and $stored.IsReadOnly) "the original is stored read-only in the data folder"

# The worker service (NT SERVICE\CaseFiles-worker, no internet) converts it
# to Markdown in the background, and reads a picture of text with Tesseract.
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap 1700, 600
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$g.DrawString("The steel never arrived in Leeds", (New-Object System.Drawing.Font "Arial", 40), [System.Drawing.Brushes]::Black, 80, 200)
$png = New-Object IO.MemoryStream
$bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
$scan = AppCall Put "/api/cases/$($case.id)/files?path=Evidence%2Fscan.png" $png.ToArray() "application/octet-stream"
function WaitConverted($id) {
  $deadline = (Get-Date).AddMinutes(4)
  do {
    $f = AppCall Get "/api/cases/$($case.id)/files/$id"
    if ($f.status -notin @("queued", "converting")) { return $f }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  return $f
}
$f = WaitConverted $up.file.id
Check ($f.status -eq "converted") "the worker converts the text file to Markdown ($($f.status) $($f.error))"
$md = AppCall Get "/api/cases/$($case.id)/files/$($up.file.id)/markdown"
Check ($md.body -match "hello from CI" -and $md.front_matter -match "source_type") "the Markdown has the text and its front matter"
$f = WaitConverted $scan.file.id
$md = if ($f.status -eq "converted") { AppCall Get "/api/cases/$($case.id)/files/$($scan.file.id)/markdown" } else { $null }
Check ($f.ocr -and $md.body -match "steel never arrived") "the worker reads a picture with Tesseract OCR ($($f.status) $($f.error))"
# The worker indexes it with the local embedding model, and search finds
# it by exact words and by meaning.
$deadline = (Get-Date).AddMinutes(4)
do {
  $f = AppCall Get "/api/cases/$($case.id)/files/$($scan.file.id)"
  if ($f.search.state -in @("indexed", "keyword_only", "failed")) { break }
  Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
Check ($f.search.state -eq "indexed") "the worker indexes it with the local model ($($f.search.state), $($f.search.passages) passages)"
$found = AppCall Get "/api/cases/$($case.id)/search?q=steel%20arrived&mode=keyword"
Check ($found.results.Count -ge 1 -and $found.results[0].file.id -eq $scan.file.id) "keyword search finds it"
$found = AppCall Get "/api/cases/$($case.id)/search?q=metal%20delivery&mode=semantic"
Check ($found.meaning -eq "on" -and $found.results.Count -ge 1) "meaning search runs on the local model ($($found.meaning))"
# AI services: an API key is sealed with DPAPI for the web app's account and
# never shown again, and a request to an online service stops at the
# confirmation step until the person confirms.
$key = "sk-ant-ci-not-a-real-key-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$p = AppCall Put "/api/settings/ai/providers/claude" (@{ enabled = $true; api_key = $key } | ConvertTo-Json)
$raw = (Invoke-WebRequest -Uri "$app/api/settings/ai" -Headers @{ Origin = $app } -WebSession $appSession -SkipCertificateCheck -UseBasicParsing).Content
Check ($p.has_key -and $p.key_hint -eq $key.Substring($key.Length - 4) -and -not $raw.Contains($key)) "an API key is saved and never shown again"
function ReadShared($path) {
  if (-not (Test-Path $path)) { return "" }
  $fs = [IO.File]::Open($path, "Open", "Read", "ReadWrite")
  try { $buf = New-Object byte[] $fs.Length; [void]$fs.Read($buf, 0, $buf.Length); [Text.Encoding]::ASCII.GetString($buf) } finally { $fs.Dispose() }
}
$dbText = (ReadShared "$root\data\app.db") + (ReadShared "$root\data\app.db-wal")
Check ($dbText.Contains("dpapi:") -and -not $dbText.Contains($key)) "the key is stored sealed with DPAPI, not as text"
$t = AppCall Post "/api/settings/ai/providers/claude/test" "{}"
Check ($t.message -notmatch "can't be read") "the web app opens the sealed key to test it ($($t.message))"
$call = AppCall Post "/api/cases/$($case.id)/ai" (@{ kind = "ask"; question = "Did the steel arrive?"; provider = "claude" } | ConvertTo-Json)
Check ($call.state -eq "prepared" -and $call.provider.external -and $call.sources.Count -ge 1) "a question shows what would be sent ($($call.sources.Count) passages to $($call.provider.destination))"
$blocked = $false
try { AppCall Post "/api/cases/$($case.id)/ai/$($call.id)/send" (@{ confirmed = $false } | ConvertTo-Json) | Out-Null } catch { $blocked = [int]$_.Exception.Response.StatusCode -eq 409 }
Check $blocked "nothing is sent off this machine without confirmation"
AppCall Post "/api/cases/$($case.id)/ai/$($call.id)/cancel" "{}" | Out-Null

# The worker (a restricted account) builds a case export the Admin downloads.
$exp = AppCall Post "/api/cases/$($case.id)/exports" "{}"
$deadline = (Get-Date).AddMinutes(3)
do {
  Start-Sleep -Seconds 3
  $exp = (AppCall Get "/api/cases/$($case.id)/exports").items | Where-Object id -eq $exp.id
} while ($exp.state -in @("queued", "building") -and (Get-Date) -lt $deadline)
Check ($exp.state -eq "ready") "the worker builds a case export ($($exp.state) $($exp.error))"
if ($exp.state -eq "ready") {
  $zipPath = Join-Path ([IO.Path]::GetTempPath()) "case-export.zip"
  Invoke-WebRequest -Uri "$app/api/cases/$($case.id)/exports/$($exp.id)/file" -Headers @{ Origin = $app } -WebSession $appSession -SkipCertificateCheck -OutFile $zipPath
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
  try { $names = @($zip.Entries | ForEach-Object FullName) } finally { $zip.Dispose() }
  $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
  Check ($hash -eq $exp.sha256 -and $names -contains "manifest-sha256.txt" -and $names -contains "audit/case-audit-trail.csv" -and ($names | Where-Object { $_ -like "originals/*" })) "the export downloads and opens in Windows ($($names.Count) files)"
}

$jobs = Invoke-RestMethod -Uri "$base/api/status" -WebSession $session -SkipCertificateCheck
Check ($jobs.jobs -and $jobs.jobs.updated) "the Control Center shows the worker's job counts"

$audit = AppCall Get "/api/audit"
Check ($audit.tamper_check.intact -and $audit.tamper_check.entries -ge 4) "the audit log records it and passes the tamper check ($($audit.tamper_check.entries) entries)"

# Nobody but Administrators and SYSTEM can read the CA key or the launcher's files.
foreach ($p in @("$root\certs\ca.key", "$root\launcher\auth.json", "$root\launcher\audit.jsonl")) {
  $others = (Get-Acl $p).Access | Where-Object { $_.IdentityReference -notmatch "BUILTIN\\Administrators|NT AUTHORITY\\SYSTEM" }
  Check (-not $others) "$p is Administrators/SYSTEM only"
}
$keyAcl = (Get-Acl "$root\certs\server.key").Access | Where-Object IdentityReference -eq "NT SERVICE\CaseFiles-api"
Check ([bool]$keyAcl) "the web app can read its own server key"
$modelsOnData = (Get-Acl "$root\data").Access | Where-Object IdentityReference -eq "NT SERVICE\CaseFiles-models"
Check (-not $modelsOnData) "the models service has no access to case data"

$rules = @(Get-NetFirewallRule -DisplayName "Case File Manager*")
Check ($rules.Count -eq 2) "two firewall rules installed"
$out = $rules | Where-Object Direction -eq Outbound
Check ($out.Action -eq "Block" -and ($out | Get-NetFirewallServiceFilter).Service -eq "CaseFiles-worker") "worker blocked from the internet"
if ($Desktop) {
  # Where the installer put the app, from its Settings › Apps entry.
  $entry = Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" | Where-Object DisplayName -like "Case File Manager*" | Select-Object -First 1
  $appDir = $entry.InstallLocation
  Check ([bool]$appDir -and (Test-Path "$appDir\CaseFileManager.exe")) "the app is listed in Settings › Apps ($appDir)"
  Check (Test-Path "$env:PUBLIC\Desktop\Case File Manager.lnk") "desktop shortcut to the app"
  Check (Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Case File Manager.lnk") "Start menu shortcut to the app"
  Check (-not (Test-Path "$env:PUBLIC\Desktop\Case File Manager Control Center.url")) "no browser shortcut"
  Check (-not (Test-Path "$root\uninstall.cmd")) "no uninstall.cmd (the app is removed from Settings)"
  $caCopy = "$appDir\resources\local-ca.crt"
  Check ((Test-Path $caCopy) -and (Get-FileHash $caCopy).Hash -eq (Get-FileHash "$root\certs\ca.crt").Hash) "the app has this computer's CA certificate"
  Check ((Get-Content -Raw "$root\launcher.json" | ConvertFrom-Json).desktop_app -eq $true) "launcher.json records the desktop install"
} else {
  Check (Test-Path "$env:PUBLIC\Desktop\Case File Manager Control Center.url") "desktop shortcut to the Control Center"
  Check (Test-Path "$root\uninstall.cmd") "uninstall.cmd is in the install folder"
}
$in = $rules | Where-Object Direction -eq Inbound
Check ($in.Profile -eq "Private") "web app port open on Private networks only ($($in.Profile))"

# Simulate a reboot: stop everything, start only the launcher. The services
# that were running must come back without anyone signing in.
Stop-Service CaseFileManagerLauncher
Stop-Service CaseFiles-worker, CaseFiles-api, CaseFiles-models
Start-Service CaseFileManagerLauncher
$deadline = (Get-Date).AddMinutes(5)
do {
  Start-Sleep -Seconds 5
  $states = @("api", "worker", "models") | ForEach-Object { (Get-Service "CaseFiles-$_").Status }
} while (($states | Where-Object { $_ -ne "Running" }) -and (Get-Date) -lt $deadline)
Check (-not ($states | Where-Object { $_ -ne "Running" })) "services started again after a launcher restart ($($states -join ', '))"

# Ollama's cloud models stay off, so case text never leaves this computer.
Check (Select-String -Quiet -SimpleMatch "Ollama cloud disabled: true" "$root\logs\models\service.log") "Ollama cloud models are turned off"

# A crash is restarted automatically.
Start-Sleep -Seconds 5
$before = Get-Content -Raw "$root\logs\worker\status.json" | ConvertFrom-Json
Stop-Process -Id $before.pid -Force
Start-Sleep -Seconds 10
$after = Get-Content -Raw "$root\logs\worker\status.json" | ConvertFrom-Json
Check ($after.running -and $after.pid -ne $before.pid -and $after.restarts -ge 1) "worker restarted after a crash (pid $($before.pid) -> $($after.pid))"

if ($Desktop) {
  # The app window must trust the local certificate and show the case manager.
  $out = Join-Path $env:RUNNER_TEMP "desktop-smoke.txt"
  $p = Start-Process -FilePath "$appDir\CaseFileManager.exe" -ArgumentList "--smoke-test=$out" -PassThru -Wait
  $result = if (Test-Path $out) { (Get-Content -Raw $out).Trim() } else { "no result" }
  Check ($p.ExitCode -eq 0 -and $result.StartsWith("OK https://localhost/")) "the app window loads the case manager over trusted HTTPS ($result)"

  if ($Screenshot) {
    $app = Start-Process -FilePath "$appDir\CaseFileManager.exe" -PassThru
    Start-Sleep -Seconds 15
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    [System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save($Screenshot, [System.Drawing.Imaging.ImageFormat]::Png)
    Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
    Get-Process CaseFileManager -ErrorAction SilentlyContinue | Stop-Process -Force
  }
}

if ($failures.Count) {
  Write-Host ""
  Write-Host "$($failures.Count) check(s) failed:" -ForegroundColor Red
  $failures | ForEach-Object { Write-Host "  - $_" }
  exit 1
}
Write-Host "All Windows install checks passed." -ForegroundColor Green
