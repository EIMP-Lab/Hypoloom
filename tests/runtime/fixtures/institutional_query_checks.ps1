param([string]$Connector, [string]$Python, [string]$Peer, [string]$Directory, [string]$Scenario)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Connector,[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw ($errors | Out-String)}
$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Value,[string]$Message) {if (-not $Value) {throw $Message}}
$script:RealCaptured=${function:Start-Captured}
$script:Data=Join-Path $Directory 'status.json'; $script:Pids=Join-Path $Directory 'pids'
$script:ssh='fixture'; $Server='fixture'; $Repository='/repo'; $PythonExecutable='/python'
$StateRoot='/state/final'; $RunId='final'; $RequestId='create-final'; $PreparationRoot='/state/preparation'
$script:Clock=[Diagnostics.Stopwatch]::StartNew(); $script:QueryWait=15000; $script:DiscoveryChild=$null
$script:Now=0L
$script:UnhealthySince=$null; $script:LastScopeContact=$null; $script:AwaitingDeadline=$null
function Get-ElapsedMilliseconds {if ($Scenario -eq 'after_attach') {$script:Now} else {$script:Clock.ElapsedMilliseconds}}
$script:RealRemaining=${function:Get-RemainingMilliseconds}
function Get-RemainingMilliseconds([int]$Maximum=15000) {& $script:RealRemaining ([Math]::Min($Maximum,$script:QueryWait))}
function Start-Captured([string]$Executable,[string[]]$Values,[switch]$Handshake,[switch]$KeepInput) {
    if ($Scenario -eq 'after_attach' -and -not $Values[-1].Contains("'connections'")) {
        $process=[pscustomobject]@{HasExited=($Values -notcontains '-R');ExitCode=0}
        $process | Add-Member ScriptMethod WaitForExit {param($Timeout) $true}
        $process | Add-Member ScriptMethod Kill {param($Tree) $this.HasExited=$true}
        $process | Add-Member ScriptMethod Dispose {}
        $response=@{scope=$script:Status.scope;lifecycle='preparing';connected=$true} | ConvertTo-Json -Compress
        return @{Process=$process;Out=[Threading.Tasks.Task]::FromResult($response)}
    }
    Assert ($Values -contains '-T') 'query allocated a terminal'
    Assert ($Handshake -and $KeepInput) 'query did not own bidirectional pipes'
    $child=& $script:RealCaptured $Python @($Peer,$script:Data,$script:Pids,$Values[-1]) -Handshake -KeepInput
    # Exercise Windows StreamWriter newlines even when this fixture runs on Linux.
    $child.Process.StandardInput.NewLine="`r`n"
    $child
}
function Set-Status([string]$Lifecycle) {
    $script:Status=@{scope=@{state_root=$StateRoot;run_id=$RunId;request_id=$RequestId};lifecycle=$Lifecycle;connections=@()}
    if ($Lifecycle -eq 'preparing') {
        $script:Status.connections=@(@{state_root=$PreparationRoot;run_id='preparation';mode='client';socket_path='/state/preparation/browser.sock'})
    }
    Save-Status
}
function Save-Status {$script:Status | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $script:Data -Encoding utf8}
function Check-Gone([int]$ChildId) {Assert (-not (Get-Process -Id $ChildId -ErrorAction SilentlyContinue)) 'owned query child survived'}
Set-Status preparing
try {
    if ($Scenario -in @('watch_cleanup','after_attach')) {
        if ($Scenario -eq 'watch_cleanup') {Set-Status awaiting_launch; $script:Status.remaining_seconds=60; Save-Status}
        $script:BrowserStops=0; $script:BrowserStarts=0
        function Ensure-Browser {$script:BrowserStarts++; @{webSocketDebuggerUrl='ws://127.0.0.1:12345/devtools/browser/fixture'}}
        function Stop-OwnedBrowser {$script:BrowserStops++}
        function Start-Sleep {
            param($Milliseconds)
            $script:Now+=60000
            if ($Scenario -eq 'watch_cleanup' -or $script:Now -ge 360000) {Set-Status terminal}
            Assert ($script:BrowserStops -eq 0) 'healthy browser closed while control session still worked'
        }
        $messages=@(Watch-Runs)
        Assert ($messages -contains 'run_terminal; connection_closed') 'terminal response did not end helper'
        Assert ($script:BrowserStops -eq 1 -and -not $script:DiscoveryChild) 'watch did not clean query and browser'
        if ($Scenario -eq 'after_attach') {
            Assert ($script:Now -ge 360000 -and $script:BrowserStarts -eq 1) 'healthy run did not survive past prior outage window'
            Assert (@(Get-Content -LiteralPath $script:Pids).Count -eq 1) 'attached helper repeatedly created query connections'
        }
    } else {
        $first=Get-RunConnections; $childId=$script:DiscoveryChild.Process.Id
        Assert ($first.lifecycle -eq 'preparing') 'initial query failed'
        switch ($Scenario) {
            'reuse' {
                Assert (-not $script:DiscoveryChild.Out.Wait(100)) 'server generated unsolicited stale responses'
                Set-Status terminal
                $second=Get-RunConnections
                Assert ($second.lifecycle -eq 'terminal' -and $script:DiscoveryChild.Process.Id -eq $childId) 'fresh status did not reuse SSH session'
                Assert (@(Get-Content -LiteralPath $script:Pids).Count -eq 1) 'query spawned another SSH connection'
            }
            'timeout' {
                $script:Status.delay=2; Save-Status; $script:QueryWait=150
                $caught=''; try {Get-RunConnections | Out-Null} catch {$caught=$_.Exception.Message}
                Assert ($caught -eq 'run_discovery_timeout' -and -not $script:DiscoveryChild) 'timed-out session retained a late response'
                Check-Gone $childId
                $script:QueryWait=15000; Set-Status terminal
                $next=Get-RunConnections
                Assert ($next.lifecycle -eq 'terminal' -and $script:DiscoveryChild.Process.Id -ne $childId) 'recovery reused old status or process'
            }
            'scope' {
                $script:Status.scope.run_id='other'; Save-Status
                $caught=''; try {Get-RunConnections | Out-Null} catch {$caught=$_.Exception.Message}
                Assert ($caught -eq 'run_scope_mismatch' -and -not $script:DiscoveryChild) 'channel accepted another run'
                Check-Gone $childId
            }
            'ssh_exit' {
                $script:Status.exit=255; Save-Status
                $caught=''; try {Get-RunConnections | Out-Null} catch {$caught=$_.Exception.Message}
                Assert ($caught -eq 'run_discovery_ssh_failed' -and -not $script:DiscoveryChild) 'SSH failure was hidden or retained'
                Check-Gone $childId
            }
            'stdin_eof' {
                $script:DiscoveryChild.Process.StandardInput.Close()
                Assert ($script:DiscoveryChild.Process.WaitForExit(3000)) 'remote query ignored client EOF'
                Assert ($script:DiscoveryChild.Process.ExitCode -eq 0) 'EOF was misclassified as failed query'
                $other=& $script:RealCaptured $Python @('-c','import sys; print("eof" if sys.stdin.read() == "" else "unexpected")')
                try {
                    Assert ($other.Process.WaitForExit(3000)) 'noninteractive child inherited open console stdin'
                    Assert ($other.Out.GetAwaiter().GetResult().Trim() -eq 'eof') 'noninteractive input was not closed'
                } finally {Stop-Child $other}
            }
        }
    }
} finally {
    if ($script:DiscoveryChild) {Stop-Child $script:DiscoveryChild; $script:DiscoveryChild=$null}
}
foreach ($value in Get-Content -LiteralPath $script:Pids) {Check-Gone ([int]$value)}
Write-Output "query_$Scenario passed"
