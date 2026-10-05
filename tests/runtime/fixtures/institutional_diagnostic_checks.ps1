param([Parameter(Mandatory)][string]$Connector, [Parameter(Mandatory)][string]$Directory, [Parameter(Mandatory)][string]$Scenario)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Connector,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false) | ForEach-Object { . ([scriptblock]::Create($_.Extent.Text)) }
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
$env:LOCALAPPDATA=$Directory; $Server='fixture@server'; $RequestId='create-fixture'; $StateRoot='/fixture/final'; $RunId='final'
$script:userSid='fixture-sid'; $script:ClientStage='startup'; $script:Private=$true
function Get-Acl {
    param($LiteralPath)
    $acl=[pscustomobject]@{}
    $acl | Add-Member ScriptMethod GetOwner {param($Type) [pscustomobject]@{Value='fixture-sid'} }
    $acl | Add-Member ScriptMethod GetAccessRules {
        param($Explicit,$Inherited,$Type)
        if (-not $script:Private) { [pscustomobject]@{AccessControlType='Allow';IdentityReference=@{Value='other-user'};IsInherited=$false} }
    }
    $acl
}
if ($Scenario -eq 'status_missing') {
    function Get-ScheduledTask {param($TaskName,$ErrorAction) $null}
    function Get-RunConnections {throw 'status_made_network_request'}
    function Ensure-Browser {throw 'status_started_browser'}
    $view=Show-ClientStatus | ConvertFrom-Json -AsHashtable
    Assert ($view.local_status_reason -eq 'task_not_found' -and -not $view.task -and $view.recent_events.Count -eq 0) 'absent local task misreported'
    Assert (-not (Test-Path -LiteralPath (Split-Path $view.diagnostic_path))) 'status created its log directory'
    exit 0
}
Initialize-ClientDiagnostic
if ($Scenario -eq 'bounded_log') {
    foreach ($index in 1..140) { Write-ClientDiagnostic 'connection_retry' 'run_discovery_timeout' 'discovery' }
    $entries=@(Read-ClientDiagnostic $script:DiagnosticPath)
    Assert ($entries.Count -eq 128 -and (Get-Item $script:DiagnosticPath).Length -le 65536) 'log bounds exceeded or history missing'
    $directory=Split-Path $script:DiagnosticPath
    [IO.File]::WriteAllText((Join-Path $directory 'unrelated.txt'), 'keep')
    foreach ($index in 1..10) {
        $script:RequestId="create-$index"; Initialize-ClientDiagnostic
        Write-ClientDiagnostic 'client_started' 'client_started' 'startup'
    }
    Assert (@(Get-ChildItem -LiteralPath $directory -Filter '*.jsonl').Count -eq 8) 'request log retention unbounded'
    Assert ([IO.File]::ReadAllText((Join-Path $directory 'unrelated.txt')) -eq 'keep') 'unrelated file removed'
    exit 0
}
if ($Scenario -eq 'private_logs') {
    $script:Private=$false
    Write-ClientDiagnostic 'connection_ended' 'raw-credential-secret' 'startup'
    Assert (-not (Test-Path -LiteralPath $script:DiagnosticPath)) 'wrote into nonprivate log directory'
    $script:DiagnosticPath=$null
    Initialize-ClientDiagnostic
    Assert (-not $script:DiagnosticPath) 'nonprivate directory accepted at startup'
    exit 0
}
if ($Scenario -eq 'safe_stderr') {
    foreach ($raw in @(
        'password=DO_NOT_SAVE',
        '{"event":"connection_watch_failed","reason":"cdp_identity_mismatch","token":"DO_NOT_SAVE"}',
        '{"event":"connection_watch_failed","reason":"secret_invented_reason"}',
        '{"event":"connection_watch_failed","reason":"cdp_response_invalid"}',
        '{"event":"connection_watch_failed","reason":"connection_watch_output_unavailable"}'
    )) {
        $child=@{Process=@{HasExited=$true;ExitCode=2};Err=[Threading.Tasks.Task]::FromResult($raw)}
        Read-WatchFailure $child
    }
    $rawLog=[IO.File]::ReadAllText($script:DiagnosticPath)
    Assert (-not $rawLog.Contains('DO_NOT_SAVE') -and -not $rawLog.Contains('secret_invented_reason')) 'raw child output leaked'
    $events=@(Read-ClientDiagnostic $script:DiagnosticPath)
    Assert (($events.reason -join ',') -eq 'watch_exited,cdp_identity_mismatch,watch_exited,cdp_response_invalid,connection_watch_output_unavailable') 'safe watch reasons lost'
    exit 0
}
if ($Scenario -in @('status_offline','corrupt_log','status_denied','status_cim')) {
    Write-ClientDiagnostic 'connection_ended' 'run_connection_expired' 'discovery'
    if ($Scenario -eq 'corrupt_log') { [IO.File]::WriteAllText($script:DiagnosticPath, '{"secret":"DO_NOT_SAVE"}') }
    function Get-ScheduledTask {
        [CmdletBinding()]
        param([string]$TaskName)
        Assert (-not $TaskName) 'status used a missing-task query that conflates absence and query failure'
        if ($Scenario -eq 'status_denied') { Write-Error 'DO_NOT_SAVE_task_error' -Category PermissionDenied; return }
        if ($Scenario -eq 'status_cim') { Write-Error 'DO_NOT_SAVE_task_error' -Category ResourceUnavailable; return }
        @{TaskName='Hypoloom-Run-other-create-fixture-fixture-sid';State='Running'}
        @{TaskName='Hypoloom-Run-fixture@server-create-fixture-fixture-sid';State='Ready'}
    }
    function Get-ScheduledTaskInfo {param([Parameter(ValueFromPipeline)]$InputObject) process { @{LastRunTime=[datetime]'2026-10-06T00:00:00Z';LastTaskResult=2} } }
    function Get-RunConnections {throw 'status_made_network_request'}
    function Ensure-Browser {throw 'status_started_browser'}
    $raw=Show-ClientStatus
    $view=$raw | ConvertFrom-Json -AsHashtable
    if ($Scenario -in @('status_denied','status_cim')) {
        Assert (-not $view.task -and $view.local_status_reason -eq 'task_status_unavailable') 'task query failure misreported as absent'
    } else { Assert ($view.task.state -eq 'Ready' -and $view.task.last_task_result -eq 2) 'local task reason unavailable' }
    Assert (-not $raw.Contains('DO_NOT_SAVE')) 'status exposed extra log fields'
    if ($Scenario -eq 'corrupt_log') { Assert ($view.diagnostic_reason -eq 'client_diagnostic_unavailable') 'corrupt log not bounded/safe' }
    else { Assert ($view.recent_events[-1].reason -eq 'run_connection_expired') 'offline status lost local cause' }
    exit 0
}
if ($Scenario -eq 'stderr_drain') {
    Add-Type -TypeDefinition @'
using System.Threading.Tasks;
public static class FixtureDiagnosticDrain {
    public static async Task<string> After(int milliseconds) {
        await Task.Delay(milliseconds);
        return "{\"event\":\"connection_watch_failed\",\"reason\":\"cdp_connection_failed\"}";
    }
}
'@
    $child=@{Process=@{HasExited=$true;ExitCode=2};Err=[FixtureDiagnosticDrain]::After(30)}
    Read-WatchFailure $child
    Assert (@(Read-ClientDiagnostic $script:DiagnosticPath)[-1].reason -eq 'cdp_connection_failed') 'exited watch drain lost safe reason'
    $child=@{Process=@{HasExited=$false};Err=[FixtureDiagnosticDrain]::After(1000)}
    $timer=[Diagnostics.Stopwatch]::StartNew()
    Read-WatchFailure $child
    Assert ($timer.ElapsedMilliseconds -lt 500) 'diagnostic read waited for live watch'
    exit 0
}
if ($Scenario -in @('cleanup_discovery','cleanup_ready','cleanup_handshake')) {
    function Get-ElapsedMilliseconds {0L}
    $script:ssh='fixture'; $Repository='/repo'; $PythonExecutable='/python'; $script:Started=0; $script:BrowserCleared=0
    $entry=@{mode='client';state_root=$StateRoot;run_id=$RunId;socket_path='/fixture/final/browser.sock'}
    if ($Scenario -ne 'cleanup_discovery') {
        function Get-RunConnections { @{scope=@{state_root=$StateRoot;run_id=$RunId;request_id=$RequestId};lifecycle='active';connections=@($entry)} }
    }
    function Start-Captured([string]$Executable,[string[]]$Values) {
        $script:Started++
        $ready=$Scenario -eq 'cleanup_handshake' -and $Values -notcontains '-R'
        $process=[pscustomobject]@{HasExited=$ready;ExitCode=0}
        $process | Add-Member ScriptMethod WaitForExit {param($Timeout) $this.HasExited}
        $process | Add-Member ScriptMethod Kill {param($Tree)}
        $process | Add-Member ScriptMethod Dispose {}
        @{Process=$process;Out=[Threading.Tasks.Task]::FromResult('{}')}
    }
    function Ensure-Browser { @{webSocketDebuggerUrl='ws://127.0.0.1:12345/devtools/browser/fixture'} }
    function Stop-OwnedBrowser {$script:BrowserCleared++}
    function Start-Sleep {throw 'retried_after_cleanup_failure'}
    $caught=''; try { Watch-Runs } catch {$caught=$_.Exception.Message}
    $expected=if ($Scenario -eq 'cleanup_discovery') {'run_discovery_timeout'} else {'run_forward_unavailable'}
    $starts=if ($Scenario -eq 'cleanup_handshake') {2} else {1}
    Assert ($caught -eq $expected -and $script:Started -eq $starts -and $script:BrowserCleared -eq 1) 'cleanup failure obscured primary or retried'
    $events=@(Read-ClientDiagnostic $script:DiagnosticPath)
    Assert ($events.reason -contains $expected -and $events.event -contains 'cleanup_failed') 'cleanup retry failure not diagnosable'
    exit 0
}
if ($Scenario -eq 'cleanup_all') {
    $script:Cleared=@(); $script:BrowserCleared=0
    function Stop-Child($Child) { $script:Cleared+=$Child; if ($Child -eq 'first') {throw 'child_cleanup_failed'} }
    function Stop-OwnedBrowser {$script:BrowserCleared++}
    $failure=Clear-OwnedProcesses @('first','second') -Browser
    Assert ($failure.Exception.Message -eq 'child_cleanup_failed' -and ($script:Cleared -join ',') -eq 'first,second' -and $script:BrowserCleared -eq 1) 'one cleanup failure skipped other owners'
    Assert (@(Read-ClientDiagnostic $script:DiagnosticPath)[-1].event -eq 'cleanup_failed') 'cleanup failure not recorded'
    exit 0
}
if ($Scenario -eq 'cleanup_primary') {
    function Get-ElapsedMilliseconds {0L}
    function Get-RunConnections { @{scope=@{state_root='/other';run_id='other';request_id='other'};lifecycle='active';connections=@()} }
    $script:Disposed=0
    $browser=[pscustomobject]@{HasExited=$false}
    $browser | Add-Member ScriptMethod Kill {param($Tree)}
    $browser | Add-Member ScriptMethod WaitForExit {param($Timeout) $false}
    $browser | Add-Member ScriptMethod Dispose {$script:Disposed++}
    $script:OwnedBrowser=$browser
    $caught=''; try {Watch-Runs} catch {$caught=$_.Exception.Message}
    Assert ($caught -eq 'run_scope_mismatch' -and $script:Disposed -eq 1 -and -not $script:OwnedBrowser) 'cleanup covered primary failure'
    $events=@(Read-ClientDiagnostic $script:DiagnosticPath)
    Assert ($events.reason -contains 'run_scope_mismatch' -and $events.reason -contains 'child_cleanup_failed') 'primary/cleanup diagnosis incomplete'
    exit 0
}
throw 'unknown_fixture_scenario'
