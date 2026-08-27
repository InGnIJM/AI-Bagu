"""Explicit local release workflow. Default: dry-run, no credentials or writes.

prepare --execute builds/signs locally; publish/feed --execute also require exact
repository/version confirmations. No source push, forced references or deletion.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import release_metadata as meta

ROOT = Path(__file__).resolve().parents[1]
FEED_BRANCH = "codex/update-feed"
FEED_ROOT = "https://ingnijm.github.io/AI-Bagu/updates/"
FEED_FILES = {".nojekyll", "updates/beta.json", "updates/stable.json"}


def command(args, cwd=ROOT, data=None, timeout=120):
    result = subprocess.run(args, cwd=cwd, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # Tool output can contain credentials or signed URLs: do not echo it.
        raise ValueError(f"{Path(args[0]).name} failed (exit {result.returncode}); inspect the tool locally")
    return result.stdout


def local_preflight(root=ROOT):
    version = meta.load_version(root / "version.json")
    def git(*args):
        return command(["git", *args], cwd=root).decode("utf-8").strip()
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("dirty checkout: explicitly review and commit source before preparing a release")
    origin = git("remote", "get-url", "origin").removesuffix(".git").lower()
    if origin not in ("https://github.com/ingnijm/ai-bagu", "git@github.com:ingnijm/ai-bagu"):
        raise ValueError("origin does not match confirmed repository")
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "MIT License" not in license_text or "Permission is hereby granted" not in license_text:
        raise ValueError("MIT license missing")
    for name in git("ls-files").splitlines():
        parts = Path(name).parts
        if (name in (".env", "settings.json", "bagu.db") or
                any(p in (".signing", "dist") for p in parts) or
                name.endswith((".bagu-backup", ".db", ".sqlite", ".sqlite3", ".jks", ".keystore", ".key", ".p12", ".pfx"))):
            raise ValueError("tracked private data or generated artifact blocks public release")
    return version, git("rev-parse", "HEAD")


def validate_download_url(url, pages=False):
    parsed = urlsplit(url)
    hosts = {"ingnijm.github.io"} if pages else {"github.com", "release-assets.githubusercontent.com"}
    if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError("unsafe download URL")
    if pages and not parsed.path.startswith("/AI-Bagu/updates/"):
        raise ValueError("unsafe Pages path")


class SafeRedirects(HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, pages):
        self.pages = pages

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl, self.pages)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def anonymous_download(url, limit, pages=False):
    validate_download_url(url, pages)
    request = Request(url, headers={"User-Agent": "Bagu-release-verifier", "Cache-Control": "no-cache"})
    with build_opener(SafeRedirects(pages)).open(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("anonymous download failed")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download exceeds limit")
    return data


class GitHub:
    def __init__(self):
        if not shutil.which("gh"):
            raise ValueError("Install GitHub CLI and complete gh auth login yourself first")
        self.prefix = f"repos/{meta.REPOSITORY}"

    def api(self, path, method="GET", body=None, optional=False, binary=False):
        args = ["gh", "api", path, "--hostname", "github.com", "--method", method,
                "-H", "Accept: " + ("application/octet-stream" if binary else "application/vnd.github+json")]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = meta.json_bytes(body)
        result = subprocess.run(args, cwd=ROOT, input=payload, capture_output=True, timeout=180)
        if result.returncode:
            if optional and b"(HTTP 404)" in result.stderr:
                return None
            raise ValueError(f"GitHub {method} request failed; no automatic destructive retry")
        return result.stdout if binary else json.loads(result.stdout)

    def find_release(self, tag):
        # The tag endpoint only exposes published releases. Authenticated list
        # results also contain drafts, including their pending tag names.
        matches = []
        for page in range(1, 101):
            releases = self.api(f"{self.prefix}/releases?per_page=100&page={page}")
            matches.extend(release for release in releases if release["tag_name"] == tag)
            if len(matches) > 1:
                raise ValueError("duplicate releases use the same tag; resolve them manually")
            if len(releases) < 100:
                return self.get_release(matches[0]["id"]) if matches else None
        raise ValueError("release listing exceeded safety limit")

    def get_release(self, release_id):
        return self.api(f"{self.prefix}/releases/{release_id}")

    def verify_tag(self, tag, commit, allow_missing=False):
        ref = self.api(f"{self.prefix}/git/ref/tags/{tag}", optional=True)
        if ref is None:
            if allow_missing:
                return
            raise ValueError("release tag missing")
        obj = ref["object"]
        for _ in range(5):
            if obj["type"] == "commit":
                if obj["sha"] != commit:
                    raise ValueError("existing tag points to another commit")
                return
            if obj["type"] != "tag":
                break
            obj = self.api(f"{self.prefix}/git/tags/{obj['sha']}")["object"]
        raise ValueError("invalid tag reference")

    def create_draft(self, tag, commit, prerelease, notes):
        return self.api(f"{self.prefix}/releases", "POST", {
            "tag_name": tag, "target_commitish": commit, "name": tag,
            "body": notes, "draft": True, "prerelease": prerelease, "make_latest": "false",
        })

    def upload(self, tag, path):
        command(["gh", "release", "upload", tag, str(path), "--repo", "github.com/" + meta.REPOSITORY])

    def download_asset(self, asset, limit):
        if not 0 < asset["size"] <= limit:
            raise ValueError("remote asset size invalid")
        data = self.api(f"{self.prefix}/releases/assets/{asset['id']}", binary=True)
        if len(data) > limit:
            raise ValueError("remote asset exceeds size limit")
        return data

    def publish_draft(self, release):
        self.api(f"{self.prefix}/releases/{release['id']}", "PATCH", {
            "draft": False, "make_latest": "false" if release["prerelease"] else "true",
        })

    def remote_preflight(self, version, commit, recover_published=False):
        repo = self.api(self.prefix)
        if repo.get("private") is not False or repo.get("archived"):
            raise ValueError("repository must be public and writable; visibility is never changed automatically")
        if self.api(f"{self.prefix}/commits/{commit}")["sha"] != commit:
            raise ValueError("source commit must already exist remotely; this script never pushes source")
        tag = "v" + version["versionName"]
        if recover_published:
            release = self.find_release(tag)
            if not release or release["draft"]:
                raise ValueError("feed requires an exact already-published release")
            validate_release_identity(release, version, commit)
            self.verify_tag(tag, commit)
            # Recovery verifies the existing exact assets later; a newer release
            # in the other channel must not prevent repairing this channel feed.
            # merge_feed_files still forbids target-channel downgrades/conflicts.
            return
        self.verify_tag(tag, commit, allow_missing=True)
        for page in range(1, 101):
            releases = self.api(f"{self.prefix}/releases?per_page=100&page={page}")
            for release in releases:
                if release["tag_name"] == tag or release.get("draft"):
                    continue
                assets = [a for a in release.get("assets", []) if a["name"] == "update.json"]
                if len(assets) != 1:
                    raise ValueError("historical release has no unique update.json; manually resolve its versionCode first")
                old = json.loads(self.download_asset(assets[0], meta.MAX_FEED))
                code = old.get("release", {}).get("versionCode")
                if type(code) is not int or code >= version["versionCode"]:
                    raise ValueError("remote versionCode is unknown, equal or newer")
            if len(releases) < 100:
                return
        raise ValueError("release listing exceeded safety limit")


def validate_release_identity(release, version, commit):
    if release["tag_name"] != "v" + version["versionName"]:
        raise ValueError("existing release tag differs")
    if release["target_commitish"] != commit:
        raise ValueError("existing release commit differs")
    if release["prerelease"] != (version["channel"] == "beta"):
        raise ValueError("existing release channel differs")


def publish_release(remote, directory, version, commit, execute=False, published_only=False):
    paths = meta.validate_directory(directory, version)
    if not execute:
        return {"release": "dry-run", "assets": [p.name for p in paths], "pages": "not-written"}
    tag = "v" + version["versionName"]
    remote.verify_tag(tag, commit, allow_missing=True)
    release = remote.find_release(tag)
    if published_only and (release is None or release["draft"]):
        raise ValueError("feed requires an exact already-published release")
    if release is None:
        release = remote.create_draft(tag, commit, version["channel"] == "beta",
                                      (Path(directory) / "RELEASE_NOTES.md").read_text(encoding="utf-8"))
    validate_release_identity(release, version, commit)
    expected = {p.name: p for p in paths}
    assets = release.get("assets", [])
    if len({a["name"] for a in assets}) != len(assets) or not {a["name"] for a in assets} <= set(expected):
        raise ValueError("remote asset allowlist violation")
    for asset in assets:
        path = expected[asset["name"]]
        if remote.download_asset(asset, path.stat().st_size) != path.read_bytes():
            raise ValueError("existing remote asset differs; refusing overwrite")
    present = {a["name"] for a in assets}
    for name, path in expected.items():
        if name not in present:
            if not release["draft"]:
                raise ValueError("published release is incomplete; refusing mutation")
            remote.upload(tag, path)
    release = remote.get_release(release["id"])
    validate_release_identity(release, version, commit)
    if published_only and release["draft"]:
        raise ValueError("feed requires an exact already-published release")
    assets = release["assets"]
    if {a["name"] for a in assets} != set(expected) or len(assets) != len(expected):
        raise ValueError("uploaded asset set differs")
    for asset in assets:
        path = expected[asset["name"]]
        if remote.download_asset(asset, path.stat().st_size) != path.read_bytes():
            raise ValueError("uploaded asset hash/content verification failed")
    if release["draft"]:
        remote.publish_draft(release)
    remote.verify_tag(tag, commit)
    return {"release": "published", "releaseUrl": f"https://github.com/{meta.REPOSITORY}/releases/tag/{tag}", "pages": "not-written"}


def merge_feed_files(existing, feed):
    if not set(existing) <= FEED_FILES:
        raise ValueError("feed branch contains non-allowlisted files")
    result = dict(existing)
    for channel in ("beta", "stable"):
        name = f"updates/{channel}.json"
        if name in result:
            if len(result[name]) > meta.MAX_FEED:
                raise ValueError("existing feed too large")
            previous = json.loads(result[name])
            if (set(previous) != {"schema_version", "channel", "release"} or previous["schema_version"] != 1
                    or previous["channel"] != channel):
                raise ValueError("existing feed invalid")
        else:
            previous = {"schema_version": 1, "channel": channel, "release": None}
            result[name] = meta.json_bytes(previous)
        if channel == feed["channel"]:
            old = previous["release"]
            if old is not None:
                old_code = old.get("versionCode")
                new_code = feed["release"]["versionCode"]
                if type(old_code) is not int or old_code > new_code or (old_code == new_code and previous != feed):
                    raise ValueError("feed downgrade or same-version conflict")
            result[name] = meta.json_bytes(feed)
    result[".nojekyll"] = b""
    return result


def update_feed(remote, feed):
    prefix = remote.prefix
    ref = remote.api(f"{prefix}/git/ref/heads/{FEED_BRANCH}", optional=True)
    old_commit = None if ref is None else ref["object"]["sha"]
    existing = {}
    if old_commit:
        commit = remote.api(f"{prefix}/git/commits/{old_commit}")
        tree = remote.api(f"{prefix}/git/trees/{commit['tree']['sha']}?recursive=1")
        if tree.get("truncated"):
            raise ValueError("feed tree truncated")
        for entry in tree["tree"]:
            if entry["path"] == "updates" and entry["type"] == "tree":
                continue
            if entry["path"] not in FEED_FILES or entry["type"] != "blob" or entry["mode"] != "100644":
                raise ValueError("feed branch contains private or unexpected content")
            if entry.get("size", meta.MAX_FEED + 1) > meta.MAX_FEED:
                raise ValueError("existing feed too large")
            blob = remote.api(f"{prefix}/git/blobs/{entry['sha']}")
            if blob["encoding"] != "base64":
                raise ValueError("invalid feed blob encoding")
            existing[entry["path"]] = base64.b64decode(blob["content"])
    files = merge_feed_files(existing, feed)
    if files != existing:
        tree = remote.api(f"{prefix}/git/trees", "POST", {"tree": [
            {"path": name, "mode": "100644", "type": "blob", "content": content.decode("utf-8")}
            for name, content in sorted(files.items())]})
        commit = remote.api(f"{prefix}/git/commits", "POST", {
            "message": f"chore(updates): publish {feed['release']['versionName']}",
            "tree": tree["sha"], "parents": [old_commit] if old_commit else [],
        })
        if old_commit:
            remote.api(f"{prefix}/git/refs/heads/{FEED_BRANCH}", "PATCH", {"sha": commit["sha"], "force": False})
        else:
            remote.api(f"{prefix}/git/refs", "POST", {"ref": f"refs/heads/{FEED_BRANCH}", "sha": commit["sha"]})
    pages = remote.api(f"{prefix}/pages", optional=True)
    if not pages or pages.get("source") != {"branch": FEED_BRANCH, "path": "/"}:
        raise ValueError("feed branch ready; configure Pages source codex/update-feed / in GitHub, then retry feed")
    return files


def verify_public_assets(directory, feed):
    base = feed["release"]["apkUrl"].rsplit("/", 1)[0] + "/"
    for path in Path(directory).iterdir():
        if anonymous_download(base + path.name, path.stat().st_size) != path.read_bytes():
            raise ValueError("anonymous asset verification failed")


def verify_live_feeds(files):
    for name, expected in files.items():
        if name == ".nojekyll":
            continue
        for attempt in range(6):
            try:
                actual = anonymous_download(FEED_ROOT + Path(name).name, meta.MAX_FEED, pages=True)
                if json.loads(actual) == json.loads(expected):
                    break
            except (OSError, ValueError):
                pass
            if attempt == 5:
                raise ValueError("Release published; Pages not yet serving expected feed. Retry feed later")
            time.sleep(5)


def verify_receipt(directory, commit):
    receipt = json.loads((directory.parent / "verification.json").read_text(encoding="utf-8"))
    if receipt.get("commit") != commit or receipt.get("assets") != {
            p.name: meta.file_hash(p) for p in directory.iterdir()} or receipt.get("checks") != ["pytest", "node", "public-build-unit-lint"]:
        raise ValueError("verification receipt does not cover exact committed source and assets; run prepare")


def preparation_paths(directory, version):
    """Resolve every move/write target under the expected version directory."""
    meta.validate_version(version)
    directory = Path(directory).absolute()
    root = ROOT.resolve()
    parent = root / "dist" / "android" / version["versionName"]
    if directory != parent / "public":
        raise ValueError("preparation path must be the expected public version directory")
    for path in (root / "dist", root / "dist/android", parent, directory,
                 parent / "verification.json", parent / "preparation.json"):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("preparation paths must not traverse symlinks or junctions")
    return directory, parent / "verification.json", parent / "preparation.json"


def write_atomic_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(meta.json_bytes(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def retain_interrupted_output(directory, version):
    directory, _, _ = preparation_paths(directory, version)
    if not directory.is_dir() or any(not p.is_file() or p.is_symlink() for p in directory.iterdir()):
        raise ValueError("interrupted output contains unexpected directories or links; inspect it manually")
    retained = directory.with_name(f"public.interrupted-{uuid.uuid4().hex}")
    if (retained.parent.resolve() != directory.parent or retained.resolve() != retained
            or retained.exists() or retained.is_symlink()):
        raise ValueError("unsafe interrupted-output destination; refusing overwrite")
    directory.rename(retained)
    print(f"Retained unverified interrupted output at {retained}; rebuilding from committed source.")


def prepare(directory, version, commit):
    directory, receipt_path, journal_path = preparation_paths(directory, version)
    if local_preflight() != (version, commit):
        raise ValueError("source commit/version changed before preparation")
    existing = receipt_path.exists()
    journal = {"commit": commit, "version": version, "stage": "building"}
    if existing:
        # A completed receipt is the only basis for artifact reuse.
        meta.validate_directory(directory, version)
        verify_receipt(directory, commit)
    else:
        if journal_path.exists():
            if json.loads(journal_path.read_text(encoding="utf-8")) != journal:
                raise ValueError("unfinished preparation provenance belongs to another commit/version")
        elif directory.exists():
            raise ValueError("unowned existing output has no preparation provenance; inspect it manually")
        if directory.exists():
            # A start journal proves ownership, not APK provenance. Retain all
            # incomplete output and rebuild; never certify interrupted bytes.
            retain_interrupted_output(directory, version)
        directory.parent.mkdir(parents=True, exist_ok=True)
        write_atomic_json(journal_path, journal)
    checks = [[sys.executable, "-m", "pytest", "test", "-q"],
              ["node", "--test", *[str(p) for p in sorted((ROOT / "test").glob("*.test.cjs"))]],
              ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(ROOT / "scripts/android.ps1"), "-Mode", "Check" if existing else "Build"]]
    if existing:
        checks.append(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(ROOT / "scripts/android.ps1"), "-Mode", "Verify"])
    for args in checks:
        result = subprocess.run(args, cwd=ROOT)
        if result.returncode:
            raise ValueError("local release verification failed; no remote changes made")
    meta.validate_directory(directory, version)
    if local_preflight() != (version, commit):
        raise ValueError("source changed during build")
    receipt = {"commit": commit, "assets": {p.name: meta.file_hash(p) for p in directory.iterdir()},
               "checks": ["pytest", "node", "public-build-unit-lint"]}
    write_atomic_json(receipt_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "prepare", "publish", "feed"), nargs="?", default="preflight")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-repository")
    parser.add_argument("--confirm-version")
    args = parser.parse_args()
    try:
        version, commit = local_preflight()
        directory = ROOT / "dist/android" / version["versionName"] / "public"
        print(json.dumps({"stage": args.stage, "dry_run": not args.execute, "repository": meta.REPOSITORY,
                          "tag": "v" + version["versionName"], "versionCode": version["versionCode"],
                          "commit": commit, "assets": [meta.apk_name(version), "SHA256SUMS", "certificate-sha256.txt", "update.json", "INSTALL.md", "RELEASE_NOTES.md"]}, ensure_ascii=False))
        if not args.execute:
            print("Dry-run: no credentials used, no signing/build, remote state NOT checked or changed.")
            return 0
        if args.stage in ("publish", "feed") and (args.confirm_repository != meta.REPOSITORY or args.confirm_version != version["versionName"]):
            raise ValueError("publication requires exact --confirm-repository and --confirm-version")
        remote = GitHub()
        remote.remote_preflight(version, commit, recover_published=args.stage == "feed")
        if args.stage == "preflight":
            print("Local and authenticated remote preflight passed; no remote writes.")
            return 0
        if args.stage == "prepare":
            prepare(directory, version, commit)
            print("Local public artifacts prepared; no Release or Pages writes.")
            return 0
        meta.validate_directory(directory, version)
        verify_receipt(directory, commit)
        command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(ROOT / "scripts/android.ps1"), "-Mode", "Verify"], timeout=300)
        if args.stage == "feed":
            release = remote.find_release("v" + version["versionName"])
            if not release or release["draft"]:
                raise ValueError("feed cannot reference an unpublished Release")
        result = publish_release(remote, directory, version, commit, execute=True,
                                 published_only=args.stage == "feed")
        print(json.dumps(result))
        try:
            feed = json.loads((directory / "update.json").read_text(encoding="utf-8"))
            verify_public_assets(directory, feed)
            files = update_feed(remote, feed)
            verify_live_feeds(files)
        except Exception:
            print("PARTIAL: Release published; feed/Pages verification incomplete. Retry feed; no deletion or force-push attempted.", file=sys.stderr)
            return 2
        print("Release=verified; anonymous assets=verified; Pages=verified")
        return 0
    except ValueError as exc:
        print(f"Release stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("Release stopped on a local/network error. No destructive retry; inspect tools locally.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
