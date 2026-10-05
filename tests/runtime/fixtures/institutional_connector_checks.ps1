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
if ($IsWindows) {
    & (Join-Path $PSScriptRoot 'institutional_acl_native.ps1') -Connector $Connector
}
# Exercise the real privacy validator against the reported inherited grant.
# Only Windows ACL/filesystem APIs are substituted; no real login data is used.
& {
    $script:userSid = 'fixture-user'
    $script:DefaultBrowserProfile = 'C:\Users\Fixture\Hypoloom\Institutional-msedge'
    $BrowserProfile = $script:DefaultBrowserProfile
    function New-Rule($Sid, $Inherited = $true, $Type = 'Allow') {
        [pscustomobject]@{IdentityReference=[pscustomobject]@{Value=$Sid};IsInherited=$Inherited;
            AccessControlType=$Type;FileSystemRights=[Security.AccessControl.FileSystemRights]::FullControl;
            InheritanceFlags='ContainerInherit, ObjectInherit';PropagationFlags='None'}
    }
    function Reset-Profile {
        $script:ProfileExists = $true; $script:ProfileWrites = 0; $script:ProfileReads = 0
        $script:ProfileReparse = $false; $script:ProfileWriteFails = $false; $script:ProfileWriteIgnored = $false
        $script:ProfileAcl = [pscustomobject]@{Owner='fixture-user';AreAccessRulesProtected=$false;Rules=@(
            (New-Rule 'fixture-extra-group'), (New-Rule 'S-1-5-18'),
            (New-Rule 'S-1-5-32-544'), (New-Rule 'fixture-user'), (New-Rule 'fixture-denied' $true 'Deny'))}
        $script:ProfileAcl | Add-Member ScriptMethod GetOwner { param($Type) [pscustomobject]@{Value=$this.Owner} }
        $script:ProfileAcl.Rules[0].FileSystemRights = [Security.AccessControl.FileSystemRights]::ReadAndExecute
        $script:ProfileAcl | Add-Member ScriptMethod GetAccessRules { param($Explicit, $Inherited, $Type) $this.Rules }
        $script:ProfileAcl | Add-Member ScriptMethod SetAccessRuleProtection {
            param($Protected, $Preserve)
            $this.AreAccessRulesProtected = $Protected
            # .NET preserves inherited flags in memory when Preserve is true.
            # Conversion on filesystem persistence is a different operation.
            if ($Protected -and -not $Preserve) {
                $this.Rules = @($this.Rules | Where-Object { -not $_.IsInherited })
            }
        }
        $script:ProfileAcl | Add-Member ScriptMethod RemoveAccessRuleSpecific {
            param($Rule)
            $this.Rules = @($this.Rules | Where-Object { $_.IsInherited -or -not [object]::ReferenceEquals($_, $Rule) })
        }
        $script:ProfileAcl | Add-Member ScriptMethod AddAccessRule {
            param($Rule)
            Assert (-not $Rule.IsInherited) 'added another inherited rule'
            $this.Rules += $Rule
        }
        $script:ProfileAcl | Add-Member ScriptMethod AccessRuleFactory {
            param($Identity, [int]$Mask, $Inherited, $Flags, $Propagation, $Type)
            [pscustomobject]@{IdentityReference=$Identity;FileSystemRights=[Security.AccessControl.FileSystemRights]$Mask;
                InheritanceFlags=$Flags;PropagationFlags=$Propagation;AccessControlType=$Type;IsInherited=$Inherited}
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
        Assert (-not @($AclObject.Rules | Where-Object { $_.IdentityReference.Value -eq 'fixture-extra-group' }).Count) 'persisted an intermediate ACL with extra access'
        Assert ($AclObject.Rules.Count -ge 4) 'persisted an ACL that discarded retained users or deny'
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
    foreach ($rule in $script:ProfileAcl.Rules) {
        Assert ($rule.FileSystemRights -eq 'FullControl' -and $rule.InheritanceFlags -eq 'ContainerInherit, ObjectInherit' -and $rule.PropagationFlags -eq 'None') 'copy changed rights or propagation'
    }
    Assert-ProfilePrivacy
    Initialize-BrowserProfile
    Assert ($script:ProfileWrites -eq 1) 'private profile was rewritten on repeat startup'
    Reset-Profile
    $script:ProfileExists = $false
    Initialize-BrowserProfile
    Assert ($script:ProfileExists -and $script:ProfileWrites -eq 1) 'new profile inherited an unusable ACL'
    Reset-Profile
    $script:ProfileAcl.Rules[1].IsInherited = $false
    $script:ProfileAcl.Rules[4].IsInherited = $false
    $script:ProfileAcl.Rules[2].FileSystemRights = [Security.AccessControl.FileSystemRights]::ReadData
    $script:ProfileAcl.Rules[2].PropagationFlags = 'InheritOnly'
    $explicitSystem = $script:ProfileAcl.Rules[1]; $explicitDeny = $script:ProfileAcl.Rules[4]
    Initialize-BrowserProfile
    Assert ($script:ProfileAcl.Rules -contains $explicitSystem -and $script:ProfileAcl.Rules -contains $explicitDeny) 'repair replaced explicit retained rules'
    $copiedAdmin = @($script:ProfileAcl.Rules | Where-Object { $_.IdentityReference.Value -eq 'S-1-5-32-544' })[0]
    Assert ($copiedAdmin.FileSystemRights -eq 'ReadData' -and $copiedAdmin.PropagationFlags -eq 'InheritOnly') 'repair broadened a copied rule'
    foreach ($case in @('owner', 'explicit', 'custom', 'redirect', 'write_failure', 'write_ignored')) {
        Reset-Profile
        $BrowserProfile = $script:DefaultBrowserProfile
        $expected = 'browser_profile_must_be_private'
        switch ($case) {
            'owner' { $script:ProfileAcl.Owner = 'fixture-other-owner'; $expected = 'browser_profile_owner_mismatch' }
            'explicit' { $script:ProfileAcl.Rules[0].IsInherited = $false; $expected = 'browser_profile_explicit_access_requires_manual_permissions' }
            'custom' { $BrowserProfile = 'C:\custom\profile'; $expected = 'browser_profile_custom_path_requires_manual_permissions' }
            'redirect' { $script:ProfileReparse = $true; $expected = 'browser_profile_directory_required' }
            'write_failure' { $script:ProfileWriteFails = $true; $expected = 'browser_profile_permissions_write_failed' }
            'write_ignored' { $script:ProfileWriteIgnored = $true; $expected = 'browser_profile_permissions_not_applied' }
        }
        $caught = ''
        try { Initialize-BrowserProfile } catch { $caught = $_.Exception.Message }
        Assert ($caught -eq $expected) "profile repair did not reject $case"
        $expectedWrites = if ($case -in @('write_failure', 'write_ignored')) { 1 } else { 0 }
        Assert ($script:ProfileWrites -eq $expectedWrites) "unexpected mutation for $case"
    }

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
Write-Output 'connector ACL privacy, literal arguments, browser identity and owned child cleanup passed'
