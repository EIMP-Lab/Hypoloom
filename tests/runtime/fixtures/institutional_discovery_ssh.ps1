param([string]$Connector, [string]$Ssh, [string]$Repository, [string]$PythonExecutable,
    [string]$StateRoot, [string]$RunId, [string]$RequestId, [string]$PreparationRoot,
    [int]$LocalPort, [string]$StopFile)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
$script:ssh = $Ssh; $Server = 'fixture'; $script:BrowserRequests = 0
$script:Clock=[Diagnostics.Stopwatch]::StartNew()
function Get-ElapsedMilliseconds { $script:Clock.ElapsedMilliseconds }
function Stop-OwnedBrowser {}
# The HTTP CDP endpoint and SSH are real; only Windows browser startup and time
# between polls are substituted. Deadlines and retry policy have separate tests.
function Ensure-Browser {
    $script:BrowserRequests++
    @{webSocketDebuggerUrl="ws://127.0.0.1:$LocalPort/devtools/browser/fixture"}
}
function Start-Sleep {
    param($Seconds, $Milliseconds)
    if (Test-Path -LiteralPath $StopFile) { throw 'fixture_complete' }
    Microsoft.PowerShell.Utility\Start-Sleep -Milliseconds 100
}
try { Watch-Runs }
catch { if ($_.Exception.Message -ne 'fixture_complete') { throw } }
if ($script:BrowserRequests -ne 1) { throw 'outage_recreated_browser_or_forward' }
Write-Output 'fixture_stopped'
