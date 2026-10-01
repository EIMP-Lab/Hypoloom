#Requires -Version 7.2
<# Dedicated Windows browser and loopback SSH forwarding. No login secrets are stored.
   Run in the desktop user's PowerShell. Install adds an optional current-user logon task.
   Keep this file at a stable path when installing the task. #>
[CmdletBinding()]
param(
    [ValidateSet('Run', 'Status', 'Install', 'Stop', 'Uninstall')][string]$Action = 'Run',
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.@-]*$')][string]$Server,
    [string]$BrowserExecutable,
    [string]$BrowserProfile,
    [ValidateRange(1024, 65535)][int]$LocalPort = 9222,
    [ValidateRange(1024, 65535)][int]$RemotePort = 9222
)
$ErrorActionPreference = 'Stop'

function Quote-Native([string]$Value) {
    # CommandLineToArgvW quoting; never construct a PowerShell expression.
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Start-Captured([string]$Executable, [string[]]$Values) {
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($value in $Values) { $info.ArgumentList.Add($value) }
    $process = [Diagnostics.Process]::Start($info)
    @{ Process = $process; Out = $process.StandardOutput.ReadToEndAsync(); Err = $process.StandardError.ReadToEndAsync() }
}

function Stop-Child($Child) {
    if ($null -ne $Child) {
        if (-not $Child.Process.HasExited) { $Child.Process.Kill(); $Child.Process.WaitForExit() }
        $Child.Process.Dispose()
    }
}

function Get-LocalCDP {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/json/version" -NoProxy -TimeoutSec 3 }
    catch { $null }
}

function Get-RemoteCDP {
    # The only remote command is fixed curl text plus a validated integer port.
    $child = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server,
        "curl --noproxy '*' --silent --fail --max-time 3 http://127.0.0.1:$RemotePort/json/version")
    try {
        if ($child.Process.WaitForExit(10000) -and $child.Process.ExitCode -eq 0) {
            try { return ($child.Out.GetAwaiter().GetResult() | ConvertFrom-Json) } catch { return $null }
        }
        return $null
    } finally { Stop-Child $child }
}

function Same-Browser($Local, $Remote) {
    if (-not $Local -or -not $Remote -or -not $Local.webSocketDebuggerUrl -or -not $Remote.webSocketDebuggerUrl) { return $false }
    # CDP's existing browser identity verifies the forward; never print its URL.
    ([uri]$Local.webSocketDebuggerUrl).AbsolutePath -eq ([uri]$Remote.webSocketDebuggerUrl).AbsolutePath
}

function Test-ProfileCommand([string]$Command) {
    $profile = [regex]::Escape($BrowserProfile)
    $data = '(?:^|\s)(?:"--user-data-dir=' + $profile + '"|--user-data-dir="' + $profile + '"|--user-data-dir=' + $profile + ')(?=\s|$)'
    $port = '(?:^|\s)"?--remote-debugging-port=' + $LocalPort + '"?(?=\s|$)'
    $Command -match $data -and $Command -match $port
}

function Assert-LocalOwner {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)
    if (-not $listeners) { return $false }
    foreach ($listener in $listeners) {
        if ($listener.LocalAddress -notin @('127.0.0.1', '::1')) { throw 'browser_listener_must_be_loopback' }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
        if ($owner.Sid -ne $script:userSid -or $process.ExecutablePath -ne $BrowserExecutable -or
            -not (Test-ProfileCommand $process.CommandLine)) { throw 'browser_endpoint_ownership_mismatch' }
    }
    $true
}

function Assert-ProfilePrivacy {
    $acl = Get-Acl -LiteralPath $BrowserProfile
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $script:userSid) { throw 'browser_profile_owner_mismatch' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($script:userSid, 'S-1-5-18', 'S-1-5-32-544')) {
            throw 'browser_profile_must_be_private'
        }
    }
}

function Ensure-Browser {
    if (Test-Path -LiteralPath $BrowserProfile) { Assert-ProfilePrivacy }
    if (Assert-LocalOwner) {
        $observed = Get-LocalCDP
        if (-not $observed.webSocketDebuggerUrl) { throw 'existing_browser_cdp_unavailable' }
        return $observed
    }
    $null = New-Item -ItemType Directory -Path $BrowserProfile -Force
    Assert-ProfilePrivacy
    $values = @('--no-first-run', '--no-default-browser-check', '--remote-debugging-address=127.0.0.1',
        "--remote-debugging-port=$LocalPort", "--user-data-dir=$BrowserProfile", 'about:blank')
    # Leave browser/profile cleanup to the operator; never delete stored sessions.
    Start-Process -FilePath $BrowserExecutable -ArgumentList (($values | ForEach-Object { Quote-Native $_ }) -join ' ')
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Assert-LocalOwner) {
            $observed = Get-LocalCDP
            if ($observed.webSocketDebuggerUrl) { return $observed }
        }
    }
    throw 'browser_start_failed_check_profile_and_desktop'
}

function Connector-Main {
    if (-not $IsWindows) { throw 'windows_desktop_required' }
    $script:userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $script:ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    if (-not $script:BrowserExecutable) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
        $script:BrowserExecutable = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $BrowserExecutable -or -not (Test-Path -LiteralPath $BrowserExecutable -PathType Leaf)) { throw 'browser_executable_missing' }
    $script:BrowserExecutable = (Resolve-Path -LiteralPath $BrowserExecutable).Path
    if (-not $script:BrowserProfile) {
        $script:BrowserProfile = Join-Path $env:LOCALAPPDATA ('Hypoloom\Institutional-' + [IO.Path]::GetFileNameWithoutExtension($BrowserExecutable))
    }
    if (-not [IO.Path]::IsPathFullyQualified($BrowserProfile)) { throw 'absolute_dedicated_profile_required' }
    $script:BrowserProfile = [IO.Path]::GetFullPath($BrowserProfile).TrimEnd('\')
    foreach ($personal in @('Google\Chrome\User Data', 'Microsoft\Edge\User Data', 'Chromium\User Data')) {
        $path = Join-Path $env:LOCALAPPDATA $personal
        if ($BrowserProfile -eq $env:USERPROFILE -or $BrowserProfile -eq $path -or $BrowserProfile.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($BrowserProfile + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'personal_browser_profile_forbidden' }
    }
    $taskName = "Hypoloom-Institutional-$Server-$RemotePort"
    $taskValues = @('-NoProfile', '-NonInteractive', '-File', $PSCommandPath, '-Action', 'Run', '-Server', $Server,
        '-BrowserExecutable', $BrowserExecutable, '-BrowserProfile', $BrowserProfile, '-LocalPort', "$LocalPort", '-RemotePort', "$RemotePort")
    $taskArguments = ($taskValues | ForEach-Object { Quote-Native $_ }) -join ' '
    $powershell = (Get-Process -Id $PID).Path
    if ($Action -in @('Install', 'Stop', 'Uninstall')) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task -and (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $powershell -or
                $task.Actions[0].Arguments -ne $taskArguments -or $task.Principal.UserId -notin @($script:userSid, [Security.Principal.WindowsIdentity]::GetCurrent().Name))) {
            throw 'scheduled_task_ownership_mismatch'
        }
        if ($Action -eq 'Install') {
            if (-not $task) {
                $principal = New-ScheduledTaskPrincipal -UserId $script:userSid -LogonType Interactive -RunLevel Limited
                $trigger = New-ScheduledTaskTrigger -AtLogOn -User $script:userSid
                $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
                $command = New-ScheduledTaskAction -Execute $powershell -Argument $taskArguments
                $null = Register-ScheduledTask -TaskName $taskName -Action $command -Trigger $trigger -Principal $principal -Settings $settings
            }
            Write-Output 'installed_for_next_user_logon; use Run now to connect'
        } else {
            if ($task) {
                Stop-ScheduledTask -TaskName $taskName
                if ($Action -eq 'Uninstall') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
            }
            Write-Output 'task_stopped; browser_profile_preserved; foreground_Run_uses_Ctrl+C'
        }
        return
    }
    if ($Action -eq 'Status') {
        $owned = Assert-LocalOwner
        $local = Get-LocalCDP
        $remote = Get-RemoteCDP
        if ($owned -and (Same-Browser $local $remote)) { Write-Output 'ready'; return }
        throw 'connection_unavailable_check_browser_ssh_and_remote_curl'
    }
    $local = Ensure-Browser
    # Check unattended SSH authentication/host trust before any retry loop.
    $check = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server, 'command -v curl >/dev/null')
    try {
        if (-not $check.Process.WaitForExit(10000) -or $check.Process.ExitCode -ne 0) { throw 'ssh_preflight_failed_check_key_known_hosts_and_remote_curl' }
    } finally { Stop-Child $check }
    $tunnel = $null
    $failures = 0
    try {
        while ($true) {
          try {
            $remote = Get-RemoteCDP
            if ($remote -and -not (Same-Browser $local $remote)) { throw 'remote_endpoint_occupied' }
            if (-not (Same-Browser $local $remote)) {
                Stop-Child $tunnel; $tunnel = $null
                $tunnel = Start-Captured $script:ssh @('-N', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                    '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                    '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-R', "127.0.0.1:${RemotePort}:127.0.0.1:$LocalPort", $Server)
                Start-Sleep -Seconds 2
                if ($tunnel.Process.HasExited) { throw 'ssh_forward_failed_check_remote_port_and_authorization' }
                $remote = Get-RemoteCDP
                if (-not (Same-Browser $local $remote)) { throw 'forward_health_failed' }
                Write-Output 'ready; keep this connector running; Ctrl+C stops its SSH child'
            }
            $failures = 0
            Start-Sleep -Seconds 15
            if (-not (Assert-LocalOwner)) { throw 'browser_closed_restart_connector_after_opening_it' }
            $local = Get-LocalCDP
            if (-not $local) { throw 'browser_cdp_unavailable' }
          } catch {
            if ($_.Exception.Message -notin @('ssh_forward_failed_check_remote_port_and_authorization', 'forward_health_failed') -or ++$failures -ge 3) { throw }
            Stop-Child $tunnel; $tunnel = $null
            Write-Output 'connection_retry_in_30_seconds'
            Start-Sleep -Seconds 30
          }
        }
    } finally { Stop-Child $tunnel }
}

try { Connector-Main } catch {
    # No raw SSH/browser diagnostics, callback URLs, or credentials in logs.
    $reason = $_.Exception.Message
    if ($reason -notmatch '^[a-z][a-z0-9_]+$') { $reason = 'connector_unavailable_check_local_dependencies_and_permissions' }
    Write-Output $reason
    exit 2
}
