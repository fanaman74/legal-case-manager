<#
.SYNOPSIS
  Removes the Case File Manager services and firewall rules.
  Case data, the audit log and the certificates are NOT deleted.
#>
[CmdletBinding()]
param([string]$InstallDir = "C:\CaseFiles")
$ErrorActionPreference = "Continue"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host "Run this in PowerShell opened with 'Run as administrator'." -ForegroundColor Red
  exit 1
}
& (Join-Path $InstallDir "launcher.exe") service uninstall
Get-NetFirewallRule -Group "Case File Manager" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Removed the services and firewall rules."
Write-Host "Your case data, audit log and certificates are still in $InstallDir. Delete that folder yourself if you no longer need them."
