"""Exercise release state transitions using a narrow in-memory GitHub boundary."""
import importlib.util
import json
from pathlib import Path
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def publisher():
    spec = importlib.util.spec_from_file_location("release_github", ROOT / "scripts/release_github.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
