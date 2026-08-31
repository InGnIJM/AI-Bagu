"""Public release validation uses synthetic files, never the personal database."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BETA6_VERSION = {"versionName": "0.1.0-beta.6", "versionCode": 6, "channel": "beta"}
BETA6_PACK = {
    "schema_version": 2,
    "versionName": "0.1.0-beta.6",
    "file_name": "ai-bagu-2026-autumn-interviews-r1.bagu-pack",
    "sha256": "47aa6b28768be85322924df4a7c17199bf248660997cd10247066821d6d23864",
    "pack_id": "autumn-recruit-interviews-2026",
    "revision": 1,
    "display_version": "2026.08.30-r1",
    "question_count": 748,
    "experience_count": 27,
    "android_delivery": "bundled_confirm",
}


def load_release():
    spec = importlib.util.spec_from_file_location("release_metadata", ROOT / "scripts/release_metadata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_rejects_invalid_channel_code_and_name(tmp_path):
    release = load_release()
    for version in (
        {"versionName": "0.1.0", "versionCode": True, "channel": "stable"},
        {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "stable"},
        {"versionName": "../escape", "versionCode": 2, "channel": "beta"},
        {"versionName": "0.1.0", "versionCode": 0, "channel": "stable"},
    ):
        path = tmp_path / "version.json"
        path.write_text(json.dumps(version), encoding="utf-8")
        with pytest.raises(ValueError):
            release.load_version(path)


def test_beta6_public_release_contract_is_canonical_and_has_seven_assets():
    release = load_release()

    assert release.load_version(ROOT / "version.json") == BETA6_VERSION
    descriptor_path = ROOT / "docs/releases/0.1.0-beta.6-question-pack.json"
    descriptor_bytes = descriptor_path.read_bytes()
    assert descriptor_bytes == release.json_bytes(BETA6_PACK)
    descriptor = release.parse_question_pack_descriptor(descriptor_bytes, BETA6_VERSION)
    assert descriptor.bundled_confirm and not descriptor.external_only


@pytest.mark.parametrize("descriptor", [
    "docs/releases/0.1.0-beta.5-question-pack.json",
    "docs/releases/0.1.0-beta.6-question-pack.json",
])
def test_question_pack_descriptors_pin_lf_checkout_bytes(descriptor):
    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", descriptor],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"{descriptor}: text: set",
        f"{descriptor}: eol: lf",
    ]


def test_metadata_uses_exact_tag_url_and_public_version(tmp_path):
    release = load_release()
    apk = tmp_path / "bagu-0.1.0-beta.2-public-arm64-v8a.apk"
    apk.write_bytes(b"synthetic validated apk")
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    feed = release.make_feed(version, apk, "修复迁移\n<script>plain text</script>", "2026-08-28T00:00:00Z")
    assert feed["schema_version"] == 1 and feed["channel"] == "beta"
    item = feed["release"]
    assert item["versionCode"] == 2 and item["distribution"] == "public"
    assert item["apkUrl"] == "https://github.com/InGnIJM/AI-Bagu/releases/download/v0.1.0-beta.2/" + apk.name
    assert item["sha256"] == hashlib.sha256(apk.read_bytes()).hexdigest()
    assert item["size"] == len(b"synthetic validated apk")
    assert item["notes"] == "修复迁移\n<script>plain text</script>"


def test_release_directory_rejects_private_extra_files_and_tampering(tmp_path):
    release = load_release()
    apk = tmp_path / "bagu-0.1.0-beta.2-public-arm64-v8a.apk"
    apk.write_bytes(b"synthetic validated apk")
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    release.write_metadata(tmp_path, version, "变化", "2026-08-28T00:00:00Z")
    assert len(release.validate_directory(tmp_path, version)) == 6
    extra = tmp_path / "private.bagu-backup"
    extra.write_bytes(b"must not upload")
    with pytest.raises(ValueError, match="allowlist"):
        release.validate_directory(tmp_path, version)
    extra.unlink()
    apk.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash|size"):
        release.validate_directory(tmp_path, version)


def test_metadata_writes_do_not_overwrite_existing_release(tmp_path):
    release = load_release()
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    (tmp_path / release.apk_name(version)).write_bytes(b"fake")
    release.write_metadata(tmp_path, version, "one", "2026-08-28T00:00:00Z")
    before = (tmp_path / "update.json").read_bytes()
    with pytest.raises(ValueError, match="existing"):
        release.write_metadata(tmp_path, version, "different", "2026-08-28T00:00:00Z")
    assert (tmp_path / "update.json").read_bytes() == before


@pytest.mark.parametrize("character,limit", [("a", 12000), ("题", 12000), ("\U00020000", 6000)])
def test_release_notes_share_android_utf16_limit(tmp_path, character, limit):
    release = load_release()
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    apk = tmp_path / release.apk_name(version)
    apk.write_bytes(b"synthetic apk")
    release.write_metadata(tmp_path, version, character * limit, "2026-08-28T00:00:00Z")
    assert len(release.validate_directory(tmp_path, version)) == 6
    with pytest.raises(ValueError, match="release notes"):
        release.make_feed(version, apk, character * (limit + 1), "2026-08-28T00:00:00Z")


def test_release_notes_reject_unpaired_surrogates(tmp_path):
    release = load_release()
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    apk = tmp_path / release.apk_name(version)
    apk.write_bytes(b"synthetic apk")
    with pytest.raises(ValueError, match="release notes"):
        release.make_feed(version, apk, "invalid\ud800", "2026-08-28T00:00:00Z")


def test_public_build_plan_never_invokes_internal_seed_or_old_version():
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts/android.ps1"), "-Mode", "Plan",
    ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["versionCode"] == 6
    assert plan["flavor"] == "public"
    assert plan["tasks"] == [":app:assemblePublicRelease", ":app:testPublicDebugUnitTest", ":app:lintPublicRelease"]
    assert plan["deliveryName"] == "bagu-0.1.0-beta.6-public-arm64-v8a.apk"
    assert "questionPack" not in plan
