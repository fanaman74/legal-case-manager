<#
  End-to-end check of a real Windows install, run by CI after Setup.exe.
  Signs in with the setup code, waits for the launcher to install every
  component and start every service by itself, reinstalls one component
  through the Control Center API, and checks the security settings the
  installer applies.
#>
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
Check (Test-Path "$env:PUBLIC\Desktop\Case File Manager Control Center.url") "desktop shortcut to the Control Center"
Check (Test-Path "$root\uninstall.cmd") "uninstall.cmd is in the install folder"
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

if ($failures.Count) {
  Write-Host ""
  Write-Host "$($failures.Count) check(s) failed:" -ForegroundColor Red
  $failures | ForEach-Object { Write-Host "  - $_" }
  exit 1
}
Write-Host "All Windows install checks passed." -ForegroundColor Green
