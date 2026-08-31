[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApkPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$Beta5ApkPath,
    [string]$BuildPython = '',
    [string]$JavaHome = 'C:\Program Files\Java\jdk-17.0.10'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$androidRoot = Join-Path $repoRoot 'android'
$packageName = 'io.github.ingnijm.baguhelper'
$descriptorPath = Join-Path $repoRoot 'docs\releases\0.1.0-beta.6-question-pack.json'
$versionPath = Join-Path $repoRoot 'version.json'
$sdkRoot = if ([string]::IsNullOrWhiteSpace($env:ANDROID_SDK_ROOT)) {
    Join-Path $repoRoot '.android-sdk'
} else { $env:ANDROID_SDK_ROOT }
$gradle = Join-Path $repoRoot '.toolchains\gradle-9.1.0\bin\gradle.bat'
$adb = Join-Path $sdkRoot 'platform-tools\adb.exe'
$emulator = Join-Path $sdkRoot 'emulator\emulator.exe'
$avdManager = Join-Path $sdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$aapt = Join-Path $sdkRoot 'build-tools\36.0.0\aapt.exe'
$apkSigner = Join-Path $sdkRoot 'build-tools\36.0.0\apksigner.bat'
$releaseMetadata = Join-Path $repoRoot 'scripts\release_metadata.py'
$apis = @(29, 36)
$imageIds = @{
    29 = 'system-images;android-29;google_apis;x86_64'
    36 = 'system-images;android-36;google_apis;x86_64'
}
$runId = [Guid]::NewGuid().ToString('N')
$runRoot = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('bagu-beta6-avd-' + $runId)))
$originalAvdHome = $env:ANDROID_AVD_HOME
$originalAndroidUserHome = $env:ANDROID_USER_HOME
$originalAndroidHome = $env:ANDROID_HOME
$originalAndroidSdkRoot = $env:ANDROID_SDK_ROOT
$originalGradleUserHome = $env:GRADLE_USER_HOME
$originalJavaHome = $env:JAVA_HOME
$serial = $null
$port = 0
$api = 0
$avdName = $null
$emulatorProcess = $null
$scenarioResults = New-Object Collections.Generic.List[object]
$apiResults = New-Object Collections.Generic.List[object]

function Stop-Gate([string]$Code) {
    throw [InvalidOperationException]::new($Code)
}

function Require-File([string]$Path, [string]$Code) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Stop-Gate $Code }
}

function Invoke-NativeQuietResult([string]$Tool, [string[]]$Arguments) {
    if ([string]::IsNullOrWhiteSpace($Tool) -or -not (Test-Path -LiteralPath $Tool -PathType Leaf)) {
        Stop-Gate 'native-process-invocation-failed'
    }
    $previousErrorActionPreference = $ErrorActionPreference
    $previousLastExitCode = $global:LASTEXITCODE
    $lines = $null
    $exitCode = $null
    try {
        # Windows PowerShell 5 surfaces successful native stderr as ErrorRecord objects.
        # Continue is scoped to this external process only; invocation failures remain fatal.
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = $null
        $lines = & $Tool @Arguments 2>$null
        $exitCode = $global:LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        $global:LASTEXITCODE = $previousLastExitCode
    }
    if ($null -eq $exitCode) { Stop-Gate 'native-process-invocation-failed' }
    return [pscustomobject]@{
        Output = (@($lines) -join "`n").Trim()
        ExitCode = [int]$exitCode
    }
}

function Invoke-Quiet([string]$Tool, [string[]]$Arguments, [string]$FailureCode) {
    $result = Invoke-NativeQuietResult $Tool $Arguments
    if ($result.ExitCode -ne 0) { Stop-Gate $FailureCode }
    return $result.Output
}

function Get-ScopedAdb([string[]]$Arguments) {
    if ([string]::IsNullOrWhiteSpace($serial)) { Stop-Gate 'serial-not-selected' }
    $result = Invoke-NativeQuietResult $adb (@('-s', $serial) + $Arguments)
    if ($result.ExitCode -ne 0) { Stop-Gate 'adb-read-failed' }
    return $result.Output
}

function Get-DeviceProperty([string]$Name) {
    return Get-ScopedAdb @('shell', 'getprop', $Name)
}

function Assert-DisposableEmulator {
    # Explicit forbidden hardware markers: vivo and V2309A.
    if ($null -eq $emulatorProcess -or $emulatorProcess.HasExited) { Stop-Gate 'emulator-process-not-owned' }
    if ($emulatorProcess.Id -le 0) { Stop-Gate 'emulator-process-invalid' }
    if ($serial -ne "emulator-$port") { Stop-Gate 'emulator-serial-mismatch' }
    if ((Get-ScopedAdb @('get-state')) -ne 'device') { Stop-Gate 'emulator-not-ready' }
    if ((Get-DeviceProperty 'ro.kernel.qemu') -ne '1') { Stop-Gate 'device-not-qemu' }
    if ((Get-DeviceProperty 'ro.build.version.sdk') -ne [string]$api) { Stop-Gate 'device-api-mismatch' }
    $manufacturer = Get-DeviceProperty 'ro.product.manufacturer'
    $model = Get-DeviceProperty 'ro.product.model'
    $device = Get-DeviceProperty 'ro.product.device'
    if (("$manufacturer $model $device") -match '(?i)vivo|v2309a') { Stop-Gate 'forbidden-device-identity' }
    $bootAvdName = Get-DeviceProperty 'ro.boot.qemu.avd_name'
    $consoleAvdName = (Get-ScopedAdb @('emu', 'avd', 'name')).Split("`n")[0].Trim()
    if ($bootAvdName -ne $avdName -or $consoleAvdName -ne $avdName) {
        Stop-Gate 'emulator-avd-mismatch'
    }
}

function Invoke-ScopedAdbMutation([string[]]$Arguments, [string]$FailureCode) {
    # The identity proof is intentionally repeated immediately before every mutation.
    Assert-DisposableEmulator
    $result = Invoke-NativeQuietResult $adb (@('-s', $serial) + $Arguments)
    if ($result.ExitCode -ne 0) { Stop-Gate $FailureCode }
    return $result.Output
}

function Get-FreeEmulatorPort {
    for ($candidate = 5556; $candidate -le 5680; $candidate += 2) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $candidate)
        try { $listener.Start() }
        catch {
            $listener.Stop()
            continue
        }
        try {
            $probeSerial = "emulator-$candidate"
            $result = Invoke-NativeQuietResult $adb @('-s', $probeSerial, 'get-state')
            if ($result.ExitCode -ne 0) { return $candidate }
        }
        finally { $listener.Stop() }
    }
    Stop-Gate 'no-free-emulator-port'
}

function Get-TrustedCertificateFingerprint {
    $loader = "import pathlib,sys; path=pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0,str(path.parent)); import release_metadata; print(release_metadata.CERTIFICATE)"
    $trusted = Invoke-Quiet $BuildPython @('-c', $loader, $releaseMetadata) 'certificate-pin-load-failed'
    if ($trusted -notmatch '^[0-9a-f]{64}$') { Stop-Gate 'certificate-pin-invalid' }
    return $trusted
}

function Assert-ApkContract([string]$Path, [string]$VersionName, [int]$VersionCode, [bool]$RequiresPack) {
    Require-File $Path 'apk-missing'
    if ([IO.Path]::GetExtension($Path) -ne '.apk') { Stop-Gate 'apk-extension-invalid' }
    $badging = Invoke-Quiet $aapt @('dump', 'badging', $Path) 'apk-badging-invalid'
    foreach ($expected in @(
        "package: name='$packageName' versionCode='$VersionCode' versionName='$VersionName'",
        "native-code: 'x86_64'"
    )) {
        if (-not $badging.Contains($expected)) { Stop-Gate 'apk-identity-invalid' }
    }
    $certificate = Invoke-Quiet $apkSigner @('verify', '--verbose', '--print-certs', $Path) 'apk-signature-invalid'
    $trusted = Get-TrustedCertificateFingerprint
    $match = [regex]::Match($certificate, 'certificate SHA-256 digest:\s*([0-9a-fA-F:]+)')
    if (-not $match.Success -or (($match.Groups[1].Value -replace ':', '').ToLowerInvariant()) -ne $trusted) {
        Stop-Gate 'apk-certificate-mismatch'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
    try {
        $packs = @($zip.Entries | Where-Object { $_.FullName.EndsWith('.bagu-pack', [StringComparison]::OrdinalIgnoreCase) })
        if ($RequiresPack) {
            if ($packs.Count -ne 1 -or $packs[0].FullName -ne 'assets/question-pack/bundled.bagu-pack') {
                Stop-Gate 'apk-bundled-pack-invalid'
            }
        } elseif ($packs.Count -ne 0) { Stop-Gate 'beta5-unexpected-pack' }
    } finally { $zip.Dispose() }
}

function Copy-ValidatedBundledPack([string]$SourceApk, [string]$Destination) {
    $descriptor = Get-Content -LiteralPath $descriptorPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($descriptor.android_delivery -ne 'bundled_confirm' -or $descriptor.pack_id -ne 'autumn-recruit-interviews-2026' -or
        $descriptor.revision -ne 1 -or $descriptor.question_count -ne 748 -or $descriptor.experience_count -ne 27) {
        Stop-Gate 'descriptor-invalid'
    }
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $SourceApk).Path)
    try {
        $entry = $zip.GetEntry('assets/question-pack/bundled.bagu-pack')
        if ($null -eq $entry) { Stop-Gate 'apk-bundled-pack-missing' }
        $input = $entry.Open()
        try {
            $output = [IO.File]::Create($Destination)
            try { $input.CopyTo($output) } finally { $output.Dispose() }
        } finally { $input.Dispose() }
    } finally { $zip.Dispose() }
    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $descriptor.sha256) { Stop-Gate 'apk-bundled-pack-hash-invalid' }
    $validation = "import pathlib,sqlite3,sys; sys.path.insert(0,str(pathlib.Path(sys.argv[2]).resolve())); import bagu; data=pathlib.Path(sys.argv[1]).read_bytes(); c=sqlite3.connect(':memory:'); bagu.init_db(c); p=bagu.inspect_interview_pack(c,data); assert p['pack_id']=='autumn-recruit-interviews-2026' and p['revision']==1 and p['question_count']==748 and p['experience_count']==27; c.close()"
    & $BuildPython -c $validation $Destination $repoRoot 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { Stop-Gate 'apk-bundled-pack-runtime-invalid' }
}

function Build-InstrumentationApk([string]$PackSnapshot) {
    $arguments = @(
        '--no-daemon', '--console=plain', ':app:assemblePublicReleaseAndroidTest',
        '-PbaguTestBuildType=release', '-PbaguAbi=x86_64',
        '-PbaguAndroidDelivery=bundled_confirm', "-PbaguBundledQuestionPack=$PackSnapshot",
        "-PbaguBuildPython=$BuildPython"
    )
    Push-Location $androidRoot
    try { [void](Invoke-Quiet $gradle $arguments 'android-test-build-failed') }
    finally { Pop-Location }
    $testApks = @(Get-ChildItem -LiteralPath (Join-Path $androidRoot 'app\build\outputs\apk\androidTest') -Recurse -File -Filter '*.apk' |
        Where-Object { $_.Name -match 'public.*release.*androidTest|androidTest.*public.*release' })
    if ($testApks.Count -ne 1) { Stop-Gate 'android-test-apk-ambiguous' }
    return $testApks[0].FullName
}

function Add-ScenarioResult([string]$Name, [string]$Status) {
    $scenarioResults.Add([ordered]@{ api = $api; name = $Name; status = $Status })
}

function Invoke-InstrumentationScenario([string]$Method, [string]$Name) {
    $target = "$packageName.BundledPackAcceptanceTest#$Method"
    $output = Invoke-ScopedAdbMutation @('shell', 'am', 'instrument', '-w', '-r', '-e', 'class', $target,
        "$packageName.test/androidx.test.runner.AndroidJUnitRunner") ('scenario-' + $Name + '-failed')
    if ($output -notmatch '(?m)^OK \(' -or $output -match '(?m)FAILURES|INSTRUMENTATION_FAILED') {
        Stop-Gate ('scenario-' + $Name + '-failed')
    }
    Add-ScenarioResult $Name 'passed'
}

function Install-TestPair([string]$TargetApk, [string]$TestApk) {
    [void](Invoke-ScopedAdbMutation @('install', '-r', '-t', $TargetApk) 'target-install-failed')
    [void](Invoke-ScopedAdbMutation @('install', '-r', '-t', $TestApk) 'test-install-failed')
}

function Clear-TargetData {
    $output = Invoke-ScopedAdbMutation @('shell', 'pm', 'clear', $packageName) 'target-clear-failed'
    if ($output -notmatch '(?i)success') { Stop-Gate 'target-clear-not-confirmed' }
}

function Run-ApiGate([int]$TargetApi, [string]$TargetApk, [string]$TestApk) {
    $script:api = $TargetApi
    $script:avdName = "bagu-beta6-$TargetApi-$runId"
    $script:port = Get-FreeEmulatorPort
    $script:serial = "emulator-$port"
    $script:emulatorProcess = $null
    $apiAvdHome = Join-Path $runRoot ("avd-$TargetApi")
    $apiUserHome = Join-Path $runRoot ("user-$TargetApi")
    New-Item -ItemType Directory -Path $apiAvdHome, $apiUserHome -Force | Out-Null
    $env:ANDROID_AVD_HOME = $apiAvdHome
    $env:ANDROID_USER_HOME = $apiUserHome
    $imageFolder = Join-Path $sdkRoot ("system-images\android-$TargetApi\google_apis\x86_64")
    if (-not (Test-Path -LiteralPath $imageFolder -PathType Container)) { Stop-Gate "system-image-$TargetApi-missing" }
    [void](Invoke-Quiet $avdManager @('create', 'avd', '--name', $avdName, '--package', $imageIds[$TargetApi], '--device', 'pixel_2', '--force') "avd-$TargetApi-create-failed")
    try {
        $script:emulatorProcess = Start-Process -FilePath $emulator -ArgumentList @(
            '-avd', $avdName, '-port', $port, '-no-window', '-no-audio', '-no-snapshot',
            '-no-boot-anim', '-wipe-data'
        ) -PassThru -WindowStyle Hidden
        $deadline = [DateTime]::UtcNow.AddMinutes(4)
        $ready = $false
        while ([DateTime]::UtcNow -lt $deadline -and -not $emulatorProcess.HasExited) {
            Start-Sleep -Milliseconds 1000
            try {
                $ready = (Get-ScopedAdb @('get-state')) -eq 'device' -and
                    (Get-DeviceProperty 'sys.boot_completed') -eq '1'
            } catch { $ready = $false }
            if ($ready) { break }
        }
        if (-not $ready) { Stop-Gate "emulator-$TargetApi-timeout" }
        Assert-DisposableEmulator
        Install-TestPair $TargetApk $TestApk

        Clear-TargetData
        Invoke-InstrumentationScenario 'cleanCancelLeavesEmptyAndSuppressesSameHashRestart' 'clean-cancel-suppress'
        Clear-TargetData
        Invoke-InstrumentationScenario 'settingsInstallDisablesDailyReviewWithoutHidingSimulation' 'settings-install-toggle-simulation'
        Clear-TargetData
        Invoke-InstrumentationScenario 'recreationOpenSessionAndOperationsNeverImplicitlyInstall' 'lifecycle-session-operation-gates'
        Clear-TargetData
        Invoke-InstrumentationScenario 'processDeathStagePersistsOnlyPromptMarker' 'process-death-stage'
        [void](Invoke-ScopedAdbMutation @('shell', 'am', 'force-stop', $packageName) 'process-death-force-stop-failed')
        Invoke-InstrumentationScenario 'processDeathRestartDoesNotRestoreBytesOrReprompt' 'process-death-restart'

        if ([string]::IsNullOrWhiteSpace($Beta5ApkPath)) {
            Add-ScenarioResult 'beta5-installed-pack-upgrade' 'skipped_beta5_apk_not_supplied'
            Add-ScenarioResult 'beta5-uninstalled-pack-upgrade' 'skipped_beta5_apk_not_supplied'
        } else {
            Clear-TargetData
            Invoke-InstrumentationScenario 'prepareInstalledBeta5UpgradeState' 'beta5-installed-pack-prepare'
            [void](Invoke-ScopedAdbMutation @('install', '-r', '-d', '-t', $Beta5ApkPath) 'beta5-installed-state-downgrade-failed')
            [void](Invoke-ScopedAdbMutation @('install', '-r', '-t', $TargetApk) 'beta6-installed-state-upgrade-failed')
            Invoke-InstrumentationScenario 'verifyInstalledBeta5UpgradeDoesNotDuplicateOrPrompt' 'beta5-installed-pack-upgrade'

            Clear-TargetData
            Invoke-InstrumentationScenario 'prepareUninstalledBeta5UpgradeState' 'beta5-uninstalled-pack-prepare'
            [void](Invoke-ScopedAdbMutation @('install', '-r', '-d', '-t', $Beta5ApkPath) 'beta5-uninstalled-state-downgrade-failed')
            [void](Invoke-ScopedAdbMutation @('install', '-r', '-t', $TargetApk) 'beta6-uninstalled-state-upgrade-failed')
            Invoke-InstrumentationScenario 'verifyUninstalledBeta5UpgradePromptsOnceAndPreservesLocalProgress' 'beta5-uninstalled-pack-upgrade'
        }
        $apiResults.Add([ordered]@{ api = $TargetApi; serial = $serial; status = 'passed' })
    } finally {
        if ($null -ne $emulatorProcess -and -not $emulatorProcess.HasExited) {
            $needsOwnedKill = $false
            try { [void](Invoke-ScopedAdbMutation @('emu', 'kill') 'emulator-stop-failed') }
            catch { $needsOwnedKill = $true }
            if (-not $needsOwnedKill -and -not $emulatorProcess.WaitForExit(15000)) {
                $needsOwnedKill = $true
            }
            if ($needsOwnedKill) {
                # This exact Start-Process object is the only fallback target.
                try { $emulatorProcess.Kill() }
                catch { Stop-Gate 'emulator-process-kill-failed' }
                if (-not $emulatorProcess.WaitForExit(15000)) {
                    Stop-Gate 'emulator-process-stop-timeout'
                }
            }
            if (-not $emulatorProcess.HasExited) { Stop-Gate 'emulator-process-stop-timeout' }
        }
        $script:serial = $null
        $script:emulatorProcess = $null
    }
}

function Write-SafeStatus([string]$OverallStatus, [string]$FailureCode) {
    $fullOutput = [IO.Path]::GetFullPath($OutputPath)
    $parent = Split-Path -Parent $fullOutput
    if (-not [string]::IsNullOrWhiteSpace($parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $payload = [ordered]@{
        schema_version = 1
        overall_status = $OverallStatus
        failure_code = $FailureCode
        api_results = $apiResults.ToArray()
        scenario_results = $scenarioResults.ToArray()
    }
    [IO.File]::WriteAllText($fullOutput, ($payload | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
}

try {
    foreach ($tool in @($adb, $emulator, $avdManager, $aapt, $apkSigner, $gradle, $releaseMetadata, $descriptorPath, $versionPath)) {
        Require-File $tool 'toolchain-missing'
    }
    if ([string]::IsNullOrWhiteSpace($BuildPython)) {
        $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $pythonCommand) { Stop-Gate 'python-missing' }
        $BuildPython = $pythonCommand.Source
    }
    Require-File $BuildPython 'python-missing'
    if ([string]::IsNullOrWhiteSpace($JavaHome)) { $JavaHome = $env:JAVA_HOME }
    if ([string]::IsNullOrWhiteSpace($JavaHome)) { Stop-Gate 'jdk17-missing' }
    $java = Join-Path $JavaHome 'bin\java.exe'
    Require-File $java 'jdk17-missing'
    $javaVersionLines = & $java --version
    if ($LASTEXITCODE -ne 0) { Stop-Gate 'jdk17-version-invalid' }
    $javaVersion = (@($javaVersionLines) -join "`n")
    if ($javaVersion -notmatch '(?m)^(?:java|openjdk) 17(?:\.|\s|$)') {
        Stop-Gate 'jdk17-version-invalid'
    }
    $env:JAVA_HOME = $JavaHome
    $version = Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($version.versionName -ne '0.1.0-beta.6' -or $version.versionCode -ne 6 -or $version.channel -ne 'beta') {
        Stop-Gate 'source-version-invalid'
    }
    Assert-ApkContract $ApkPath '0.1.0-beta.6' 6 $true
    if (-not [string]::IsNullOrWhiteSpace($Beta5ApkPath)) {
        Assert-ApkContract $Beta5ApkPath '0.1.0-beta.5' 5 $false
    }
    New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $env:GRADLE_USER_HOME = Join-Path $repoRoot '.gradle-user-home'
    $env:ANDROID_USER_HOME = Join-Path $runRoot 'build-user'
    $env:ANDROID_AVD_HOME = Join-Path $runRoot 'build-avd'
    New-Item -ItemType Directory -Path $env:ANDROID_USER_HOME, $env:ANDROID_AVD_HOME -Force | Out-Null
    $packSnapshot = Join-Path $runRoot 'bundled.bagu-pack'
    Copy-ValidatedBundledPack $ApkPath $packSnapshot
    $testApk = Build-InstrumentationApk $packSnapshot
    foreach ($targetApi in $apis) { Run-ApiGate $targetApi $ApkPath $testApk }
    Write-SafeStatus 'passed' $null
} catch {
    $failureCode = if ($_.Exception.Message -match '^[a-z0-9-]+$') { $_.Exception.Message } else { 'gate-failed' }
    Write-SafeStatus 'failed' $failureCode
    throw [InvalidOperationException]::new($failureCode)
} finally {
    if (($null -eq $emulatorProcess -or $emulatorProcess.HasExited) -and
            (Test-Path -LiteralPath $runRoot -PathType Container)) {
        $resolvedRunRoot = (Resolve-Path -LiteralPath $runRoot).Path
        $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedRunRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedRunRoot) -eq ('bagu-beta6-avd-' + $runId)) {
            Remove-Item -LiteralPath $runRoot -Recurse -Force
        }
    }
    if ($null -eq $originalAvdHome) { Remove-Item Env:ANDROID_AVD_HOME -ErrorAction SilentlyContinue }
    else { $env:ANDROID_AVD_HOME = $originalAvdHome }
    if ($null -eq $originalAndroidUserHome) { Remove-Item Env:ANDROID_USER_HOME -ErrorAction SilentlyContinue }
    else { $env:ANDROID_USER_HOME = $originalAndroidUserHome }
    if ($null -eq $originalAndroidHome) { Remove-Item Env:ANDROID_HOME -ErrorAction SilentlyContinue }
    else { $env:ANDROID_HOME = $originalAndroidHome }
    if ($null -eq $originalAndroidSdkRoot) { Remove-Item Env:ANDROID_SDK_ROOT -ErrorAction SilentlyContinue }
    else { $env:ANDROID_SDK_ROOT = $originalAndroidSdkRoot }
    if ($null -eq $originalGradleUserHome) { Remove-Item Env:GRADLE_USER_HOME -ErrorAction SilentlyContinue }
    else { $env:GRADLE_USER_HOME = $originalGradleUserHome }
    if ($null -eq $originalJavaHome) { Remove-Item Env:JAVA_HOME -ErrorAction SilentlyContinue }
    else { $env:JAVA_HOME = $originalJavaHome }
}
