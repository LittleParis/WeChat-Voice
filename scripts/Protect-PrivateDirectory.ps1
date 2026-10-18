param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $Path -Force | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$directory = [IO.DirectoryInfo]::new($Path)
$acl = $directory.GetAccessControl([Security.AccessControl.AccessControlSections]::Access)
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))) {
    $acl.RemoveAccessRuleAll($rule)
}
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $userSid, [Security.AccessControl.FileSystemRights]::FullControl,
    [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow))
$directory.SetAccessControl($acl)
