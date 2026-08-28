#Requires -Version 5.1
# Advisory scope status reporter (personal lab edition). Never blocks ACT:
# always exits 0 for an existing case; exit 1 only for bad usage.
# -Force / -Quiet are accepted for backward compatibility.
# Usage:
#   powershell -File skills/scripts/case-guard.ps1 -CaseRoot work\my-case
param(
    [Parameter(Mandatory = $true)]
    [string] $CaseRoot,

    [switch] $Force,
    [switch] $Quiet
)
$ErrorActionPreference = 'Stop'

function Write-Info([string] $m) {
    if (-not $Quiet) { Write-Host $m }
}

if (-not (Test-Path -LiteralPath $CaseRoot)) {
    Write-Host ("ERROR: CaseRoot missing: {0}" -f $CaseRoot) -ForegroundColor Red
    exit 1
}

$scopePath = Join-Path $CaseRoot 'scope.md'
if (-not (Test-Path -LiteralPath $scopePath)) {
    Write-Info ("CASE-GUARD: no scope.md under {0} (advisory only; scaffold one with case-init)" -f $CaseRoot)
    exit 0
}

$scope = Get-Content -LiteralPath $scopePath -Raw -Encoding UTF8

function Get-ScopeSection([string] $Text, [string] $Name) {
    $pattern = '(?ms)^##\s*' + [regex]::Escape($Name) + '\s*\r?\n(?<body>.*?)(?=^##\s|\z)'
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) { return $match.Groups['body'].Value }
    return ''
}

function Get-SectionField([string] $Section, [string] $Name) {
    $pattern = '(?m)^\s*-\s*' + [regex]::Escape($Name) + ':\s*(?<value>.*?)\s*$'
    $match = [regex]::Match($Section, $pattern)
    if ($match.Success) { return $match.Groups['value'].Value.Trim() }
    return ''
}

$authStatus = Get-SectionField -Section (Get-ScopeSection -Text $scope -Name 'auth') -Name 'status'
$netMode = Get-SectionField -Section (Get-ScopeSection -Text $scope -Name 'network_profile') -Name 'mode'
$ready = Get-SectionField -Section (Get-ScopeSection -Text $scope -Name 'signoff') -Name 'ready_for_act'

Write-Info ("CASE-GUARD OK: {0} (advisory; never blocks)" -f $CaseRoot)
Write-Info ("  auth.status={0} network_profile={1} ready_for_act={2}" -f $authStatus, $netMode, $ready)
exit 0
