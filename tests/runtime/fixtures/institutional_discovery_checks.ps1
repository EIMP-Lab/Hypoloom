param([Parameter(Mandatory)][string]$Connector)
$ErrorActionPreference = 'Stop'
foreach ($scenario in @('terminal', 'outage', 'recover', 'sleep', 'wrong_scope', 'pending', 'handoff', 'deadline_wait')) {
    & (Get-Process -Id $PID).Path -NoProfile -File (Join-Path $PSScriptRoot 'institutional_scope_checks.ps1') -Connector $Connector -Scenario $scenario
    if ($LASTEXITCODE -ne 0) { throw "scoped_discovery_check_failed_$scenario" }
}
