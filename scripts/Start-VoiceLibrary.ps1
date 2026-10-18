[CmdletBinding()]
param([int]$Port = 8766, [switch]$RefreshData)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Push-Location $projectPath
try {
    $serverArguments = @('.\scripts\local_library_server.py', '--port', $Port)
    if ($RefreshData) { $serverArguments += '--refresh' }
    & '.\.venv\Scripts\python.exe' @serverArguments
    if ($LASTEXITCODE -ne 0) { throw 'Local voice library stopped unexpectedly.' }
} finally { Pop-Location }
