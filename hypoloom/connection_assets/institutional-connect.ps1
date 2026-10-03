#Requires -Version 7.2
<# Dedicated Windows browser and loopback SSH forwarding. No login secrets are stored.
   Run in the desktop user's PowerShell. Install adds an optional current-user logon task.
   Keep this file at a stable path when installing the task. #>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Connect', 'Init', 'Run', 'Status', 'Install', 'Stop', 'Uninstall')][string]$Action = 'Run',
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.@-]*$')][string]$Server,
    [string]$BrowserExecutable,
    [string]$BrowserProfile,
    [ValidateRange(0, 65535)][int]$LocalPort = 0,
    [string]$Repository,
    [string]$PythonExecutable,
    [string]$RunsRoot
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

function Same-Browser($Local, $Remote) {
    if (-not $Local -or -not $Remote -or -not $Local.webSocketDebuggerUrl -or -not $Remote.webSocketDebuggerUrl) { return $false }
    # CDP's existing browser identity verifies the forward; never print its URL.
    ([uri]$Local.webSocketDebuggerUrl).AbsolutePath -eq ([uri]$Remote.webSocketDebuggerUrl).AbsolutePath
}

function Test-ProfileCommand([string]$Command) {
    $profile = [regex]::Escape($BrowserProfile)
    $data = '(?:^|\s)(?:"--user-data-dir=' + $profile + '"|--user-data-dir="' + $profile + '"|--user-data-dir=' + $profile + ')(?=\s|$)'
    $selectedPort = if ($null -ne $script:BrowserCommandPort) { $script:BrowserCommandPort } else { $LocalPort }
    $port = '(?:^|\s)"?--remote-debugging-port=' + $selectedPort + '"?(?=\s|$)'
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

function Assert-ProfilePrivacy([switch]$RepairInherited) {
    $acl = Get-Acl -LiteralPath $BrowserProfile
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $script:userSid) { throw 'browser_profile_owner_mismatch' }
    $allowed = @($script:userSid, 'S-1-5-18', 'S-1-5-32-544')
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    $unexpected = @($rules | Where-Object {
        $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -notin $allowed
    })
    if (-not $unexpected.Count) { return }
    if (-not $RepairInherited) { throw 'browser_profile_must_be_private' }
    if ($BrowserProfile -ne $script:DefaultBrowserProfile) {
        throw 'browser_profile_custom_path_requires_manual_permissions'
    }
    if (@($unexpected | Where-Object { -not $_.IsInherited }).Count) {
        throw 'browser_profile_explicit_access_requires_manual_permissions'
    }
    $directory = Get-Item -LiteralPath $BrowserProfile -Force
    if (-not $directory.PSIsContainer -or ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'browser_profile_directory_required'
    }
    # Restrict only this owned default profile. Preserve allowed identities and
    # denies; never edit its parent, group membership, or browser login content.
    # Protect(preserve=true) leaves inherited ACE flags intact in memory, and
    # RemoveAccessRuleSpecific skips those ACEs. Build retained explicit copies
    # before the single filesystem write instead of persisting that intermediate ACL.
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in $rules) {
        if ($rule.IsInherited -and ($rule.AccessControlType -ne 'Allow' -or $rule.IdentityReference.Value -in $allowed)) {
            $copy = $acl.AccessRuleFactory($rule.IdentityReference, [int]$rule.FileSystemRights, $false,
                $rule.InheritanceFlags, $rule.PropagationFlags, $rule.AccessControlType)
            $acl.AddAccessRule($copy)
        }
    }
    try { Set-Acl -LiteralPath $BrowserProfile -AclObject $acl }
    catch { throw 'browser_profile_permissions_write_failed' }
    try { Assert-ProfilePrivacy }
    catch {
        if ($_.Exception.Message -eq 'browser_profile_must_be_private') { throw 'browser_profile_permissions_not_applied' }
        throw
    }
    Write-Host 'browser_profile_permissions_repaired; login_data_preserved'
}

function Initialize-BrowserProfile {
    if (-not (Test-Path -LiteralPath $BrowserProfile)) {
        $null = New-Item -ItemType Directory -Path $BrowserProfile -Force
    }
    Assert-ProfilePrivacy -RepairInherited
}

function Get-AvailableBrowserPort {
    # LocalPort=0 means automatic selection by this helper. Passing port 0 to
    # Chromium itself also enables its webdriver flag, even before CDP attaches.
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        $listener.LocalEndpoint.Port
    } finally { $listener.Stop() }
}

function Ensure-Browser {
    Initialize-BrowserProfile
    $script:LocalPort = $script:RequestedPort
    $script:BrowserCommandPort = $script:RequestedPort
    if ($script:RequestedPort -eq 0) {
        # Discover only this user's exact executable/profile from the live
        # command line; no port file or connector PID registry is needed.
        foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "Name='$([IO.Path]::GetFileName($BrowserExecutable))'")) {
            if ($candidate.ExecutablePath -ne $BrowserExecutable) { continue }
            $matched = [regex]::Match($candidate.CommandLine, '(?:^|\s)"?--remote-debugging-port=(\d+)"?(?=\s|$)')
            if (-not $matched.Success) { continue }
            $script:BrowserCommandPort = [int]$matched.Groups[1].Value
            if (-not (Test-ProfileCommand $candidate.CommandLine)) { continue }
            $owner = Invoke-CimMethod -InputObject $candidate -MethodName GetOwnerSid
            if ($owner.Sid -ne $script:userSid) { throw 'browser_profile_owner_mismatch' }
            # Launch options cannot be updated in an existing browser. Preserve
            # its windows/profile; the operator closes it once before reconnecting.
            if ($script:BrowserCommandPort -eq 0) { throw 'browser_restart_required_close_dedicated_window' }
            $script:LocalPort = $script:BrowserCommandPort
            break
        }
    }
    if ($LocalPort -gt 0 -and (Assert-LocalOwner)) {
        $observed = Get-LocalCDP
        if (-not $observed.webSocketDebuggerUrl) { throw 'existing_browser_cdp_unavailable' }
        return $observed
    }
    Assert-ProfilePrivacy
    if ($script:RequestedPort -eq 0) { $script:LocalPort = Get-AvailableBrowserPort }
    $script:BrowserCommandPort = $LocalPort
    $values = @('--no-first-run', '--no-default-browser-check', '--remote-debugging-address=127.0.0.1',
        "--remote-debugging-port=$LocalPort", "--user-data-dir=$BrowserProfile", 'about:blank')
    # Leave browser/profile cleanup to the operator; never delete stored sessions.
    Start-Process -FilePath $BrowserExecutable -ArgumentList (($values | ForEach-Object { Quote-Native $_ }) -join ' ')
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        # Selection/release is not a reservation. Reject a competing listener
        # using the same ownership checks before issuing any CDP request.
        if (Assert-LocalOwner) {
            $observed = Get-LocalCDP
            if ($observed.webSocketDebuggerUrl) { return $observed }
        }
    }
    throw 'browser_start_failed_check_profile_and_desktop'
}

function Quote-Remote([string]$Value) {
    if ($Value.Contains([char]0) -or $Value.Contains("`n") -or $Value.Contains("`r")) { throw 'invalid_remote_argument' }
    $quote = [string][char]39
    $quote + $Value.Replace($quote, $quote + [char]34 + $quote + [char]34 + $quote) + $quote
}

function Remote-Command([string[]]$Values) {
    'cd ' + (Quote-Remote $Repository) + ' && ' + ((@($PythonExecutable, '-B', '-m', 'hypoloom', 'institutional') + $Values |
        ForEach-Object { Quote-Remote $_ }) -join ' ')
}

function Resolve-ServerPaths {
    if (-not $Repository.StartsWith('/')) { throw 'absolute_repository_path_required' }
    $script:Repository = $Repository.TrimEnd('/')
    if (-not $script:Repository) { throw 'repository_root_not_allowed' }
    if (-not $script:PythonExecutable) { $script:PythonExecutable = "$Repository/.conda/envs/hypoloom/bin/python" }
    if (-not $script:RunsRoot) { $script:RunsRoot = "$Repository/state" }
    foreach ($value in @($Repository, $PythonExecutable, $RunsRoot)) {
        if (-not $value.StartsWith('/')) { throw 'absolute_server_paths_required' }
        $null = Quote-Remote $value
    }
}

function Init-Command([switch]$Check) {
    $body = 'cd ' + (Quote-Remote $Repository) + ' || exit 42; '
    if ($Check) {
        $body += 'test -x ' + (Quote-Remote $PythonExecutable) + ' || exit 40; '
        $body += if ($Action -in @('Setup', 'Connect')) { 'true' } else { 'command -v claude >/dev/null || exit 41' }
    } else {
        $body += 'exec claude ' + (Quote-Remote '/autoresearch init')
    }
    'bash -lc ' + (Quote-Remote $body)
}

function Check-InitConnection {
    $child = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
        '-o', 'ConnectionAttempts=1', $Server, (Init-Command -Check))
    try {
        if (-not $child.Process.WaitForExit(15000)) { throw 'init_ssh_connection_timeout' }
        switch ($child.Process.ExitCode) {
            0 { return }
            40 { throw 'init_control_python_unavailable' }
            41 { throw 'init_claude_command_unavailable' }
            42 { throw 'init_repository_unavailable' }
            default { throw 'init_ssh_connection_unavailable_check_key_and_known_host' }
        }
    } finally { Stop-Child $child }
}

function Start-InitSession([string]$TaskName) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output 'client_discovery_started; init will verify the browser connection before opening login pages'
    & $script:ssh -t -o BatchMode=yes -o ConnectTimeout=5 $Server (Init-Command)
    if ($LASTEXITCODE -ne 0) { throw 'remote_init_ended_unsuccessfully_client_task_retained' }
}

function Start-ClientConnection([string]$TaskName) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output 'client_discovery_started'
    Write-Output '客户端连接任务已启动。回到原来的 init 会话查看进展；准备程序会自动检测连接。'
    Write-Output '后台任务使用隐藏窗口；Connect 返回后可关闭此 PowerShell 窗口。'
    Write-Output '任务启动不代表浏览器已连通；需要提前检查时回复：继续连接。若准备已结束，回复：重新准备。'
}

function Setup-ClientEntry([string]$Source) {
    # The downloaded file is the program. Conversation output supplies values,
    # never source code. Preserve its bytes at the existing task's stable path.
    Write-Host '本机接入安装：保留服务器上的 init 会话；这里只启动客户端连接。'
    $setupServer = $Server
    if (-not $setupServer) {
        Write-Host '127.0.0.1/localhost 指向这台 Windows；不要填写密码或整条 ssh 命令。'
        $setupServer = (Read-Host '服务器 user@host 或 SSH 别名').Trim()
    }
    if ($setupServer -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') { throw 'invalid_ssh_target' }
    $script:Server = $setupServer
    if (-not $Repository) { $script:Repository = (Read-Host '服务器仓库绝对路径（见 init 显示的值）').Trim() }
    if (-not $PythonExecutable) {
        $script:PythonExecutable = (Read-Host '服务器 Python 绝对路径（回车使用仓库下 .conda/envs/hypoloom/bin/python）').Trim()
    }
    if (-not $RunsRoot) {
        $script:RunsRoot = (Read-Host '服务器运行目录绝对路径（回车使用仓库下 state）').Trim()
    }
    Resolve-ServerPaths
    # Authentication and server paths must work before replacing an installed entry.
    Check-InitConnection
    $destination = Join-Path $env:LOCALAPPDATA 'Hypoloom/institutional-connect.ps1'
    if ([IO.Path]::GetFullPath($Source) -ne [IO.Path]::GetFullPath($destination)) {
        $bytes = [IO.File]::ReadAllBytes($Source)
        if (-not $bytes.Length) { throw 'empty_client_entry' }
        $directory = Split-Path $destination
        $null = New-Item -ItemType Directory -Force -Path $directory
        $temporary = Join-Path $directory ([IO.Path]::GetRandomFileName())
        try {
            [IO.File]::WriteAllBytes($temporary, $bytes)
            Move-Item -LiteralPath $temporary -Destination $destination -Force
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        }
    }
    $values = @('-NoProfile', '-NonInteractive', '-File', $destination,
        '-Action', 'Connect', '-Server', $Server, '-Repository', $Repository,
        '-PythonExecutable', $PythonExecutable, '-RunsRoot', $RunsRoot, '-LocalPort', "$LocalPort")
    if ($BrowserExecutable) { $values += @('-BrowserExecutable', $BrowserExecutable) }
    if ($BrowserProfile) { $values += @('-BrowserProfile', $BrowserProfile) }
    & (Get-Process -Id $PID).Path @values
    if ($LASTEXITCODE -ne 0) { throw 'client_setup_connection_failed' }
}

function Get-RunConnections {
    $child = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server,
        (Remote-Command @('connections', '--runs-root', $RunsRoot)))
    try {
        if (-not $child.Process.WaitForExit(15000) -or $child.Process.ExitCode -ne 0) { throw 'run_discovery_unavailable' }
        @((($child.Out.GetAwaiter().GetResult() | ConvertFrom-Json).connections) | Where-Object { $_.mode -eq 'client' -and $_.socket_path })
    } finally { Stop-Child $child }
}

function Watch-Runs {
    $children = @{}; $failures = @{}; $retryAt = @{}; $discoveryFailures = 0
    $idle = $false
    try {
        while ($true) {
            try { $entries = @(Get-RunConnections); $discoveryFailures = 0 }
            catch {
                $discoveryFailures++
                if ($discoveryFailures -ge 3) { throw 'run_discovery_unavailable' }
                # Existing forwards retain their independent remote watch. A
                # failed listing never authorizes creating a stale connection.
                Start-Sleep -Seconds 5
                continue
            }
            if (-not $entries.Count -and -not $idle) {
                Write-Output 'waiting_for_preparation_or_run; foreground_Run_uses_Ctrl+C'
            }
            $idle = $entries.Count -eq 0
            $paths = @($entries | ForEach-Object { $_.socket_path })
            foreach ($path in @($children.Keys)) {
                if ($path -notin $paths) { Stop-Child $children[$path]; $children.Remove($path); $failures.Remove($path); $retryAt.Remove($path) }
            }
            foreach ($entry in $entries) {
                $path = [string]$entry.socket_path
                if ($children.ContainsKey($path) -and -not $children[$path].Process.HasExited) { continue }
                if ($children.ContainsKey($path)) {
                    Stop-Child $children[$path]; $children.Remove($path)
                    $failures[$path] = 1 + [int]$failures[$path]
                    $retryAt[$path] = [DateTime]::UtcNow.AddSeconds(30)
                }
                if ([int]$failures[$path] -ge 3 -or ($retryAt.ContainsKey($path) -and [DateTime]::UtcNow -lt $retryAt[$path])) { continue }
                if ($path -notmatch '^/[^\r\n:]+/browser\.sock$') { throw 'invalid_run_socket' }
                $ready = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                    '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server,
                    (Remote-Command @('forward-ready', '--state-root', $entry.state_root, '--run-id', $entry.run_id, '--socket-path', $path)))
                try { $available = $ready.Process.WaitForExit(15000) -and $ready.Process.ExitCode -eq 0 }
                finally { Stop-Child $ready }
                if (-not $available) {
                    $failures[$path] = 1 + [int]$failures[$path]
                    $retryAt[$path] = [DateTime]::UtcNow.AddSeconds(30)
                    continue
                }
                $local = Ensure-Browser
                $identity = ([uri]$local.webSocketDebuggerUrl).AbsolutePath
                $watch = Remote-Command @('watch', '--state-root', $entry.state_root, '--run-id', $entry.run_id,
                    '--socket-path', $path, '--browser-path', $identity)
                $children[$path] = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                    '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                    '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-R', "${path}:127.0.0.1:$LocalPort", $Server, $watch)
                Write-Output 'run_connection_started; server verifies browser identity'
            }
            Start-Sleep -Seconds 5
        }
    } finally { foreach ($child in $children.Values) { Stop-Child $child } }
}

function Resolve-AccountSid([string]$Name) {
    ([Security.Principal.NTAccount]::new($Name)).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Test-TaskUser([string]$UserId) {
    if ($UserId -eq $script:userSid) { return $true }
    # Task Scheduler may return a short name even when registered by SID.
    # Resolve through Windows, never compare an unqualified username suffix.
    try { (Resolve-AccountSid $UserId) -eq $script:userSid }
    catch { $false }
}

function Invoke-ClientTask([string]$TaskName, [string[]]$TaskValues, [string]$PowerShell) {
    $previousArguments = ($TaskValues | ForEach-Object { Quote-Native $_ }) -join ' '
    $taskArguments = ((@('-WindowStyle', 'Hidden') + $TaskValues) | ForEach-Object { Quote-Native $_ }) -join ' '
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $PowerShell -or
            $task.Actions[0].Arguments -notin @($taskArguments, $previousArguments) -or
            -not (Test-TaskUser $task.Principal.UserId))) {
        throw 'scheduled_task_ownership_mismatch'
    }
    if ($Action -in @('Connect', 'Init', 'Install')) {
        if ($Action -in @('Connect', 'Init')) { Initialize-BrowserProfile }
        $command = New-ScheduledTaskAction -Execute $PowerShell -Argument $taskArguments
        if (-not $task) {
            $principal = New-ScheduledTaskPrincipal -UserId $script:userSid -LogonType Interactive -RunLevel Limited
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $script:userSid
            $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
            $null = Register-ScheduledTask -TaskName $TaskName -Action $command -Trigger $trigger -Principal $principal -Settings $settings
        } elseif ($task.Actions[0].Arguments -eq $previousArguments) {
            # Upgrade only the exact prior invocation's window presentation.
            # Preserve principal/triggers/settings and every connection argument.
            $null = Set-ScheduledTask -TaskName $TaskName -Action $command
            if ($Action -in @('Connect', 'Init') -and $task.State -eq 'Running') {
                Stop-ScheduledTask -TaskName $TaskName
            }
        }
        if ($Action -eq 'Init') {
            Start-InitSession $TaskName
        } elseif ($Action -eq 'Connect') {
            Start-ClientConnection $TaskName
        } else {
            Write-Output 'installed_for_next_user_logon; use Run now to connect'
        }
    } else {
        if ($task) {
            Stop-ScheduledTask -TaskName $TaskName
            if ($Action -eq 'Uninstall') { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
        }
        Write-Output 'task_stopped; browser_profile_preserved; foreground_Run_uses_Ctrl+C'
    }
}

function Connector-Main {
    if (-not $IsWindows) { throw 'windows_desktop_required' }
    $script:ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    if ($Action -eq 'Setup') { Setup-ClientEntry $PSCommandPath; return }
    if (-not $Server -or -not $Repository) { throw 'server_and_repository_required' }
    $script:userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $script:RequestedPort = $LocalPort
    Resolve-ServerPaths
    if ($Action -in @('Connect', 'Init')) { Check-InitConnection }
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
    $script:DefaultBrowserProfile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA (
        'Hypoloom\Institutional-' + [IO.Path]::GetFileNameWithoutExtension($BrowserExecutable)))).TrimEnd('\')
    if (-not $script:BrowserProfile) { $script:BrowserProfile = $script:DefaultBrowserProfile }
    if (-not [IO.Path]::IsPathFullyQualified($BrowserProfile)) { throw 'absolute_dedicated_profile_required' }
    $script:BrowserProfile = [IO.Path]::GetFullPath($BrowserProfile).TrimEnd('\')
    foreach ($personal in @('Google\Chrome\User Data', 'Microsoft\Edge\User Data', 'Chromium\User Data')) {
        $path = Join-Path $env:LOCALAPPDATA $personal
        if ($BrowserProfile -eq $env:USERPROFILE -or $BrowserProfile -eq $path -or $BrowserProfile.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($BrowserProfile + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'personal_browser_profile_forbidden' }
    }
    $taskName = "Hypoloom-Institutional-$Server-$script:userSid"
    $taskValues = @('-NoProfile', '-NonInteractive', '-File', $PSCommandPath, '-Action', 'Run', '-Server', $Server,
        '-BrowserExecutable', $BrowserExecutable, '-BrowserProfile', $BrowserProfile, '-LocalPort', "$LocalPort",
        '-Repository', $Repository, '-PythonExecutable', $PythonExecutable, '-RunsRoot', $RunsRoot)
    $powershell = (Get-Process -Id $PID).Path
    if ($Action -in @('Connect', 'Init', 'Install', 'Stop', 'Uninstall')) {
        Invoke-ClientTask $taskName $taskValues $powershell
        return
    }
    if ($Action -eq 'Status') {
        $entries = @(Get-RunConnections)
        Write-Output ("active_run_connections=" + $entries.Count)
        return
    }
    Watch-Runs

}

try { Connector-Main } catch {
    # No raw SSH/browser diagnostics, callback URLs, or credentials in logs.
    $reason = $_.Exception.Message
    if ($reason -notmatch '^[a-z][a-z0-9_]+$') { $reason = 'connector_unavailable_check_local_dependencies_and_permissions' }
    Write-Output $reason
    exit 2
}
