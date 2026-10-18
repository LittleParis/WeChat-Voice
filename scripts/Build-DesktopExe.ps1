[CmdletBinding()]
param([string]$PythonPath = '.\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
$originalPath = $env:PATH
try {
    $dependency = Join-Path $project 'third_party/wechat-decrypt'
    $commit = & git -C $dependency rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $commit.Trim() -ne 'f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd') {
        throw 'Download the pinned wechat-decrypt dependency described in README before building.'
    }
    # Keep unrelated developer tools (e.g. Poppler's incompatible icuuc.dll)
    # out of PyInstaller's native dependency search. Qt uses Windows' ICU.
    $pythonDirectory = Split-Path -Parent (Resolve-Path -LiteralPath $PythonPath).Path
    $env:PATH = "$pythonDirectory;$env:SystemRoot\System32;$env:SystemRoot"
    & $PythonPath -m PyInstaller --noconfirm --clean --distpath build/desktop-dist --workpath build/desktop-work desktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    Get-FileHash -LiteralPath build/desktop-dist/VoiceKeep.exe -Algorithm SHA256
} finally {
    $env:PATH = $originalPath
    Pop-Location
}
