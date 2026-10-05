param([Parameter(Mandatory)][string]$Connector, [Parameter(Mandatory)][string]$Scenario)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$Server='fixture'; $Repository='/repo'; $PythonExecutable='/python'
$StateRoot='/state/final'; $RunId='final'; $RequestId='create-final'; $PreparationRoot='/state/preparation'
$script:ssh='ssh-fixture'; $LocalPort=12345
$script:Now=0L; $script:UnhealthySince=$null; $script:LastScopeContact=0L; $script:AwaitingDeadline=$null
function Get-ElapsedMilliseconds { $script:Now }
function New-Status([string]$Lifecycle, [array]$Entries=@()) {
    @{scope=@{state_root=$StateRoot;run_id=$RunId;request_id=$RequestId};lifecycle=$Lifecycle;connections=$Entries}
}
$script:FinalEntry=@{mode='client';state_root=$StateRoot;run_id=$RunId;socket_path='/state/final/browser.sock'}
$script:PreparationEntry=@{mode='client';state_root=$PreparationRoot;run_id='preparation';socket_path='/state/preparation/browser.sock'}
if ($Scenario -eq 'native_job') {
    Initialize-OwnedJob
    $child=Start-Captured (Get-Process -Id $PID).Path @('-NoProfile','-Command','Start-Sleep -Seconds 60')
    try {
        [Console]::WriteLine($child.Process.Id)
        $null=[Console]::ReadLine()
    } finally { Stop-Child $child }
    exit 0
}
if ($Scenario -eq 'native_compile') {
    Initialize-ClientNative
    Assert ('HypoloomClientLifetime' -as [type]) 'Windows ownership implementation did not compile'
    exit 0
}
if ($Scenario -eq 'legacy_task') {
    $script:userSid='fixture-sid'; $script:TaskEvents=@()
    $PowerShell='C:\pwsh.exe'; $Source='C:\connector.ps1'
    function Resolve-AccountSid([string]$Name) { if ($Name -in @('FixtureUser','DESKTOP\FixtureUser')) {'fixture-sid'} else {'different-sid'} }
    function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:ScheduledTask }
    function Split-TaskArguments { param($Arguments) $script:LegacyArguments }
    function Disable-ScheduledTask { param($TaskName) $script:TaskEvents+='disable' }
    function Stop-ScheduledTask { param($TaskName) $script:TaskEvents+='stop' }
    function Unregister-ScheduledTask { param($TaskName,$Confirm) $script:TaskEvents+='uninstall' }
    $script:ScheduledTask=$null
    $removed=Retire-LegacyTask $PowerShell $Source
    Assert ($removed -ceq $false -and $script:TaskEvents.Count -eq 0) 'absent legacy task was reported as removed'
    foreach ($case in @('valid', 'short_user', 'qualified_user', 'owner', 'exe', 'source', 'server', 'action', 'extra_action', 'duplicate', 'injected', 'missing_file')) {
        $script:TaskEvents=@()
        $script:ScheduledTask=@{Actions=@(@{Execute=$PowerShell;Arguments='fixture'});Principal=@{UserId='fixture-sid'}}
        $script:LegacyArguments=@('-NoProfile','-NonInteractive','-File',$Source,'-Action','Run','-Server',$Server,'-RunsRoot','/old/runs')
        switch ($case) {
            'short_user' { $script:ScheduledTask.Principal.UserId='FixtureUser' }
            'qualified_user' { $script:ScheduledTask.Principal.UserId='DESKTOP\FixtureUser' }
            'owner' { $script:ScheduledTask.Principal.UserId='other' }
            'exe' { $script:ScheduledTask.Actions[0].Execute='C:\other.exe' }
            'source' { $script:LegacyArguments[3]='C:\other.ps1' }
            'server' { $script:LegacyArguments[7]='other' }
            'action' { $script:LegacyArguments[5]='Other' }
            'extra_action' { $script:ScheduledTask.Actions+=@{Execute=$PowerShell;Arguments='fixture'} }
            'duplicate' { $script:LegacyArguments+=@('-File',$Source) }
            'injected' { $script:LegacyArguments+=@('-Command','unexpected') }
            'missing_file' { $script:LegacyArguments=@('-NoProfile','-Action','Run','-Server',$Server) }
        }
        $caught=''; $removed=$false
        try { $removed=Retire-LegacyTask $PowerShell $Source } catch { $caught=$_.Exception.Message }
        if ($case -in @('valid','short_user','qualified_user')) {
            Assert (-not $caught -and $removed -ceq $true -and ($script:TaskEvents -join ',') -eq 'disable,stop,uninstall') "legacy migration failed $case"
        } else { Assert ($caught -eq 'legacy_task_ownership_mismatch' -and $script:TaskEvents.Count -eq 0) "legacy mismatch changed task $case" }
    }
    exit 0
}
if ($Scenario -eq 'deadline_wait') {
    $script:Now=299000L; $script:UnhealthySince=0L
    $script:Cleaned=0
    function Start-Captured([string]$Executable,[string[]]$Values) {
        Assert ($Values[-1].Contains("'--state-root' '/state/final'")) 'final root missing'
        Assert ($Values[-1].Contains("'--request-id' 'create-final'")) 'creation identity missing'
        Assert (-not $Values[-1].Contains('--runs-root')) 'global discovery retained'
        $process=[pscustomobject]@{ExitCode=0}
        $process | Add-Member ScriptMethod WaitForExit { param($Timeout) Assert ($Timeout -le 1000) 'call exceeded remaining deadline'; $script:Now+=1000; $false }
        @{Process=$process}
    }
    function Stop-Child($Child) { $script:Cleaned++ }
    $caught=''; try { Get-RunConnections } catch { $caught=$_.Exception.Message }
    Assert ($caught -eq 'run_discovery_timeout' -and $script:Cleaned -eq 1) 'bounded discovery cleanup failed'
    $caught=''; try { Assert-ConnectionDeadline } catch { $caught=$_.Exception.Message }
    Assert ($caught -eq 'run_connection_expired') 'deadline did not expire'
    exit 0
}
if ($Scenario -eq 'owned_cleanup') {
    $child=Start-Captured (Get-Process -Id $PID).Path @('-NoProfile','-Command','Start-Sleep -Seconds 60')
    $unrelated=Start-Captured (Get-Process -Id $PID).Path @('-NoProfile','-Command','Start-Sleep -Seconds 60')
    $childId=$child.Process.Id
    try {
        Stop-Child $child
        Assert (-not (Get-Process -Id $childId -ErrorAction SilentlyContinue)) 'owned child survived'
        Assert (-not $unrelated.Process.HasExited) 'unrelated child was killed'
    } finally { Stop-Child $unrelated }
    exit 0
}
if ($Scenario -eq 'task_ownership') {
    $script:userSid='fixture-sid'; $script:TaskEvents=@(); $Action='Connect'
    $PowerShell='C:\pwsh.exe'; $script:ScheduledTask=$null
    $values=@('-NoProfile','-NonInteractive','-File','C:\connector.ps1','-Action','Run','-Server','fixture',
        '-StateRoot',$StateRoot,'-RunId',$RunId,'-RequestId',$RequestId)
    function Split-TaskArguments { param($Arguments) @([regex]::Matches($Arguments, '"([^"]*)"') | ForEach-Object { $_.Groups[1].Value }) }
    function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:ScheduledTask }
    function Initialize-BrowserProfile {}
    function New-ScheduledTaskAction { param($Execute,$Argument) @{Execute=$Execute;Arguments=$Argument} }
    function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) @{UserId=$UserId} }
    function New-ScheduledTaskSettingsSet { param($MultipleInstances,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) @{ExecutionTimeLimit=$ExecutionTimeLimit} }
    function Register-ScheduledTask { param($TaskName,$Action,$Principal,$Settings) $script:TaskEvents+='register'; $script:ScheduledTask=@{Actions=@($Action);Principal=$Principal;State='Ready'} }
    function Start-ScheduledTask { param($TaskName) $script:TaskEvents+='start' }
    function Stop-ScheduledTask { param($TaskName) $script:TaskEvents+='stop' }
    function Unregister-ScheduledTask { param($TaskName,$Confirm) $script:TaskEvents+='uninstall' }
    Invoke-ClientTask 'owned' $values $PowerShell | Out-Null
    Assert (($script:TaskEvents -join ',') -eq 'register,start') 'one-shot task not started'
    Assert ($script:ScheduledTask.Actions[0].Arguments.Contains('"-StateRoot" "/state/final"')) 'task scope lost'
    $script:ScheduledTask.Actions[0].Arguments+=' "-RunId" "other"'; $script:TaskEvents=@()
    $caught=''; try { Invoke-ClientTask 'owned' $values $PowerShell } catch { $caught=$_.Exception.Message }
    Assert ($caught -eq 'scheduled_task_ownership_mismatch' -and $script:TaskEvents.Count -eq 0) 'mismatched task changed'
    $script:ScheduledTask.Actions[0].Arguments=((@('-WindowStyle','Hidden')+$values+@('-PreparationRoot','/state/old-preparation')) | ForEach-Object {Quote-Native $_}) -join ' '
    $script:ScheduledTask.State='Running'; $script:TaskEvents=@()
    $caught=''; try { Invoke-ClientTask 'owned' $values $PowerShell } catch { $caught=$_.Exception.Message }
    Assert ($caught -eq 'scheduled_task_ownership_mismatch' -and $script:TaskEvents.Count -eq 0) 'running preparation task was replaced'
    $script:ScheduledTask.State='Ready'; $script:TaskEvents=@()
    Invoke-ClientTask 'owned' $values $PowerShell | Out-Null
    Assert (($script:TaskEvents -join ',') -eq 'uninstall,register,start') 'completed preparation prevented explicit formal-run reconnect'
    Assert (-not $script:ScheduledTask.Actions[0].Arguments.Contains('-PreparationRoot')) 'stale preparation was retained'
    $Action='Uninstall'; $script:TaskEvents=@()
    $messages=@(Invoke-ClientTask 'owned' $values $PowerShell)
    Assert (($script:TaskEvents -join ',') -eq 'stop,uninstall' -and $messages -contains 'task_stopped; browser_profile_preserved; foreground_Run_uses_Ctrl+C') 'existing task removal was not reported'
    $script:ScheduledTask=$null; $script:TaskEvents=@()
    $messages=@(Invoke-ClientTask 'owned' $values $PowerShell)
    Assert ($script:TaskEvents.Count -eq 0 -and $messages -contains 'task_not_found; browser_profile_preserved; foreground_Run_uses_Ctrl+C') 'absent task was reported as stopped'
    exit 0
}
$script:Polls=0; $script:Browsers=0; $script:Forwards=@(); $script:Stopped=@(); $script:BrowserStopped=0
function Stop-OwnedBrowser { $script:BrowserStopped++ }
function Ensure-Browser { Assert-ConnectionDeadline; $script:Browsers++; @{webSocketDebuggerUrl='ws://127.0.0.1:12345/devtools/browser/fixture'} }
function Get-RunConnections {
    $script:Polls++
    switch ($Scenario) {
        'terminal' { if ($script:Polls -eq 1) { New-Status active @($script:FinalEntry) } else { New-Status terminal } }
        'outage' { throw 'run_discovery_ssh_failed' }
        'pending' { New-Status pending }
        'sleep' { New-Status active @($script:FinalEntry) }
        'empty_active' { New-Status active }
        'empty_preparing' { New-Status preparing }
        'awaiting_launch' { $result=New-Status awaiting_launch; $result.remaining_seconds=600-$script:Now/1000; $result }
        'retry_preparation' {
            if ($script:Polls -eq 1) { $result=New-Status awaiting_launch; $result.remaining_seconds=10; $result }
            elseif ($script:Polls -eq 2) { New-Status preparing @($script:PreparationEntry) }
            elseif ($script:Polls -eq 3) { $result=New-Status awaiting_launch; $result.remaining_seconds=600; $result }
            else { New-Status terminal }
        }
        'review_reconnect' {
            if ($script:Polls -eq 2) { throw 'run_discovery_ssh_failed' }
            elseif ($script:Polls -lt 4) { $result=New-Status awaiting_launch @($script:PreparationEntry); $result.remaining_seconds=600-$script:Now/1000; $result }
            else { New-Status terminal }
        }
        'fast_retry' {
            if ($script:Polls -eq 1) { $result=New-Status awaiting_launch; $result.remaining_seconds=600; $result }
            elseif ($script:Polls -eq 2) { $result=New-Status awaiting_launch; $result.remaining_seconds=10; $result }
            elseif ($script:Polls -eq 3) { $result=New-Status awaiting_launch; $result.remaining_seconds=600; $result }
            else { New-Status terminal }
        }
        'interrupt' { if ($script:Polls -eq 1) { New-Status active @($script:FinalEntry) } else { throw 'fixture_interrupted' } }
        'wrong_entry' { $result=New-Status active @(@{mode='client';state_root='/state/other';run_id='other';socket_path='/state/other/browser.sock'}); $result }
        'handshake_scope' { New-Status active @($script:FinalEntry) }
        'handshake_failure' { New-Status active @($script:FinalEntry) }
        'recover' {
            if ($script:Polls -le 3) { throw 'run_discovery_ssh_failed' }
            if ($script:Polls -eq 4) { New-Status active @($script:FinalEntry) }
            elseif ($script:Polls -le 6) { throw 'run_discovery_timeout' }
            else { New-Status terminal }
        }
        'wrong_scope' { $result=New-Status active @($script:FinalEntry); $result.scope.run_id='other'; $result }
        'handoff' { if ($script:Polls -eq 1) { New-Status preparing @($script:PreparationEntry) } elseif ($script:Polls -eq 2) { New-Status active @($script:FinalEntry) } else { New-Status terminal } }
    }
}
function Start-Captured([string]$Executable,[string[]]$Values) {
    $command=$Values[-1]
    Assert ($command.Contains("'--state-root' '/state/final'")) 'forward used temporary root as authority'
    Assert ($command.Contains("'--run-id' 'final'") -and $command.Contains("'--request-id' 'create-final'")) 'forward changed final identity'
    Assert ($command.Contains("'--preparation-root' '/state/preparation'")) 'handoff scope lost'
    $forward=$Values -contains '-R'; $label=if ($forward) {$Values[$Values.IndexOf('-R')+1]} else {'ready'}
    if ($forward) { $script:Forwards+=$label }
    $process=[pscustomobject]@{HasExited=$false;ExitCode=0}
    $process | Add-Member ScriptMethod WaitForExit { param($Timeout) $true }
    $response=@{scope=@{state_root=$StateRoot;run_id=$RunId;request_id=$RequestId};lifecycle='active';connected=$true}
    if ($Scenario -eq 'review_reconnect') { $response.lifecycle='awaiting_launch'; if ($forward) { $script:LatestForward=$process } }
    if ($Scenario -eq 'handshake_scope') { $response.scope.run_id='other' }
    if ($Scenario -eq 'handshake_failure') { $response.connected=$false }
    $handshake=$response | ConvertTo-Json -Compress
    @{Process=$process;Label=$label;Out=[Threading.Tasks.Task]::FromResult($handshake)}
}
function Stop-Child($Child) { if ($Child.Label -ne 'ready') { $script:Stopped+=$Child.Label }; $Child.Process.HasExited=$true }
function Start-Sleep {
    param($Seconds,$Milliseconds)
    if ($Scenario -eq 'review_reconnect' -and $script:Polls -eq 1) { $script:LatestForward.HasExited=$true }
    $script:Now+=if ($Scenario -eq 'sleep') { 301000 } elseif ($Scenario -eq 'retry_preparation' -and $script:Polls -eq 2) { 20000 } elseif ($Scenario -eq 'fast_retry' -and $script:Polls -eq 3) { 10000 } elseif ($Milliseconds) { $Milliseconds } else { $Seconds*1000 }
    Assert ($script:Polls -lt 200) 'loop had no bounded exit'
}
$caught=''; $messages=@(try { Watch-Runs } catch { $caught=$_.Exception.Message })
switch ($Scenario) {
    'outage' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 300000 -and $Browsers -eq 0) 'outage renewed its deadline or opened browser' }
    'pending' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 300000 -and $Browsers -eq 0) 'pending renewed its deadline' }
    'empty_active' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 300000 -and $Browsers -eq 0) 'active with no endpoint renewed deadline' }
    'empty_preparing' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 300000 -and $Browsers -eq 0) 'preparing with no endpoint renewed deadline' }
    'awaiting_launch' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 600000 -and $Browsers -eq 0) 'awaiting launch deadline was extended' }
    'handshake_scope' { Assert ($caught -eq 'run_scope_mismatch') 'different-run handshake accepted' }
    'handshake_failure' { Assert ($caught -eq 'run_connection_expired' -and $Now -eq 300000) 'unsuccessful handshake renewed deadline' }
    'wrong_entry' { Assert ($caught -eq 'run_scope_mismatch' -and $Browsers -eq 0) 'different-run entry accepted' }
    'interrupt' { Assert ($caught -eq 'fixture_interrupted') 'interruption was swallowed' }
    'wrong_scope' { Assert ($caught -eq 'run_scope_mismatch' -and $Browsers -eq 0) 'another run authorized browser startup' }
    'sleep' { Assert ($caught -eq 'run_connection_expired' -and $Polls -eq 1 -and $Browsers -eq 1) 'wake contacted server or reopened browser after expiry' }
    default { Assert (-not $caught) "unexpected loop failure $caught"; Assert ($messages -contains 'run_terminal; connection_closed') 'terminal did not exit' }
}
Assert ($Stopped.Count -eq $Forwards.Count -and $BrowserStopped -eq 1) 'owned cleanup incomplete'
if ($Scenario -eq 'recover') { Assert ($Forwards.Count -eq 1 -and $messages -contains 'run_discovery_recovered') 'same-run recovery failed' }
if ($Scenario -eq 'handoff') { Assert ($Forwards.Count -eq 2 -and $Stopped[0] -like '/state/preparation/*') 'preparation forward not retired before run handoff' }
if ($Scenario -eq 'review_reconnect') { Assert ($Forwards.Count -eq 2 -and $script:AwaitingDeadline -eq 600000) 'review reconnect failed or extended preparation deadline' }
if ($Scenario -eq 'fast_retry') { Assert ($script:Polls -eq 4 -and $script:Now -eq 20000 -and $script:AwaitingDeadline -eq 610000) 'retry between polls retained an obsolete review deadline' }
Write-Output "scoped_$Scenario passed"
