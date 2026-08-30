"""Exercise release state transitions using a narrow in-memory GitHub boundary."""
import importlib.util
import io
import base64
import hashlib
from http.client import BadStatusLine
import json
from pathlib import Path
import sys
from copy import deepcopy
from types import SimpleNamespace
import subprocess
import socket
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("release tests must mock external HTTP; real network is forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def publisher():
    spec = importlib.util.spec_from_file_location("release_github", ROOT / "scripts/release_github.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_preflight_with_tracked_path(tmp_path, monkeypatch, tracked_path):
    module = publisher()
    (tmp_path / "version.json").write_bytes((ROOT / "version.json").read_bytes())
    (tmp_path / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())

    def fake_command(args, cwd=tmp_path, data=None, timeout=120):
        assert cwd == tmp_path and args[0] == "git"
        if args[1:3] == ["status", "--porcelain"]:
            return b""
        if args[1:] == ["remote", "get-url", "origin"]:
            return b"https://github.com/InGnIJM/AI-Bagu.git\n"
        if args[1:] == ["ls-files"]:
            return ("README.md\n" + tracked_path + "\n").encode()
        if args[1:] == ["rev-parse", "HEAD"]:
            return ("a" * 40).encode()
        raise AssertionError(args)

    monkeypatch.setattr(module, "command", fake_command)
    return module.local_preflight(tmp_path)


@pytest.mark.parametrize("private_path", [
    "fixtures/interviews.bagu-pack",
    "assets/private/catalog.json",
    "assets/private/catalog-index.json",
    "assets/private/package-catalog.json",
    "assets/private/release_catalog.v2.json",
    "docs/private-catalog.json",
    "docs/private-interview-catalog.json",
])
def test_local_preflight_rejects_tracked_interview_pack_material(tmp_path, monkeypatch, private_path):
    with pytest.raises(ValueError, match="private|artifact|pack|catalog"):
        local_preflight_with_tracked_path(tmp_path, monkeypatch, private_path)


@pytest.mark.parametrize("allowed_path", [
    "docs/package-catalog.json",
    "docs/interviewing-catalog.json",
    "docs/privately-owned-catalog.json",
    "assets/private/mycatalog.json",
])
def test_local_preflight_allows_marker_substrings_without_private_catalog_match(
        tmp_path, monkeypatch, allowed_path):
    _, commit = local_preflight_with_tracked_path(tmp_path, monkeypatch, allowed_path)
    assert commit == "a" * 40


@pytest.fixture
def prepared(tmp_path):
    import release_metadata as meta
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    (tmp_path / meta.apk_name(version)).write_bytes(b"synthetic-apk")
    feed = meta.write_metadata(tmp_path, version, "notes", "2026-08-28T00:00:00Z")
    return tmp_path, version, feed


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.assets = {}
        self.events = []
        self.corrupt = False

    def find_release(self, tag):
        return self.release

    def get_release(self, release_id):
        assert release_id == self.release["id"]
        return self.release

    def verify_tag(self, tag, commit, allow_missing=False):
        self.events.append("tag")

    def create_draft(self, tag, commit, prerelease, notes):
        self.events.append("draft")
        self.release = {"id": 1, "tag_name": tag, "target_commitish": commit,
                        "draft": True, "prerelease": prerelease, "assets": []}
        return self.release

    def upload(self, tag, path):
        assert self.release["draft"]
        self.events.append("upload:" + path.name)
        self.assets[path.name] = path.read_bytes()
        self.release["assets"].append({"id": len(self.assets), "name": path.name, "size": path.stat().st_size})

    def download_asset(self, asset, limit):
        self.events.append("verify:" + asset["name"])
        return b"bad" if self.corrupt else self.assets[asset["name"]]

    def publish_draft(self, release):
        assert len(self.assets) == 6
        self.events.append("publish")
        self.release["draft"] = False


def test_dry_run_has_no_remote_calls(prepared):
    module = publisher()
    directory, version, feed = prepared
    remote = FakeGitHub()
    result = module.publish_release(remote, directory, version, "a" * 40, execute=False)
    assert result["release"] == "dry-run" and remote.events == []


def test_v2_descriptor_keeps_seven_external_assets_in_release_dry_run(
        tmp_path, monkeypatch, capsys):
    module = publisher()
    version = {"versionName": "0.1.0-beta.6", "versionCode": 6, "channel": "beta"}
    descriptor = {
        "schema_version": 2,
        "versionName": version["versionName"],
        "file_name": "synthetic-v2.bagu-pack",
        "sha256": "a" * 64,
        "pack_id": "synthetic-v2-pack",
        "revision": 1,
        "display_version": "2026.08.30-r1",
        "question_count": 1,
        "experience_count": 1,
        "android_delivery": "bundled_confirm",
    }
    path = tmp_path / "docs/releases/0.1.0-beta.6-question-pack.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(module.meta.json_bytes(descriptor))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "local_preflight", lambda: (version, "a" * 40))
    monkeypatch.setattr(sys, "argv", ["release_github.py", "preflight"])

    assert module.main() == 0

    announcement = json.loads(capsys.readouterr().out.splitlines()[0])
    assert len(announcement["assets"]) == 7
    assert descriptor["file_name"] in announcement["assets"]


def test_prepare_receipt_records_bundled_delivery_without_source_path(tmp_path, monkeypatch):
    module = publisher()
    version = {"versionName": "0.1.0-beta.6", "versionCode": 6, "channel": "beta"}
    directory = tmp_path / "dist/android/0.1.0-beta.6/public"
    source = tmp_path / "private-source" / "synthetic-v2.bagu-pack"
    source.parent.mkdir()
    source.write_bytes(b"synthetic pack bytes")
    descriptor = module.meta.QuestionPackDescriptor({
        "schema_version": 2, "versionName": version["versionName"],
        "file_name": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pack_id": "synthetic-v2-pack", "revision": 1,
        "display_version": "2026.08.30-r1", "question_count": 1,
        "experience_count": 1, "android_delivery": "bundled_confirm",
    })
    pack_context = {
        "descriptor": descriptor,
        "bound": SimpleNamespace(path=source),
        "provenance": {
            "file_name": source.name,
            "sha256": descriptor["sha256"],
            "descriptor_sha256": "d" * 64,
        },
    }
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "local_preflight", lambda: (version, "a" * 40))
    monkeypatch.setattr(module.meta, "validate_directory", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_release_pack_context", lambda *args, **kwargs: pack_context)

    def run(args, cwd):
        if "-Mode" in args and args[args.index("-Mode") + 1] == "Build":
            directory.mkdir(parents=True)
            (directory / module.meta.apk_name(version)).write_bytes(b"synthetic apk")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)

    module.prepare(directory, version, "a" * 40, question_pack=source, pack_context=pack_context)

    receipt_bytes = (directory.parent / "verification.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt["android_delivery"] == "bundled_confirm"
    assert receipt["bundled_pack_member"] == "assets/question-pack/bundled.bagu-pack"
    assert receipt["bundled_pack_sha256"] == descriptor["sha256"]
    assert str(source.resolve()).encode("utf-8") not in receipt_bytes


def test_release_is_verified_before_publication_and_feed(prepared):
    module = publisher()
    directory, version, feed = prepared
    remote = FakeGitHub()
    result = module.publish_release(remote, directory, version, "a" * 40, execute=True)
    assert result["release"] == "published"
    assert remote.events[1] == "draft"
    assert remote.events.index("publish") > max(i for i, e in enumerate(remote.events) if e.startswith("verify:"))
    assert remote.release["prerelease"] is True
    before = remote.events.count("publish")
    module.publish_release(remote, directory, version, "a" * 40, execute=True)
    assert remote.events.count("publish") == before


def test_bad_remote_asset_aborts_without_publish(prepared):
    module = publisher()
    directory, version, feed = prepared
    remote = FakeGitHub()
    remote.corrupt = True
    with pytest.raises(ValueError, match="asset"):
        module.publish_release(remote, directory, version, "a" * 40, execute=True)
    assert "publish" not in remote.events


def test_existing_different_tag_or_extra_asset_is_not_overwritten(prepared):
    module = publisher()
    directory, version, feed = prepared
    remote = FakeGitHub()
    remote.create_draft("v0.1.0-beta.2", "b" * 40, True, "notes")
    with pytest.raises(ValueError, match="commit"):
        module.publish_release(remote, directory, version, "a" * 40, execute=True)
    assert not remote.assets
    remote.release["target_commitish"] = "a" * 40
    remote.release["assets"] = [{"name": "private.db", "id": 9, "size": 1}]
    with pytest.raises(ValueError, match="allowlist"):
        module.publish_release(remote, directory, version, "a" * 40, execute=True)


def test_download_redirect_policy_blocks_downgrade_and_foreign_hosts():
    module = publisher()
    for url in ("http://github.com/a", "https://evil.test/a", "https://github.com.evil.test/a",
                "https://user:pass@github.com/a", "https://github.com:444/a"):
        with pytest.raises(ValueError):
            module.validate_download_url(url)
    module.validate_download_url("https://release-assets.githubusercontent.com/a?signature=temporary")


def test_feed_preserves_other_channel_and_rejects_downgrade(prepared):
    module = publisher()
    directory, version, feed = prepared
    stable = {"schema_version": 1, "channel": "stable", "release": None}
    files = {".nojekyll": b"", "updates/stable.json": b'{"schema_version":1,"channel":"stable","release":null}\n'}
    merged = module.merge_feed_files(files, feed)
    assert merged["updates/stable.json"] == files["updates/stable.json"]
    assert json.loads(merged["updates/beta.json"])["release"]["versionCode"] == 2
    newer = json.loads(merged["updates/beta.json"])
    newer["release"]["versionCode"] = 3
    files["updates/beta.json"] = json.dumps(newer).encode()
    with pytest.raises(ValueError, match="downgrade"):
        module.merge_feed_files(files, feed)


class ReleaseApi:
    """API boundary: tag lookup hides drafts; authenticated list/ID expose them."""

    def __init__(self, module):
        self.remote = object.__new__(module.GitHub)
        self.remote.prefix = "repos/InGnIJM/AI-Bagu"
        self.remote.api = self.api
        self.remote.upload = self.upload
        self.releases = []
        self.assets = {}
        self.calls = []
        self.fail_upload_after = None

    def api(self, path, method="GET", body=None, optional=False, binary=False):
        self.calls.append((path, method))
        route = path.removeprefix(self.remote.prefix)
        if route == "":
            return {"private": False, "archived": False}
        if route.startswith("/commits/"):
            return {"sha": route.rsplit("/", 1)[1]}
        if route.startswith("/git/ref/tags/"):
            tag = route.removeprefix("/git/ref/tags/")
            release = next((r for r in self.releases if r["tag_name"] == tag and not r["draft"]), None)
            return None if release is None else {"object": {"type": "commit", "sha": release["target_commitish"]}}
        if route.startswith("/releases/tags/"):
            tag = route.removeprefix("/releases/tags/")
            return deepcopy(next((r for r in self.releases if r["tag_name"] == tag and not r["draft"]), None))
        if route.startswith("/releases?per_page=100&page="):
            page = int(route.rsplit("=", 1)[1])
            return deepcopy(self.releases[(page - 1) * 100:page * 100])
        if route == "/releases" and method == "POST":
            release = {**body, "id": len(self.releases) + 1, "assets": []}
            self.releases.append(release)
            return deepcopy(release)
        if route.startswith("/releases/assets/"):
            assert binary
            return self.assets[int(route.rsplit("/", 1)[1])]
        if route.startswith("/releases/"):
            release_id = int(route.rsplit("/", 1)[1])
            release = next(r for r in self.releases if r["id"] == release_id)
            if method == "PATCH":
                release.update(body)
            return deepcopy(release)
        raise AssertionError((route, method))

    def upload(self, tag, path):
        if self.fail_upload_after is not None and len(self.assets) >= self.fail_upload_after:
            raise ValueError("simulated upload interruption")
        release = next(r for r in self.releases if r["tag_name"] == tag and r["draft"])
        asset_id = len(self.assets) + 1
        self.assets[asset_id] = path.read_bytes()
        release["assets"].append({"id": asset_id, "name": path.name, "size": path.stat().st_size})


def test_real_draft_lookup_and_id_reload_publish_first_release(prepared):
    module = publisher()
    directory, version, _ = prepared
    boundary = ReleaseApi(module)
    result = module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    assert result["release"] == "published"
    assert len(boundary.releases) == 1 and not boundary.releases[0]["draft"]
    assert ("repos/InGnIJM/AI-Bagu/releases/1", "GET") in boundary.calls


def test_interrupted_upload_resumes_existing_draft_without_duplicate(prepared):
    module = publisher()
    directory, version, _ = prepared
    boundary = ReleaseApi(module)
    boundary.fail_upload_after = 2
    with pytest.raises(ValueError, match="interruption"):
        module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    original_assets = deepcopy(boundary.assets)
    boundary.fail_upload_after = None
    result = module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    assert result["release"] == "published" and len(boundary.releases) == 1
    assert all(boundary.assets[key] == value for key, value in original_assets.items())


def test_duplicate_draft_tags_are_rejected_before_upload(prepared):
    module = publisher()
    directory, version, _ = prepared
    boundary = ReleaseApi(module)
    for _ in range(2):
        boundary.remote.create_draft("v0.1.0-beta.2", "a" * 40, True, "notes")
    with pytest.raises(ValueError, match="duplicate"):
        module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    assert not boundary.assets


def prepare_environment(tmp_path, monkeypatch, crash_at):
    module = publisher()
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    directory = tmp_path / "dist/android/0.1.0-beta.2/public"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    state = {"builds": 0, "crash_pending": True, "modes": []}

    def run(args, cwd):
        if "-Mode" in args:
            mode = args[args.index("-Mode") + 1]
            state["modes"].append(mode)
            if mode == "Build":
                state["builds"] += 1
                directory.mkdir(parents=True)
                (directory / module.meta.apk_name(version)).write_bytes(f"synthetic-build-{state['builds']}".encode())
                module.meta.write_metadata(directory, version, "notes", "2026-08-28T00:00:00Z")
                if crash_at == "build" and state["crash_pending"]:
                    state["crash_pending"] = False
                    raise OSError("simulated build interruption")
        return SimpleNamespace(returncode=0)

    def preflight():
        if crash_at == "final_preflight" and state["builds"] and state["crash_pending"]:
            state["crash_pending"] = False
            raise OSError("simulated receipt interruption")
        return version, "a" * 40

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "local_preflight", preflight)
    return module, directory, version, state


@pytest.mark.parametrize("crash_at", ["build", "final_preflight"])
def test_prepare_rebuilds_interrupted_unproven_artifact_and_retains_original(tmp_path, monkeypatch, crash_at):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, crash_at)
    with pytest.raises(OSError, match="interruption"):
        module.prepare(directory, version, "a" * 40)
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    module.prepare(directory, version, "a" * 40)
    assert state["builds"] == 2
    retained = list(directory.parent.glob("public.interrupted-*"))
    assert len(retained) == 1
    assert {p.name: p.read_bytes() for p in retained[0].iterdir()} == original
    assert (directory / module.meta.apk_name(version)).read_bytes() == b"synthetic-build-2"
    module.verify_receipt(directory, "a" * 40)
    module.prepare(directory, version, "a" * 40)
    assert state["builds"] == 2 and state["modes"][-2:] == ["Check", "Verify"]


def test_prepare_rejects_unowned_existing_output_without_moving_it(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, None)
    directory.mkdir(parents=True)
    stale = directory / "old.apk"
    stale.write_bytes(b"unproven previous output")
    with pytest.raises(ValueError, match="provenance|receipt|unowned"):
        module.prepare(directory, version, "a" * 40)
    assert stale.read_bytes() == b"unproven previous output" and state["builds"] == 0


def test_prepare_rejects_journal_from_another_commit(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, "build")
    with pytest.raises(OSError):
        module.prepare(directory, version, "a" * 40)
    with pytest.raises(ValueError, match="commit|provenance"):
        module.prepare(directory, version, "b" * 40)
    assert state["builds"] == 1 and directory.is_dir()
    assert not list(directory.parent.glob("public.interrupted-*"))


def test_feed_retry_allows_newer_other_channel_release_but_new_publish_does_not(prepared):
    module = publisher()
    _, version, _ = prepared
    boundary = ReleaseApi(module)
    boundary.remote.create_draft("v0.1.0-beta.2", "a" * 40, True, "notes")
    boundary.releases[0]["draft"] = False
    boundary.remote.create_draft("v0.1.0", "b" * 40, False, "notes")
    boundary.releases[1]["draft"] = False
    boundary.releases[1]["assets"] = [{"id": 1, "name": "update.json", "size": 34}]
    boundary.assets[1] = b'{"release": {"versionCode": 3}}'
    with pytest.raises(ValueError, match="newer"):
        boundary.remote.remote_preflight(version, "a" * 40)
    boundary.remote.remote_preflight(version, "a" * 40, recover_published=True)
    assert all(method == "GET" for _, method in boundary.calls[2:])


@pytest.mark.parametrize("condition", ["missing", "draft", "wrong_commit", "wrong_channel"])
def test_feed_retry_requires_exact_published_release(prepared, condition):
    module = publisher()
    _, version, _ = prepared
    boundary = ReleaseApi(module)
    if condition != "missing":
        boundary.remote.create_draft("v0.1.0-beta.2", "b" * 40 if condition == "wrong_commit" else "a" * 40,
                                     condition != "wrong_channel", "notes")
        boundary.releases[0]["draft"] = condition == "draft"
    with pytest.raises(ValueError, match="published|commit|channel"):
        boundary.remote.remote_preflight(version, "a" * 40, recover_published=True)


def test_duplicate_tag_on_later_page_is_not_missed(prepared):
    module = publisher()
    directory, version, _ = prepared
    boundary = ReleaseApi(module)
    boundary.remote.create_draft("v0.1.0-beta.2", "a" * 40, True, "notes")
    for index in range(99):
        boundary.remote.create_draft(f"unrelated-{index}", "b" * 40, True, "notes")
    boundary.remote.create_draft("v0.1.0-beta.2", "a" * 40, True, "notes")
    with pytest.raises(ValueError, match="duplicate"):
        module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    assert not boundary.assets


def test_completed_receipt_tampering_is_not_rebuilt_or_reblessed(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, None)
    module.prepare(directory, version, "a" * 40)
    apk = directory / module.meta.apk_name(version)
    apk.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        module.prepare(directory, version, "a" * 40)
    assert state["builds"] == 1 and apk.read_bytes() == b"tampered"
    assert not list(directory.parent.glob("public.interrupted-*"))


def test_interrupted_output_symlink_is_never_moved(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, "build")
    with pytest.raises(OSError):
        module.prepare(directory, version, "a" * 40)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    # A resolved-path mismatch also covers Windows junctions, unlike is_symlink.
    original_resolve = Path.resolve
    def redirected_resolve(path, *args, **kwargs):
        if path == directory:
            return outside
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected_resolve)
    with pytest.raises(ValueError, match="symlinks|junctions"):
        module.prepare(directory, version, "a" * 40)
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert state["builds"] == 1 and directory.exists()


def test_interrupted_output_retention_never_overwrites_a_sibling(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, "build")
    with pytest.raises(OSError):
        module.prepare(directory, version, "a" * 40)
    retained = directory.with_name("public.interrupted-" + "c" * 32)
    retained.mkdir()
    sentinel = retained / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex="c" * 32))
    with pytest.raises(ValueError, match="overwrite"):
        module.prepare(directory, version, "a" * 40)
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert state["builds"] == 1 and directory.exists()


def test_interrupted_receipt_write_does_not_certify_output_and_retry_rebuilds(tmp_path, monkeypatch):
    module, directory, version, state = prepare_environment(tmp_path, monkeypatch, None)
    original_replace = module.os.replace
    def fail_receipt_write(source, target):
        if target.name == "verification.json":
            raise OSError("simulated receipt interruption")
        return original_replace(source, target)
    monkeypatch.setattr(module.os, "replace", fail_receipt_write)
    with pytest.raises(OSError, match="interruption"):
        module.prepare(directory, version, "a" * 40)
    assert not (directory.parent / "verification.json").exists()
    monkeypatch.setattr(module.os, "replace", original_replace)
    module.prepare(directory, version, "a" * 40)
    assert state["builds"] == 2
    module.verify_receipt(directory, "a" * 40)


@pytest.mark.parametrize("newer_target_feed", [False, True])
def test_feed_cli_repairs_exact_release_without_remote_writes_or_channel_downgrade(tmp_path, monkeypatch, newer_target_feed):
    module, directory, version, _ = prepare_environment(tmp_path, monkeypatch, None)
    module.prepare(directory, version, "a" * 40)
    boundary = ReleaseApi(module)
    module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    boundary.remote.create_draft("v0.1.0", "b" * 40, False, "newer stable")
    boundary.releases[1]["draft"] = False
    feed = json.loads((directory / "update.json").read_text(encoding="utf-8"))
    stable = b'{"schema_version":1,"channel":"stable","release":null}\n'
    files = {"updates/stable.json": stable}
    if newer_target_feed:
        newer = deepcopy(feed)
        newer["release"]["versionCode"] = 3
        files["updates/beta.json"] = json.dumps(newer).encode()
    original_releases = deepcopy(boundary.releases)
    original_assets = deepcopy(boundary.assets)
    boundary.calls.clear()
    monkeypatch.setattr(module, "GitHub", lambda: boundary.remote)
    monkeypatch.setattr(module, "command", lambda *args, **kwargs: b"")
    monkeypatch.setattr(module, "verify_public_assets", lambda *args: None)
    monkeypatch.setattr(module, "verify_live_feeds", lambda *args: None)
    updated = []
    def update_feed(remote, candidate):
        merged = module.merge_feed_files(files, candidate)
        updated.append(merged)
        return merged
    monkeypatch.setattr(module, "update_feed", update_feed)
    monkeypatch.setattr(sys, "argv", ["release_github.py", "feed", "--execute",
        "--confirm-repository", "InGnIJM/AI-Bagu", "--confirm-version", "0.1.0-beta.2"])
    assert module.main() == (2 if newer_target_feed else 0)
    assert boundary.releases == original_releases and boundary.assets == original_assets
    assert all(method == "GET" for _, method in boundary.calls)
    if newer_target_feed:
        assert updated == []
    else:
        assert updated[0]["updates/stable.json"] == stable
        assert json.loads(updated[0]["updates/beta.json"]) == feed


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503, None])
def test_github_errors_keep_only_actual_status(monkeypatch, status):
    module = publisher()
    remote = object.__new__(module.GitHub)
    output = b"" if status is None else f"HTTP/2.0 {status} Failure\r\nX-Secret: sk-test\r\n\r\n".encode() + b'{"message":"private-data"}'
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=output, stderr=b"credentials sk-test private-data"))
    with pytest.raises(ValueError) as error:
        remote.api("repos/InGnIJM/AI-Bagu")
    assert getattr(error.value, "status", "missing") == status
    assert "sk-test" not in str(error.value) and "private-data" not in str(error.value)
    assert str(status) in str(error.value) if status is not None else "no response" in str(error.value)


def test_github_optional_404_is_explicit_and_binary_keeps_raw_bytes(monkeypatch):
    module = publisher()
    remote = object.__new__(module.GitHub)
    requests = []
    def run(args, **kwargs):
        requests.append(args)
        if "application/octet-stream" in args[-1]:
            assert "--include" not in args
            return SimpleNamespace(returncode=0, stdout=b"\x00apk\xff\r\n", stderr=b"")
        assert "--include" in args
        return SimpleNamespace(returncode=1, stdout=b"HTTP/2.0 404 Not Found\r\n\r\n{}", stderr=b"do not echo")
    monkeypatch.setattr(module.subprocess, "run", run)
    assert remote.api("asset", binary=True) == b"\x00apk\xff\r\n"
    assert remote.api("ref", optional=True) is None
    with pytest.raises(ValueError):
        remote.api("ref")


def test_github_success_parses_headers_without_exposing_them(monkeypatch):
    module = publisher()
    remote = object.__new__(module.GitHub)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b'HTTP/2.0 200 OK\nX-Secret: sk-test\n\n{"ok":true}', stderr=b""))
    assert remote.api("repo") == {"ok": True}


def test_github_timeout_is_safe_no_response(monkeypatch):
    module = publisher()
    remote = object.__new__(module.GitHub)
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret command", 180, output=b"sk-test")
    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="no response") as error:
        remote.api("repo", optional=True)
    assert error.value.status is None and "sk-test" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("versionCode", True), ("size", 0), ("size", 128 * 1024 * 1024 + 1),
    ("minSdk", 28), ("minSdk", True), ("distribution", "internal"),
    ("packageName", "evil.package"), ("abi", "x86"), ("sha256", "invalid"),
    ("apkUrl", "https://evil.test/app.apk"), ("releaseUrl", "https://evil.test/"),
    ("publishedAt", "2026-02-30T00:00:00Z"), ("notes", ""),
    pytest.param("notes", "\U00020000" * 6001, id="notes-too-long"), ("extra", "not allowed"),
])
def test_existing_feed_validates_complete_release_schema(prepared, field, value):
    module = publisher()
    _, _, feed = prepared
    corrupt = deepcopy(feed)
    corrupt["release"][field] = value
    candidate = deepcopy(feed)
    candidate["release"]["versionCode"] = 3
    with pytest.raises(ValueError):
        module.merge_feed_files({"updates/beta.json": json.dumps(corrupt, ensure_ascii=False).encode()}, candidate)


@pytest.mark.parametrize("raw", [
    b'{"schema_version":true,"channel":"beta","release":null}',
    b'{"schema_version":1,"channel":"beta","release":null,"release":null}',
    b'{"schema_version":1,"channel":"beta","release":[]}',
    b'[]', b'null', b'not json',
])
def test_existing_feed_rejects_invalid_envelope_and_duplicate_fields(prepared, raw):
    module = publisher()
    with pytest.raises(ValueError):
        module.merge_feed_files({"updates/beta.json": raw}, prepared[2])


def empty_feeds():
    return {".nojekyll": b"", "updates/beta.json": b'{"schema_version":1,"channel":"beta","release":null}\n',
            "updates/stable.json": b'{"schema_version":1,"channel":"stable","release":null}\r\n'}


class FeedApi(ReleaseApi):
    """In-memory Git data boundary; no source checkout or network is involved."""
    def __init__(self, module, files=None):
        super().__init__(module)
        self.module = module
        self.repo = {"full_name": "InGnIJM/AI-Bagu", "private": False, "archived": False,
                     "default_branch": "main", "permissions": {"pull": True, "push": True}}
        self.pages = {"source": {"branch": "codex/update-feed", "path": "/"}}
        self.blobs = {}
        self.trees = {}
        self.commits = {"c" * 40: {"sha": "c" * 40, "tree": {"sha": "d" * 40}}}
        self.trees["d" * 40] = {"sha": "d" * 40, "tree": [{"path": "private-source.py", "type": "blob"}], "truncated": False}
        self.head = None
        if files is not None:
            self.head = "e" * 40
            self.commits[self.head] = {"sha": self.head, "tree": {"sha": "f" * 40}}
            self.trees["f" * 40] = self.store_tree(files, "f" * 40)
        self.writes = []
        self.failure = None
        self.conflict = False

    def store_tree(self, files, sha):
        entries = []
        if any(name.startswith("updates/") for name in files):
            entries.append({"path": "updates", "type": "tree", "mode": "040000", "sha": "1" * 40})
        for name, data in files.items():
            blob_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            self.blobs[blob_sha] = {"sha": blob_sha, "encoding": "base64", "size": len(data),
                                    "content": base64.b64encode(data).decode()}
            entries.append({"path": name, "type": "blob", "mode": "100644", "size": len(data), "sha": blob_sha})
        return {"sha": sha, "tree": entries, "truncated": False}

    def files(self):
        tree = self.trees[self.commits[self.head]["tree"]["sha"]]
        return {entry["path"]: base64.b64decode(self.blobs[entry["sha"]]["content"])
                for entry in tree["tree"] if entry["type"] == "blob"}

    def api(self, path, method="GET", body=None, optional=False, binary=False):
        route = path.removeprefix(self.remote.prefix)
        if self.failure and route == self.failure[0]:
            self.calls.append((path, method))
            raise self.module.GitHubError(self.failure[1])
        if route == "" or route.startswith(("/git/commits", "/git/trees", "/git/blobs", "/git/ref/heads/", "/git/refs")) or route == "/pages":
            self.calls.append((path, method))
            if method != "GET":
                self.writes.append((route, method, deepcopy(body)))
            if route == "":
                return deepcopy(self.repo)
            if route == "/pages":
                assert method == "GET", "Pages settings must never be changed"
                return deepcopy(self.pages)
            if route == "/git/ref/heads/main":
                return {"object": {"type": "commit", "sha": "c" * 40}}
            if route == "/git/ref/heads/codex/update-feed":
                if self.head is None:
                    assert optional
                    return None
                return {"object": {"type": "commit", "sha": self.head}}
            if route.startswith("/git/commits/"):
                return deepcopy(self.commits[route.rsplit("/", 1)[1]])
            if route.startswith("/git/trees/"):
                return deepcopy(self.trees[route.rsplit("/", 1)[1].split("?")[0]])
            if route.startswith("/git/blobs/"):
                return deepcopy(self.blobs[route.rsplit("/", 1)[1]])
            if route == "/git/trees" and method == "POST":
                assert "base_tree" not in body, "must not copy source or unexpected tree files"
                files = {e["path"]: e["content"].encode() if "content" in e else base64.b64decode(self.blobs[e["sha"]]["content"])
                         for e in body["tree"]}
                sha = "2" * 40
                self.trees[sha] = self.store_tree(files, sha)
                return {"sha": sha}
            if route == "/git/commits" and method == "POST":
                sha = "3" * 40
                self.commits[sha] = {"sha": sha, "tree": {"sha": body["tree"]}, "parents": body["parents"]}
                return {"sha": sha}
            if route in ("/git/refs", "/git/refs/heads/codex/update-feed"):
                if self.conflict:
                    raise self.module.GitHubError(409)
                if method == "PATCH":
                    assert body["force"] is False
                else:
                    assert method == "POST" and self.head is None
                    assert body["ref"] == "refs/heads/codex/update-feed"
                self.head = body["sha"]
                return {"object": {"type": "commit", "sha": self.head}}
            raise AssertionError((route, method))
        return super().api(path, method, body, optional, binary)


def init_cli(module, tmp_path, monkeypatch, boundary, extra=()):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "GitHub", lambda: boundary.remote)
    def run(args, **kwargs):
        assert args == ["git", "remote", "get-url", "origin"], "initialization must not require clean/pushed/versioned source"
        return SimpleNamespace(returncode=0, stdout=b"https://github.com/InGnIJM/AI-Bagu.git\n", stderr=b"")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["release_github.py", "init-feed", *extra])
    return module.main()


def test_init_dry_run_ignores_dirty_checkout_and_uses_no_tools(tmp_path, monkeypatch, capsys):
    module = publisher()
    (tmp_path / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    def forbidden(*a, **k):
        pytest.fail("offline initialization dry-run must not invoke tools/credentials")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module.shutil, "which", forbidden)
    monkeypatch.setattr(sys, "argv", ["release_github.py", "init-feed"])
    assert module.main() == 0
    assert list(tmp_path.iterdir()) == [tmp_path / "dirty.txt"]
    assert "dry" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("confirm", [[], ["--confirm-repository", "other/repo"]])
def test_init_execute_requires_exact_confirmation_before_tools(tmp_path, monkeypatch, confirm):
    module = publisher()
    boundary = FeedApi(module)
    assert init_cli(module, tmp_path, monkeypatch, boundary, ["--execute", *confirm]) == 1
    assert not boundary.calls and not boundary.writes


@pytest.mark.parametrize("state", ["missing", "partial", "ready"])
def test_init_adds_only_missing_feeds_preserving_original_bytes(tmp_path, monkeypatch, prepared, state):
    module = publisher()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    files = empty_feeds()
    files["updates/beta.json"] = json.dumps(prepared[2], ensure_ascii=False, indent=4).encode() + b"\r\n"
    if state == "partial":
        del files["updates/stable.json"]
    boundary = FeedApi(module, None if state == "missing" else files)
    before = deepcopy(files)
    assert init_cli(module, checkout, monkeypatch, boundary,
                    ["--execute", "--confirm-repository", "InGnIJM/AI-Bagu"]) == 0
    after = boundary.files()
    assert set(after) == {".nojekyll", "updates/beta.json", "updates/stable.json"}
    if state == "missing":
        assert boundary.commits[boundary.head]["parents"] == []
        assert json.loads(after["updates/beta.json"])["release"] is None
    else:
        assert all(after[name] == data for name, data in before.items())
    if state == "ready":
        assert not boundary.writes
    assert all("/pages" not in path and "/releases" not in path for path, _ in boundary.calls)
    assert not list(checkout.iterdir())


@pytest.mark.parametrize("field,value", [("private", True), ("archived", True),
    ("full_name", "other/repo"), ("permissions", {"pull": True, "push": False}),
    ("permissions", {"pull": False, "push": True}), ("permissions", {})])
def test_init_rejects_unsafe_repository_before_writes(field, value):
    module = publisher()
    boundary = FeedApi(module)
    boundary.repo[field] = value
    with pytest.raises(ValueError):
        module.initialize_feed(boundary.remote)
    assert not boundary.writes


@pytest.mark.parametrize("route,status", [("", 404), ("/git/ref/heads/main", 404),
    ("/git/commits/" + "c" * 40, 404), ("/git/trees/" + "d" * 40, 404),
    ("/git/ref/heads/codex/update-feed", 403), ("/git/ref/heads/codex/update-feed", None)])
def test_init_never_interprets_permission_or_network_failure_as_missing(route, status):
    module = publisher()
    boundary = FeedApi(module)
    boundary.failure = (route, status)
    with pytest.raises(ValueError):
        module.initialize_feed(boundary.remote)
    assert not boundary.writes
    if route != "/git/ref/heads/codex/update-feed":
        assert not any(path.endswith("codex/update-feed") for path, _ in boundary.calls)


@pytest.mark.parametrize("corruption", ["extra", "symlink", "directory-mode", "duplicate", "truncated", "many", "oversized", "blob-oversized", "blob-size", "blob-base64", "schema"])
def test_init_rejects_untrusted_tree_and_blob_without_writes(corruption):
    module = publisher()
    boundary = FeedApi(module, empty_feeds())
    tree = boundary.trees["f" * 40]
    entry = next(e for e in tree["tree"] if e["path"] == "updates/beta.json")
    blob = boundary.blobs[entry["sha"]]
    if corruption == "extra":
        entry["path"] = "private.db"
    elif corruption == "symlink":
        entry["mode"] = "120000"
    elif corruption == "directory-mode":
        tree["tree"][0]["mode"] = "160000"
    elif corruption == "duplicate":
        tree["tree"].append(deepcopy(entry))
    elif corruption == "truncated":
        tree["truncated"] = True
    elif corruption == "many":
        tree["tree"] *= 100
    elif corruption == "oversized":
        entry["size"] = 65537
    elif corruption == "blob-oversized":
        blob["content"] = base64.b64encode(b"x" * 65537).decode()
    elif corruption == "blob-size":
        blob["size"] += 1
    elif corruption == "blob-base64":
        blob["content"] = "??"
    elif corruption == "schema":
        invalid = b'{"schema_version":1,"channel":"beta","release":{"versionCode":2}}'
        blob["content"] = base64.b64encode(invalid).decode()
        entry["size"] = blob["size"] = len(invalid)
    with pytest.raises(ValueError):
        module.initialize_feed(boundary.remote)
    assert not boundary.writes


@pytest.mark.parametrize("exists", [False, True])
def test_init_concurrent_branch_conflict_never_forces_or_retries(exists):
    module = publisher()
    boundary = FeedApi(module, {".nojekyll": b""} if exists else None)
    boundary.conflict = True
    old = boundary.head
    with pytest.raises(ValueError, match="409"):
        module.initialize_feed(boundary.remote)
    assert boundary.head == old
    assert len([w for w in boundary.writes if w[0].startswith("/git/refs")]) == 1


@pytest.mark.parametrize("problem", ["missing-config", "wrong-branch", "wrong-root", "missing-file", "malformed", "wrong-release", "oversized", "http404"])
def test_pages_readiness_requires_config_and_both_valid_matching_feeds(monkeypatch, prepared, problem):
    module = publisher()
    files = empty_feeds()
    files["updates/beta.json"] = json.dumps(prepared[2]).encode()
    boundary = FeedApi(module, files)
    served = dict(files)
    if problem == "missing-config":
        boundary.pages = None
    elif problem == "wrong-branch":
        boundary.pages["source"]["branch"] = "main"
    elif problem == "wrong-root":
        boundary.pages["source"]["path"] = "/docs"
    elif problem == "missing-file":
        boundary = FeedApi(module, {"updates/beta.json": files["updates/beta.json"]})
    elif problem == "malformed":
        served["updates/stable.json"] = b'{"schema_version":1,"channel":"stable","release":{}}'
    elif problem == "wrong-release":
        other = deepcopy(prepared[2])
        other["release"]["versionCode"] = 3
        served["updates/beta.json"] = json.dumps(other).encode()
    elif problem == "oversized":
        served["updates/stable.json"] += b" " * 65536
    def download(url, limit, pages=False):
        assert pages and limit == 65536
        assert url in ("https://ingnijm.github.io/AI-Bagu/updates/beta.json", "https://ingnijm.github.io/AI-Bagu/updates/stable.json")
        if problem == "http404":
            raise OSError("anonymous HTTP 404")
        return served["updates/" + url.rsplit("/", 1)[1]]
    monkeypatch.setattr(module, "anonymous_download", download)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(ValueError):
        module.verify_pages_ready(boundary.remote)
    assert not boundary.writes


@pytest.mark.parametrize("build_type", ["missing", "legacy"])
def test_pages_readiness_accepts_complete_unchanged_branch(monkeypatch, build_type):
    module = publisher()
    files = empty_feeds()
    boundary = FeedApi(module, files)
    if build_type != "missing":
        boundary.pages["build_type"] = build_type
    downloaded = []
    def download(url, limit, pages=False):
        downloaded.append(url)
        return files["updates/" + url.rsplit("/", 1)[1]]
    monkeypatch.setattr(module, "anonymous_download", download)
    module.verify_pages_ready(boundary.remote)
    assert downloaded == ["https://ingnijm.github.io/AI-Bagu/updates/beta.json", "https://ingnijm.github.io/AI-Bagu/updates/stable.json"]
    assert not boundary.writes


def release_cli_environment(tmp_path, monkeypatch, stage):
    module = publisher()
    version = {"versionName": "0.1.0-beta.2", "versionCode": 2, "channel": "beta"}
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "version.json").write_text(json.dumps(version), encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\nPermission is hereby granted", encoding="utf-8")
    directory = tmp_path / "dist/android/0.1.0-beta.2/public"
    directory.mkdir(parents=True)
    (directory / module.meta.apk_name(version)).write_bytes(b"synthetic apk")
    module.meta.write_metadata(directory, version, "notes", "2026-08-28T00:00:00Z")
    (directory.parent / "verification.json").write_text(json.dumps({"commit": "a" * 40,
        "assets": {p.name: module.meta.file_hash(p) for p in directory.iterdir()},
        "checks": ["pytest", "node", "public-build-unit-lint"]}), encoding="utf-8")
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        if args[:1] == ["git"]:
            responses = {("status", "--porcelain", "--untracked-files=all"): b"",
                ("remote", "get-url", "origin"): b"https://github.com/InGnIJM/AI-Bagu.git\n",
                ("ls-files",): b"LICENSE\nversion.json\nbagu.py\n", ("rev-parse", "HEAD"): b"a" * 40}
            return SimpleNamespace(returncode=0, stdout=responses[tuple(args[1:])], stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    boundary = FeedApi(module, empty_feeds())
    monkeypatch.setattr(module, "GitHub", lambda: boundary.remote)
    monkeypatch.setattr(sys, "argv", ["release_github.py", stage, "--execute", "--confirm-repository", "InGnIJM/AI-Bagu",
                                     "--confirm-version", "0.1.0-beta.2"])
    return module, boundary, directory, version, commands


@pytest.mark.parametrize("stage", ["preflight", "prepare", "publish"])
def test_release_stage_checks_pages_before_build_or_public_mutation(tmp_path, monkeypatch, stage, capsys):
    module, boundary, _, _, commands = release_cli_environment(tmp_path, monkeypatch, stage)
    def unavailable(*args, **kwargs):
        raise OSError("anonymous feeds unavailable")
    monkeypatch.setattr(module, "anonymous_download", unavailable)
    boundary.pages["source"]["branch"] = "main"
    assert module.main() == 1
    assert "Pages" in capsys.readouterr().err
    assert all(args[0] == "git" for args in commands)
    assert not boundary.writes and not boundary.releases and not boundary.assets


def test_feed_cli_repairs_before_pages_readiness_and_preserves_other_channel(tmp_path, monkeypatch):
    module, boundary, directory, version, _ = release_cli_environment(tmp_path, monkeypatch, "feed")
    module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    original_releases = deepcopy(boundary.releases)
    stable = boundary.files()["updates/stable.json"]
    feed = json.loads((directory / "update.json").read_text(encoding="utf-8"))
    served_pages = []
    def download(url, limit, pages=False):
        if pages:
            # Before repair the deployed beta feed is broken. A pre-check here
            # would block recovery, so prove the branch was repaired first.
            assert json.loads(boundary.files()["updates/beta.json"]) == feed
            served_pages.append(url)
            return boundary.files()["updates/" + url.rsplit("/", 1)[1]]
        return (directory / url.rsplit("/", 1)[1]).read_bytes()
    monkeypatch.setattr(module, "anonymous_download", download)
    assert module.main() == 0
    assert boundary.files()["updates/stable.json"] == stable
    assert len(served_pages) == 2 and boundary.releases == original_releases


def test_release_published_with_pages_failure_returns_partial_without_deletion(tmp_path, monkeypatch, capsys):
    module, boundary, directory, _, _ = release_cli_environment(tmp_path, monkeypatch, "publish")
    def download(url, limit, pages=False):
        if pages:
            if boundary.releases:
                raise OSError("Pages deployment pending")
            return empty_feeds()["updates/" + url.rsplit("/", 1)[1]]
        return (directory / url.rsplit("/", 1)[1]).read_bytes()
    monkeypatch.setattr(module, "anonymous_download", download)
    assert module.main() == 2
    assert "PARTIAL" in capsys.readouterr().err
    assert len(boundary.releases) == 1 and boundary.releases[0]["draft"] is False
    assert len(boundary.assets) == 6
    assert all(method != "DELETE" for _, method in boundary.calls)


def test_release_success_requires_reloading_published_state(prepared):
    module = publisher()
    directory, version, _ = prepared
    boundary = ReleaseApi(module)
    original_api = boundary.remote.api
    def api(path, method="GET", body=None, **kwargs):
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": "a" * 40}}
        if path.endswith("/releases/1") and method == "PATCH":
            return deepcopy(boundary.releases[0])  # publication did not become visible
        return original_api(path, method, body, **kwargs)
    boundary.remote.api = api
    with pytest.raises(ValueError, match="published"):
        module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)


@pytest.mark.parametrize("value", [None, [], {}, {"schema_version": 1, "channel": "beta", "release": None}])
def test_publishing_feed_requires_a_valid_nonempty_candidate(value):
    module = publisher()
    with pytest.raises(ValueError):
        module.merge_feed_files(empty_feeds(), value)


def test_init_wrong_origin_cannot_access_github(tmp_path, monkeypatch):
    module = publisher()
    boundary = FeedApi(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "GitHub", lambda: boundary.remote)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b"https://github.com/other/repo.git", stderr=b""))
    monkeypatch.setattr(sys, "argv", ["release_github.py", "init-feed", "--execute", "--confirm-repository", "InGnIJM/AI-Bagu"])
    assert module.main() == 1 and not boundary.calls


@pytest.mark.parametrize("when", ["during-read", "after-commit"])
def test_init_detects_branch_movement_before_reference_write(when):
    module = publisher()
    boundary = FeedApi(module, empty_feeds() if when == "during-read" else {".nojekyll": b""})
    original_api = boundary.remote.api
    def api(path, method="GET", body=None, **kwargs):
        result = original_api(path, method, body, **kwargs)
        if (when == "during-read" and "/git/blobs/" in path
                or when == "after-commit" and path.endswith("/git/commits") and method == "POST"):
            boundary.head = "9" * 40
        return result
    boundary.remote.api = api
    with pytest.raises(ValueError, match="concurrent"):
        module.initialize_feed(boundary.remote)
    assert not any(route.startswith("/git/refs") for route, _, _ in boundary.writes)


@pytest.mark.parametrize("status", [401, 403, 429, 500, None])
def test_optional_api_does_not_hide_other_statuses(monkeypatch, status):
    module = publisher()
    remote = object.__new__(module.GitHub)
    raw = b"" if status is None else f"HTTP/2.0 {status} Failure\r\n\r\n{{}}".encode()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=raw, stderr=b"sk-test"))
    with pytest.raises(module.GitHubError) as error:
        remote.api("ref", optional=True)
    assert error.value.status == status


def test_command_failure_never_echoes_tool_output(monkeypatch):
    module = publisher()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=b"private source", stderr=b"sk-test"))
    with pytest.raises(ValueError) as error:
        module.command(["gh", "release", "upload"])
    assert "private source" not in str(error.value) and "sk-test" not in str(error.value)


def test_preflight_success_requires_both_anonymous_feeds(tmp_path, monkeypatch, capsys):
    module, boundary, _, _, commands = release_cli_environment(tmp_path, monkeypatch, "preflight")
    downloaded = []
    def download(url, limit, pages=False):
        assert pages and limit == 65536
        downloaded.append(url)
        return empty_feeds()["updates/" + url.rsplit("/", 1)[1]]
    monkeypatch.setattr(module, "anonymous_download", download)
    assert module.main() == 0
    assert len(downloaded) == 2 and "Pages" in capsys.readouterr().out
    assert not boundary.writes and all(args[0] == "git" for args in commands)


def test_invalid_github_json_keeps_status_without_response_text(monkeypatch):
    module = publisher()
    remote = object.__new__(module.GitHub)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b"HTTP/2.0 200 OK\r\n\r\nsk-test private-body", stderr=b""))
    with pytest.raises(ValueError) as error:
        remote.api("repo")
    assert getattr(error.value, "status", None) == 200
    assert "sk-test" not in str(error.value) and "private-body" not in str(error.value)


def test_partial_feed_failure_reports_safe_github_status(tmp_path, monkeypatch, capsys):
    module, boundary, directory, version, _ = release_cli_environment(tmp_path, monkeypatch, "feed")
    module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    boundary.failure = ("/pages", 403)
    monkeypatch.setattr(module, "anonymous_download", lambda url, limit, pages=False: (directory / url.rsplit("/", 1)[1]).read_bytes())
    assert module.main() == 2
    assert "HTTP 403" in capsys.readouterr().err


@pytest.mark.parametrize("version_name", ["\u0661.2.3-beta.1", "1.\uff12.3-beta.1", "1.2.\u0969-beta.1", "1.2.3-beta.\u0661"],
                         ids=["major-arabic", "minor-fullwidth", "patch-devanagari", "beta-arabic"])
def test_init_rejects_non_ascii_version_digits_with_matching_urls(prepared, version_name):
    module = publisher()
    feed = deepcopy(prepared[2])
    feed["release"]["versionName"] = version_name
    feed["release"]["apkUrl"] = (
        f"https://github.com/InGnIJM/AI-Bagu/releases/download/v{version_name}/"
        f"bagu-{version_name}-public-arm64-v8a.apk")
    feed["release"]["releaseUrl"] = f"https://github.com/InGnIJM/AI-Bagu/releases/tag/v{version_name}"
    files = empty_feeds()
    files["updates/beta.json"] = json.dumps(feed, ensure_ascii=False).encode("utf-8")
    boundary = FeedApi(module, files)
    with pytest.raises(ValueError, match="versionName"):
        module.initialize_feed(boundary.remote)
    assert not boundary.writes


def test_pages_api_404_remains_an_http_error_not_a_configuration_hint(monkeypatch):
    module = publisher()
    remote = object.__new__(module.GitHub)
    remote.prefix = "repos/InGnIJM/AI-Bagu"
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=b'HTTP/2.0 404 Not Found\r\nX-Secret: sk-test\r\n\r\n{"message":"private"}', stderr=b"sk-test"))
    with pytest.raises(module.GitHubError) as error:
        module.require_pages_source(remote)
    assert error.value.status == 404
    assert "configure" not in str(error.value) and "sk-test" not in str(error.value)


def anonymous_failure(status):
    if status is None:
        return URLError("private path sk-test https://private.example/")
    return HTTPError("https://private.example/?key=sk-test", status, "private reason",
                     {"X-Secret": "sk-test"}, io.BytesIO(b"private body"))


class AnonymousResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status


def anonymous_sequence(module, monkeypatch, outcomes):
    pending = iter(outcomes)
    calls = []
    def open_response(request, timeout):
        calls.append(request.full_url)
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, AnonymousResponse) else AnonymousResponse(result)
    monkeypatch.setattr(module, "build_opener", lambda *args: SimpleNamespace(open=open_response))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return calls


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503, None])
def test_anonymous_boundary_retains_only_status_or_no_response(monkeypatch, status):
    module = publisher()
    anonymous_sequence(module, monkeypatch, [anonymous_failure(status)])
    with pytest.raises(ValueError) as error:
        module.anonymous_download("https://ingnijm.github.io/AI-Bagu/updates/beta.json", 65536, pages=True)
    assert getattr(error.value, "status", "missing") == status
    assert "no response" in str(error.value) if status is None else f"HTTP {status}" in str(error.value)
    assert all(secret not in str(error.value) for secret in ("sk-test", "private", "https://"))
    assert error.value.__suppress_context__


def test_anonymous_unexpected_response_status_is_preserved_without_reading_body(monkeypatch):
    module = publisher()
    response = AnonymousResponse(b"private body", status=503)
    anonymous_sequence(module, monkeypatch, [response])
    with pytest.raises(ValueError) as error:
        module.anonymous_download("https://github.com/InGnIJM/AI-Bagu/releases/download/v1/file", 65536)
    assert getattr(error.value, "status", None) == 503
    assert response.closed and "private" not in str(error.value)


@pytest.mark.parametrize("last_status", [401, 403, 404, 429, 500, 503, None])
def test_live_feed_retries_preserve_only_last_network_status(monkeypatch, last_status):
    module = publisher()
    calls = anonymous_sequence(module, monkeypatch, [anonymous_failure(404), anonymous_failure(last_status)])
    with pytest.raises(ValueError) as error:
        module.verify_live_feeds(empty_feeds(), attempts=2)
    assert getattr(error.value, "status", "missing") == last_status
    assert len(calls) == 2
    assert all(secret not in str(error.value) for secret in ("sk-test", "private", "https://"))


@pytest.mark.parametrize("last_body", [b"invalid JSON", b'{"schema_version":1,"channel":"beta","release":{}}'])
def test_live_feed_content_failure_does_not_reuse_old_http_status(monkeypatch, last_body):
    module = publisher()
    calls = anonymous_sequence(module, monkeypatch, [anonymous_failure(404), last_body])
    with pytest.raises(ValueError) as error:
        module.verify_live_feeds(empty_feeds(), attempts=2)
    assert getattr(error.value, "status", None) is None
    assert "404" not in str(error.value) and len(calls) == 2


def test_live_feed_valid_but_different_content_drops_old_http_status(monkeypatch, prepared):
    module = publisher()
    files = empty_feeds()
    files["updates/beta.json"] = json.dumps(prepared[2]).encode()
    calls = anonymous_sequence(module, monkeypatch, [anonymous_failure(403), empty_feeds()["updates/beta.json"]])
    with pytest.raises(ValueError) as error:
        module.verify_live_feeds(files, attempts=2)
    assert getattr(error.value, "status", None) is None
    assert "403" not in str(error.value) and len(calls) == 2


def test_live_feed_success_does_not_reuse_prior_http_failure(monkeypatch):
    module = publisher()
    files = empty_feeds()
    calls = anonymous_sequence(module, monkeypatch, [anonymous_failure(404), files["updates/beta.json"], files["updates/stable.json"]])
    assert module.verify_live_feeds(files, attempts=2) is None
    assert len(calls) == 3


@pytest.mark.parametrize("point,status", [("assets", 403), ("assets", None), ("pages", 429), ("pages", None)])
def test_partial_anonymous_failure_keeps_release_and_safe_status(tmp_path, monkeypatch, capsys, point, status):
    module, boundary, directory, version, _ = release_cli_environment(tmp_path, monkeypatch, "feed")
    module.publish_release(boundary.remote, directory, version, "a" * 40, execute=True)
    original_releases = deepcopy(boundary.releases)
    outcomes = [] if point == "assets" else [path.read_bytes() for path in directory.iterdir()]
    outcomes.extend(anonymous_failure(status) for _ in range(1 if point == "assets" else 6))
    calls = anonymous_sequence(module, monkeypatch, outcomes)
    assert module.main() == 2
    error = capsys.readouterr().err
    assert "PARTIAL" in error
    assert "no response" in error if status is None else f"HTTP {status}" in error
    assert all(secret not in error for secret in ("sk-test", "private", "https://"))
    assert boundary.releases == original_releases and len(calls) == (1 if point == "assets" else 12)
    assert all(method != "DELETE" for _, method in boundary.calls)


@pytest.mark.parametrize("failure", [TimeoutError("sk-test private timeout"),
    ConnectionError("sk-test private connection"), BadStatusLine("sk-test private status")],
    ids=["timeout", "connect", "bad-status"])
def test_anonymous_transport_errors_are_safe_no_response(monkeypatch, failure):
    module = publisher()
    anonymous_sequence(module, monkeypatch, [failure])
    with pytest.raises(ValueError) as error:
        module.anonymous_download("https://ingnijm.github.io/AI-Bagu/updates/beta.json", 65536, pages=True)
    assert error.value.status is None and "no response" in str(error.value)
    assert "sk-test" not in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("build_type", ["workflow", None, "", "unknown", False])
def test_pages_readiness_rejects_nonbranch_build_type_despite_matching_feeds(monkeypatch, build_type):
    module = publisher()
    files = empty_feeds()
    boundary = FeedApi(module, files)
    boundary.pages["build_type"] = build_type
    anonymous_sequence(module, monkeypatch, [files["updates/beta.json"], files["updates/stable.json"]])
    with pytest.raises(ValueError, match="Pages"):
        module.verify_pages_ready(boundary.remote)
    assert not boundary.writes
