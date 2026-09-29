<#
  End-to-end check of a real Windows install, run by CI after install.ps1.
  Signs in with the setup code, starts every service through the Control
  Center API, and checks the security settings the installer applies.
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

Post "/api/actions" @{ action = "stack.start_all" } $csrf | Out-Null
$snap = WaitOp 600
Show $snap
$op = $snap.operations | Select-Object -First 1
Check ($op.state -eq "succeeded") "Start all succeeded ($($op.message))"
foreach ($s in $snap.services) { Check ($s.state -eq "running") "$($s.name) is running" }
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

# Nobody but Administrators and SYSTEM can read the CA key or the launcher's files.
foreach ($p in @("$root\certs\ca.key", "$root\launcher\auth.json", "$root\launcher\audit.jsonl")) {
  $others = (Get-Acl $p).Access | Where-Object { $_.IdentityReference -notmatch "BUILTIN\\Administrators|NT AUTHORITY\\SYSTEM" }
  Check (-not $others) "$p is Administrators/SYSTEM only"
}
$keyAcl = (Get-Acl "$root\certs\server.key").Access | Where-Object IdentityReference -eq "NT SERVICE\CaseFiles-api"
Check ([bool]$keyAcl) "the web app can read its own server key"
$modelsOnData = (Get-Acl "$root\data").Access | Where-Object IdentityReference -eq "NT SERVICE\CaseFiles-models"
Check (-not $modelsOnData) "the models service has no access to case data"

$rules = Get-NetFirewallRule -Group "Case File Manager"
Check ($rules.Count -eq 2) "two firewall rules installed"
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
