[CmdletBinding()]
param(
    [string]$SdkPath = $env:ANDROID_SDK_ROOT,
    [string]$JdkPath = $env:JAVA_HOME,
    [string]$BuildToolsVersion = '34.0.0',
    [string]$Platform = 'android-34',
    [switch]$WithTests
)
$ErrorActionPreference = 'Stop'
if (-not $SdkPath) { throw 'Set ANDROID_SDK_ROOT or pass -SdkPath.' }
if (-not $JdkPath) { throw 'Set JAVA_HOME or pass -JdkPath.' }
$projectPath = Split-Path -Parent $PSScriptRoot
$toolsPath = Join-Path $SdkPath "build-tools\$BuildToolsVersion"
$androidJar = Join-Path $SdkPath "platforms\$Platform\android.jar"
$javac = Join-Path $JdkPath 'bin\javac.exe'
$jar = Join-Path $JdkPath 'bin\jar.exe'
$keytool = Join-Path $JdkPath 'bin\keytool.exe'
$aapt = Join-Path $toolsPath 'aapt2.exe'
$d8 = Join-Path $toolsPath 'd8.bat'
$zipalign = Join-Path $toolsPath 'zipalign.exe'
$signer = Join-Path $toolsPath 'apksigner.bat'
foreach ($requiredPath in @($androidJar,$javac,$jar,$keytool,$aapt,$d8,$zipalign,$signer)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Missing tool: $requiredPath" }
}
function Invoke-LibraryTool {
    param([string]$Tool,[string[]]$Arguments)
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Build failed ($LASTEXITCODE): $Tool" }
}
$originalJavaHome = $env:JAVA_HOME
Push-Location $projectPath
try {
    $env:JAVA_HOME = $JdkPath
    # Each build has its own directory so old classes or test fixtures cannot leak into an APK.
    $relativeBuild = 'build/library-' + [guid]::NewGuid().ToString('N').Substring(0,10)
    $buildPath = Join-Path $projectPath $relativeBuild
    foreach ($folder in @('classes','dex','assets')) { New-Item -ItemType Directory -Path (Join-Path $buildPath $folder) -Force | Out-Null }
    $sourceDirectories = @('android-library/src')
    if ($WithTests) {
        $sourceDirectories += 'tests/android'
        $fixtures = 'build/android-test-fixtures-v2'
        if (-not (Test-Path -LiteralPath "$fixtures/valid.voicepack.zip")) { throw 'Generate fixtures first: python scripts/make_mobile_test_fixtures.py' }
        Copy-Item -LiteralPath $fixtures -Destination (Join-Path $buildPath 'assets/fixtures') -Recurse
    }
    $sources = foreach ($directory in $sourceDirectories) {
        Get-ChildItem -LiteralPath $directory -Recurse -File -Filter '*.java' | ForEach-Object { '"' + $_.FullName.Substring($projectPath.Length+1).Replace('\','/') + '"' }
    }
    [IO.File]::WriteAllLines((Join-Path $buildPath 'sources.args'),$sources,[Text.UTF8Encoding]::new($false))
    Invoke-LibraryTool $javac @('-encoding','UTF-8','-source','8','-target','8','-classpath',$androidJar,'-d',"$relativeBuild/classes","@$relativeBuild/sources.args")
    Invoke-LibraryTool $jar @('--create','--file',"$relativeBuild/program.jar",'-C',"$relativeBuild/classes",'.')
    Invoke-LibraryTool $d8 @('--lib',$androidJar,'--min-api','29','--output',"$relativeBuild/dex","$relativeBuild/program.jar")
    $manifest = [IO.File]::ReadAllText((Join-Path $projectPath 'android-library/AndroidManifest.xml'))
    if ($WithTests) { $manifest = $manifest.Replace('</manifest>','<instrumentation android:name="cn.voicekeep.library.LibraryChecks" android:targetPackage="cn.voicekeep.library" android:label="Offline library checks" /><instrumentation android:name="cn.voicekeep.library.LibraryUiChecks" android:targetPackage="cn.voicekeep.library" android:label="Foreground UI checks" /></manifest>') }
    [IO.File]::WriteAllText((Join-Path $buildPath 'AndroidManifest.xml'),$manifest,[Text.UTF8Encoding]::new($false))
    Invoke-LibraryTool $aapt @('compile','--dir','android-library/res','-o',"$relativeBuild/resources.zip")
    Invoke-LibraryTool $aapt @('link','-o',"$relativeBuild/unsigned.apk",'--manifest',"$relativeBuild/AndroidManifest.xml",'-I',$androidJar,"$relativeBuild/resources.zip")
    # This bundled Windows aapt2 writes backslashes inside nested asset names.
    # JDK jar packs portable '/' names that Android AssetManager can resolve.
    if (Get-ChildItem -LiteralPath (Join-Path $buildPath 'assets') -Recurse -File) {
        Invoke-LibraryTool $jar @('--update','--file',"$relativeBuild/unsigned.apk",'-C',$relativeBuild,'assets')
    }
    Invoke-LibraryTool $jar @('--update','--file',"$relativeBuild/unsigned.apk",'-C',"$relativeBuild/dex",'classes.dex')
    Invoke-LibraryTool $zipalign @('-f','4',"$relativeBuild/unsigned.apk","$relativeBuild/aligned.apk")
    if (-not (Test-Path -LiteralPath 'build/library-debug.keystore')) {
        Invoke-LibraryTool $keytool @('-genkeypair','-keystore','build/library-debug.keystore','-alias','voicekeep','-keyalg','RSA','-keysize','2048','-validity','10000','-storepass','android','-keypass','android','-dname','CN=VoiceKeep Development')
    }
    $apk = if ($WithTests) { 'build/voice-library-checks.apk' } else { 'build/voice-library-debug.apk' }
    Invoke-LibraryTool $signer @('sign','--ks','build/library-debug.keystore','--ks-key-alias','voicekeep','--ks-pass','pass:android','--key-pass','pass:android','--out',$apk,"$relativeBuild/aligned.apk")
    Invoke-LibraryTool $signer @('verify','--verbose',$apk)
    Get-FileHash -LiteralPath $apk -Algorithm SHA256
} finally { $env:JAVA_HOME = $originalJavaHome; Pop-Location }
