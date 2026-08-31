"""Android host contracts and real local Python-runtime behavior (no real user data)."""
import concurrent.futures
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import pytest

import bagu

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"
NS = "{http://schemas.android.com/apk/res/android}"


def test_shared_html_declares_cover_viewport():
    class Metadata(HTMLParser):
        def __init__(self):
            super().__init__()
            self.viewports = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "meta" and attrs.get("name") == "viewport":
                self.viewports.append(attrs.get("content", ""))
    page = Metadata()
    page.feed((ROOT / "web/index.html").read_text(encoding="utf-8"))
    assert len(page.viewports) == 1
    assert {part.strip() for part in page.viewports[0].split(",")} >= {
        "width=device-width", "initial-scale=1", "viewport-fit=cover"}


def test_shared_html_hides_the_bundled_pack_action_until_android_capability_probe():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    match = re.search(r'<button\b[^>]*\bid="btn-bundled-pack-import"[^>]*>(.*?)</button>', html, re.S)
    assert match is not None
    assert "hidden" in match.group(0)
    assert "安装内置题包" in match.group(1)


def load_release_verifier():
    path = ROOT / "scripts/verify_android_apk.py"
    assert path.is_file(), "release APK verifier not implemented"
    spec = importlib.util.spec_from_file_location("verify_android_apk_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_release_metadata():
    path = ROOT / "scripts/release_metadata.py"
    spec = importlib.util.spec_from_file_location("release_metadata_for_apk_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_packaged_seed(path, *, public=False, private_entry=None, pack_state=False):
    bagu.prepare_mobile_database(path)
    if not public or pack_state:
        conn = bagu.get_conn(path)
        if not public:
            conn.execute(
                "INSERT INTO questions(category,question,answer,url) VALUES(?,?,?,?)",
                ("Android", "发布校验题", "只验证打包种子", "https://example.test/release"),
            )
        if pack_state:
            conn.execute(
                """INSERT INTO question_packs(
                       pack_id,name,revision,display_version,source_snapshot_sha256,
                       question_count,experience_count,questions_sha256,experiences_sha256,
                       manifest_sha256,include_in_review,installed_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("private-pack", "私有面经包", 1, "1.0", "1" * 64, 0, 0,
                 "2" * 64, "3" * 64, "4" * 64, 1,
                 "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z"),
            )
        conn.commit()
        conn.close()
    app_archive = io.BytesIO()
    with zipfile.ZipFile(app_archive, "w") as archive:
        archive.writestr("bagu.pyc", b"compiled-bagu")
        archive.writestr("android_runtime.pyc", b"compiled-runtime")
    apk = path.with_suffix(".apk")
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/static/web/index.html", b"<html></html>")
        archive.writestr("assets/static/assets/branding/bagu-helper-icon-concept.png", b"png")
        for name in (
            "PlusJakartaSans.ttf", "FiraCode.ttf", "MaterialSymbolsRounded.ttf",
            "PlusJakartaSans-OFL.txt", "FiraCode-OFL.txt", "MaterialSymbolsRounded-APACHE-2.0.txt",
        ):
            archive.writestr(f"assets/static/assets/fonts/{name}", b"asset")
        archive.writestr("assets/seed/bagu-seed.db", path.read_bytes())
        archive.writestr("assets/chaquopy/app.imy", app_archive.getvalue())
        if private_entry:
            archive.writestr(private_entry, b"must-not-ship")
    add_pinned_native_payloads(apk)
    return apk


def make_interview_pack_fixture():
    questions = [{
        "stable_id": "android-review-1",
        "question": "Explain a transaction.",
        "category": "database",
        "kind": "review",
        "answer": "PRIVATE_PACK_ANSWER_SENTINEL",
        "review_status": "reviewed",
        "retired": False,
        "sources": [{"path": "private/interview.md", "url": "https://example.test/interview"}],
    }]
    experiences = [{
        "stable_id": "android-experience-1",
        "kind": "interview",
        "direction": "backend",
        "company": "Acme",
        "position": "engineer",
        "stage": "technical",
        "sections": [{
            "stable_id": "android-round-1", "order": 1, "title": "Round one",
            "recommended": True, "question_ids": ["android-review-1"],
        }],
    }]
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    question_bytes = canonical(questions)
    experience_bytes = canonical(experiences)
    manifest = {
        "format": "bagu-pack", "schema_version": 1,
        "pack_id": "android-private-pack", "name": "Android private pack",
        "revision": 1, "display_version": "1.0",
        "source_snapshot_sha256": "1" * 64,
        "question_count": 1, "experience_count": 1,
        "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        "experiences_sha256": hashlib.sha256(experience_bytes).hexdigest(),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical(manifest))
        archive.writestr("questions.json", question_bytes)
        archive.writestr("experiences.json", experience_bytes)
    return output.getvalue()


def make_bundled_descriptor(pack, *, schema_version=2):
    metadata = load_release_metadata()
    version = {
        "versionName": "0.1.0-beta.6", "versionCode": 6, "channel": "beta",
    }
    value = {
        "schema_version": schema_version,
        "versionName": version["versionName"],
        "file_name": "synthetic-interviews-r1.bagu-pack",
        "sha256": hashlib.sha256(pack).hexdigest(),
        "pack_id": "android-private-pack",
        "revision": 1,
        "display_version": "1.0",
        "question_count": 1,
        "experience_count": 1,
    }
    if schema_version == 2:
        value["android_delivery"] = "bundled_confirm"
    return metadata.parse_question_pack_descriptor(metadata.json_bytes(value), version)


def add_apk_member(apk, name, payload):
    with zipfile.ZipFile(apk, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)


def add_pinned_native_payloads(apk):
    """Populate synthetic APKs with the reviewed native manifest, not a wildcard."""
    verifier = load_release_verifier()
    with zipfile.ZipFile(apk, "a") as archive:
        for name in verifier.EXPECTED_NATIVE_LIBRARIES:
            if "!" not in name:
                archive.writestr(name, b"not-an-elf")
        grouped = {}
        for name in verifier.EXPECTED_NATIVE_LIBRARIES:
            if "!" in name:
                archive_name, member = name.split("!", 1)
                grouped.setdefault(archive_name, []).append(member)
        for archive_name, members in grouped.items():
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as nested_archive:
                for member in members:
                    nested_archive.writestr(member, b"not-an-elf")
            archive.writestr(archive_name, payload.getvalue())


def make_isolated_release_script_root(tmp_path):
    root = tmp_path / "isolated-release"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/android.ps1", scripts / "android.ps1")
    shutil.copy2(ROOT / "scripts/verify_android_apk.py", scripts / "verify_android_apk.py")
    shutil.copy2(ROOT / "version.json", root / "version.json")
    return root


def run_isolated_setup(root):
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "SetupSigning"],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )


def test_setup_signing_creates_and_preserves_an_isolated_identity(tmp_path):
    """Catches SetupSigning refusing first run or replacing a valid key on rerun."""
    root = make_isolated_release_script_root(tmp_path)

    first = run_isolated_setup(root)

    signing = root / ".signing"
    key = signing / "release.jks"
    properties = signing / "keystore.properties"
    fingerprint = signing / "certificate-sha256.txt"
    assert first.returncode == 0, first.stderr
    assert key.stat().st_size > 0 and properties.stat().st_size > 0
    assert re.fullmatch(r"[0-9a-f]{64}\n", fingerprint.read_text(encoding="ascii"))
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (key, properties, fingerprint)}

    second = run_isolated_setup(root)

    assert second.returncode == 0, second.stderr
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (key, properties, fingerprint)}
    assert "storePassword" not in first.stdout + first.stderr + second.stdout + second.stderr


def test_setup_signing_rejects_an_isolated_partial_identity(tmp_path):
    """Catches a setup run that overwrites or accepts a keystore without its properties."""
    root = make_isolated_release_script_root(tmp_path)
    signing = root / ".signing"
    signing.mkdir()
    key = signing / "release.jks"
    key.write_bytes(b"partial")

    result = run_isolated_setup(root)

    assert result.returncode != 0
    assert "partial" in (result.stdout + result.stderr).lower()
    assert key.read_bytes() == b"partial"


def test_verify_rejects_missing_companion_metadata_before_sdk_tools(tmp_path):
    """Catches Verify accepting a delivery set that omits the promised certificate/install metadata."""
    root = make_isolated_release_script_root(tmp_path)
    delivery = root / "dist/android/0.1.0-beta.5/public"
    delivery.mkdir(parents=True)
    apk = delivery / "bagu-0.1.0-beta.5-public-arm64-v8a.apk"
    apk.write_bytes(b"test-apk")
    (delivery / "SHA256SUMS").write_text(
        f"{hashlib.sha256(apk.read_bytes()).hexdigest()} *{apk.name}\n", encoding="utf-8"
    )
    for path in (
        root / ".toolchains/gradle-9.1.0/bin/gradle.bat",
        root / ".android-sdk/build-tools/36.0.0/aapt.exe",
        root / ".android-sdk/build-tools/36.0.0/apksigner.bat",
        root / ".android-sdk/build-tools/36.0.0/zipalign.exe",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "Verify",
         "-BuildPython", sys.executable],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode != 0
    assert "certificate-sha256" in result.stdout + result.stderr


def test_verify_independently_hashes_every_checksum_row_before_apk_tools(tmp_path):
    """Catches Verify checking only the APK row after Python directory validation."""
    root = make_isolated_release_script_root(tmp_path)
    (root / "version.json").write_text(json.dumps({
        "versionName": "0.1.0-beta.5", "versionCode": 5, "channel": "beta",
    }), encoding="utf-8")
    delivery = root / "dist/android/0.1.0-beta.5/public"
    delivery.mkdir(parents=True)
    apk = delivery / "bagu-0.1.0-beta.5-public-arm64-v8a.apk"
    pack = delivery / "ai-bagu-synthetic-interviews-r1.bagu-pack"
    apk.write_bytes(b"test-apk")
    pack.write_bytes(b"test-pack")
    (delivery / "SHA256SUMS").write_bytes((
        f"{'0' * 64} *{pack.name}\n"
        f"{hashlib.sha256(apk.read_bytes()).hexdigest()} *{apk.name}\n"
    ).encode("ascii"))
    (delivery / "certificate-sha256.txt").write_text("a" * 64 + "\n", encoding="ascii")
    (delivery / "INSTALL.md").write_text(
        f'adb install "{apk.name}"\n', encoding="utf-8"
    )
    (delivery / "RELEASE_NOTES.md").write_text("synthetic\n", encoding="utf-8")
    (delivery / "update.json").write_text("{}\n", encoding="utf-8")
    # Isolate this assertion from Python validation: Verify must still hash every row itself.
    (root / "scripts/release_metadata.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    for path in (
        root / ".toolchains/gradle-9.1.0/bin/gradle.bat",
        root / ".android-sdk/build-tools/36.0.0/aapt.exe",
        root / ".android-sdk/build-tools/36.0.0/apksigner.bat",
        root / ".android-sdk/build-tools/36.0.0/zipalign.exe",
        root / "readelf.exe",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "Verify",
         "-BuildPython", sys.executable, "-ReadElf", str(root / "readelf.exe")],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SHA256SUMS" in combined and pack.name in combined
    assert "apksigner" not in combined.lower()


def test_question_pack_parameter_is_rejected_outside_public_build(tmp_path):
    root = make_isolated_release_script_root(tmp_path)
    pack = tmp_path / "synthetic.bagu-pack"
    pack.write_bytes(b"synthetic")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "Plan", "-QuestionPack", str(pack)],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode != 0
    assert "QuestionPack" in result.stdout + result.stderr


def test_public_build_rejects_bad_question_pack_before_environment_or_gradle(tmp_path):
    root = make_isolated_release_script_root(tmp_path)
    shutil.copy2(ROOT / "scripts/release_metadata.py", root / "scripts/release_metadata.py")
    shutil.copy2(ROOT / "bagu.py", root / "bagu.py")
    (root / "version.json").write_text(json.dumps({
        "versionName": "0.1.0-beta.5", "versionCode": 5, "channel": "beta",
    }), encoding="utf-8")
    original = make_interview_pack_fixture()
    pack_name = "synthetic-interviews-r1.bagu-pack"
    pack = tmp_path / pack_name
    pack.write_bytes(original + b"tampered")
    descriptor = {
        "schema_version": 1,
        "versionName": "0.1.0-beta.5",
        "file_name": pack_name,
        "sha256": hashlib.sha256(original).hexdigest(),
        "pack_id": "android-private-pack",
        "revision": 1,
        "display_version": "1.0",
        "question_count": 1,
        "experience_count": 1,
    }
    descriptor_path = root / "docs/releases/0.1.0-beta.5-question-pack.json"
    descriptor_path.parent.mkdir(parents=True)
    descriptor_path.write_bytes(
        (json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "Build",
         "-BuildPython", sys.executable, "-QuestionPack", str(pack)],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "question-pack hash" in combined
    assert "JDK17" not in combined and "Gradle" not in combined


def test_public_build_snapshots_valid_v2_pack_before_environment_or_gradle(tmp_path):
    root = make_isolated_release_script_root(tmp_path)
    shutil.copy2(ROOT / "scripts/release_metadata.py", root / "scripts/release_metadata.py")
    shutil.copy2(ROOT / "bagu.py", root / "bagu.py")
    version = {"versionName": "0.1.0-beta.6", "versionCode": 6, "channel": "beta"}
    (root / "version.json").write_text(json.dumps(version), encoding="utf-8")
    pack = make_interview_pack_fixture()
    source = tmp_path / "private-source" / "synthetic-interviews-r1.bagu-pack"
    source.parent.mkdir()
    source.write_bytes(pack)
    descriptor = {
        "schema_version": 2, "versionName": version["versionName"],
        "file_name": source.name, "sha256": hashlib.sha256(pack).hexdigest(),
        "pack_id": "android-private-pack", "revision": 1, "display_version": "1.0",
        "question_count": 1, "experience_count": 1, "android_delivery": "bundled_confirm",
    }
    descriptor_path = root / "docs/releases/0.1.0-beta.6-question-pack.json"
    descriptor_path.parent.mkdir(parents=True)
    metadata = load_release_metadata()
    descriptor_path.write_bytes(metadata.json_bytes(descriptor))

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(root / "scripts/android.ps1"), "-Mode", "Build", "-BuildPython", sys.executable,
         "-JavaHome", str(root / "missing-jdk"), "-QuestionPack", str(source)],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )

    snapshot = root / "android/app/build/generated/bagu/input/question-pack/bundled.bagu-pack"
    assert result.returncode != 0
    assert snapshot.read_bytes() == pack
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == descriptor["sha256"]


def test_gradle_generates_only_fixed_public_release_pack_asset(tmp_path):
    pack_path = tmp_path / "validated-copy.bagu-pack"
    pack_path.write_bytes(make_interview_pack_fixture())
    gradle = ROOT / ".toolchains/gradle-9.1.0/bin/gradle.bat"
    env = os.environ.copy()
    env.update({
        "JAVA_HOME": "C:/Program Files/Java/jdk-17.0.10",
        "ANDROID_HOME": str(ROOT / ".android-sdk"),
        "ANDROID_SDK_ROOT": str(ROOT / ".android-sdk"),
        "ANDROID_USER_HOME": str(ROOT / ".android-user-home"),
        "GRADLE_USER_HOME": str(ROOT / ".gradle-user-home"),
    })

    completed = subprocess.run(
        [str(gradle), "--offline", "--no-daemon", "--console=plain",
         ":app:syncPublicReleaseBundledPack", "-PbaguAndroidDelivery=bundled_confirm",
         f"-PbaguBundledQuestionPack={pack_path}", f"-PbaguBuildPython={sys.executable}"],
        cwd=ANDROID, env=env, capture_output=True, text=True, encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    generated = ANDROID / "app/build/generated/bagu/publicRelease/bundled-pack-assets"
    files = [path.relative_to(generated).as_posix() for path in generated.rglob("*") if path.is_file()]
    assert files == ["question-pack/bundled.bagu-pack"]
    assert (generated / files[0]).read_bytes() == pack_path.read_bytes()


def run_release_gradle_graph(*arguments):
    gradle = ROOT / ".toolchains/gradle-9.1.0/bin/gradle.bat"
    env = os.environ.copy()
    env.update({
        "JAVA_HOME": "C:/Program Files/Java/jdk-17.0.10",
        "ANDROID_HOME": str(ROOT / ".android-sdk"),
        "ANDROID_SDK_ROOT": str(ROOT / ".android-sdk"),
        "ANDROID_USER_HOME": str(ROOT / ".android-user-home"),
        "GRADLE_USER_HOME": str(ROOT / ".gradle-user-home"),
    })
    return subprocess.run(
        [str(gradle), "--offline", "--no-daemon", "--console=plain", *arguments,
         f"-PbaguBuildPython={sys.executable}"],
        cwd=ANDROID, env=env, capture_output=True, text=True, encoding="utf-8",
    )


def test_public_release_gradle_graph_requires_explicit_android_delivery():
    completed = run_release_gradle_graph(":app:assemblePublicRelease", "--dry-run")

    assert completed.returncode != 0
    assert "baguAndroidDelivery" in completed.stdout + completed.stderr


def test_aggregate_release_gradle_graph_rejects_bundled_pack_for_internal(tmp_path):
    pack = tmp_path / "validated-copy.bagu-pack"
    pack.write_bytes(make_interview_pack_fixture())

    completed = run_release_gradle_graph(
        ":app:assembleRelease", "--dry-run", "-PbaguAndroidDelivery=bundled_confirm",
        f"-PbaguBundledQuestionPack={pack}",
    )

    assert completed.returncode != 0
    assert "internal" in (completed.stdout + completed.stderr).lower()


def test_check_graph_does_not_require_android_delivery_or_pack():
    completed = run_release_gradle_graph(
        ":app:testPublicDebugUnitTest", ":app:lintPublicRelease", "--dry-run",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gitignore_keeps_android_private_material_and_generated_state_untracked(tmp_path):
    """Pins ignore behavior without opening real secrets or changing this repository."""
    isolated = tmp_path / "ignore-contract"
    isolated.mkdir()
    initialized = subprocess.run(
        ["git", "init", "-q"], cwd=isolated, capture_output=True, text=True, encoding="utf-8",
    )
    assert initialized.returncode == 0, initialized.stderr
    (isolated / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    paths = [
        ".env", "settings.json", "bagu.db", ".signing/release.jks",
        ".signing/keystore.properties", ".toolchains/gradle-9.1.0/bin/gradle.bat",
        ".android-sdk/build-tools/36.0.0/aapt.exe", ".gradle-user-home/caches/state.bin",
        "android/local.properties", "dist/android/generated.apk",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", *paths],
        cwd=isolated, capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(paths)


@pytest.mark.parametrize("entry", [
    "lib/arm64-v8a/libunexpected.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/unexpected.so",
])
def test_release_archive_verifier_rejects_unpinned_native_payload(tmp_path, entry):
    """Catches a prefix-only native allowlist admitting an injected library."""
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db")
    with zipfile.ZipFile(apk, "a") as archive:
        archive.writestr(entry, b"not-an-elf")

    with pytest.raises(ValueError, match="native manifest|native payload"):
        verifier.verify_apk_contents(apk, "internal")


def test_release_archive_verifier_rejects_unpinned_nested_native_payload(tmp_path):
    """Catches an injected ELF hidden inside a reviewed Chaquopy .imy archive."""
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db")
    archive_name = "assets/chaquopy/stdlib-arm64-v8a.imy"
    with zipfile.ZipFile(apk, "a") as apk_archive:
        payload = io.BytesIO(apk_archive.read(archive_name))
        with zipfile.ZipFile(payload, "a") as nested:
            nested.writestr("unexpected.cpython-311.so", b"not-an-elf")
        with pytest.warns(UserWarning, match=r"^Duplicate name: 'assets/chaquopy/stdlib-arm64-v8a\.imy'$"):
            apk_archive.writestr(archive_name, payload.getvalue())

    with pytest.raises(ValueError, match="native manifest"):
        verifier.verify_apk_contents(apk, "internal")


def test_release_archive_verifier_accepts_explicit_assets_and_clean_flavor_seeds(tmp_path):
    """Catches a verifier that skips the delivery asset or zero-history contract."""
    verifier = load_release_verifier()
    internal = make_packaged_seed(tmp_path / "internal-seed.db")
    public = make_packaged_seed(tmp_path / "public-seed.db", public=True)

    internal_report = verifier.verify_apk_contents(internal, "internal")
    public_report = verifier.verify_apk_contents(public, "public")

    assert internal_report["questions"] == 1
    assert internal_report["python_modules"] == ["android_runtime.pyc", "bagu.pyc"]
    assert internal_report["abis"] == ["arm64-v8a"]
    assert public_report["questions"] == 0
    assert public_report["sessions"] == 0 and public_report["session_items"] == 0


def test_release_archive_verifier_accepts_only_descriptor_bound_fixed_pack(tmp_path):
    verifier = load_release_verifier()
    pack = make_interview_pack_fixture()
    descriptor = make_bundled_descriptor(pack)
    apk = make_packaged_seed(tmp_path / "public-v2.db", public=True)
    add_apk_member(apk, "assets/question-pack/bundled.bagu-pack", pack)

    report = verifier.verify_apk_contents(apk, "public", descriptor=descriptor)

    assert report["android_delivery"] == "bundled_confirm"
    assert report["bundled_pack_member"] == "assets/question-pack/bundled.bagu-pack"
    assert report["bundled_pack_sha256"] == hashlib.sha256(pack).hexdigest()
    assert report["questions"] == report["packs"] == report["experiences"] == 0


@pytest.mark.parametrize("mutation", [
    "missing", "wrong-path", "nested", "extra", "wrong-hash", "malformed", "schema-v1", "internal",
])
def test_release_archive_verifier_rejects_invalid_bundled_pack_delivery(tmp_path, mutation):
    verifier = load_release_verifier()
    pack = make_interview_pack_fixture()
    descriptor = make_bundled_descriptor(pack, schema_version=1 if mutation == "schema-v1" else 2)
    flavor = "internal" if mutation == "internal" else "public"
    apk = make_packaged_seed(tmp_path / f"{mutation}.db", public=flavor == "public")
    fixed = "assets/question-pack/bundled.bagu-pack"
    if mutation == "wrong-path":
        add_apk_member(apk, "assets/question-pack/source-name.bagu-pack", pack)
    elif mutation == "nested":
        add_apk_member(apk, "META-INF/nested/bundled.bagu-pack", pack)
    elif mutation == "extra":
        add_apk_member(apk, fixed, pack)
        add_apk_member(apk, "assets/question-pack/extra.bagu-pack", pack)
    elif mutation == "wrong-hash":
        add_apk_member(apk, fixed, pack + b"tampered")
    elif mutation == "malformed":
        malformed = b"not-a-bagu-pack"
        descriptor = make_bundled_descriptor(malformed)
        add_apk_member(apk, fixed, malformed)
    elif mutation != "missing":
        add_apk_member(apk, fixed, pack)

    with pytest.raises(ValueError, match="pack|bundle|delivery|member|hash|ZIP"):
        verifier.verify_apk_contents(apk, flavor, descriptor=descriptor)


def test_release_archive_verifier_rejects_private_state(tmp_path):
    """Catches an allowlist regression that packages a private desktop setting."""
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db", private_entry="assets/settings.json")

    with pytest.raises(ValueError, match="private|私有|settings"):
        verifier.verify_apk_contents(apk, "internal")


@pytest.mark.parametrize("private_entry", [
    "interviews.bagu-pack",
    "lib/arm64-v8a/interviews.BAGU-PACK",
    "META-INF/nested/interviews.bagu-pack",
])
def test_release_archive_verifier_rejects_pack_members_in_every_apk_path(tmp_path, private_entry):
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db", private_entry=private_entry)

    with pytest.raises(ValueError, match="private interview pack"):
        verifier.verify_apk_contents(apk, "internal")


@pytest.mark.parametrize(("private_entry", "message"), [
    ("interviews.bagu-pack", "private interview pack"),
    ("private/package-catalog.json", "private catalog"),
    ("nested/settings.json", "private state"),
])
def test_release_archive_verifier_rejects_private_material_inside_non_application_imy(
        tmp_path, private_entry, message):
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db")
    nested_payload = io.BytesIO()
    with zipfile.ZipFile(nested_payload, "w") as nested:
        nested.writestr(private_entry, b"must-not-ship")
    with zipfile.ZipFile(apk, "a") as archive:
        archive.writestr("assets/chaquopy/requirements-common.imy", nested_payload.getvalue())

    with pytest.raises(ValueError, match=message):
        verifier.verify_apk_contents(apk, "internal")


@pytest.mark.parametrize("private_entry", [
    "assets/private/package-catalog.json",
    "docs/PRIVATE/catalog-index.JSON",
    "docs/private/release_catalog.v2.json",
    "docs/private-interview-catalog.json",
    "docs/private-catalog.json",
])
def test_release_archive_verifier_rejects_only_precise_private_catalog_members(tmp_path, private_entry):
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db", private_entry=private_entry)

    with pytest.raises(ValueError, match="private catalog"):
        verifier.verify_apk_contents(apk, "internal")


@pytest.mark.parametrize("legal_entry", [
    "docs/package-catalog.json",
    "docs/interviewing-catalog.json",
    "docs/privately-owned-catalog.json",
    "docs/private/mycatalog.json",
    "docs/private-notes/catalog.json",
])
def test_release_archive_verifier_does_not_treat_catalog_substrings_as_private(tmp_path, legal_entry):
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db", private_entry=legal_entry)

    assert verifier.verify_apk_contents(apk, "internal")["questions"] == 1


@pytest.mark.parametrize("flavor", ["public", "internal"])
def test_release_archive_verifier_rejects_nonempty_pack_seed_state(tmp_path, flavor):
    verifier = load_release_verifier()
    apk = make_packaged_seed(
        tmp_path / "seed.db", public=flavor == "public", pack_state=True
    )

    with pytest.raises(ValueError, match="pack|experience|题包|专题"):
        verifier.verify_apk_contents(apk, flavor)


def test_release_archive_verifier_allows_only_the_expected_chaquopy_runtime_assets(tmp_path):
    """Catches an allowlist that rejects the packaged Chaquopy runtime or permits another ABI."""
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db")
    with zipfile.ZipFile(apk, "a") as archive:
        archive.writestr("assets/chaquopy/build.json", b"{}")
        archive.writestr("assets/chaquopy/requirements-common.imy", b"PK\x05\x06" + b"\0" * 18)
        archive.writestr("assets/chaquopy/requirements-arm64-v8a.imy", b"PK\x05\x06" + b"\0" * 18)

    report = verifier.verify_apk_contents(apk, "internal")

    assert report["questions"] == 1


def test_release_archive_verifier_checks_outer_and_nested_native_libraries(tmp_path):
    """Catches a verifier that misses an ELF hidden in a Chaquopy archive."""
    verifier = load_release_verifier()
    apk = make_packaged_seed(tmp_path / "seed.db")
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("native/libnested.so", b"not-an-elf")
    with zipfile.ZipFile(apk, "a") as archive:
        archive.writestr("assets/chaquopy/bootstrap.imy", nested.getvalue())
    readelf = tmp_path / "readelf.cmd"
    readelf.write_text(
        "@echo off\r\necho  LOAD 0x000000 0x000000 0x000000 0x1000 0x1000 R E 0x4000\r\n"
        "echo  GNU_RELRO 0x000000 0x000000 0x000000 0x1000 0x1000 R 0x1\r\n",
        encoding="ascii",
    )

    reports = verifier.verify_native_elfs(apk, readelf)

    names = [report["name"] for report in reports]
    assert "lib/arm64-v8a/libchaquopy_java.so" in names
    assert "assets/chaquopy/bootstrap.imy!native/libnested.so" in names
    assert all(report["load_alignments"] == [0x4000] and report["relro"] for report in reports)


def test_release_build_configuration_fails_without_signing_in_an_isolated_copy(tmp_path):
    """Catches a release config that falls back to an unsigned/debug identity."""
    isolated_root = tmp_path / "isolated-project"
    shutil.copytree(ANDROID, isolated_root / "android", ignore=shutil.ignore_patterns("build", ".gradle"))
    shutil.copy2(ROOT / "version.json", isolated_root / "version.json")
    gradle = ROOT / ".toolchains/gradle-9.1.0/bin/gradle.bat"
    env = os.environ.copy()
    env.update({
        "JAVA_HOME": "C:/Program Files/Java/jdk-17.0.10",
        "ANDROID_HOME": str(ROOT / ".android-sdk"),
        "ANDROID_SDK_ROOT": str(ROOT / ".android-sdk"),
        "ANDROID_USER_HOME": str(ROOT / ".android-user-home"),
        "GRADLE_USER_HOME": str(ROOT / ".gradle-user-home"),
    })
    for name in ("BAGU_STORE_FILE", "BAGU_STORE_PASSWORD", "BAGU_KEY_ALIAS", "BAGU_KEY_PASSWORD"):
        env.pop(name, None)
    completed = subprocess.run(
        [str(gradle), "--offline", "--no-daemon", "--console=plain", ":app:tasks"],
        cwd=isolated_root / "android", env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert completed.returncode != 0
    assert "Missing local release signing configuration" in completed.stdout + completed.stderr


def web_section(start, end):
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert start in html, f"Missing web interface: {start}"
    return html[html.index(start):html.index(end, html.index(start))]


def run_web_js(source):
    completed = subprocess.run(["node", "-"], input=source, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def diagnostic_bootstrap_source():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert '<script id="diagnostics-bootstrap">' in html
    return html.split('<script id="diagnostics-bootstrap">', 1)[1].split('</script>', 1)[0]


def test_web_diagnostics_collects_sanitized_errors_with_bounded_retry():
    result = run_web_js("""
const listeners={};const calls=[];let online=false;
const window={addEventListener:(name,fn)=>listeners[name]=fn,crypto:require('crypto').webcrypto};
const location={search:'',origin:'http://127.0.0.1:8765'};
const fetch=async(url,init)=>{calls.push([url,JSON.parse(init.body)]);if(!online)throw Error('PRIVATE_SERVER');return {ok:true};};
""" + diagnostic_bootstrap_source() + """
(async()=>{
 for(let i=0;i<105;i++)listeners.error({error:new TypeError('sk-test-secret PRIVATE_ANSWER'),filename:'http://127.0.0.1/?token=PRIVATE_TOKEN',lineno:42});
 await window.baguDiagnostics.flush(); const failedCalls=calls.length;
 online=true; await window.baguDiagnostics.flush();
 process.stdout.write(JSON.stringify({failedCalls,calls}));
})();
""")
    assert result["failedCalls"] == 1
    batches = result["calls"][1:]
    assert len(batches) <= 6
    events = [e for _, body in batches for e in body["events"]]
    assert len(events) <= 101 and any(e["event"] == "web.dropped" for e in events)
    assert sum(e.get("dropped", 0) for e in events) == 5
    assert all(len(body["events"]) <= 20 for _, body in batches)
    raw = json.dumps(result)
    assert all(secret not in raw for secret in ("sk-test-secret", "PRIVATE_ANSWER", "PRIVATE_TOKEN", "PRIVATE_SERVER"))


def test_diagnostic_export_flush_includes_errors_queued_during_existing_upload():
    result = run_web_js("""
const calls=[];let release;
const window={addEventListener:()=>{},crypto:require('crypto').webcrypto};
const location={search:''};
const fetch=async(url,init)=>{calls.push(JSON.parse(init.body).events);if(calls.length===1)await new Promise(r=>release=r);return {ok:true};};
""" + diagnostic_bootstrap_source() + """
(async()=>{
 window.baguDiagnostics.record({event:'web.action'});
 const uploading=window.baguDiagnostics.flush();
 await Promise.resolve();
 window.baguDiagnostics.record({event:'web.error'});
 const exporting=window.baguDiagnostics.flush(true);
 release(); await exporting;
 process.stdout.write(JSON.stringify(calls.flat().map(e=>e.event)));
})();
""")
    assert result == ["web.action", "web.error"]


def test_web_diagnostics_native_route_never_uses_http_or_raw_error_text():
    result = run_web_js("""
const listeners={},events=[];
const window={addEventListener:(n,f)=>listeners[n]=f,crypto:require('crypto').webcrypto,BaguNative:{reportDiagnostic:s=>events.push(JSON.parse(s))}};
const location={search:'?platform=android&token=PRIVATE_TOKEN'};
const fetch=()=>{throw Error('must not use HTTP');};
""" + diagnostic_bootstrap_source() + """
listeners.unhandledrejection({reason:new Error('PRIVATE_VOICE sk-test-private')});
process.stdout.write(JSON.stringify(events));
""")
    assert result[0]["event"] == "web.unhandledrejection"
    assert "PRIVATE" not in json.dumps(result) and "sk-test" not in json.dumps(result)


def test_web_diagnostic_api_errors_keep_backend_request_id_out_of_payload():
    source = web_section("async function api", "function startJudgeProgress")
    result = run_web_js("""
const events=[];const window={baguDiagnostics:{record:e=>events.push(e),flush:async()=>{},id:()=> 'w_'+'a'.repeat(32)}};
const requestHeaders=()=>({});
const fetch=async()=>({ok:false,status:502,headers:{get:n=>n==='X-Bagu-Request-Id'?'r_1234abcd':'application/json'},json:async()=>({error:'模型调用失败'})});
""" + source + """
(async()=>{try{await api('POST','/api/answer',{text:'PRIVATE_ANSWER'});}catch(e){process.stdout.write(JSON.stringify({message:e.message,events}));}})();
""")
    assert "r_1234abcd" in result["message"]
    assert result["events"][-1]["request_id"] == "r_1234abcd"
    assert "PRIVATE_ANSWER" not in json.dumps(result)


WEB_STORAGE = """
function memoryStorage() {
  const values = new Map();
  return { getItem: k => values.has(k) ? values.get(k) : null,
    setItem: (k,v) => values.set(k,String(v)), removeItem: k => values.delete(k),
    key: i => Array.from(values.keys())[i] || null, keys: () => JSON.stringify(Array.from(values.keys())),
    get length() { return values.size; } };
}
const localStorage = memoryStorage();
"""


@pytest.mark.parametrize("platform,bridge,native", [("android", True, True), ("android", False, False), ("", True, False)])
def test_web_android_storage_requires_platform_and_bridge(platform, bridge, native):
    bootstrap = web_section("const startupParams", "const presets")
    drafts = web_section("const ACTIVE_SUBMISSION_KEY", "function setStudyMode")
    result = run_web_js(WEB_STORAGE + f"""
const store = memoryStorage();
const window = {{BaguNative: {"store" if bridge else "null"}}};
const location = {{search: '?platform={platform}&token=test-startup-token'}};
{bootstrap}
const session = {{session_id:'s_one'}};
const selectedMode = 'answer';
function currentQuestion() {{ return {{id:7}}; }}
function $(id) {{ return {{value:'恢复草稿'}}; }}
{drafts}
saveDraft(); rememberSessionMode('s_one','memorize');
appStorage.setItem('bagu-number', 3);
appStorage.setItem(draftKey('s_other',8), 'other');
const before = [loadDraft(), currentSessionMode(), appStorage.getItem('bagu-number')];
clearSessionDrafts('s_one');
process.stdout.write(JSON.stringify({{before, remaining:appStorage.getItem(draftKey('s_other',8)),
 native:store.length > 0, browser:localStorage.length > 0,
 tokenSaved: Array.from({{length:appStorage.length}},(_,i)=>appStorage.getItem(appStorage.key(i))).includes('test-startup-token')}}));
""")
    assert result == {"before": ["恢复草稿", "memorize", "3"], "remaining": "other", "native": native, "browser": not native, "tokenSaved": False}


def test_web_android_api_and_stream_send_startup_header():
    bootstrap = web_section("const startupParams", "const presets")
    requests = web_section("async function api", "function startJudgeProgress")
    result = run_web_js(WEB_STORAGE + f"""
const location = {{search:'?platform=android&token=test-startup-token'}};
const window = {{BaguNative:memoryStorage()}};
{bootstrap}
const calls = [];
async function fetch(path, init) {{
 calls.push({{path,headers:init.headers,body:init.body}});
 const chunks = [new TextEncoder().encode('data: {{"type":"done","result":{{"grade":"good"}}}}\\n\\n')];
 return {{ok:true,headers:{{get:()=> 'application/json'}},json:async()=>({{ok:true}}),
 body:{{getReader:()=>({{read:async()=> chunks.length ? {{value:chunks.shift(),done:false}}:{{done:true}}}})}}}};
}}
{requests}
(async()=>{{await api('GET','/api/stats'); await api('POST','/api/draw',{{n:5}});
 const verdict = await streamAnswer({{text:'回答'}},null);
 process.stdout.write(JSON.stringify({{calls,verdict}}));}})().catch(e=>{{console.error(e);process.exit(1)}});
""")
    assert [c["headers"].get("X-Bagu-Token") for c in result["calls"]] == ["test-startup-token"] * 3
    assert "Content-Type" not in result["calls"][0]["headers"]
    assert result["calls"][1]["headers"]["Content-Type"] == "application/json"
    assert "token" not in result["calls"][2]["body"]
    assert result["verdict"] == {"grade": "good"}


def test_web_android_native_state_survives_a_new_loopback_origin():
    bootstrap = web_section("const startupParams", "const presets")
    drafts = web_section("const ACTIVE_SUBMISSION_KEY", "function setStudyMode")
    source = bootstrap + "\nconst session={session_id:'s_one'}; const selectedMode='answer';\n" + drafts
    result = run_web_js(WEB_STORAGE + f"""
const vm=require('vm'); const store=memoryStorage();
const source={json.dumps(source)};
function restart(port,action) {{return vm.runInNewContext(source+action,{{
 URLSearchParams,Uint8Array,location:{{search:'?platform=android&token=test-'+port,origin:'http://127.0.0.1:'+port}},
 window:{{BaguNative:store}},localStorage:memoryStorage(),
 currentQuestion:()=>({{id:7}}),$:()=>({{value:'进程恢复草稿'}}),
 crypto:{{randomUUID:()=> '12345678-1234-4234-8234-123456789abc'}}
}});}}
restart(1111,"saveDraft();rememberSessionMode('s_one','memorize');ensureActiveSubmission('s_one',7,'answer');");
const recovered=restart(2222,"[loadDraft(),currentSessionMode(),readActiveSubmission().submission_id,requestHeaders(false)['X-Bagu-Token']]");
process.stdout.write(JSON.stringify(recovered));
""")
    assert result == ["进程恢复草稿", "memorize", "sub_12345678-1234-4234-8234-123456789abc", "test-2222"]


def test_web_android_uuid_fallback_uses_16_secure_bytes_and_v4_bits():
    uuid = web_section("function newSubmissionId", "function ensureActiveSubmission")
    result = run_web_js(f"""
const lengths=[];
const crypto={{getRandomValues:bytes=>{{lengths.push(bytes.length); bytes.fill(255); return bytes;}}}};
{uuid}
process.stdout.write(JSON.stringify({{id:newSubmissionId(),lengths}}));
""")
    assert result == {"id": "sub_ffffffff-ffff-4fff-bfff-ffffffffffff", "lengths": [16]}


def test_web_android_storage_failure_preserves_answer_and_blocks_submission():
    source = web_section("const ACTIVE_SUBMISSION_KEY", "function sessionModeKey")
    result = run_web_js(WEB_DOM + f"""
const appStorage={{getItem:()=>null,setItem:()=>{{throw Error('private storage details');}}}};
const session={{session_id:'s_one'}};function currentQuestion(){{return {{id:7}};}}
const crypto={{randomUUID:()=> '12345678-1234-4234-8234-123456789abc'}};
$('ans').value='未保存的回答';
{source}
const saved=saveDraft(); const submission=prepareSubmission('s_one',7,'answer');
process.stdout.write(JSON.stringify({{saved,submission,answer:$('ans').value,message:window.__lastContextError.message,
 context:window.__lastContextError.context}}));
""")
    assert result["saved"] is False and result["submission"] is None
    assert result["answer"] == "未保存的回答"
    assert result["context"] == "session"
    assert "private storage details" not in result["message"] and "保存" in result["message"]


WEB_DOM = """
const elements = new Map();
function $(id) {
 if (!elements.has(id)) { const classes = new Set(); elements.set(id, {
   id, disabled:false, textContent:'', dataset:{}, children:[],
   classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
     toggle:(c,on)=>{if(on) classes.add(c);else classes.delete(c);}},
   setAttribute(k,v){this[k]=v;}, appendChild(child){this.children.push(child);},
   querySelectorAll(){return [];}, focus(){}, blur(){} }); }
 return elements.get(id);
}
const document={querySelector:s=>s==='.app' ? $('app'):null,querySelectorAll:()=>[],activeElement:null};
const window={};
function showContextError(context,message,options={}) {
 window.__lastContextError={context,message,options};
}
"""


def test_web_android_navigation_back_unwinds_without_resetting_quiz():
    nav = (web_section("let speechInput = null", "function setNativeMessage")
           + web_section("function showView", "function renderStats"))
    result = run_web_js(WEB_DOM + f"""
const isAndroidApp=true; let currentView='quiz';
{nav}
const result=[];
for (const view of ['question-edit','edit','lib','overview','settings','questions']) {{
 showView(view); const handled=window.baguHandleBack(); result.push([view,handled,currentView]);
}}
showView('quiz'); result.push(['quiz',window.baguHandleBack(),currentView]);
process.stdout.write(JSON.stringify(result));
""")
    assert result == [["question-edit", True, "questions"], ["edit", True, "lib"], ["lib", True, "settings"],
                      ["overview", True, "quiz"], ["settings", True, "quiz"], ["questions", True, "quiz"], ["quiz", False, "quiz"]]


def test_web_android_back_closes_keyboard_and_details_before_navigation():
    nav = (web_section("let speechInput = null", "function setNativeMessage")
           + web_section("function showView", "function renderStats"))
    result = run_web_js(WEB_DOM + f"""
const isAndroidApp=true; let currentView='questions'; let blurred=false;
const details={{open:true}};
$('view-questions').querySelectorAll=()=>details.open?[details]:[];
document.activeElement={{tagName:'INPUT',blur:()=>{{blurred=true;document.activeElement=null;}}}};
{nav}
const keyboard=window.baguHandleBack(), afterKeyboard=currentView;
const detail=window.baguHandleBack(), afterDetail=currentView;
const back=window.baguHandleBack();
process.stdout.write(JSON.stringify({{keyboard,blurred,afterKeyboard,detail,afterDetail,open:details.open,back,currentView}}));
""")
    assert result == {"keyboard": True, "blurred": True, "afterKeyboard": "questions", "detail": True,
                      "afterDetail": "questions", "open": False, "back": True, "currentView": "quiz"}


def test_web_android_bad_native_result_does_not_refresh_or_leave_busy():
    source = web_section("function setNativeMessage", "function readTextFile")
    result = run_web_js(WEB_DOM + f"""
let session={{session_id:null}};let nativeBusy='';let currentView='settings';let refreshed=0;
const nativeStore={{exportBackup:()=>{{throw Error('private detail');}},importBackup:()=>{{}}}};
async function refresh(){{refreshed++;}} async function loadQuestions(){{}}
{source}
(async()=>{{startNativeOperation('export');const thrown=$('native-message').textContent;
 startNativeOperation('import');await handleNativeResult({{detail:{{operation:'import',status:'ok',added:-1,updated:3}}}});
 process.stdout.write(JSON.stringify({{thrown,refreshed,busy:nativeBusy,message:$('native-message').textContent}}));}})();
""")
    assert result["refreshed"] == 0 and result["busy"] == ""
    assert "private detail" not in result["thrown"] and "无法" in result["thrown"]
    assert "无效" in result["message"]


def test_web_android_backup_blocks_open_session_and_handles_native_results():
    source = web_section("function setNativeMessage", "function readTextFile")
    result = run_web_js(WEB_DOM + f"""
let session={{session_id:'s_open'}}; let nativeBusy=''; let currentView='settings';
const calls=[]; const nativeStore={{exportBackup:()=>calls.push('export'),importBackup:()=>calls.push('import'),
 saveCsvTemplate:csv=>calls.push(csv),getAppInfo:()=>JSON.stringify({{name:'八股助手',packageName:'test',versionName:'0.1.0-beta.9',versionCode:9,flavor:'internal'}})}};
let refreshed=0; async function refresh(){{refreshed++;}} async function loadQuestions(){{}}
{source}
(async()=>{{
 startNativeOperation('import'); const blocked=calls.length===0 && $('native-message').textContent.includes('本轮');
 session.session_id=null; startNativeOperation('export'); startNativeOperation('export');
 const busy=$('btn-backup-export').disabled;
 await handleNativeResult({{detail:{{operation:'export',status:'cancelled',message:'已取消。'}}}});
 const cancelled=!$('btn-backup-export').disabled && $('native-message').textContent.includes('取消');
 startNativeOperation('import');
 await handleNativeResult({{detail:{{operation:'import',status:'ok',message:'操作完成。',added:2,updated:3}}}});
 const summary=$('native-message').textContent;
 startNativeOperation('export');
 await handleNativeResult({{detail:{{operation:'wrong',status:'ok',message:'bad'}}}});
 const ignored=nativeBusy==='export';
 await handleNativeResult({{detail:{{operation:'export',status:'error',message:'写入失败'}}}});
 renderAppInfo();
 process.stdout.write(JSON.stringify({{blocked,busy,cancelled,calls,refreshed,summary,ignored,
 error:$('native-message').textContent,info:$('app-version').textContent,notice:$('app-distribution').textContent}}));
}})().catch(e=>{{console.error(e);process.exit(1)}});
""")
    assert result["blocked"] and result["busy"] and result["cancelled"] and result["ignored"]
    assert result["calls"] == ["export", "import", "export"]
    assert result["refreshed"] == 1
    assert "2" in result["summary"] and "3" in result["summary"]
    assert result["error"] == "写入失败"
    assert "0.1.0-beta.9" in result["info"] and "内部" in result["notice"]


@pytest.mark.parametrize("mode,want", [("modern", "新文本"), ("legacy", "旧文本"), ("error", "读取文件失败"), ("abort", "已取消读取文件")])
def test_web_android_csv_reader_handles_old_webview(mode, want):
    source = web_section("function readTextFile", "async function api")
    result = run_web_js(f"""
const file={{text:{"async()=> '新文本'" if mode == "modern" else "null"}}};
class FileReader {{readAsText(file,encoding) {{
 if(encoding!=='UTF-8') throw Error('wrong encoding'); this.result='旧文本';
 {"this.onerror()" if mode == "error" else "this.onabort()" if mode == "abort" else "this.onload()"};
}}}}
{source}
readTextFile(file).then(value=>process.stdout.write(JSON.stringify(value))).catch(e=>process.stdout.write(JSON.stringify(e.message)));
""")
    assert result == want


def test_web_android_offline_assets_and_legacy_parse_contract():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert 'name="referrer" content="no-referrer"' in html
    assert html.index('name="referrer"') < html.index('<link')
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "?." not in script and "Math.random" not in script
    for stem, notice in [("PlusJakartaSans", "OFL"), ("FiraCode", "OFL"), ("MaterialSymbolsRounded", "APACHE-2.0")]:
        assert f"/assets/fonts/{stem}.ttf" in html
        assert (ROOT / f"assets/fonts/{stem}.ttf").stat().st_size > 10000
        assert (ROOT / f"assets/fonts/{stem}-{notice}.txt").is_file()
    for view in ("overview", "settings"):
        assert f'id="view-{view}"' in html
    assert "bagu-native-result" in html and "safe-area-inset-bottom" in html
    assert "button:focus," in html and "body.android-app" in html


def _web_css_rules():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for order, (selectors, body) in enumerate(re.findall(r"([^{}]+)\{([^{}]+)\}", css)):
        declarations = {
            key.strip(): value.strip()
            for declaration in body.split(";")
            if ":" in declaration
            for key, value in [declaration.split(":", 1)]
        }
        for selector in selectors.split(","):
            selector = selector.strip()
            if selector and not selector.startswith("@"):
                rules.append((selector, declarations, order))
    return rules


def _simple_css_selector_matches(selector, node):
    if any(token in selector for token in (":", "[", "]")):
        return False
    tag = re.match(r"^[a-zA-Z][\w-]*|^\*", selector)
    if tag and tag.group() != "*" and node["tag"] != tag.group():
        return False
    ids = re.findall(r"#([\w-]+)", selector)
    if ids and node.get("id") not in ids:
        return False
    return set(re.findall(r"\.([\w-]+)", selector)) <= node["classes"]


def _css_selector_matches_path(selector, path):
    if ">" in selector:
        return False
    parts = selector.split()
    if not parts or not _simple_css_selector_matches(parts[-1], path[0]):
        return False
    ancestor = 1
    for part in reversed(parts[:-1]):
        while ancestor < len(path) and not _simple_css_selector_matches(part, path[ancestor]):
            ancestor += 1
        if ancestor == len(path):
            return False
        ancestor += 1
    return True


def _css_specificity(selector):
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+|\[[^]]+\]|:(?!:)[\w-]+", selector))
    tags = sum(
        bool(re.match(r"^[a-zA-Z][\w-]*", part))
        for part in re.split(r"\s+|>", selector)
        if part and part != "*"
    )
    return ids, classes, tags


def _css_declarations_for(selector):
    declarations = {}
    for candidate, body, _ in _web_css_rules():
        if candidate == selector:
            declarations.update(body)
    return declarations


def _css_grid_tracks(value):
    tracks, token, depth = [], [], 0
    for char in value:
        if char.isspace() and depth == 0:
            if token:
                tracks.append("".join(token))
                token = []
            continue
        token.append(char)
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
    if token:
        tracks.append("".join(token))
    return [re.sub(r"\s+", "", track) for track in tracks]


def test_pager_icon_font_is_not_overridden_by_page_counter_styles():
    icon_path = [
        {"tag": "span", "id": "", "classes": {"material-symbols-rounded"}},
        {"tag": "button", "id": "q-prev", "classes": {"compact-btn"}},
        {"tag": "div", "id": "q-pager", "classes": {"pager"}},
        {"tag": "body", "id": "", "classes": {"android-app"}},
    ]
    candidates = [
        (_css_specificity(selector), order, body["font-family"])
        for selector, body, order in _web_css_rules()
        if "font-family" in body and _css_selector_matches_path(selector, icon_path)
    ]

    assert max(candidates)[2] == "'Material Symbols Rounded'"


def test_android_pager_uses_centered_single_row_tracks():
    pager = _css_declarations_for("body.android-app .pager")
    page_info = _css_declarations_for("#q-page-info")
    button = _css_declarations_for("body.android-app .pager button")

    assert not _css_declarations_for(".pager span")
    assert _css_grid_tracks(pager["grid-template-columns"]) == [
        "minmax(0,1fr)", "auto", "minmax(0,1fr)",
    ]
    assert page_info.get("text-align") == "center"
    assert page_info.get("white-space") == "nowrap"
    assert button.get("min-width") == "0"
    assert button.get("width") == "100%"
    assert button.get("justify-content") == "center"
    assert button.get("white-space") == "nowrap"


@pytest.mark.parametrize("selector", [
    "body.android-app .question-meta a",
    "body.android-app .markdown-body a:not(.answer-image-link)",
    "body.android-app .answer-source",
    "body.android-app .answer-image-link",
])
def test_web_android_interactive_link_targets_have_sized_boxes(selector):
    """Guard the CSS sizing contract; controller browser QA verifies actual geometry."""
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    declarations = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]+)\}", css):
        if selector in [part.strip() for part in selectors.split(",")]:
            declarations.update(part.strip().split(":", 1) for part in body.split(";") if ":" in part)
    declarations = {key.strip(): value.strip() for key, value in declarations.items()}
    assert float(declarations.get("min-width", "0px").removesuffix("px")) >= 44
    assert float(declarations.get("min-height", "0px").removesuffix("px")) >= 44
    if selector.endswith(".answer-image-link"):
        # The existing block media link must keep its layout/no-padding image scaling.
        assert declarations.get("display", "block") == "block"
        assert declarations.get("padding", "0") == "0"
    else:
        assert declarations.get("display") in {"inline-block", "inline-flex", "block", "flex"}
        assert declarations.get("max-width") == "100%"


def test_android_manifest_limits_permissions_and_launch_surface():
    path = ANDROID / "app/src/main/AndroidManifest.xml"
    assert path.is_file(), "Android manifest not implemented"
    manifest = ET.parse(path).getroot()
    assert {p.get(NS + "name") for p in manifest.findall("uses-permission")} == {
        "android.permission.INTERNET", "android.permission.RECORD_AUDIO", "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.QUERY_ADVANCED_PROTECTION_MODE"}
    assert [action.get(NS + "name") for action in manifest.findall("queries/intent/action")] == [
        "android.speech.RecognitionService"]
    app = manifest.find("application")
    assert app.get(NS + "allowBackup") == "false"
    assert app.get(NS + "networkSecurityConfig") == "@xml/network_security_config"
    activities = {activity.get(NS + "name"): activity for activity in app.findall("activity")}
    assert set(activities) == {".MainActivity", ".UpdateInstallActivity"}
    activity = activities[".MainActivity"]
    assert activity.get(NS + "exported") == "true"
    assert activity.get(NS + "resizeableActivity") == "true"
    filters = activity.findall("intent-filter")
    assert len(filters) == 1
    assert [a.get(NS + "name") for a in filters[0].findall("action")] == ["android.intent.action.MAIN"]
    assert not filters[0].findall("data"), "Do not accept external deep-link launches"
    callback = activities[".UpdateInstallActivity"]
    assert callback.get(NS + "exported") == "false"
    assert callback.get(NS + "excludeFromRecents") == "true"
    assert callback.get(NS + "noHistory") == "true"
    assert callback.get(NS + "theme") == "@style/Theme.Bagu.InstallCallback"
    assert not callback.findall("intent-filter")
    providers = {provider.get(NS + "name") for provider in app.findall("provider")}
    assert providers == {".ImportProvider"}, "APK bytes must be handed to a PackageInstaller session, not a provider URI"


def test_android_build_graph_declares_bounded_generated_sources():
    path = ANDROID / "app/build.gradle"
    assert path.is_file(), "Android build graph not implemented"
    source = path.read_text(encoding="utf-8")
    for required in ("minSdk 29", "targetSdk 36", "compileSdk 36", "io.github.ingnijm.baguhelper",
                     "syncPython", "syncStatic", "merge.*PythonSources", "arm64-v8a", "baguVersionCode"):
        assert required in source
    assert "applicationIdSuffix" not in source
    assert "srcDirs = [repoRoot]" not in source


def test_android_cleartext_exception_is_only_loopback():
    path = ANDROID / "app/src/main/res/xml/network_security_config.xml"
    assert path.is_file(), "Android transport security not implemented"
    config = ET.parse(path).getroot()
    assert config.find("base-config").get("cleartextTrafficPermitted") == "false"
    domains = config.findall("domain-config/domain")
    assert len(domains) == 1 and domains[0].text == "127.0.0.1"
    assert domains[0].get("includeSubdomains") == "false"


def test_native_bridge_exposes_only_the_agreed_storage_file_and_speech_contract():
    source = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/NativeBridge.java").read_text(encoding="utf-8")
    methods = re.findall(r"@JavascriptInterface public (?:synchronized )?(\w+) (\w+)\(([^)]*)\)", source)
    assert {(result, name, args) for result, name, args in methods} == {
        ("String", "getItem", "String key"), ("void", "setItem", "String key, String value"),
        ("void", "removeItem", "String key"), ("String", "keys", ""),
        ("void", "exportBackup", ""), ("void", "exportQuestionBank", ""), ("void", "importBackup", ""),
        ("void", "importInterviewPack", ""),
        ("boolean", "hasBundledInterviewPack", ""), ("void", "importBundledInterviewPack", ""),
        ("void", "saveCsvTemplate", "String csv"),
        ("void", "exportDiagnostics", ""), ("void", "reportDiagnostic", "String json"),
        ("String", "getAppInfo", ""),
        ("void", "startSpeech", "String requestId"),
        ("void", "stopSpeech", "String requestId"),
        ("void", "cancelSpeech", "String requestId"),
        ("String", "getUpdateState", ""),
        ("void", "setAutomaticUpdates", "boolean enabled, String operationId"),
        ("boolean", "checkForUpdate", "String operationId"),
        ("boolean", "downloadUpdate", "String candidateId, String operationId"),
        ("void", "cancelUpdate", "String operationId"),
        ("boolean", "installUpdate", "String candidateId, String operationId"),
    }


def test_android_pack_picker_callbacks_are_operation_scoped_and_saved_state_has_only_a_marker():
    source = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/MainActivity.java").read_text(encoding="utf-8")
    pending = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/PendingImport.java").read_text(encoding="utf-8")

    assert "documentRequestCode" in source
    assert "nextDocumentRequestCode" in source
    assert "request != state.documentRequestCode" in source
    assert "PACK_DOCUMENT_REQUEST" in source
    assert 'saved.putString("documentPendingImportKind"' in source
    assert 'saved.putByteArray' not in source
    assert 'saved.putSerializable' not in source
    assert "pendingImport.snapshot()" not in source[source.index("onSaveInstanceState"):]
    dispatch = source[source.index("private void flushResults"):source.index("private void publishImeVisibility")]
    assert "pendingImport" not in dispatch and "snapshot" not in dispatch and "preview" not in dispatch
    assert "Set.of(" not in pending


def test_android_csv_picker_enters_the_shared_native_operation_arbiter():
    source = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/MainActivity.java").read_text(encoding="utf-8")
    chooser = source[source.index("onShowFileChooser"):source.index("root.addView(web")]

    assert 'openDocument("csv", null)' in chooser
    assert 'claimDocument(operation)' in source


def test_android_diagnostics_and_other_file_operations_share_update_busy_and_lease_gate():
    source = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/MainActivity.java").read_text(encoding="utf-8")
    guard = source[source.index("void openDocument"):source.index("if (\"template\".equals(operation)")]

    assert "updater != null && !updater.fileOperationIdle()" in guard
    assert '!"diagnostics".equals(operation) && updater' not in guard


def test_android_native_operation_arbiter_claims_before_ui_post_and_tracks_update_completion():
    arbiter = ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/NativeOperationArbiter.java"
    assert arbiter.is_file(), "native file/update arbiter not implemented"
    assert ".isBlank()" not in arbiter.read_text(encoding="utf-8")
    bridge = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/NativeBridge.java").read_text(encoding="utf-8")
    activity = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/MainActivity.java").read_text(encoding="utf-8")
    updater = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/UpdateController.java").read_text(encoding="utf-8")

    assert "fileIdle() && updater" not in bridge
    assert "owner.requestDocument(operation, content)" in bridge
    assert "owner.checkForUpdate(operationId)" in bridge
    assert "owner.downloadUpdate(candidateId, operationId)" in bridge
    assert "owner.installUpdate(candidateId, operationId)" in bridge
    assert "NativeOperationArbiter arbiter" in activity
    assert "NativeOperationArbiter.process()" in activity
    assert "documentLease" in activity and "releaseDocument" in activity
    destroy = activity[activity.index("@Override protected void onDestroy") :]
    assert "state.releaseAll()" not in destroy
    assert "if (!state.working) state.releaseDocument" in destroy
    assert "NativeOperationLeaseTracker leaseTracker" in updater
    assert "leaseTracker.observe(state)" in updater
    assert "aliases" not in updater and "aliasTrackedLease" not in updater


def test_android_update_lease_requires_observed_active_before_terminal_release():
    tracker = ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/NativeOperationLeaseTracker.java"
    assert tracker.is_file(), "update lease state tracker not implemented"
    source = tracker.read_text(encoding="utf-8")
    updater = (ANDROID / "app/src/main/java/io/github/ingnijm/baguhelper/UpdateController.java").read_text(encoding="utf-8")

    assert "seenActive" in source
    assert 'tracked.operationId.equals(state.get("operationId"))' in source
    assert "acceptedActionWindow && matchingOperation" in source
    assert "NativeOperationLeaseTracker" in updater
    assert "engine::state" in updater
    assert "trackedLease" not in updater


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = ANDROID / "app/src/main/python/android_runtime.py"
    assert path.is_file(), "Android runtime not implemented"
    spec = importlib.util.spec_from_file_location("android_runtime_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old_opener = urllib.request._opener
    # Source-adjacent desktop defaults must never be selected by the host.
    monkeypatch.setattr(bagu, "DB_PATH", tmp_path / "forbidden-desktop.db")
    static = tmp_path / "apk-static"
    (static / "web").mkdir(parents=True)
    (static / "web/index.html").write_text("<html>test-shell</html>", encoding="utf-8")
    seed = tmp_path / "seed.db"
    bagu.prepare_mobile_database(seed)
    conn = bagu.get_conn(seed)
    conn.execute("INSERT INTO questions(category,question,answer,url) VALUES(?,?,?,?)", ("测试", "题目", "答案", "https://example.org"))
    conn.commit()
    conn.close()
    yield module, tmp_path / "private", static, seed
    if module._server is not None:
        module._server.shutdown()
        module._server.server_close()
        module._thread.join(timeout=3)
    urllib.request._opener = old_opener
    for handler in list(bagu.EVENT_LOGGER.handlers):
        if str(tmp_path) in getattr(handler, "baseFilename", ""):
            bagu.EVENT_LOGGER.removeHandler(handler)
            handler.close()


def launch(runtime):
    module, private, static, seed = runtime
    return json.loads(module.start(str(private), str(static), str(seed), "internal"))


def test_runtime_startup_failure_is_logged_before_database_preparation(runtime, monkeypatch):
    module, private, static, seed = runtime
    monkeypatch.setattr(bagu, "prepare_mobile_database", lambda *a: (_ for _ in ()).throw(OSError("sk-test-private")))
    with pytest.raises(OSError):
        launch(runtime)
    raw = (private / "logs/bagu-server.log").read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[-1]["event"] == "runtime.error"
    assert "sk-test-private" not in raw


def test_runtime_start_is_singleton_with_authenticated_isolated_server(runtime):
    module, private, static, seed = runtime
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        launches = list(pool.map(lambda _: launch(runtime), range(8)))
    assert all(item == launches[0] for item in launches)
    info = launches[0]
    url = urllib.parse.urlsplit(info["url"])
    assert url.hostname == "127.0.0.1" and url.port == info["port"] > 0
    query = urllib.parse.parse_qs(url.query)
    assert len(query["token"][0]) >= 40
    assert query["platform"] == ["android"] and query["variant"] == ["internal"]
    with urllib.request.urlopen(info["url"]) as response:
        assert b"test-shell" in response.read()
    base = f"http://127.0.0.1:{info['port']}"
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(base + "/api/stats")
    assert denied.value.code == 403
    request = urllib.request.Request(base + "/api/stats", headers={"X-Bagu-Token": query["token"][0]})
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
    assert (private / "data/bagu.db").is_file()
    assert (private / "config").is_dir() and (private / "logs").is_dir()
    assert not (private.parent / "forbidden-desktop.db").exists()
    assert not (static / "bagu.db").exists()
    with pytest.raises(ValueError):
        module.start(str(private / "other"), str(static), str(seed), "internal")


def test_runtime_backup_round_trip_uses_private_database(runtime):
    module, private, _, _ = runtime
    launch(runtime)
    archive = module.export_archive()
    assert isinstance(archive, bytes)
    assert bagu.parse_backup(archive)[0]["question"] == "题目"
    result = json.loads(module.restore_archive(archive))
    assert result == {"added": 0, "updated": 1, "total": 1}
    with pytest.raises(ValueError):
        module.restore_archive(b"bad zip")
    assert len(bagu.parse_backup(module.export_archive())) == 1


def test_runtime_interview_pack_preview_and_install_use_canonical_private_database(runtime):
    module, private, _, _ = runtime
    launch(runtime)
    payload = make_interview_pack_fixture()

    summary = json.loads(module.inspect_interview_pack(payload))

    assert summary["pack_id"] == "android-private-pack"
    assert summary["status"] == "new" and summary["question_count"] == 1
    assert "PRIVATE_PACK_ANSWER_SENTINEL" not in json.dumps(summary)
    installed = json.loads(module.install_interview_pack(payload))
    assert installed["pack_id"] == "android-private-pack"
    connection = bagu.get_conn(private / "data/bagu.db")
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM question_packs WHERE pack_id='android-private-pack'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM questions WHERE pack_id='android-private-pack'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_runtime_interview_pack_preview_rejects_open_session_without_mutation(runtime):
    module, private, _, _ = runtime
    launch(runtime)
    connection = bagu.get_conn(private / "data/bagu.db")
    try:
        sid, _ = bagu.draw(connection, 1)
        before = list(connection.iterdump())
        with pytest.raises(bagu.SessionOpenError):
            module.inspect_interview_pack(make_interview_pack_fixture())
        assert list(connection.iterdump()) == before
        assert bagu.get_open_session(connection)["id"] == sid
    finally:
        connection.close()


def test_runtime_migration_uses_injected_version_and_validates_before_restore(runtime):
    module, private, static, seed = runtime
    info = json.loads(module.start(str(private), str(static), str(seed), "public", "synthetic-native-version"))
    exported = module.export_archive("questions")
    summary = json.loads(module.inspect_archive(exported))
    assert summary["mode"] == "questions" and summary["app_version"] == "synthetic-native-version"
    assert set(bagu.parse_backup(exported)[0]) == {"category", "question", "answer", "url"}
    assert json.loads(module.restore_archive(exported)) == {"added": 0, "updated": 1, "total": 1}
    with pytest.raises(ValueError):
        module.inspect_archive(b"invalid archive")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(info["url"]).query)
    request = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/backup/export?mode=questions",
                                     headers={"X-Bagu-Token": query["token"][0]})
    with urllib.request.urlopen(request) as response:
        assert bagu.inspect_backup(response.read())["app_version"] == "synthetic-native-version"


def test_runtime_native_import_preview_rejects_open_session_without_mutation(runtime):
    module, private, _, _ = runtime
    launch(runtime)
    payload = module.export_archive("questions")
    connection = bagu.get_conn(private / "data/bagu.db")
    try:
        sid, _ = bagu.draw(connection, 1)
        before = list(connection.iterdump())
        with pytest.raises(bagu.SessionOpenError):
            module.inspect_archive(payload)
        assert list(connection.iterdump()) == before
        assert bagu.get_open_session(connection)["id"] == sid
    finally:
        connection.close()


def test_runtime_page_policy_blocks_frames_but_preserves_https_answer_images(runtime):
    info = launch(runtime)
    with urllib.request.urlopen(info["url"]) as response:
        policy = response.headers.get("Content-Security-Policy", "")
        assert "frame-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "object-src 'none'" in policy and "base-uri 'none'" in policy
        assert "connect-src 'self'" in policy
        assert "script-src 'self' 'unsafe-inline'" in policy
        assert "img-src 'self' https: data:" in policy
        assert response.headers.get("Referrer-Policy") == "no-referrer"


@pytest.mark.parametrize("variant", ["", "desktop", None])
def test_runtime_rejects_unknown_variant_before_writing(runtime, variant):
    module, private, static, seed = runtime
    with pytest.raises(ValueError):
        module.start(str(private), str(static), str(seed), variant)
    assert not private.exists()


def test_runtime_requires_start_before_backup(runtime):
    with pytest.raises(RuntimeError):
        runtime[0].export_archive()


def test_redirect_policy_blocks_https_downgrade_and_strips_cross_origin_credentials(runtime):
    module = runtime[0]
    policy = module.SecureRedirectHandler()
    request = urllib.request.Request("https://one.example/v1", headers={
        "Authorization": "Bearer sk-test", "Cookie": "test=value", "Proxy-Authorization": "Basic test",
    })
    with pytest.raises(urllib.error.HTTPError):
        policy.redirect_request(request, None, 302, "redirect", {}, "http://one.example/v2")
    same = policy.redirect_request(request, None, 302, "redirect", {}, "https://one.example/v2")
    assert same.get_header("Authorization") == "Bearer sk-test"
    other = policy.redirect_request(request, None, 302, "redirect", {}, "https://two.example/v2")
    assert other.get_header("Authorization") is None and other.get_header("Cookie") is None
    assert other.get_header("Proxy-authorization") is None
    port = policy.redirect_request(request, None, 302, "redirect", {}, "https://one.example:444/v2")
    assert port.get_header("Authorization") is None
    zero_port = policy.redirect_request(request, None, 302, "redirect", {}, "https://one.example:0/v2")
    assert zero_port.get_header("Authorization") is None
    launch(runtime)
    assert any(isinstance(handler, module.SecureRedirectHandler) for handler in urllib.request._opener.handlers)
