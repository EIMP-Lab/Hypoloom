param([Parameter(Mandatory)][string]$Connector)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
# Load function definitions only; the Windows entrypoint never runs on this host.
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
# Exercise the real privacy validator against the reported inherited grant.
# Only Windows ACL/filesystem APIs are substituted; no real login data is used.
& {
    $script:userSid = 'fixture-user'
    $script:DefaultBrowserProfile = 'C:\Users\Fixture\Hypoloom\Institutional-msedge'
    $BrowserProfile = $script:DefaultBrowserProfile
    function New-Rule($Sid, $Inherited = $true, $Type = 'Allow') {
        [pscustomobject]@{IdentityReference=[pscustomobject]@{Value=$Sid};IsInherited=$Inherited;
            AccessControlType=$Type;FileSystemRights='FullControl'}
    }
    function Reset-Profile {
        $script:ProfileExists = $true; $script:ProfileWrites = 0; $script:ProfileReads = 0
        $script:ProfileReparse = $false; $script:ProfileWriteFails = $false; $script:ProfileWriteIgnored = $false
        $script:ProfileAcl = [pscustomobject]@{Owner='fixture-user';AreAccessRulesProtected=$false;Rules=@(
            (New-Rule 'fixture-extra-group'), (New-Rule 'S-1-5-18'),
            (New-Rule 'S-1-5-32-544'), (New-Rule 'fixture-user'), (New-Rule 'fixture-denied' $true 'Deny'))}
        $script:ProfileAcl | Add-Member ScriptMethod GetOwner { param($Type) [pscustomobject]@{Value=$this.Owner} }
        $script:ProfileAcl.Rules[0].FileSystemRights = 'ReadAndExecute'
        $script:ProfileAcl | Add-Member ScriptMethod GetAccessRules { param($Explicit, $Inherited, $Type) $this.Rules }
        $script:ProfileAcl | Add-Member ScriptMethod SetAccessRuleProtection {
            param($Protected, $Preserve)
            Assert ($Protected -and $Preserve) 'permission repair would discard existing access/deny rules'
            $this.AreAccessRulesProtected = $true
            foreach ($rule in $this.Rules) { $rule.IsInherited = $false }
        }
        $script:ProfileAcl | Add-Member ScriptMethod RemoveAccessRuleSpecific {
            param($Rule)
            $this.Rules = @($this.Rules | Where-Object { -not [object]::ReferenceEquals($_, $Rule) })
        }
    }
    function Get-Acl {
        param($LiteralPath)
        Assert ($LiteralPath -ceq $BrowserProfile) 'read ACL outside selected profile'
        $script:ProfileReads++
        $script:ProfileAcl
    }
    function Set-Acl {
        param($LiteralPath, $AclObject)
        Assert ($LiteralPath -ceq $BrowserProfile) 'changed ACL outside selected profile'
        $script:ProfileWrites++
        if ($script:ProfileWriteFails) { throw 'fixture_acl_write_failed' }
        if ($script:ProfileWriteIgnored) { $script:ProfileAcl.Rules += New-Rule 'fixture-extra-group' }
    }
    function Get-Item {
        param($LiteralPath, [switch]$Force)
        Assert ($LiteralPath -ceq $BrowserProfile) 'inspected another directory'
        [pscustomobject]@{PSIsContainer=$true;Attributes=if ($script:ProfileReparse) {
            [IO.FileAttributes]::ReparsePoint
        } else { [IO.FileAttributes]::Directory }}
    }
    function Test-Path { param($LiteralPath) $script:ProfileExists }
    function New-Item {
        param($ItemType, $Path, [switch]$Force)
        Assert ($ItemType -eq 'Directory' -and $Path -ceq $BrowserProfile) 'created outside the selected directory'
        $script:ProfileExists = $true
    }
    Reset-Profile
    $rejected = $false
    try { Assert-ProfilePrivacy } catch { $rejected = $_.Exception.Message -eq 'browser_profile_must_be_private' }
    Assert ($rejected -and $script:ProfileWrites -eq 0) 'privacy assertion accepted inherited cross-user access'
    Assert-ProfilePrivacy -RepairInherited
    Assert ($script:ProfileWrites -eq 1 -and $script:ProfileReads -ge 3) 'repair did not write and reread the OS ACL'
    Assert ($script:ProfileAcl.AreAccessRulesProtected) 'parent permissions would reappear'
    Assert (($script:ProfileAcl.Rules.IdentityReference.Value -join ',') -ceq 'S-1-5-18,S-1-5-32-544,fixture-user,fixture-denied') 'repair changed required users or dropped a deny'
    Assert-ProfilePrivacy
    Initialize-BrowserProfile
    Assert ($script:ProfileWrites -eq 1) 'private profile was rewritten on repeat startup'
    Reset-Profile
    $script:ProfileExists = $false
    Initialize-BrowserProfile
    Assert ($script:ProfileExists -and $script:ProfileWrites -eq 1) 'new profile inherited an unusable ACL'
    foreach ($case in @('owner', 'explicit', 'custom', 'redirect', 'write_failure', 'write_ignored')) {
        Reset-Profile
        $BrowserProfile = $script:DefaultBrowserProfile
        $expected = 'browser_profile_must_be_private'
        switch ($case) {
            'owner' { $script:ProfileAcl.Owner = 'fixture-other-owner'; $expected = 'browser_profile_owner_mismatch' }
            'explicit' { $script:ProfileAcl.Rules[0].IsInherited = $false }
            'custom' { $BrowserProfile = 'C:\custom\profile' }
            'redirect' { $script:ProfileReparse = $true; $expected = 'browser_profile_directory_required' }
            'write_failure' { $script:ProfileWriteFails = $true; $expected = 'fixture_acl_write_failed' }
            'write_ignored' { $script:ProfileWriteIgnored = $true }
        }
        $caught = ''
        try { Initialize-BrowserProfile } catch { $caught = $_.Exception.Message }
        Assert ($caught -eq $expected) "profile repair did not reject $case"
        $expectedWrites = if ($case -in @('write_failure', 'write_ignored')) { 1 } else { 0 }
        Assert ($script:ProfileWrites -eq $expectedWrites) "unexpected mutation for $case"
    }
    Reset-Profile
    $BrowserProfile = $script:DefaultBrowserProfile
    $script:RequestedPort = 9222
    function Assert-LocalOwner { $true }
    function Get-LocalCDP { [pscustomobject]@{webSocketDebuggerUrl='ws://127.0.0.1:9222/devtools/browser/fixture'} }
    $observed = @(Ensure-Browser)
    Assert ($observed.Count -eq 1 -and $observed[0].webSocketDebuggerUrl) 'permission message polluted the browser result'
    Assert ($script:ProfileWrites -eq 1) 'browser reuse bypassed profile preparation'
}
$BrowserProfile = 'C:\Users\Test User\Hypoloom\Institutional-msedge'
$LocalPort = 9222
foreach ($flag in @('--user-data-dir="' + $BrowserProfile + '"', '"--user-data-dir=' + $BrowserProfile + '"', '--user-data-dir=' + $BrowserProfile)) {
    Assert (Test-ProfileCommand ('msedge.exe ' + $flag + ' --remote-debugging-port=9222')) 'dedicated command rejected'
}
Assert (-not (Test-ProfileCommand ('msedge.exe --user-data-dir="' + $BrowserProfile + '-other" --remote-debugging-port=9222'))) 'adopted different profile'
Assert (-not (Test-ProfileCommand ('msedge.exe --user-data-dir="' + $BrowserProfile + '" --remote-debugging-port=92221'))) 'adopted different port'
Assert (Same-Browser @{ webSocketDebuggerUrl = 'ws://localhost:9222/devtools/browser/one' } @{ webSocketDebuggerUrl = 'ws://127.0.0.1:9999/devtools/browser/one' }) 'forward identity rejected'
Assert (-not (Same-Browser @{ webSocketDebuggerUrl = 'ws://localhost:9222/devtools/browser/one' } @{ webSocketDebuggerUrl = 'ws://localhost:9222/devtools/browser/two' })) 'different browser accepted'
Assert (-not (Same-Browser @{} @{})) 'missing browser accepted'
Assert ((Quote-Native 'space $literal `text') -eq '"space $literal `text"') 'quoted literal changed'
Assert ((Quote-Native 'tail\') -eq '"tail\\"') 'trailing slash incorrectly quoted'
Assert ((Quote-Native 'say"x') -eq '"say\"x"') 'quote incorrectly escaped'
$child = Start-Captured (Get-Process -Id $PID).Path @('-NoProfile', '-Command', 'Start-Sleep -Seconds 60')
$childId = $child.Process.Id
Stop-Child $child
Assert (-not (Get-Process -Id $childId -ErrorAction SilentlyContinue)) 'SSH-shaped child leaked'
if (Test-Path '/bin/bash') {
    $literal = 'space $HOME `literal $(printf altered) '' quote'
    $quoted = Start-Captured '/bin/bash' @('-c', ('exec /usr/bin/printf %s ' + (Quote-Remote $literal)))
    try {
        Assert ($quoted.Process.WaitForExit(5000)) 'remote quote check hung'
        Assert ($quoted.Out.GetAwaiter().GetResult() -ceq $literal) 'remote argument was executed or altered'
    } finally { Stop-Child $quoted }
}
# Init derives standard server locations without modifying SSH authentication.
$Repository = '/repo with spaces/'
$PythonExecutable = ''; $RunsRoot = ''
Resolve-ServerPaths
Assert ($PythonExecutable -ceq '/repo with spaces/.conda/envs/hypoloom/bin/python') 'init Python default incorrect'
Assert ($RunsRoot -ceq '/repo with spaces/state') 'init runs root default incorrect'
$PythonExecutable = '/custom/python'; $RunsRoot = '/custom/state'
Resolve-ServerPaths
Assert ($PythonExecutable -ceq '/custom/python' -and $RunsRoot -ceq '/custom/state') 'explicit client paths replaced'
Assert ((Init-Command).Contains('/autoresearch init')) 'init command not passed to remote Claude'
Assert ((Init-Command -Check).Contains('command -v claude')) 'init did not check remote prerequisites'
$script:InitEvents = @(); $script:ssh = 'Invoke-FixtureSSH'; $Server = 'fixture'
function Start-ScheduledTask { param($TaskName) $script:InitEvents += "task:$TaskName" }
function Invoke-FixtureSSH {
    Assert ($args -contains '-t' -and $args -contains 'fixture') 'remote init lost terminal or SSH target'
    Assert ($args[-1] -ceq (Init-Command)) 'remote init command changed'
    $script:InitEvents += 'remote-init'
    $global:LASTEXITCODE = 0
}
Start-InitSession 'owned-task'
Assert ($script:InitEvents.Count -eq 2 -and $script:InitEvents[0] -eq 'task:owned-task' -and $script:InitEvents[1] -eq 'remote-init') 'init did not start client discovery before remote session'
$script:InitEvents = @(); $Action = 'Connect'
Start-ClientConnection 'owned-task'
Assert ($script:InitEvents.Count -eq 1 -and $script:InitEvents[0] -eq 'task:owned-task') 'connect entered another remote init session'
Assert (-not (Init-Command -Check).Contains('command -v claude')) 'connect unnecessarily requires another Claude session'
# Actual task orchestration with substituted Windows scheduler APIs. The previous
# exact invocation may gain Hidden; unrelated task ownership/options stay strict.
$script:userSid = 'fixture-sid'; $script:ScheduledTask = $null; $script:TaskEvents = @()
function Get-ScheduledTask { param($TaskName, $ErrorAction) $script:ScheduledTask }
function New-ScheduledTaskAction { param($Execute, $Argument) [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal {
    param($UserId, $LogonType, $RunLevel)
    Assert ($UserId -eq 'fixture-sid' -and $LogonType -eq 'Interactive' -and $RunLevel -eq 'Limited') 'task changed desktop user privileges'
    [pscustomobject]@{UserId=$UserId}
}
function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) 'original-trigger' }
function New-ScheduledTaskSettingsSet { 'original-settings' }
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Principal, $Settings)
    $script:TaskEvents += 'register'
    $script:ScheduledTask = [pscustomobject]@{Actions=@($Action);Principal=$Principal;State='Ready';Triggers=$Trigger;Settings=$Settings}
}
function Initialize-BrowserProfile {
    $script:TaskEvents += 'profile'
    if ($script:FailProfilePreparation) { throw 'browser_profile_must_be_private' }
}
function Set-ScheduledTask {
    param($TaskName, $Action)
    $script:TaskEvents += 'update'; $script:ScheduledTask.Actions = @($Action)
}
function Start-ScheduledTask { param($TaskName) $script:TaskEvents += 'start' }
function Stop-ScheduledTask { param($TaskName) $script:TaskEvents += 'stop' }
function Unregister-ScheduledTask { param($TaskName, $Confirm) $script:TaskEvents += 'uninstall' }
$values = @('-NoProfile', '-NonInteractive', '-File', 'C:\entry with spaces.ps1', '-Action', 'Run', '-Server', 'fixture',
    '-BrowserExecutable', 'C:\Edge\msedge.exe', '-BrowserProfile', 'C:\dedicated', '-LocalPort', '0',
    '-Repository', '/repo', '-PythonExecutable', '/python', '-RunsRoot', '/state')
$legacy = ($values | ForEach-Object { Quote-Native $_ }) -join ' '
$hidden = '"-WindowStyle" "Hidden" ' + $legacy
$Action = 'Connect'
Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null
Assert (($script:TaskEvents -join ',') -eq 'profile,register,start') 'profile was not prepared before new task startup'
Assert ($script:ScheduledTask.Actions[0].Arguments -ceq $hidden) 'new discovery task has a visible console'
$script:ScheduledTask.Actions[0].Arguments = $legacy; $script:ScheduledTask.State = 'Running'; $script:TaskEvents = @()
Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null
Assert (($script:TaskEvents -join ',') -eq 'profile,update,stop,start') 'legacy visible task was not updated/restarted'
Assert ($script:ScheduledTask.Actions[0].Arguments -ceq $hidden) 'upgrade altered connection arguments'
Assert ($script:ScheduledTask.Triggers -eq 'original-trigger' -and $script:ScheduledTask.Settings -eq 'original-settings') 'upgrade replaced task settings'
$script:TaskEvents = @()
Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null
Assert (($script:TaskEvents -join ',') -eq 'profile,start') 'repeat connect needlessly rewrote/stopped the current task'
$saved = $script:ScheduledTask
$script:FailProfilePreparation = $true; $script:TaskEvents = @(); $caught = ''
try { Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' } catch { $caught = $_.Exception.Message }
Assert ($caught -eq 'browser_profile_must_be_private' -and ($script:TaskEvents -join ',') -eq 'profile') 'Connect started a task after profile preparation failed'
$script:FailProfilePreparation = $false
# Windows can return a short account name after registering the task by SID.
# Substitute only the OS account lookup; acceptance still uses production code.
function Resolve-AccountSid([string]$Name) {
    switch ($Name) {
        'FixtureUser' { 'fixture-sid' }
        'DESKTOP\FixtureUser' { 'fixture-sid' }
        'other-user' { 'other-sid' }
        'OTHER\FixtureUser' { 'other-sid' }
        default { throw 'fixture_account_unmapped' }
    }
}
foreach ($owner in @('fixture-sid', 'FixtureUser', 'DESKTOP\FixtureUser')) {
    $script:ScheduledTask = [pscustomobject]@{Actions=@([pscustomobject]@{Execute='C:\pwsh.exe';Arguments=$legacy});Principal=[pscustomobject]@{UserId=$owner};State='Ready'}
    $script:TaskEvents = @()
    Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null
    Assert (($script:TaskEvents -join ',') -eq 'profile,update,start') 'same account spelling blocked an idle task upgrade'
    Assert ($script:ScheduledTask.Principal.UserId -ceq $owner) 'account spelling repair rewrote the task principal'
}
foreach ($arguments in @($legacy, $hidden)) {
    foreach ($mismatch in @('owner', 'other_domain', 'unmapped_owner', 'missing_owner', 'executable', 'arguments', 'extra_action')) {
        $script:ScheduledTask = [pscustomobject]@{Actions=@([pscustomobject]@{Execute='C:\pwsh.exe';Arguments=$arguments});Principal=[pscustomobject]@{UserId='fixture-sid'};State='Running'}
        switch ($mismatch) {
            'owner' { $script:ScheduledTask.Principal.UserId = 'other-user' }
            'other_domain' { $script:ScheduledTask.Principal.UserId = 'OTHER\FixtureUser' }
            'unmapped_owner' { $script:ScheduledTask.Principal.UserId = 'missing-user' }
            'missing_owner' { $script:ScheduledTask.Principal.UserId = '' }
            'executable' { $script:ScheduledTask.Actions[0].Execute = 'C:\other.exe' }
            'arguments' { $script:ScheduledTask.Actions[0].Arguments += ' "-RunsRoot" "/other"' }
            'extra_action' { $script:ScheduledTask.Actions += $script:ScheduledTask.Actions[0] }
        }
        $script:TaskEvents = @(); $rejected = $false
        try { Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null }
        catch { $rejected = $_.Exception.Message -eq 'scheduled_task_ownership_mismatch' }
        Assert ($rejected -and $script:TaskEvents.Count -eq 0) 'changed task was adopted or mutated'
    }
}
$script:ScheduledTask = $saved; $script:TaskEvents = @(); $Action = 'Uninstall'
Invoke-ClientTask 'owned-task' $values 'C:\pwsh.exe' | Out-Null
Assert (($script:TaskEvents -join ',') -eq 'stop,uninstall') 'hidden task cannot be uninstalled'
# Exercise discovery changes through the real loop; Windows APIs are substituted.
$Repository = '/repo'; $PythonExecutable = '/python'; $RunsRoot = '/state'; $Server = 'fixture'
$script:ssh = 'ssh-fixture'; $script:Step = 0; $script:Browsers = 0; $script:Forwards = @(); $script:Stopped = @()
$a = [pscustomobject]@{socket_path='/tmp/run-a/browser.sock';state_root='/state/a';run_id='a'}
$b = [pscustomobject]@{socket_path='/tmp/run-b/browser.sock';state_root='/state/b';run_id='b'}
function Get-RunConnections {
    if ($script:Step -eq 1) { @($a, $b) } elseif ($script:Step -eq 2) { @($b) } else { @() }
}
function Ensure-Browser { $script:Browsers++; @{webSocketDebuggerUrl='ws://127.0.0.1:9222/devtools/browser/fixture'} }
function Start-Captured([string]$Executable, [string[]]$Values) {
    $forward = $Values -contains '-R'
    $label = if ($forward) { $Values[$Values.IndexOf('-R') + 1] } else { 'readiness' }
    if ($forward) { $script:Forwards += $label }
    $process = [pscustomobject]@{HasExited=$false;ExitCode=0}
    $process | Add-Member ScriptMethod WaitForExit { param($Timeout) $true }
    @{Process=$process;Label=$label}
}
function Stop-Child($Child) { if ($Child.Label -ne 'readiness') { $script:Stopped += $Child.Label }; $Child.Process.HasExited=$true }
function Start-Sleep { param($Seconds) $script:Step++; if ($script:Step -eq 5) { throw 'fixture_complete' } }
$messages = @(try { Watch-Runs } catch { Assert ($_.Exception.Message -eq 'fixture_complete') 'unexpected connector error' })
Assert (@($messages | Where-Object { $_ -like 'waiting_for_preparation_or_run;*' }).Count -eq 2) 'idle waiting was silent or logged every poll'
Assert ($script:Browsers -eq 2 -and $script:Forwards.Count -eq 2) 'ended runs were reconnected or idle discovery opened a browser'
Assert ($script:Stopped.Count -eq 2 -and $script:Stopped[0] -like '/tmp/run-a/*' -and $script:Stopped[1] -like '/tmp/run-b/*') 'one run retirement disturbed another forward'
Write-Output 'connector parser, literal arguments, browser identity, child cleanup and per-run retirement passed'
