param([Parameter(Mandatory)][string]$Connector)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Reset-Browser {
    $script:BrowserExecutable = 'C:\Program Files\Edge\msedge.exe'
    $script:BrowserProfile = 'C:\Users\Fixture\Hypoloom\Institutional-msedge'
    $script:userSid = 'fixture-user'
    $script:RequestedPort = 0; $script:LocalPort = 0; $script:BrowserCommandPort = $null
    $script:Candidates = @(); $script:CurrentBrowser = $null; $script:OwnedBrowser = $null
    $script:Now=0L; $script:UnhealthySince=$null; $script:LastScopeContact=0L
    $script:Launches = 0; $script:Waits = 0; $script:CdpReads = 0
    $script:CdpUnavailable = $false; $script:NoListener = $false
    $script:LaunchOwner = 'fixture-user'; $script:ListenAddress = '127.0.0.1'
}
function New-Browser([int]$Port, [string]$Owner = 'fixture-user') {
    [pscustomobject]@{ExecutablePath=$BrowserExecutable;ProcessId=123;Owner=$Owner;
        CommandLine=('msedge.exe "--user-data-dir=' + $BrowserProfile + '" "--remote-debugging-port=' + $Port + '"');Port=$Port}
}
# Substitute desktop APIs only. The real allocator, discovery and ownership
# validator run; an actual OS socket checks allocation/release below.
function Initialize-BrowserProfile {}
function Assert-ProfilePrivacy {}
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($Filter -like 'ProcessId=*') { return $script:CurrentBrowser }
    $script:Candidates
}
function Invoke-CimMethod { param($InputObject, $MethodName) @{Sid=$InputObject.Owner} }
function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    if ($script:CurrentBrowser -and -not $script:NoListener -and $LocalPort -eq $script:CurrentBrowser.Port) {
        [pscustomobject]@{LocalAddress=$script:ListenAddress;OwningProcess=$script:CurrentBrowser.ProcessId}
    }
}
function Get-LocalCDP {
    $script:CdpReads++
    if (-not $script:CdpUnavailable) { @{webSocketDebuggerUrl="ws://127.0.0.1:$LocalPort/devtools/browser/fixture"} }
}
function Get-ElapsedMilliseconds { $script:Now }
function Start-Sleep { param($Seconds, $Milliseconds) $script:Waits++; $script:Now += 1000 }
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$PassThru)
    $script:Launches++
    $match = [regex]::Match($ArgumentList, '--remote-debugging-port=(\d+)')
    Assert ($match.Success -and [int]$match.Groups[1].Value -gt 0) 'browser launched with automation-triggering port 0'
    Assert ($ArgumentList.Contains('--remote-debugging-address=127.0.0.1')) 'browser lost loopback binding'
    $script:CurrentBrowser = New-Browser ([int]$match.Groups[1].Value) $script:LaunchOwner
    $script:CurrentBrowser.CommandLine = $ArgumentList
    $script:Candidates = @($script:CurrentBrowser)
    $owned=[pscustomobject]@{Id=123;HasExited=$false}
    $owned | Add-Member ScriptMethod Dispose {}
    $owned | Add-Member ScriptMethod Kill { param($EntireTree) $this.HasExited=$true }
    $owned | Add-Member ScriptMethod WaitForExit { param($Timeout) $true }
    $owned
}
function Expect-Failure([string]$Reason) {
    $caught = ''
    try { Ensure-Browser | Out-Null } catch { $caught = $_.Exception.Message }
    Assert ($caught -eq $Reason) "expected $Reason; got $caught"
}

Reset-Browser
$observed = Ensure-Browser
Assert ($observed.webSocketDebuggerUrl -and $LocalPort -gt 0 -and $RequestedPort -eq 0) 'automatic launch failed'
Assert ($Launches -eq 1 -and $CdpReads -eq 1) 'fresh launch did extra browser/CDP work'
$firstPort = $LocalPort
# The same invocation reuses only the Process object it launched.
Ensure-Browser | Out-Null
Assert ($LocalPort -eq $firstPort -and $Launches -eq 1) 'owned browser not reused'
# A closed owned browser gets a fresh bounded launch.
$script:OwnedBrowser.HasExited=$true
$script:CurrentBrowser = $null; $script:Candidates = @()
Ensure-Browser | Out-Null
Assert ($LocalPort -gt 0 -and $Launches -eq 2) 'closed browser did not relaunch'

Reset-Browser
$script:RequestedPort = 9222; $script:LocalPort = 9222
Ensure-Browser | Out-Null
Assert ($LocalPort -eq 9222 -and $CurrentBrowser.Port -eq 9222) 'explicit port changed'
Ensure-Browser | Out-Null
Assert ($Launches -eq 1) 'explicit browser not reused'

Reset-Browser
$script:Candidates = @((New-Browser 0))
Expect-Failure 'browser_profile_already_in_use_close_previous_dedicated_window'
Assert ($Launches -eq 0 -and $CdpReads -eq 0) 'legacy browser was silently reused or replaced'

Reset-Browser
$script:Candidates = @((New-Browser 45678 'other-user'))
Expect-Failure 'browser_profile_already_in_use_close_previous_dedicated_window'
Assert ($Launches -eq 0 -and $CdpReads -eq 0) 'foreign candidate adopted'

Reset-Browser
$otherProfile = New-Browser 45678
$otherProfile.CommandLine = $otherProfile.CommandLine.Replace($BrowserProfile, $BrowserProfile + '-other')
$otherExecutable = New-Browser 45679
$otherExecutable.ExecutablePath = 'C:\Other\msedge.exe'
$script:Candidates = @($otherProfile, $otherExecutable)
Ensure-Browser | Out-Null
Assert ($Launches -eq 1) 'unrelated browser prevented fresh launch'

# Port selection is not ownership. A different owner winning the bind, or a
# public listener, must fail before requesting any browser endpoint.
Reset-Browser
$script:LaunchOwner = 'other-user'
Expect-Failure 'browser_endpoint_ownership_mismatch'
Assert ($CdpReads -eq 0 -and $Launches -eq 1) 'port collision bypassed ownership check'
Reset-Browser
$script:ListenAddress = '0.0.0.0'
Expect-Failure 'browser_listener_must_be_loopback'
Assert ($CdpReads -eq 0) 'non-loopback listener contacted'

Reset-Browser
$script:NoListener = $true
Expect-Failure 'browser_start_failed_check_profile_and_desktop'
Assert ($Launches -eq 1 -and $Waits -eq 30 -and $CdpReads -eq 0) 'startup was not bounded'

Reset-Browser
$script:CurrentBrowser = New-Browser 45678
$script:Candidates = @($script:CurrentBrowser)
$script:CdpUnavailable = $true
Expect-Failure 'browser_profile_already_in_use_close_previous_dedicated_window'
Assert ($Launches -eq 0 -and $CdpReads -eq 0) 'existing browser was contacted or adopted'

$selected = Get-AvailableBrowserPort
Assert ($selected -gt 0 -and $selected -le 65535) 'OS allocation returned invalid port'
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $selected)
try { $probe.Start(); Assert ($probe.LocalEndpoint.Port -eq $selected) 'allocator did not release socket' }
finally { $probe.Stop() }
Write-Output 'browser_port_checks_passed'
