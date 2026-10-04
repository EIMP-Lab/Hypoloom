param([Parameter(Mandatory)][string]$Connector)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$Repository = '/repo'; $PythonExecutable = '/python'; $RunsRoot = '/state'; $Server = 'fixture'
$script:ssh = 'ssh-fixture'

& {
    $script:Step = 0; $script:Browsers = 0; $script:Forwards = @(); $script:Stopped = @(); $script:Delays = @()
    $a = [pscustomobject]@{socket_path='/tmp/run-a/browser.sock';state_root='/state/a';run_id='a'}
    $b = [pscustomobject]@{socket_path='/tmp/run-b/browser.sock';state_root='/state/b';run_id='b'}
    function Get-RunConnections {
        switch ($script:Step) {
            0 { @($a) }
            { $_ -ge 1 -and $_ -le 6 } { throw 'run_discovery_ssh_failed' }
            7 {
                Assert ($script:Stopped.Count -eq 0 -and $script:Forwards.Count -eq 1) 'discovery outage killed or recreated an established forward'
                @($a, $b)
            }
            8 { throw 'run_discovery_timeout' }
            9 { @($b) }
            default { @() }
        }
    }
    function Ensure-Browser { $script:Browsers++; @{webSocketDebuggerUrl='ws://127.0.0.1:9222/devtools/browser/fixture'} }
    function Start-Captured([string]$Executable, [string[]]$Values) {
        $forward = $Values -contains '-R'
        $label = if ($forward) { $Values[$Values.IndexOf('-R') + 1] } else { 'readiness' }
        if ($forward) { $script:Forwards += $label }
        $process = [pscustomobject]@{HasExited=$false;ExitCode=0}
        $process | Add-Member ScriptMethod WaitForExit { param($Timeout) $true }
        if ($forward) { $script:LatestForward = $process }
        @{Process=$process;Label=$label}
    }
    function Stop-Child($Child) { if ($Child.Label -ne 'readiness') { $script:Stopped += $Child.Label }; $Child.Process.HasExited=$true }
    function Start-Sleep {
        param($Seconds)
        $script:Delays += $Seconds
        $script:Step++
        if ($script:Step -eq 11) { throw 'fixture_complete' }
    }
    $messages = @(try { Watch-Runs } catch { Assert ($_.Exception.Message -eq 'fixture_complete') "discovery terminated the helper: $($_.Exception.Message)" })
    Assert (($script:Delays -join ',') -eq '5,5,10,20,30,30,30,5,5,5,5') 'discovery backoff did not cap or reset after recovery'
    Assert ($script:Browsers -eq 2 -and $script:Forwards.Count -eq 2) 'stale discovery created extra browser/forward work'
    Assert ($script:Stopped.Count -eq 2 -and $script:Stopped[0] -like '/tmp/run-a/*' -and $script:Stopped[1] -like '/tmp/run-b/*') 'fresh retirement did not close only the relevant forward'
    Assert (@($messages | Where-Object { $_ -like 'run_discovery_ssh_failed;*' }).Count -eq 4) 'unchanged outage is silent or floods output'
    Assert (@($messages | Where-Object { $_ -like 'run_discovery_timeout;*' }).Count -eq 1) 'specific timeout is lost'
    Assert (@($messages | Where-Object { $_ -eq 'run_discovery_recovered' }).Count -eq 2) 'recovery transition missing'

    # A remote watch may end its own forward during a listing outage. A failed
    # listing must not reopen it from a cached entry, or start any new browser.
    $script:Step = 0; $script:Forwards = @(); $script:Stopped = @(); $script:Browsers = 0
    function Get-RunConnections {
        if ($script:Step -eq 0) { @($a) }
        elseif ($script:Step -le 6) { throw 'run_discovery_timeout' }
        else { @() }
    }
    function Start-Sleep {
        param($Seconds)
        $script:Step++
        if ($script:Step -eq 1) { $script:LatestForward.HasExited = $true }
        if ($script:Step -eq 8) { throw 'fixture_complete' }
    }
    $null = try { Watch-Runs } catch { Assert ($_.Exception.Message -eq 'fixture_complete') 'outage ended the service' }
    Assert ($script:Forwards.Count -eq 1 -and $script:Browsers -eq 1 -and $script:Stopped.Count -eq 1) 'failed listing authorized a stale connection'

    $script:Step = 0; $script:Forwards = @(); $script:Stopped = @(); $script:Browsers = 0
    function Get-RunConnections {
        if ($script:Step -le 6) { throw 'run_discovery_command_failed' }
        @()
    }
    function Start-Sleep {
        param($Seconds)
        $script:Step++
        if ($script:Step -eq 8) { throw 'fixture_complete' }
    }
    $null = try { Watch-Runs } catch { Assert ($_.Exception.Message -eq 'fixture_complete') 'initial outage ended the service' }
    Assert ($script:Forwards.Count -eq 0 -and $script:Browsers -eq 0) 'failed discovery opened a browser before any active target'

    $script:Step = 0
    function Get-RunConnections {
        if ($script:Step -eq 0) { @($a) } else { throw 'unexpected_program_error' }
    }
    $reason = ''
    try { Watch-Runs | Out-Null } catch { $reason = $_.Exception.Message }
    Assert ($reason -eq 'unexpected_program_error' -and $script:Stopped.Count -eq 1) 'unknown fault was retried or leaked its child'
}

& {
    $script:DiscoveryExit = 0; $script:DiscoveryWait = $true
    $script:DiscoveryOutput = '{"connections":[]}'
    $script:Cleaned = 0
    function Start-Captured([string]$Executable, [string[]]$Values) {
        Assert ($Values[-1] -like '*connections*--runs-root*') 'wrong discovery command'
        $process = [pscustomobject]@{ExitCode=$script:DiscoveryExit}
        $process | Add-Member ScriptMethod WaitForExit {
            param($Timeout)
            Assert ($Timeout -eq 15000) 'discovery call lost its deadline'
            $script:DiscoveryWait
        }
        @{Process=$process;Out=[Threading.Tasks.Task]::FromResult($script:DiscoveryOutput)}
    }
    function Stop-Child($Child) { $script:Cleaned++ }
    Assert (@(Get-RunConnections).Count -eq 0) 'valid empty discovery failed'
    $script:DiscoveryOutput = '{"connections":[{"mode":"client","socket_path":"/tmp/run-a/browser.sock"},{"mode":"server","socket_path":"/tmp/run-b/browser.sock"}]}'
    $selected = @(Get-RunConnections)
    Assert ($selected.Count -eq 1 -and $selected[0].socket_path -eq '/tmp/run-a/browser.sock') 'discovery changed client selection'
    foreach ($case in @('timeout', 'ssh', 'command', 'json', 'missing', 'wrong_type')) {
        $script:DiscoveryWait = $case -ne 'timeout'
        $script:DiscoveryExit = switch ($case) { 'ssh' {255} 'command' {1} default {0} }
        $script:DiscoveryOutput = switch ($case) { 'json' {'private non-json response'} 'missing' {'{}'} 'wrong_type' {'{"connections":null}'} default {'{"connections":[]}'} }
        $expected = switch ($case) { 'timeout' {'run_discovery_timeout'} 'ssh' {'run_discovery_ssh_failed'} 'command' {'run_discovery_command_failed'} default {'run_discovery_invalid_response'} }
        $reason = ''
        try { Get-RunConnections | Out-Null } catch { $reason = $_.Exception.Message }
        Assert ($reason -ceq $expected) "discovery lost its safe reason: $case -> $reason"
    }
    Assert ($script:Cleaned -eq 8) 'discovery child was not cleaned up exactly once per attempt'
}
Write-Output 'discovery outage recovery, bounded backoff, retirement and safe diagnostics passed'
