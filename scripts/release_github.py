"""Explicit local release workflow. Default: dry-run, no credentials or writes.

prepare --execute builds/signs locally; publish/feed --execute also require exact
repository/version confirmations. No source push, forced references or deletion.
"""
import argparse
import base64
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import quote, urlsplit
from urllib.error import HTTPError
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


def local_preflight(root=None):
    root = ROOT if root is None else root
    version = meta.load_version(root / "version.json")
    def git(*args):
        return command(["git", *args], cwd=root).decode("utf-8").strip()
    status = git("status", "--porcelain", "--untracked-files=all")
    dirty = [line for line in status.splitlines()
             if not (line.startswith("?? .tmp-plan-baseline/") or
                     line.startswith('?? ".tmp-plan-baseline/'))]
    if dirty:
        raise ValueError("dirty checkout: explicitly review and commit source before preparing a release")
    check_origin(root)
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "MIT License" not in license_text or "Permission is hereby granted" not in license_text:
        raise ValueError("MIT license missing")
    for name in git("ls-files").splitlines():
        parts = Path(name).parts
        lower_name = name.lower()
        lower_parts = tuple(part.lower() for part in parts)
        basename = lower_parts[-1]
        has_catalog_token = re.search(r"(?:^|[-_.])catalog(?:[-_.]|$)", basename) is not None
        is_private_catalog = basename.endswith(".json") and (
            ("private" in lower_parts[:-1] and has_catalog_token) or
            (basename.startswith("private-") and basename.endswith("-catalog.json"))
        )
        if (name in (".env", "settings.json", "bagu.db") or
                any(p in (".signing", "dist") for p in parts) or
                lower_name.endswith((".bagu-backup", ".bagu-pack", ".db", ".sqlite", ".sqlite3", ".jks", ".keystore", ".key", ".p12", ".pfx")) or
                is_private_catalog):
            raise ValueError("tracked private data or generated artifact blocks public release")
    return version, git("rev-parse", "HEAD")


def check_origin(root):
    origin = command(["git", "remote", "get-url", "origin"], cwd=root).decode("utf-8").strip().removesuffix(".git").lower()
    if origin not in ("https://github.com/ingnijm/ai-bagu", "git@github.com:ingnijm/ai-bagu"):
        raise ValueError("origin does not match confirmed repository")


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
    status = None
    try:
        with build_opener(SafeRedirects(pages)).open(request, timeout=30) as response:
            status = response.status
            if status != 200:
                raise GitHubError(status)
            data = response.read(limit + 1)
    except HTTPError as exc:
        # HTTPError also owns a response stream. Do not read its private body.
        status = exc.code
        try:
            exc.close()
        except OSError:
            pass
        raise GitHubError(status) from None
    except (OSError, HTTPException):
        raise GitHubError(status) from None
    if len(data) > limit:
        raise ValueError("download exceeds limit")
    return data


class GitHubError(ValueError):
    """Only safe protocol facts survive; tool output never enters diagnostics."""
    def __init__(self, status=None):
        self.status = status
        detail = "no response" if status is None else f"HTTP {status}"
        super().__init__(f"GitHub request failed ({detail}); no automatic destructive retry")


def included_response(data):
    status = None
    # gh --include prefixes the response body with its HTTP status and headers.
    # Also tolerate proxy/informational header blocks without searching bodies.
    for _ in range(6):
        match = re.match(rb"HTTP/\d+(?:\.\d+)? ([1-5][0-9]{2})(?: [^\r\n]*)?\r?\n", data)
        if not match:
            break
        status = int(match[1])
        separator = re.search(rb"\r?\n\r?\n", data)
        if separator is None:
            return status, b""
        data = data[separator.end():]
    return status, data


class GitHub:
    def __init__(self):
        if not shutil.which("gh"):
            raise ValueError("Install GitHub CLI and complete gh auth login yourself first")
        self.prefix = f"repos/{meta.REPOSITORY}"

    def api(self, path, method="GET", body=None, optional=False, binary=False):
        args = ["gh", "api", path, "--hostname", "github.com", "--method", method,
                "-H", "Accept: " + ("application/octet-stream" if binary else "application/vnd.github+json")]
        if not binary:
            args += ["--include"]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = meta.json_bytes(body)
        try:
            result = subprocess.run(args, cwd=ROOT, input=payload, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            raise GitHubError() from None
        if binary:
            if not result.returncode:
                return result.stdout
            match = re.search(rb"\(HTTP ([1-5][0-9]{2})\)", result.stderr)
            raise GitHubError(int(match[1]) if match else None)
        status, data = included_response(result.stdout)
        if result.returncode or status is None or not 200 <= status < 300:
            if optional and status == 404:
                return None
            raise GitHubError(status)
        try:
            return json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            raise GitHubError(status) from None

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


def publish_release(remote, directory, version, commit, execute=False, published_only=False,
                    *, descriptor=None):
    paths = meta.validate_directory(directory, version, descriptor=descriptor)
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
    release = remote.get_release(release["id"])
    validate_release_identity(release, version, commit)
    if release["draft"] or {a["name"] for a in release["assets"]} != set(expected) or len(release["assets"]) != len(expected):
        raise ValueError("published Release state or asset set not verified")
    remote.verify_tag(tag, commit)
    return {"release": "published", "releaseUrl": f"https://github.com/{meta.REPOSITORY}/releases/tag/{tag}", "pages": "not-written"}


def merge_feed_files(existing, feed):
    if not isinstance(feed, dict) or feed.get("release") is None:
        raise ValueError("publishing feed requires a release")
    meta.validate_feed(feed, feed.get("channel"))
    if not set(existing) <= FEED_FILES:
        raise ValueError("feed branch contains non-allowlisted files")
    result = dict(existing)
    for channel in ("beta", "stable"):
        name = f"updates/{channel}.json"
        if name in result:
            previous = meta.parse_feed(result[name], channel)
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


def git_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("invalid Git object identity")
    return value


def ref_commit(ref):
    if not isinstance(ref, dict) or not isinstance(ref.get("object"), dict) or ref["object"].get("type") != "commit":
        raise ValueError("invalid Git commit reference")
    return git_sha(ref["object"].get("sha"))


def feed_git_access(remote):
    """Prove repository and Git-data access before any optional missing-ref lookup."""
    repo = remote.api(remote.prefix)
    if (not isinstance(repo, dict) or repo.get("full_name", "").lower() != meta.REPOSITORY.lower()
            or repo.get("private") is not False or repo.get("archived") is not False
            or not isinstance(repo.get("permissions"), dict)
            or repo["permissions"].get("pull") is not True or repo["permissions"].get("push") is not True):
        raise ValueError("fixed repository must be public, non-archived, and grant pull/push access")
    branch = repo.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 255:
        raise ValueError("repository default branch unavailable")
    head = ref_commit(remote.api(f"{remote.prefix}/git/ref/heads/{quote(branch, safe='/')}"))
    commit = remote.api(f"{remote.prefix}/git/commits/{head}")
    if commit.get("sha") != head:
        raise ValueError("default Git commit unavailable")
    tree_sha = git_sha(commit.get("tree", {}).get("sha"))
    tree = remote.api(f"{remote.prefix}/git/trees/{tree_sha}")
    if tree.get("sha") != tree_sha or not isinstance(tree.get("tree"), list):
        raise ValueError("default Git tree unavailable")


def feed_head(remote):
    ref = remote.api(f"{remote.prefix}/git/ref/heads/{FEED_BRANCH}", optional=True)
    return None if ref is None else ref_commit(ref)


def read_feed_branch(remote):
    old_commit = feed_head(remote)
    existing = {}
    if old_commit is None:
        return old_commit, existing
    commit = remote.api(f"{remote.prefix}/git/commits/{old_commit}")
    if commit.get("sha") != old_commit:
        raise ValueError("feed commit identity mismatch")
    tree_sha = git_sha(commit.get("tree", {}).get("sha"))
    tree = remote.api(f"{remote.prefix}/git/trees/{tree_sha}?recursive=1")
    entries = tree.get("tree")
    if (tree.get("sha") != tree_sha or tree.get("truncated") is not False
            or not isinstance(entries, list) or len(entries) > 4):
        raise ValueError("feed tree invalid, truncated or oversized")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry["path"] in seen:
            raise ValueError("invalid or duplicate feed tree entry")
        name = entry["path"]
        seen.add(name)
        if name == "updates" and entry.get("type") == "tree" and entry.get("mode") == "040000":
            continue
        if name not in FEED_FILES or entry.get("type") != "blob" or entry.get("mode") != "100644":
            raise ValueError("feed branch contains private or unexpected content")
        size = entry.get("size")
        if type(size) is not int or not 0 <= size <= meta.MAX_FEED:
            raise ValueError("existing feed too large or invalid")
        sha = git_sha(entry.get("sha"))
        blob = remote.api(f"{remote.prefix}/git/blobs/{sha}")
        encoded = blob.get("content")
        if (blob.get("encoding") != "base64" or blob.get("sha") != sha or type(blob.get("size")) is not int
                or blob["size"] != size or not isinstance(encoded, str) or len(encoded) > 2 * meta.MAX_FEED):
            raise ValueError("invalid feed blob metadata or size")
        try:
            data = base64.b64decode(encoded.replace("\n", "").replace("\r", ""), validate=True)
        except ValueError:
            raise ValueError("invalid feed blob encoding") from None
        actual_sha = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if len(data) != size or actual_sha != sha:
            raise ValueError("feed blob size or identity mismatch")
        if name != ".nojekyll":
            meta.parse_feed(data, Path(name).stem)
        else:
            try:
                data.decode("utf-8")
            except UnicodeError:
                raise ValueError("invalid .nojekyll encoding") from None
        existing[name] = data
    return old_commit, existing


def write_feed_branch(remote, old_commit, existing, files, message):
    if feed_head(remote) != old_commit:
        raise ValueError("concurrent feed branch change; inspect and retry")
    if files == existing:
        return
    tree = remote.api(f"{remote.prefix}/git/trees", "POST", {"tree": [
        {"path": name, "mode": "100644", "type": "blob", "content": content.decode("utf-8")}
        for name, content in sorted(files.items())]})
    commit = remote.api(f"{remote.prefix}/git/commits", "POST", {
        "message": message, "tree": git_sha(tree.get("sha")), "parents": [old_commit] if old_commit else [],
    })
    new_commit = git_sha(commit.get("sha"))
    if feed_head(remote) != old_commit:
        raise ValueError("concurrent feed branch change; inspect and retry")
    if old_commit:
        remote.api(f"{remote.prefix}/git/refs/heads/{FEED_BRANCH}", "PATCH", {"sha": new_commit, "force": False})
    else:
        remote.api(f"{remote.prefix}/git/refs", "POST", {"ref": f"refs/heads/{FEED_BRANCH}", "sha": new_commit})
    verified_commit, verified_files = read_feed_branch(remote)
    if verified_commit != new_commit or verified_files != files:
        raise ValueError("concurrent feed branch change or verification mismatch")


def initialize_feed(remote):
    feed_git_access(remote)
    old_commit, existing = read_feed_branch(remote)
    files = dict(existing)
    files.setdefault(".nojekyll", b"")
    for channel in ("beta", "stable"):
        files.setdefault(f"updates/{channel}.json", meta.json_bytes({"schema_version": 1, "channel": channel, "release": None}))
    write_feed_branch(remote, old_commit, existing, files, "chore(updates): initialize missing feeds")
    return files


def update_feed(remote, feed):
    feed_git_access(remote)
    old_commit, existing = read_feed_branch(remote)
    files = merge_feed_files(existing, feed)
    write_feed_branch(remote, old_commit, existing, files, f"chore(updates): publish {feed['release']['versionName']}")
    require_pages_source(remote)
    return files


def require_pages_source(remote):
    pages = remote.api(f"{remote.prefix}/pages")
    if (not pages or pages.get("source") != {"branch": FEED_BRANCH, "path": "/"}
            or "build_type" in pages and pages["build_type"] != "legacy"):
        raise ValueError("feed branch ready; configure Pages source codex/update-feed / in GitHub, then retry feed")


def verify_pages_ready(remote):
    require_pages_source(remote)
    feed_git_access(remote)
    old_commit, files = read_feed_branch(remote)
    if old_commit is None or set(files) != FEED_FILES:
        raise ValueError("Pages feed branch incomplete; run init-feed and configure Pages first")
    verify_live_feeds(files, attempts=1)
    if feed_head(remote) != old_commit:
        raise ValueError("concurrent feed branch change during Pages verification")


def verify_public_assets(directory, feed):
    base = feed["release"]["apkUrl"].rsplit("/", 1)[0] + "/"
    for path in Path(directory).iterdir():
        if anonymous_download(base + path.name, path.stat().st_size) != path.read_bytes():
            raise ValueError("anonymous asset verification failed")


def verify_live_feeds(files, attempts=6):
    if set(files) != FEED_FILES:
        raise ValueError("Pages feed files incomplete")
    for channel in ("beta", "stable"):
        name = f"updates/{channel}.json"
        expected = meta.parse_feed(files[name], channel)
        for attempt in range(attempts):
            last_failure = None
            try:
                actual = anonymous_download(FEED_ROOT + Path(name).name, meta.MAX_FEED, pages=True)
                if meta.parse_feed(actual, channel) == expected:
                    break
            except GitHubError as exc:
                last_failure = exc
            except (OSError, ValueError):
                pass
            if attempt == attempts - 1:
                if last_failure is not None:
                    raise last_failure from None
                raise ValueError("Pages not serving both valid expected feeds; check deployment and retry feed later")
            time.sleep(5)


def _read_canonical_record(path, label):
    raw = Path(path).read_bytes()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate {label} field")
            value[key] = item
        return value
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not isinstance(value, dict) or meta.json_bytes(value) != raw:
        raise ValueError(f"noncanonical {label}")
    return value


def _release_pack_context(version, question_pack=None, require_external=False):
    loaded = meta.load_question_pack_descriptor(ROOT, version)
    if loaded is None:
        if question_pack is not None:
            raise ValueError("--question-pack has no version-derived descriptor")
        return None
    descriptor, descriptor_bytes, descriptor_path = loaded
    if require_external and question_pack is None:
        raise ValueError("prepare --execute requires --question-pack for this version")
    bound = None if question_pack is None else meta.read_bound_question_pack(question_pack, descriptor)
    return {
        "descriptor": descriptor,
        "descriptor_path": descriptor_path,
        "descriptor_bytes": descriptor_bytes,
        "bound": bound,
        "provenance": {
            "file_name": descriptor["file_name"],
            "sha256": descriptor["sha256"],
            "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        },
    }


def verify_receipt(directory, commit, pack_context=None):
    path = directory.parent / "verification.json"
    receipt = (_read_canonical_record(path, "verification receipt")
               if pack_context is not None else json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "commit": commit,
        "assets": {p.name: meta.file_hash(p) for p in sorted(directory.iterdir(), key=lambda item: item.name)},
        "checks": ["pytest", "node", "public-build-unit-lint"],
    }
    if pack_context is not None:
        expected["question_pack"] = pack_context["provenance"]
        expected.update(meta.android_verification_fields(pack_context["descriptor"]))
    if receipt != expected:
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


def prepare(directory, version, commit, question_pack=None, *, pack_context=None):
    pack_context = pack_context or _release_pack_context(
        version, question_pack, require_external=True
    )
    descriptor = None if pack_context is None else pack_context["descriptor"]
    directory, receipt_path, journal_path = preparation_paths(directory, version)
    if local_preflight() != (version, commit):
        raise ValueError("source commit/version changed before preparation")
    existing = receipt_path.exists()
    journal = {"commit": commit, "version": version, "stage": "building"}
    if pack_context is not None:
        journal["question_pack"] = pack_context["provenance"]
    if existing:
        # A completed receipt is the only basis for artifact reuse.
        if pack_context is not None:
            if not journal_path.exists() or _read_canonical_record(journal_path, "preparation journal") != journal:
                raise ValueError("completed preparation provenance differs")
        meta.validate_directory(directory, version, descriptor=descriptor)
        verify_receipt(directory, commit, pack_context)
    else:
        if journal_path.exists():
            existing_journal = (_read_canonical_record(journal_path, "preparation journal")
                                if pack_context is not None
                                else json.loads(journal_path.read_text(encoding="utf-8")))
            if existing_journal != journal:
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
    if pack_context is not None and not existing:
        checks[2] += ["-QuestionPack", str(pack_context["bound"].path)]
    if existing:
        checks.append(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(ROOT / "scripts/android.ps1"), "-Mode", "Verify"])
    for args in checks:
        result = subprocess.run(args, cwd=ROOT)
        if result.returncode:
            raise ValueError("local release verification failed; no remote changes made")
    meta.validate_directory(directory, version, descriptor=descriptor)
    if local_preflight() != (version, commit):
        raise ValueError("source changed during build")
    if pack_context is not None:
        final_context = _release_pack_context(
            version, pack_context["bound"].path, require_external=True
        )
        if final_context["provenance"] != pack_context["provenance"]:
            raise ValueError("question-pack provenance changed during build")
    receipt = {"commit": commit, "assets": {
                   p.name: meta.file_hash(p) for p in sorted(directory.iterdir(), key=lambda item: item.name)},
               "checks": ["pytest", "node", "public-build-unit-lint"]}
    if pack_context is not None:
        receipt["question_pack"] = pack_context["provenance"]
        receipt.update(meta.android_verification_fields(pack_context["descriptor"]))
    write_atomic_json(receipt_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "prepare", "publish", "feed", "init-feed"), nargs="?", default="preflight")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-repository")
    parser.add_argument("--confirm-version")
    parser.add_argument("--question-pack", type=Path)
    args = parser.parse_args()
    try:
        if args.question_pack is not None and not (args.stage == "prepare" and args.execute):
            raise ValueError("--question-pack is accepted only by prepare --execute")
        if args.stage == "init-feed":
            print(json.dumps({"stage": "init-feed", "dry_run": not args.execute, "repository": meta.REPOSITORY,
                              "branch": FEED_BRANCH, "files": sorted(FEED_FILES)}))
            if not args.execute:
                print("Dry-run: offline; add only missing feeds. No credentials, tools, workspace or remote changes.")
                return 0
            if args.confirm_repository != meta.REPOSITORY:
                raise ValueError("init-feed requires exact --confirm-repository InGnIJM/AI-Bagu")
            check_origin(ROOT)
            initialize_feed(GitHub())
            print("Feed branch ready; Pages deployment NOT verified. Configure Pages source codex/update-feed / manually.")
            return 0
        version, commit = local_preflight()
        pack_context = _release_pack_context(
            version, args.question_pack,
            require_external=args.stage == "prepare" and args.execute,
        )
        descriptor = None if pack_context is None else pack_context["descriptor"]
        directory = ROOT / "dist/android" / version["versionName"] / "public"
        assets = [meta.apk_name(version), "SHA256SUMS", "certificate-sha256.txt",
                  "update.json", "INSTALL.md", "RELEASE_NOTES.md"]
        if descriptor is not None:
            assets.append(descriptor["file_name"])
        print(json.dumps({"stage": args.stage, "dry_run": not args.execute, "repository": meta.REPOSITORY,
                          "tag": "v" + version["versionName"], "versionCode": version["versionCode"],
                          "commit": commit, "assets": sorted(assets)}, ensure_ascii=False))
        if not args.execute:
            print("Dry-run: no credentials used, no signing/build, remote state NOT checked or changed.")
            return 0
        if args.stage in ("publish", "feed") and (args.confirm_repository != meta.REPOSITORY or args.confirm_version != version["versionName"]):
            raise ValueError("publication requires exact --confirm-repository and --confirm-version")
        remote = GitHub()
        remote.remote_preflight(version, commit, recover_published=args.stage == "feed")
        if args.stage != "feed":
            verify_pages_ready(remote)
        if args.stage == "preflight":
            print("Local, authenticated remote and anonymous Pages preflight passed; no remote writes.")
            return 0
        if args.stage == "prepare":
            prepare(directory, version, commit, args.question_pack, pack_context=pack_context)
            print("Local public artifacts prepared; no Release or Pages writes.")
            return 0
        meta.validate_directory(directory, version, descriptor=descriptor)
        verify_receipt(directory, commit, pack_context)
        command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(ROOT / "scripts/android.ps1"), "-Mode", "Verify"], timeout=300)
        if args.stage == "feed":
            release = remote.find_release("v" + version["versionName"])
            if not release or release["draft"]:
                raise ValueError("feed cannot reference an unpublished Release")
        result = publish_release(remote, directory, version, commit, execute=True,
                                 published_only=args.stage == "feed", descriptor=descriptor)
        print(json.dumps(result))
        try:
            feed = json.loads((directory / "update.json").read_text(encoding="utf-8"))
            verify_public_assets(directory, feed)
            files = update_feed(remote, feed)
            verify_live_feeds(files)
        except Exception as exc:
            print("PARTIAL: Release published; feed/Pages verification incomplete. Retry feed; no deletion or force-push attempted.", file=sys.stderr)
            if isinstance(exc, GitHubError):
                print(str(exc), file=sys.stderr)
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
