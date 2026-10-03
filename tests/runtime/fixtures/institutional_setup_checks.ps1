param([string]$Connector, [string]$Desktop, [string]$Scenario)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
# Execute the shipped functions. Substitute only prompts and external connection
# effects; path derivation, filesystem writes, replacement and argv remain real.
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
$env:LOCALAPPDATA = $Desktop
$Action = 'Setup'; $LocalPort = 0
$script:Server = ''; $script:Repository = ''; $script:PythonExecutable = ''; $script:RunsRoot = ''
$script:prompts = 0; $script:checks = 0; $script:called = @()
$repo = '/server/repo '' $literal ` $(throw INJECTED)'
$answers = @('research@fixture', $repo, '', '')
if ($Scenario -eq 'custom') { $answers = @('research@fixture', $repo, '/custom/python space', '/custom/runs') }
if ($Scenario -eq 'invalid_target') { $answers[0] = 'ssh research@fixture' }
if ($Scenario -eq 'invalid_repository') { $answers[1] = 'relative/repository' }
if ($Scenario -eq 'supplied') {
    $script:Server = 'research@fixture'; $script:Repository = $repo
    $script:PythonExecutable = '/custom/python space'; $script:RunsRoot = '/custom/runs'
}
function Read-Host { $value = $answers[$script:prompts]; $script:prompts++; $value }
function Check-InitConnection {
    $script:checks++
    if ((Init-Command -Check).Contains('command -v claude')) { throw 'setup_requested_second_init' }
    if ($Scenario -eq 'ssh_failure') { throw 'init_ssh_connection_unavailable_check_key_and_known_host' }
}
function Get-Process { param($Id) @{ Path = 'Invoke-FixtureConnector' } }
function Invoke-FixtureConnector {
    $script:called = @($args)
    $global:LASTEXITCODE = if ($Scenario -eq 'connection_failure') { 2 } else { 0 }
}
if ($Scenario -eq 'write_failure') {
    function Move-Item { throw 'fixture_write_failure' }
}
$caught = ''
try { Setup-ClientEntry $Connector } catch { $caught = $_.Exception.Message }
@{ error = $caught; args = $script:called; prompts = $script:prompts; checks = $script:checks } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Desktop 'observed.json') -Encoding utf8
