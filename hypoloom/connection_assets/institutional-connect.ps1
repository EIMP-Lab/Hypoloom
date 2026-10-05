#Requires -Version 7.2
<# Dedicated Windows browser and loopback SSH forwarding. No login secrets are stored.
   Each explicit Connect serves one init/run. No logon trigger or automatic restart.
   Keep this file at a stable path while its one-shot task is running. #>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Connect', 'Init', 'Run', 'Status', 'Install', 'Stop', 'Uninstall')][string]$Action = 'Run',
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.@-]*$')][string]$Server,
    [string]$BrowserExecutable,
    [string]$BrowserProfile,
    [ValidateRange(0, 65535)][int]$LocalPort = 0,
    [string]$Repository,
    [string]$PythonExecutable,
    [string]$RunsRoot,
    [string]$StateRoot,
    [string]$RunId,
    [string]$RequestId,
    [string]$PreparationRoot
)
$ErrorActionPreference = 'Stop'

function Quote-Native([string]$Value) {
    # CommandLineToArgvW quoting; never construct a PowerShell expression.
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Start-Captured([string]$Executable, [string[]]$Values, [switch]$Handshake) {
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($value in $Values) { $info.ArgumentList.Add($value) }
    $process = [Diagnostics.Process]::Start($info)
    @{ Process = $process; Out = if ($Handshake) { $process.StandardOutput.ReadLineAsync() } else { $process.StandardOutput.ReadToEndAsync() }
       Err = $process.StandardError.ReadToEndAsync() }
}

function Stop-Child($Child) {
    if ($null -ne $Child) {
        try {
            if (-not $Child.Process.HasExited) {
                $Child.Process.Kill($true)
                if (-not $Child.Process.WaitForExit(5000)) { throw 'child_cleanup_failed' }
            }
        } finally { $Child.Process.Dispose() }
    }
}

function Initialize-ClientNative {
    # A process-local OS job owns only this invocation and its future children.
    # Its handle is deliberately retained until process exit, including a forced
    # Scheduled Task stop. No PID file or process-name-based cleanup is needed.
    if (-not ('HypoloomClientLifetime' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class HypoloomClientLifetime {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long ProcessTime, JobTime; public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcesses;
        public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IO {
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic Basic; public IO IO;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] public static extern ulong GetTickCount64();
    [DllImport("shell32.dll", SetLastError=true)] static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string command, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
    public static string[] ParseArguments(string arguments) {
        int count; IntPtr memory = CommandLineToArgvW("pwsh " + arguments, out count);
        if (memory == IntPtr.Zero) throw new InvalidOperationException();
        try {
            string[] values = new string[count - 1];
            for (int i=1; i<count; i++) values[i-1] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory, i * IntPtr.Size));
            return values;
        } finally { LocalFree(memory); }
    }
    static IntPtr ownedJob;
    public static void Initialize() {
        if (ownedJob != IntPtr.Zero) return;
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new InvalidOperationException();
        var info = new Extended(); info.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf<Extended>()) ||
            !AssignProcessToJobObject(job, GetCurrentProcess())) {
            CloseHandle(job); throw new InvalidOperationException();
        }
        ownedJob = job;
    }
}
'@
    }
}

function Initialize-OwnedJob {
    Initialize-ClientNative
    try { [HypoloomClientLifetime]::Initialize() } catch { throw 'client_process_ownership_unavailable' }
}

function Get-ElapsedMilliseconds { [long][HypoloomClientLifetime]::GetTickCount64() }

function Get-RemainingMilliseconds([int]$Maximum = 15000) {
    $since = if ($null -ne $script:UnhealthySince) { $script:UnhealthySince } else { $script:LastScopeContact }
    if ($null -eq $since) { return $Maximum }
    $remaining = 300000 - ((Get-ElapsedMilliseconds) - $since)
    if ($null -ne $script:AwaitingDeadline) {
        $remaining = [Math]::Min($remaining, $script:AwaitingDeadline - (Get-ElapsedMilliseconds))
    }
    if ($remaining -le 0) { throw 'run_connection_expired' }
    [int][Math]::Min($Maximum, $remaining)
}

function Assert-ConnectionDeadline { $null = Get-RemainingMilliseconds }

function Scope-Arguments {
    @('--state-root', $StateRoot, '--run-id', $RunId, '--request-id', $RequestId)
    if ($PreparationRoot) { @('--preparation-root', $PreparationRoot) }
}

function Assert-RunScope {
    if (-not $StateRoot -or -not $RunId -or -not $RequestId) { throw 'explicit_run_scope_required' }
    foreach ($id in @($RunId, $RequestId)) {
        if ($id -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$' -or $id -in @('.', '..')) { throw 'invalid_run_identity' }
    }
    foreach ($path in @($StateRoot, $PreparationRoot) | Where-Object { $_ }) {
        if (-not $path.StartsWith('/') -or $path -eq '/' -or $path.EndsWith('/') -or
            $path -match '//|/\.{1,2}(?:/|$)') { throw 'absolute_normalized_state_path_required' }
        $null = Quote-Remote $path
    }
}

function Assert-RunStatus($Status) {
    if ($Status -isnot [System.Collections.IDictionary] -or
        $Status.scope.state_root -cne $StateRoot -or $Status.scope.run_id -cne $RunId -or
        $Status.scope.request_id -cne $RequestId) { throw 'run_scope_mismatch' }
    if ($Status.lifecycle -notin @('preparing', 'awaiting_launch', 'active', 'terminal', 'pending') -or
        $Status.connections -isnot [array] -or $Status.connections.Count -gt 1) { throw 'run_discovery_invalid_response' }
    foreach ($entry in $Status.connections) {
        $final = $entry.state_root -ceq $StateRoot -and $entry.run_id -ceq $RunId
        $preparing = $PreparationRoot -and $entry.state_root -ceq $PreparationRoot -and $entry.run_id -ceq 'preparation'
        if (-not ($final -or $preparing) -or $entry.mode -ne 'client' -or
            $entry.socket_path -notmatch '^/[^\r\n:]+/browser\.sock$') { throw 'run_scope_mismatch' }
    }
    if ($Status.lifecycle -in @('terminal', 'pending') -and $Status.connections.Count) { throw 'run_discovery_invalid_response' }
}

function Get-LocalCDP {
    $remaining = Get-RemainingMilliseconds 3000
    if ($remaining -lt 1000) { throw 'run_connection_expired' }
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/json/version" -NoProxy -TimeoutSec ([int][Math]::Floor($remaining / 1000)) }
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
        if (-not $script:OwnedBrowser -or $listener.OwningProcess -ne $script:OwnedBrowser.Id -or
            $owner.Sid -ne $script:userSid -or $process.ExecutablePath -ne $BrowserExecutable -or
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

function Stop-OwnedBrowser {
    if ($null -ne $script:OwnedBrowser) {
        $browser = $script:OwnedBrowser; $script:OwnedBrowser = $null
        Stop-Child @{Process=$browser}
    }
}

function Ensure-Browser {
    Assert-ConnectionDeadline
    Initialize-BrowserProfile
    if ($script:OwnedBrowser -and -not $script:OwnedBrowser.HasExited) {
        if (-not (Assert-LocalOwner)) { throw 'owned_browser_cdp_unavailable' }
        $observed = Get-LocalCDP
        if (-not $observed.webSocketDebuggerUrl) { throw 'owned_browser_cdp_unavailable' }
        return $observed
    }
    Stop-OwnedBrowser
    # Existing windows are not ours, even if the executable, user and profile
    # match. Never adopt or close a personal/previous invocation's browser.
    $profile = [regex]::Escape($BrowserProfile)
    $data = '(?:^|\s)(?:"--user-data-dir=' + $profile + '"|--user-data-dir="' + $profile + '"|--user-data-dir=' + $profile + ')(?=\s|$)'
    foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "Name='$([IO.Path]::GetFileName($BrowserExecutable))'")) {
        if ($candidate.ExecutablePath -eq $BrowserExecutable -and $candidate.CommandLine -match $data) {
            throw 'browser_profile_already_in_use_close_previous_dedicated_window'
        }
    }
    $script:LocalPort = $script:RequestedPort
    if ($LocalPort -eq 0) { $script:LocalPort = Get-AvailableBrowserPort }
    $script:BrowserCommandPort = $LocalPort
    if (Assert-LocalOwner) { throw 'browser_endpoint_not_owned_by_this_connection' }
    Assert-ProfilePrivacy
    Assert-ConnectionDeadline
    $values = @('--no-first-run', '--no-default-browser-check', '--disable-background-mode',
        '--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$LocalPort", "--user-data-dir=$BrowserProfile", 'about:blank')
    $script:OwnedBrowser = Start-Process -FilePath $BrowserExecutable -PassThru -ArgumentList (($values | ForEach-Object { Quote-Native $_ }) -join ' ')
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds (Get-RemainingMilliseconds 1000)
        Assert-ConnectionDeadline
        if ($script:OwnedBrowser.HasExited) { throw 'browser_start_failed_check_profile_and_desktop' }
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

function Start-ClientConnection([string]$TaskName) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output 'scoped_client_started; server will verify this run connection'
    Write-Output '本次运行的连接任务已启动。回到原 init 会话查看进展；可以关闭此 PowerShell 窗口。'
    Write-Output '任务仅服务本次运行；运行结束或连接连续不可用 300 秒后退出，并关闭本次打开的专用浏览器。'
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
    if (-not $StateRoot) { $script:StateRoot = (Read-Host '本次运行 StateRoot（复制 init 提供的完整值）').Trim() }
    if (-not $RunId) { $script:RunId = (Read-Host '本次运行 RunId（复制 init 提供的值）').Trim() }
    if (-not $RequestId) { $script:RequestId = (Read-Host '本次运行 RequestId（复制 init 提供的值）').Trim() }
    if (-not $PreparationRoot) { $script:PreparationRoot = (Read-Host 'PreparationRoot（init 有提供时复制，否则回车）').Trim() }
    Resolve-ServerPaths
    Assert-RunScope
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
        '-PythonExecutable', $PythonExecutable, '-StateRoot', $StateRoot, '-RunId', $RunId, '-RequestId', $RequestId, '-LocalPort', "$LocalPort")
    if ($PreparationRoot) { $values += @('-PreparationRoot', $PreparationRoot) }
    if ($BrowserExecutable) { $values += @('-BrowserExecutable', $BrowserExecutable) }
    if ($BrowserProfile) { $values += @('-BrowserProfile', $BrowserProfile) }
    & (Get-Process -Id $PID).Path @values
    if ($LASTEXITCODE -ne 0) { throw 'client_setup_connection_failed' }
}

function Get-RunConnections {
    $wait = Get-RemainingMilliseconds
    $child = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server,
        (Remote-Command (@('connections') + @(Scope-Arguments))))
    try {
        if (-not $child.Process.WaitForExit($wait)) { throw 'run_discovery_timeout' }
        if ($child.Process.ExitCode -eq 255) { throw 'run_discovery_ssh_failed' }
        if ($child.Process.ExitCode -ne 0) { throw 'run_discovery_command_failed' }
        try { $result = $child.Out.GetAwaiter().GetResult() | ConvertFrom-Json -AsHashtable }
        catch { throw 'run_discovery_invalid_response' }
        Assert-RunStatus $result
        $result
    } finally { Stop-Child $child }
}

function Watch-Runs {
    $children = @{}; $delay = 5; $notice = ''
    $script:UnhealthySince = $null; $script:LastScopeContact = Get-ElapsedMilliseconds
    $script:AwaitingDeadline = $null
    try {
        while ($true) {
            # GetTickCount64 includes Windows sleep/hibernate. A long absence
            # expires before asking the server or opening another browser.
            Assert-ConnectionDeadline
            $started = Get-ElapsedMilliseconds
            try {
                $status = Get-RunConnections
                Assert-ConnectionDeadline
                Assert-RunStatus $status
                if ($status.lifecycle -eq 'terminal') { Write-Output 'run_terminal; connection_closed'; return }
                if ($status.lifecycle -eq 'pending') { throw 'run_not_launched' }
                $entries = @($status.connections)
                if ($status.lifecycle -eq 'awaiting_launch') {
                    if ($status.remaining_seconds -isnot [ValueType] -or $status.remaining_seconds -is [bool] -or
                        -not ($status.remaining_seconds -gt 0 -and $status.remaining_seconds -le 600)) { throw 'run_discovery_invalid_response' }
                    # The server owns this deadline. An explicit retry may pass
                    # through preparing between polls and start a new review.
                    $script:AwaitingDeadline = (Get-ElapsedMilliseconds) + [long]($status.remaining_seconds * 1000)
                } else {
                    $script:AwaitingDeadline = $null
                    if (-not $entries.Count) { throw 'run_forward_unavailable' }
                }
                $paths = @($entries | ForEach-Object { $_.socket_path })
                foreach ($path in @($children.Keys)) {
                    if ($path -notin $paths -or $children[$path].Process.HasExited) {
                        Stop-Child $children[$path]; $children.Remove($path)
                    }
                }
                foreach ($entry in $entries) {
                    $path = [string]$entry.socket_path
                    if ($children.ContainsKey($path)) { continue }
                    $wait = Get-RemainingMilliseconds
                    $ready = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', $Server,
                        (Remote-Command (@('forward-ready') + @(Scope-Arguments) + @('--socket-path', $path))))
                    try { $available = $ready.Process.WaitForExit($wait) -and $ready.Process.ExitCode -eq 0 }
                    finally { Stop-Child $ready }
                    Assert-ConnectionDeadline
                    if (-not $available) { throw 'run_forward_unavailable' }
                    $local = Ensure-Browser
                    Assert-ConnectionDeadline
                    $identity = ([uri]$local.webSocketDebuggerUrl).AbsolutePath
                    $watch = Remote-Command (@('watch') + @(Scope-Arguments) + @('--socket-path', $path, '--browser-path', $identity))
                    $children[$path] = Start-Captured $script:ssh @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                        '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-R', "${path}:127.0.0.1:$LocalPort", $Server, $watch) -Handshake
                    try {
                        if (-not $children[$path].Out.Wait((Get-RemainingMilliseconds))) { throw 'run_forward_unavailable' }
                        try { $attached = $children[$path].Out.GetAwaiter().GetResult() | ConvertFrom-Json -AsHashtable }
                        catch { throw 'run_forward_unavailable' }
                        if ($attached.connected -isnot [bool] -or -not $attached.connected -or
                            $attached.lifecycle -notin @('preparing', 'awaiting_launch', 'active')) { throw 'run_forward_unavailable' }
                        Assert-RunStatus @{scope=$attached.scope;lifecycle=$attached.lifecycle;connections=@()}
                        Assert-ConnectionDeadline
                    } catch {
                        Stop-Child $children[$path]; $children.Remove($path)
                        throw
                    }
                    Write-Output 'run_connection_started; server verifies browser identity'
                }
                if ($notice) { Write-Output 'run_discovery_recovered' }
                $notice = ''; $delay = 5
                $script:UnhealthySince = $null; $script:LastScopeContact = Get-ElapsedMilliseconds
            } catch {
                $reason = $_.Exception.Message
                if ($reason -notin @('run_discovery_timeout', 'run_discovery_ssh_failed', 'run_discovery_command_failed',
                    'run_discovery_invalid_response', 'run_not_launched', 'run_forward_unavailable', 'owned_browser_cdp_unavailable')) { throw }
                if ($null -eq $script:UnhealthySince) { $script:UnhealthySince = $started }
                $next = "${reason}; retry_in_seconds=$delay; connection_deadline_not_extended"
                if ($next -ne $notice) { Write-Output $next; $notice = $next }
            }
            Start-Sleep -Milliseconds (Get-RemainingMilliseconds ($delay * 1000))
            if ($notice) { $delay = [Math]::Min(30, $delay * 2) }
        }
    } finally {
        try { foreach ($child in $children.Values) { Stop-Child $child } }
        finally { Stop-OwnedBrowser }
    }
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

function Retire-LegacyTask([string]$PowerShell, [string]$Source) {
    $name = "Hypoloom-Institutional-$Server-$script:userSid"
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $PowerShell -or
        -not (Test-TaskUser $task.Principal.UserId)) { throw 'legacy_task_ownership_mismatch' }
    $values = @(Split-TaskArguments $task.Actions[0].Arguments)
    $options = @{}; $allowed = @('-WindowStyle', '-File', '-Action', '-Server', '-BrowserExecutable',
        '-BrowserProfile', '-LocalPort', '-Repository', '-PythonExecutable', '-RunsRoot')
    for ($i=0; $i -lt $values.Count; $i++) {
        $key = $values[$i]
        if ($options.ContainsKey($key)) { throw 'legacy_task_ownership_mismatch' }
        if ($key -in @('-NoProfile', '-NonInteractive')) { $options[$key] = $true; continue }
        if ($key -notin $allowed -or ++$i -ge $values.Count) { throw 'legacy_task_ownership_mismatch' }
        $options[$key] = $values[$i]
    }
    if ($options['-File'] -ne $Source -or $options['-Action'] -ne 'Run' -or $options['-Server'] -cne $Server -or
        ($options.ContainsKey('-WindowStyle') -and $options['-WindowStyle'] -ne 'Hidden')) { throw 'legacy_task_ownership_mismatch' }
    $null = Disable-ScheduledTask -TaskName $name
    Stop-ScheduledTask -TaskName $name
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host 'legacy_autostart_removed; previous_browser_not_adopted'
    return $true
}

function Split-TaskArguments([string]$Arguments) {
    Initialize-ClientNative
    [HypoloomClientLifetime]::ParseArguments($Arguments)
}

function Test-TaskArguments($Task, [string]$Expected, [string[]]$NewValues) {
    if ($Task.Actions[0].Arguments -ceq $Expected) { return $true }
    if ($Task.State -eq 'Running' -or $NewValues -contains '-PreparationRoot') { return $false }
    # A finished preparation may have been removed. Explicit reconnection can
    # drop that one optional reference; every remaining argument must match.
    $prior = @(Split-TaskArguments $Task.Actions[0].Arguments)
    $positions = @(for ($i=0; $i -lt $prior.Count; $i++) { if ($prior[$i] -ceq '-PreparationRoot') { $i } })
    if ($positions.Count -ne 1 -or $positions[0]+1 -ge $prior.Count) { return $false }
    $kept = @(for ($i=0; $i -lt $prior.Count; $i++) { if ($i -ne $positions[0] -and $i -ne $positions[0]+1) { $prior[$i] } })
    (($kept | ForEach-Object { Quote-Native $_ }) -join ' ') -ceq $Expected
}

function Invoke-ClientTask([string]$TaskName, [string[]]$TaskValues, [string]$PowerShell) {
    $taskArguments = ((@('-WindowStyle', 'Hidden') + $TaskValues) | ForEach-Object { Quote-Native $_ }) -join ' '
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $PowerShell -or
        -not (Test-TaskUser $task.Principal.UserId) -or -not (Test-TaskArguments $task $taskArguments $TaskValues))) {
        throw 'scheduled_task_ownership_mismatch'
    }
    if ($Action -in @('Connect', 'Init', 'Install')) {
        Initialize-BrowserProfile
        if ($task -and $task.State -eq 'Running') { Write-Output 'this_run_connection_already_running'; return }
        if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
        $command = New-ScheduledTaskAction -Execute $PowerShell -Argument $taskArguments
        $principal = New-ScheduledTaskPrincipal -UserId $script:userSid -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        # No trigger and no restart policy: every new init needs an explicit start.
        $null = Register-ScheduledTask -TaskName $TaskName -Action $command -Principal $principal -Settings $settings
        Start-ClientConnection $TaskName
    } else {
        if ($task) {
            Stop-ScheduledTask -TaskName $TaskName
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output 'task_stopped; browser_profile_preserved; foreground_Run_uses_Ctrl+C'
        } else {
            Write-Output 'task_not_found; browser_profile_preserved; foreground_Run_uses_Ctrl+C'
        }
    }
}

function Connector-Main {
    if (-not $IsWindows) { throw 'windows_desktop_required' }
    $script:ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    if ($Action -eq 'Setup') { Setup-ClientEntry $PSCommandPath; return }
    if (-not $Server -or -not $Repository) { throw 'server_and_repository_required' }
    $script:userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $powershell = (Get-Process -Id $PID).Path
    $legacyRemoved = $false
    if ($Action -in @('Connect', 'Init', 'Install', 'Stop', 'Uninstall')) { $legacyRemoved = Retire-LegacyTask $powershell $PSCommandPath }
    if ($Action -in @('Stop', 'Uninstall') -and -not $StateRoot -and -not $RunId -and -not $RequestId) {
        if ($legacyRemoved) { Write-Output 'legacy_task_removed; browser_profile_preserved' }
        else { Write-Output 'legacy_task_not_found; browser_profile_preserved' }
        return
    }
    $script:RequestedPort = $LocalPort
    Resolve-ServerPaths
    Assert-RunScope
    if ($Action -in @('Connect', 'Init', 'Install')) { Check-InitConnection }
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
        'Hypoloom\Institutional-' + $Server + '-' + [IO.Path]::GetFileNameWithoutExtension($BrowserExecutable)))).TrimEnd('\')
    if (-not $script:BrowserProfile) { $script:BrowserProfile = $script:DefaultBrowserProfile }
    if (-not [IO.Path]::IsPathFullyQualified($BrowserProfile)) { throw 'absolute_dedicated_profile_required' }
    $script:BrowserProfile = [IO.Path]::GetFullPath($BrowserProfile).TrimEnd('\')
    foreach ($personal in @('Google\Chrome\User Data', 'Microsoft\Edge\User Data', 'Chromium\User Data')) {
        $path = Join-Path $env:LOCALAPPDATA $personal
        if ($BrowserProfile -eq $env:USERPROFILE -or $BrowserProfile -eq $path -or $BrowserProfile.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($BrowserProfile + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'personal_browser_profile_forbidden' }
    }
    $taskName = "Hypoloom-Run-$Server-$RequestId-$script:userSid"
    if ($taskName.Length -gt 238) { throw 'scoped_task_name_too_long' }
    $taskValues = @('-NoProfile', '-NonInteractive', '-File', $PSCommandPath, '-Action', 'Run', '-Server', $Server,
        '-BrowserExecutable', $BrowserExecutable, '-BrowserProfile', $BrowserProfile, '-LocalPort', "$LocalPort",
        '-Repository', $Repository, '-PythonExecutable', $PythonExecutable, '-StateRoot', $StateRoot, '-RunId', $RunId, '-RequestId', $RequestId)
    if ($PreparationRoot) { $taskValues += @('-PreparationRoot', $PreparationRoot) }
    $powershell = (Get-Process -Id $PID).Path
    if ($Action -in @('Connect', 'Init', 'Install', 'Stop', 'Uninstall')) {
        Invoke-ClientTask $taskName $taskValues $powershell
        return
    }
    if ($Action -eq 'Status') {
        Initialize-ClientNative
        $status = Get-RunConnections
        Write-Output ("run_lifecycle=" + $status.lifecycle + "; connections=" + $status.connections.Count)
        return
    }
    Initialize-OwnedJob
    Watch-Runs
}

try { Connector-Main } catch {
    # No raw SSH/browser diagnostics, callback URLs, or credentials in logs.
    $reason = $_.Exception.Message
    if ($reason -notmatch '^[a-z][a-z0-9_]+$') { $reason = 'connector_unavailable_check_local_dependencies_and_permissions' }
    Write-Output $reason
    exit 2
}
