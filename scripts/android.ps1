[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('SetupSigning', 'Plan', 'Check', 'Build', 'BuildInternal', 'Verify')]
    [string]$Mode,
    [string]$JavaHome = 'C:\Program Files\Java\jdk-17.0.10',
    [string]$BuildPython = 'E:\Anaconda\python.exe',
    [string]$ReadElf = 'C:\Program Files\mingw64\bin\readelf.exe'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$AndroidRoot = Join-Path $RepoRoot 'android'
$SdkRoot = Join-Path $RepoRoot '.android-sdk'
$GradleHome = Join-Path $RepoRoot '.toolchains\gradle-9.1.0\bin\gradle.bat'
$BuildTools = Join-Path $SdkRoot 'build-tools\36.0.0'
$Aapt = Join-Path $BuildTools 'aapt.exe'
$ApkSigner = Join-Path $BuildTools 'apksigner.bat'
$ZipAlign = Join-Path $BuildTools 'zipalign.exe'
$Verifier = Join-Path $PSScriptRoot 'verify_android_apk.py'
$SigningDir = Join-Path $RepoRoot '.signing'
$Keystore = Join-Path $SigningDir 'release.jks'
$SigningProperties = Join-Path $SigningDir 'keystore.properties'
$SigningFingerprint = Join-Path $SigningDir 'certificate-sha256.txt'
$StableFingerprint = 'ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3'
$Flavor = if ($Mode -eq 'BuildInternal') { 'internal' } else { 'public' }
if ($Mode -ne 'SetupSigning') {
    $Version = Get-Content -LiteralPath (Join-Path $RepoRoot 'version.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Version.versionName -notmatch '^\d+\.\d+\.\d+(-beta\.\d+)?$' -or
        $Version.versionCode -isnot [int] -or $Version.versionCode -lt 1 -or $Version.versionCode -gt 2100000000 -or
        $Version.channel -notin @('beta', 'stable') -or
        ($Version.versionName.Contains('-beta.') -ne ($Version.channel -eq 'beta'))) {
        throw 'Invalid version.json'
    }
    $DeliveryDir = Join-Path $RepoRoot ("dist\android\{0}\{1}" -f $Version.versionName, $Flavor)
    $DeliveryName = "bagu-$($Version.versionName)-$Flavor-arm64-v8a.apk"
    $DeliveryApk = Join-Path $DeliveryDir $DeliveryName
}

function Require-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label不存在：$Path"
    }
}

function Invoke-Tool([string]$Tool, [string[]]$Arguments) {
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败（退出码 $LASTEXITCODE）：$Tool"
    }
}

function Invoke-KeyToolQuiet([string]$KeyTool, [string[]]$Arguments, [string]$FailureMessage) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # keytool emits informational status on stderr; keep it process-local and
        # judge success solely by its exit code, never by its localized output.
        $ErrorActionPreference = 'Continue'
        & $KeyTool @Arguments 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw $FailureMessage
    }
}

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Set-ProjectEnvironment {
    Require-File (Join-Path $JavaHome 'bin\java.exe') 'JDK17'
    Require-File $GradleHome '项目 Gradle'
    Require-File $BuildPython 'Chaquopy Python'
    Require-File $Aapt 'aapt'
    Require-File $ApkSigner 'apksigner'
    Require-File $ZipAlign 'zipalign'
    Require-File $ReadElf 'GNU readelf'
    Require-File $Verifier 'APK 验证器'
    $env:JAVA_HOME = $JavaHome
    $env:ANDROID_HOME = $SdkRoot
    $env:ANDROID_SDK_ROOT = $SdkRoot
    $env:ANDROID_USER_HOME = Join-Path $RepoRoot '.android-user-home'
    $env:GRADLE_USER_HOME = Join-Path $RepoRoot '.gradle-user-home'
}

function Get-KeyTool {
    $keyTool = Join-Path $JavaHome 'bin\keytool.exe'
    Require-File $keyTool 'JDK keytool'
    return $keyTool
}

function Get-SigningProperties {
    $properties = @{}
    foreach ($line in Get-Content -LiteralPath $SigningProperties -Encoding UTF8) {
        if ($line -match '^([^=]+)=(.*)$') {
            $properties[$Matches[1]] = $Matches[2]
        }
    }
    foreach ($name in @('storePassword', 'keyAlias', 'keyPassword')) {
        if (-not $properties.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($properties[$name])) {
            throw "invalid signing identity: keystore.properties lacks $name"
        }
    }
    return $properties
}

function Invoke-WithSigningEnvironment([hashtable]$Properties, [scriptblock]$Action) {
    $variables = @{
        'BAGU_SETUP_STORE_PASSWORD' = $Properties['storePassword']
        'BAGU_SETUP_KEY_PASSWORD' = $Properties['keyPassword']
    }
    $previous = @{}
    try {
        foreach ($name in $variables.Keys) {
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, [EnvironmentVariableTarget]::Process)
            [Environment]::SetEnvironmentVariable($name, $variables[$name], [EnvironmentVariableTarget]::Process)
        }
        & $Action
    }
    finally {
        foreach ($name in $variables.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], [EnvironmentVariableTarget]::Process)
        }
    }
}

function Get-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', 'A').Replace('/', 'B')
}

function Get-ValidatedKeystoreFingerprint {
    $properties = Get-SigningProperties
    $keyTool = Get-KeyTool
    $token = [guid]::NewGuid().ToString('N')
    $csr = Join-Path $SigningDir "identity-validation-$token.csr"
    $certificate = Join-Path $SigningDir "identity-validation-$token.der"
    try {
        Invoke-WithSigningEnvironment $properties {
            Invoke-KeyToolQuiet -KeyTool $keyTool -Arguments @(
                '-certreq', '-alias', $properties['keyAlias'], '-keystore', $Keystore,
                '-storepass:env', 'BAGU_SETUP_STORE_PASSWORD',
                '-keypass:env', 'BAGU_SETUP_KEY_PASSWORD', '-file', $csr
            ) -FailureMessage 'invalid signing identity: keytool could not use the private-entry password'
            Invoke-KeyToolQuiet -KeyTool $keyTool -Arguments @(
                '-exportcert', '-alias', $properties['keyAlias'], '-keystore', $Keystore,
                '-storepass:env', 'BAGU_SETUP_STORE_PASSWORD', '-file', $certificate
            ) -FailureMessage 'invalid signing identity: keytool could not export the public certificate'
        }
        return Get-Sha256 $certificate
    }
    finally {
        Remove-Item -LiteralPath $csr -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $certificate -Force -ErrorAction SilentlyContinue
    }
}

function Get-ExpectedSigningFingerprint {
    if (-not (Test-Path -LiteralPath $SigningFingerprint -PathType Leaf)) {
        return $StableFingerprint
    }
    $raw = Get-Content -LiteralPath $SigningFingerprint -Raw -Encoding UTF8
    if ($raw -notmatch '^[0-9a-f]{64}\r?\n$') {
        throw 'invalid signing identity: certificate-sha256.txt must contain exactly one lowercase SHA-256 fingerprint'
    }
    return $raw.Trim()
}

function Assert-ExistingSigningIdentity {
    $hasKeystore = Test-Path -LiteralPath $Keystore -PathType Leaf
    $hasProperties = Test-Path -LiteralPath $SigningProperties -PathType Leaf
    $hasFingerprint = Test-Path -LiteralPath $SigningFingerprint -PathType Leaf
    if (-not $hasKeystore -or -not $hasProperties) {
        if ($hasKeystore -or $hasProperties -or $hasFingerprint) {
            throw 'partial signing identity: release.jks, keystore.properties, and any certificate pin must be restored together; refusing overwrite'
        }
        throw 'missing signing identity'
    }
    $actual = Get-ValidatedKeystoreFingerprint
    $expected = Get-ExpectedSigningFingerprint
    if ($actual -ne $expected) {
        throw "invalid signing identity: certificate fingerprint mismatch ($actual)"
    }
    return $actual
}

function New-SigningIdentity {
    if ((Test-Path -LiteralPath $Keystore -PathType Leaf) -or (Test-Path -LiteralPath $SigningProperties -PathType Leaf) -or (Test-Path -LiteralPath $SigningFingerprint -PathType Leaf)) {
        throw 'partial signing identity: refusing to create over existing signing markers'
    }
    New-Item -ItemType Directory -Path $SigningDir -Force | Out-Null
    $properties = @{
        'storePassword' = Get-RandomSecret
        'keyAlias' = 'bagu-release'
        'keyPassword' = Get-RandomSecret
    }
    $keyTool = Get-KeyTool
    Invoke-WithSigningEnvironment $properties {
        Invoke-KeyToolQuiet -KeyTool $keyTool -Arguments @(
            '-genkeypair', '-alias', $properties['keyAlias'], '-keyalg', 'RSA', '-keysize', '4096', '-validity', '36500',
            '-keystore', $Keystore, '-storetype', 'JKS',
            '-storepass:env', 'BAGU_SETUP_STORE_PASSWORD', '-keypass:env', 'BAGU_SETUP_KEY_PASSWORD',
            '-dname', 'CN=Bagu Helper Android Release, OU=Local, O=Bagu Helper, C=CN'
        ) -FailureMessage 'keytool failed to create the local signing identity'
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = "storePassword=$($properties['storePassword'])`nkeyAlias=$($properties['keyAlias'])`nkeyPassword=$($properties['keyPassword'])`n"
    [System.IO.File]::WriteAllText($SigningProperties, $content, $utf8NoBom)
    $fingerprint = Get-ValidatedKeystoreFingerprint
    [System.IO.File]::WriteAllText($SigningFingerprint, "$fingerprint`n", $utf8NoBom)
    return (Assert-ExistingSigningIdentity)
}

function Get-ApkFingerprint([string]$ApkPath) {
    $output = (& $ApkSigner verify --verbose --print-certs $ApkPath 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "apksigner 验证失败：$ApkPath"
    }
    $match = [regex]::Match($output, 'certificate SHA-256 digest:\s*([0-9A-Fa-f:]+)')
    if (-not $match.Success) {
        throw 'apksigner 未输出签名证书 SHA-256 指纹。'
    }
    $fingerprint = ($match.Groups[1].Value -replace ':', '').ToLowerInvariant()
    if ($fingerprint -ne $StableFingerprint) {
        throw "签名证书指纹不匹配：$fingerprint"
    }
    return $fingerprint
}

function Assert-Badging([string]$ApkPath) {
    $badging = (& $Aapt dump badging $ApkPath 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "aapt badging 验证失败：$ApkPath"
    }
    foreach ($required in @(
        "package: name='io.github.ingnijm.baguhelper' versionCode='$($Version.versionCode)' versionName='$($Version.versionName)'",
        "application-label:'八股助手'",
        "sdkVersion:'29'",
        "targetSdkVersion:'36'",
        "native-code: 'arm64-v8a'"
    )) {
        if (-not $badging.Contains($required)) {
            throw "APK badging 不符合发布契约：缺少 $required"
        }
    }
}

function Invoke-ContentVerification([string]$ApkPath, [string]$Flavor) {
    $arguments = @($Verifier, $ApkPath, '--flavor', $Flavor, '--readelf', $ReadElf)
    if ($Flavor -eq 'public') { $arguments += @('--expected-questions', '0') }
    Invoke-Tool $BuildPython $arguments
}

function Write-DeliveryMetadata([string]$ApkPath, [string]$Fingerprint) {
    if ($Flavor -ne 'public' -or $Fingerprint -ne $StableFingerprint) { throw 'Only trusted public APKs get release metadata' }
    Invoke-Tool $BuildPython @((Join-Path $PSScriptRoot 'release_metadata.py'), 'prepare', $DeliveryDir,
        '--notes', (Join-Path $RepoRoot "docs\releases\$($Version.versionName).md"))
}

function New-AsciiToolCopy([string]$ApkPath) {
    $toolApk = Join-Path $DeliveryDir ("verify-arm64-{0}.apk" -f [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $toolApk) {
        throw 'verification temporary APK path already exists; refusing to overwrite it'
    }
    Copy-Item -LiteralPath $ApkPath -Destination $toolApk
    $sourceHash = Get-Sha256 $ApkPath
    $toolHash = Get-Sha256 $toolApk
    if ($sourceHash -ne $toolHash) {
        throw '用于 Android SDK 工具的临时 ASCII APK 与精确交付文件不一致。'
    }
    return $toolApk
}

function Invoke-DeliveryVerification {
    Require-File $DeliveryApk '精确交付 APK'
    Require-File (Join-Path $DeliveryDir 'SHA256SUMS') 'SHA256SUMS'
    Require-File (Join-Path $DeliveryDir 'certificate-sha256.txt') 'certificate-sha256.txt'
    Require-File (Join-Path $DeliveryDir 'INSTALL.md') 'INSTALL.md'
    Invoke-Tool $BuildPython @((Join-Path $PSScriptRoot 'release_metadata.py'), 'verify', $DeliveryDir)
    $sumPattern = '^([0-9a-f]{64}) \*' + [regex]::Escape($DeliveryName) + '$'
    $expectedHash = ([regex]::Match((Get-Content -LiteralPath (Join-Path $DeliveryDir 'SHA256SUMS') -Raw -Encoding UTF8), $sumPattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)).Groups[1].Value
    $actualHash = Get-Sha256 $DeliveryApk
    if ($expectedHash.Length -ne 64 -or $actualHash -ne $expectedHash) {
        throw 'SHA256SUMS 与精确交付 APK 不匹配。'
    }
    $metadataFingerprint = (Get-Content -LiteralPath (Join-Path $DeliveryDir 'certificate-sha256.txt') -Raw -Encoding UTF8).Trim()
    if ($metadataFingerprint -notmatch '^[0-9a-f]{64}$' -or $metadataFingerprint -ne $StableFingerprint) {
        throw 'certificate-sha256.txt does not match the trusted signing certificate fingerprint.'
    }
    $installNotes = Get-Content -LiteralPath (Join-Path $DeliveryDir 'INSTALL.md') -Raw -Encoding UTF8
    if ($installNotes -notmatch ('adb install.*' + [regex]::Escape($DeliveryName))) {
        throw 'INSTALL.md does not name the exact delivery APK in its adb install command.'
    }
    $toolApk = New-AsciiToolCopy $DeliveryApk
    try {
        $fingerprint = Get-ApkFingerprint $toolApk
        if ($fingerprint -ne $metadataFingerprint) {
            throw 'certificate-sha256.txt does not match the APK signer.'
        }
        Assert-Badging $toolApk
        Invoke-Tool $ZipAlign @('-c', '-P', '16', '4', $toolApk)
        Invoke-ContentVerification $DeliveryApk 'public'
        Write-Host "发布 APK 验证通过：$DeliveryName，证书 $fingerprint"
    }
    finally {
        Remove-Item -LiteralPath $toolApk -Force -ErrorAction SilentlyContinue
    }
}

switch ($Mode) {
    'Check' {
        Set-ProjectEnvironment
        Push-Location $AndroidRoot
        try {
            Invoke-Tool $GradleHome @('--no-daemon', '--console=plain',
                ':app:testPublicDebugUnitTest', ':app:lintPublicRelease',
                "-PbaguBuildPython=$BuildPython", '-PbaguAbi=arm64-v8a')
        }
        finally { Pop-Location }
    }
    'Plan' {
        [ordered]@{ versionName = $Version.versionName; versionCode = $Version.versionCode;
            channel = $Version.channel; flavor = 'public'; deliveryName = $DeliveryName;
            tasks = @(':app:assemblePublicRelease', ':app:testPublicDebugUnitTest', ':app:lintPublicRelease')
        } | ConvertTo-Json -Compress
    }
    'SetupSigning' {
        $hasSigningMarker = (Test-Path -LiteralPath $Keystore -PathType Leaf) -or (Test-Path -LiteralPath $SigningProperties -PathType Leaf) -or (Test-Path -LiteralPath $SigningFingerprint -PathType Leaf)
        if ($hasSigningMarker) {
            $fingerprint = Assert-ExistingSigningIdentity
            Write-Host "Validated and reused the existing signing identity; public fingerprint $fingerprint."
        }
        else {
            $fingerprint = New-SigningIdentity
            Write-Host "Created a local signing identity; public fingerprint $fingerprint."
        }
    }
    { $_ -in @('Build', 'BuildInternal') } {
        Set-ProjectEnvironment
        $identity = Assert-ExistingSigningIdentity
        if ($identity -ne $StableFingerprint) { throw 'Build requires the existing trusted release identity' }
        if (Test-Path -LiteralPath $DeliveryApk) { throw 'Version delivery already exists; use Verify or choose a new version, never overwrite' }
        $variant = (Get-Culture).TextInfo.ToTitleCase($Flavor)
        $gradleArgs = @(
            '--no-daemon', '--console=plain',
            ":app:assemble${variant}Release", ":app:test${variant}DebugUnitTest", ":app:lint${variant}Release",
            "-PbaguBuildPython=$BuildPython", '-PbaguAbi=arm64-v8a'
        )
        Push-Location $AndroidRoot
        try {
            Invoke-Tool $GradleHome $gradleArgs
        }
        finally {
            Pop-Location
        }
        $builtApk = Join-Path $AndroidRoot "app\build\outputs\apk\$Flavor\release\app-$Flavor-release.apk"
        Require-File $builtApk 'release APK'
        Invoke-ContentVerification $builtApk $Flavor
        $fingerprint = Get-ApkFingerprint $builtApk
        Assert-Badging $builtApk
        Invoke-Tool $ZipAlign @('-c', '-P', '16', '4', $builtApk)
        New-Item -ItemType Directory -Path $DeliveryDir -Force | Out-Null
        Copy-Item -LiteralPath $builtApk -Destination $DeliveryApk
        if ($Flavor -eq 'public') {
            Write-DeliveryMetadata $DeliveryApk $fingerprint
            Invoke-DeliveryVerification
        } else {
            Write-Host 'Internal local-use APK only; no public release metadata generated.'
        }
    }
    'Verify' {
        Set-ProjectEnvironment
        Invoke-DeliveryVerification
    }
}
