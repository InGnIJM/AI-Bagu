"""Run pure native policy tests without opening the release keystore."""
import os
import json
from pathlib import Path
import subprocess


def test_native_update_policy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    java = Path("C:/Program Files/Java/jdk-17.0.10/bin")
    cache = root / ".gradle-user-home/caches/modules-2/files-2.1"
    jars = [next((cache / path).rglob("*.jar")) for path in ("junit/junit/4.13.2", "org.hamcrest/hamcrest-core/1.3")]
    classpath = os.pathsep.join(str(p) for p in [tmp_path, *jars])
    sources = list((root / "android/app/src/main/java/io/github/ingnijm/baguhelper").glob("Update*.java"))
    sources = [p for p in sources if p.stem not in ("UpdateController", "UpdateApkProvider")]
    sources += list((root / "android/app/src/test/java/io/github/ingnijm/baguhelper").glob("Update*Test.java"))
    sources += [root / "android/app/src/main/java/io/github/ingnijm/baguhelper/SpeechInput.java",
                root / "android/app/src/test/java/io/github/ingnijm/baguhelper/SpeechInputTest.java"]
    for name in ("DiagnosticPolicy", "DiagnosticStore"):
        sources += [root / f"android/app/src/main/java/io/github/ingnijm/baguhelper/{name}.java",
                    root / f"android/app/src/test/java/io/github/ingnijm/baguhelper/{name}Test.java"]
    compiled = subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
                               "-d", str(tmp_path), *map(str, sources)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(java / "java.exe"), "-cp", classpath, "org.junit.runner.JUnitCore",
                             "io.github.ingnijm.baguhelper.UpdatePolicyTest", "io.github.ingnijm.baguhelper.UpdateEngineTest",
                             "io.github.ingnijm.baguhelper.UpdateIOTest",
                             "io.github.ingnijm.baguhelper.UpdateCheckTest",
                             "io.github.ingnijm.baguhelper.UpdateInstallGateTest",
                             "io.github.ingnijm.baguhelper.SpeechInputTest",
                             "io.github.ingnijm.baguhelper.DiagnosticPolicyTest",
                             "io.github.ingnijm.baguhelper.DiagnosticStoreTest"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout, end="")
    assert "OK (" in result.stdout


def test_native_updater_compiles_against_actual_android_api_without_signing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    java = Path("C:/Program Files/Java/jdk-17.0.10/bin")
    cache = root / ".gradle-user-home/caches/modules-2/files-2.1"
    version = json.loads((root / "version.json").read_text(encoding="utf-8"))
    config = tmp_path / "BuildConfig.java"
    config.write_text(
        'package io.github.ingnijm.baguhelper; public final class BuildConfig {'
        'public static final String APPLICATION_ID="io.github.ingnijm.baguhelper", FLAVOR="public", '
        f'VERSION_NAME={json.dumps(version["versionName"])}, UPDATE_CHANNEL={json.dumps(version["channel"])};'
        f'public static final int VERSION_CODE={version["versionCode"]};' + '}', encoding="utf-8")
    # Compile API usage before any APK build. Resource packaging is checked separately
    # by Gradle; never depend on a stale generated R.jar from this working tree.
    resources = tmp_path / "R.java"
    resources.write_text(
        'package io.github.ingnijm.baguhelper; public final class R {'
        'public static final class string { public static final int app_name=1, starting=2, retry=3, startup_error=4, startup_error_with_diagnostic=6; }'
        'public static final class drawable { public static final int brand_icon=5; }}', encoding="utf-8")
    jars = [root / ".android-sdk/platforms/android-36/android.jar",
            next((cache / "com.chaquo.python.runtime/chaquopy_java/17.0.0").rglob("*.jar"))]
    sources = list((root / "android/app/src/main/java/io/github/ingnijm/baguhelper").glob("*.java"))
    result = subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", os.pathsep.join(map(str, jars)),
                             "-d", str(tmp_path), str(config), str(resources), *map(str, sources)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
