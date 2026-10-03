param([Parameter(Mandatory)][string]$Connector)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'native_windows_required' }
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Connector, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false) | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$script:userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$root = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
try {
    $null = New-Item -ItemType Directory -Path $root
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $owner = [Security.Principal.SecurityIdentifier]::new($script:userSid)
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($script:userSid, 'S-1-5-18', 'S-1-5-32-544', 'S-1-5-32-545', 'S-1-5-7')) {
        $type = if ($sid -eq 'S-1-5-7') { 'Deny' } else { 'Allow' }
        $rights = if ($sid -in @('S-1-5-32-545', 'S-1-5-7')) { 'ReadAndExecute' } else { 'FullControl' }
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), $rights, 'ContainerInherit, ObjectInherit', 'None', $type))
    }
    Set-Acl -LiteralPath $root -AclObject $acl
    $parentBefore = (Get-Acl -LiteralPath $root).Sddl
    $script:BrowserProfile = Join-Path $root 'dedicated'
    $script:DefaultBrowserProfile = $BrowserProfile
    $null = New-Item -ItemType Directory -Path $BrowserProfile
    $childAcl = Get-Acl -LiteralPath $BrowserProfile
    $childAcl.SetOwner($owner)
    Set-Acl -LiteralPath $BrowserProfile -AclObject $childAcl
    $content = Join-Path $BrowserProfile 'fixture-login-data'
    [IO.File]::WriteAllText($content, 'fixture-content-preserved')
    $rejected = $false
    try { Assert-ProfilePrivacy } catch { $rejected = $_.Exception.Message -eq 'browser_profile_must_be_private' }
    Assert $rejected 'native inherited cross-user grant was accepted'
    Initialize-BrowserProfile
    Assert-ProfilePrivacy
    $after = Get-Acl -LiteralPath $BrowserProfile
    Assert $after.AreAccessRulesProtected 'native profile still inherits parent grants'
    $rules = @($after.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    Assert (@($rules | Where-Object { $_.IdentityReference.Value -eq 'S-1-5-7' -and $_.AccessControlType -eq 'Deny' }).Count -eq 1) 'native deny was lost'
    Assert ((Get-Acl -LiteralPath $root).Sddl -eq $parentBefore) 'parent ACL changed'
    Assert ([IO.File]::ReadAllText($content) -eq 'fixture-content-preserved') 'profile data changed'
    Assert (@((Get-Acl -LiteralPath $content).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.IdentityReference.Value -eq 'S-1-5-32-545' -and $_.AccessControlType -eq 'Allow'
    }).Count -eq 0) 'native child retained removed inherited grant'
    Initialize-BrowserProfile
    Assert ((Get-Acl -LiteralPath $BrowserProfile).Sddl -eq $after.Sddl) 'native repeat changed profile permissions'
    Write-Output 'native Windows profile ACL and child inheritance passed'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
