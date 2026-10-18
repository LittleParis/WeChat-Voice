[CmdletBinding()]
param(
    [string]$DataRoot = (Join-Path $env:USERPROFILE 'Documents\xwechat_files'),
    [string]$Account,
    [string]$Contact,
    [string]$Since,
    [string]$Until,
    [ValidateRange(1, 1000000)][int]$Limit = 10,
    [switch]$All,
    [switch]$InventoryOnly,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Missing project Python environment; see README.md.' }
if ($All -and $PSBoundParameters.ContainsKey('Limit')) { throw 'Choose -All or -Limit, not both.' }
if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) { throw 'WeChat data root does not exist.' }
$dataRootPath = (Resolve-Path -LiteralPath $DataRoot).Path
if ($Account) {
    if ($Account -match '[/\\]' -or $Account -in @('.', '..')) { throw 'Account must be a directory name under DataRoot.' }
    $accounts = @(Get-Item -LiteralPath (Join-Path $dataRootPath $Account))
} else {
    $accounts = @(Get-ChildItem -LiteralPath $dataRootPath -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName 'db_storage\message\message_0.db')
    })
}
if ($accounts.Count -ne 1) { throw 'Expected exactly one account. Specify -Account for a multi-account data root.' }
$sourcePath = Join-Path $accounts[0].FullName 'db_storage'
$keyPath = Join-Path $projectPath '.private\desktop-db-key.txt'
$decryptedPath = Join-Path $projectPath '.private\decrypted'
$privatePath = Join-Path $projectPath '.private'
New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$privateAcl = [Security.AccessControl.DirectorySecurity]::new()
$privateAcl.SetAccessRuleProtection($true, $false)
$privateAcl.SetOwner($userSid)
$privateRule = [Security.AccessControl.FileSystemAccessRule]::new(
    $userSid, [Security.AccessControl.FileSystemRights]::FullControl,
    [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow)
$privateAcl.AddAccessRule($privateRule)
Set-Acl -LiteralPath $privatePath -AclObject $privateAcl

function Invoke-LocalPython {
    param([string[]]$Arguments)
    & $pythonPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Local processing stopped (exit $LASTEXITCODE). Original WeChat data was not modified." }
}

$previousEncoding = $env:PYTHONIOENCODING
$previousUtf8 = $env:PYTHONUTF8
Push-Location $projectPath
try {
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    Invoke-LocalPython @('scripts\capture_desktop_key.py', '--db', (Join-Path $sourcePath 'message\message_0.db'), '--output', $keyPath, '--timeout', '300')
    Invoke-LocalPython @('scripts\prepare_desktop_data.py', '--source', $sourcePath, '--key-file', $keyPath, '--output', $decryptedPath)
    $arguments = @('scripts\export_original_voices.py', '--input', $decryptedPath)
    if ($Contact) { $arguments += @('--contact', $Contact) }
    if ($Since) { $arguments += @('--since', $Since) }
    if ($Until) { $arguments += @('--until', $Until) }
    if ($OutputPath) { $arguments += @('--output', $OutputPath) }
    if ($InventoryOnly) { $arguments += '--inventory-only' }
    elseif ($All) { $arguments += '--all' }
    else { $arguments += @('--limit', $Limit.ToString()) }
    Invoke-LocalPython $arguments
} finally {
    $env:PYTHONIOENCODING = $previousEncoding
    $env:PYTHONUTF8 = $previousUtf8
    Pop-Location
}
