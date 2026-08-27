[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('SetupSigning', 'Build', 'Verify')]
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
$DeliveryDir = Join-Path $RepoRoot 'dist\android'
$DeliveryName = '八股助手-0.1.0-beta.1-arm64-v8a.apk'
$DeliveryApk = Join-Path $DeliveryDir $DeliveryName
$StableFingerprint = 'ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3'

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
    if ($fingerprint -ne (Get-ExpectedSigningFingerprint)) {
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
        "package: name='io.github.ingnijm.baguhelper' versionCode='1' versionName='0.1.0-beta.1'",
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

function Invoke-ContentVerification([string]$ApkPath, [string]$Flavor, [int]$ExpectedQuestions) {
    Invoke-Tool $BuildPython @($Verifier, $ApkPath, '--flavor', $Flavor, '--expected-questions', "$ExpectedQuestions", '--readelf', $ReadElf)
}

function Write-DeliveryMetadata([string]$ApkPath, [string]$Fingerprint) {
    New-Item -ItemType Directory -Path $DeliveryDir -Force | Out-Null
    $hash = Get-Sha256 $ApkPath
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $DeliveryDir 'SHA256SUMS'), "$hash *$DeliveryName`n", $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $DeliveryDir 'certificate-sha256.txt'), "$Fingerprint`n", $utf8NoBom)
    $notes = @(
        '八股助手 Android Beta 安装说明',
        '',
        '首次安装：adb install "八股助手-0.1.0-beta.1-arm64-v8a.apk"',
        '更新：先在应用设置中导出 .bagu-backup，再使用同一签名的 adb install -r 更新 APK。',
        '卸载会清空应用私有数据；跨卸载迁移只能通过 .bagu-backup 导出/导入。',
        '请离线、受控地备份 release.jks 和 keystore.properties；丢失稳定签名身份将无法发布可信更新。',
        '此 Beta 不会自动发布到 GitHub 或任何应用商店。'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText((Join-Path $DeliveryDir 'install-notes.txt'), "$notes`r`n", $utf8NoBom)
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
    Require-File (Join-Path $DeliveryDir 'install-notes.txt') 'install-notes.txt'
    $expectedHash = ([regex]::Match((Get-Content -LiteralPath (Join-Path $DeliveryDir 'SHA256SUMS') -Raw -Encoding UTF8), '^([0-9a-f]{64}) \*八股助手-0\.1\.0-beta\.1-arm64-v8a\.apk$', [System.Text.RegularExpressions.RegexOptions]::Multiline)).Groups[1].Value
    $actualHash = Get-Sha256 $DeliveryApk
    if ($expectedHash.Length -ne 64 -or $actualHash -ne $expectedHash) {
        throw 'SHA256SUMS 与精确交付 APK 不匹配。'
    }
    $metadataFingerprint = (Get-Content -LiteralPath (Join-Path $DeliveryDir 'certificate-sha256.txt') -Raw -Encoding UTF8).Trim()
    if ($metadataFingerprint -notmatch '^[0-9a-f]{64}$' -or $metadataFingerprint -ne (Get-ExpectedSigningFingerprint)) {
        throw 'certificate-sha256.txt does not match the trusted signing certificate fingerprint.'
    }
    $installNotes = Get-Content -LiteralPath (Join-Path $DeliveryDir 'install-notes.txt') -Raw -Encoding UTF8
    if ($installNotes -notmatch ('adb install.*' + [regex]::Escape($DeliveryName))) {
        throw 'install-notes.txt does not name the exact delivery APK in its adb install command.'
    }
    $toolApk = New-AsciiToolCopy $DeliveryApk
    try {
        $fingerprint = Get-ApkFingerprint $toolApk
        if ($fingerprint -ne $metadataFingerprint) {
            throw 'certificate-sha256.txt does not match the APK signer.'
        }
        Assert-Badging $toolApk
        Invoke-Tool $ZipAlign @('-c', '-P', '16', '4', $toolApk)
        Invoke-ContentVerification $DeliveryApk 'internal' 408
        Write-Host "发布 APK 验证通过：$DeliveryName，证书 $fingerprint"
    }
    finally {
        Remove-Item -LiteralPath $toolApk -Force -ErrorAction SilentlyContinue
    }
}

switch ($Mode) {
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
    'Build' {
        Set-ProjectEnvironment
        Assert-ExistingSigningIdentity
        $gradleArgs = @(
            '--no-daemon', '--console=plain',
            ':app:assembleInternalRelease', ':app:assemblePublicRelease',
            ':app:testInternalDebugUnitTest', ':app:testPublicDebugUnitTest',
            ':app:lintInternalRelease', ':app:lintPublicRelease',
            "-PbaguBuildPython=$BuildPython", '-PbaguAbi=arm64-v8a',
            '-PbaguVersionCode=1', '-PbaguVersionName=0.1.0-beta.1'
        )
        Push-Location $AndroidRoot
        try {
            Invoke-Tool $GradleHome $gradleArgs
        }
        finally {
            Pop-Location
        }
        $internalApk = Join-Path $AndroidRoot 'app\build\outputs\apk\internal\release\app-internal-release.apk'
        $publicApk = Join-Path $AndroidRoot 'app\build\outputs\apk\public\release\app-public-release.apk'
        Require-File $internalApk '内部 release APK'
        Require-File $publicApk '公开 release APK'
        Invoke-ContentVerification $internalApk 'internal' 408
        Invoke-ContentVerification $publicApk 'public' 0
        $fingerprint = Get-ApkFingerprint $internalApk
        Get-ApkFingerprint $publicApk | Out-Null
        New-Item -ItemType Directory -Path $DeliveryDir -Force | Out-Null
        Copy-Item -LiteralPath $internalApk -Destination $DeliveryApk -Force
        Write-DeliveryMetadata $DeliveryApk $fingerprint
        Invoke-DeliveryVerification
    }
    'Verify' {
        Set-ProjectEnvironment
        Invoke-DeliveryVerification
    }
}
