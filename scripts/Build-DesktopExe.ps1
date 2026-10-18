[CmdletBinding()]
param([string]$PythonPath = '.\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    $dependency = Join-Path $project 'third_party/wechat-decrypt'
    $commit = & git -C $dependency rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $commit.Trim() -ne 'f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd') {
        throw 'Download the pinned wechat-decrypt dependency described in README before building.'
    }
    & $PythonPath -m PyInstaller --noconfirm --clean --distpath build/desktop-dist --workpath build/desktop-work desktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    Get-FileHash -LiteralPath build/desktop-dist/VoiceKeep.exe -Algorithm SHA256
} finally { Pop-Location }
