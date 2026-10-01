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
Write-Output 'connector parser, argument handling, browser identity and child cleanup passed'
