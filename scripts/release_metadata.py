"""Deterministic, allowlisted public release artifacts (standard library only).

This module validates metadata; android.ps1 separately verifies the real APK
signature, manifest, alignment, empty seed and explicit payload allowlist.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import bagu

REPOSITORY = "InGnIJM/AI-Bagu"
PACKAGE = "io.github.ingnijm.baguhelper"
CERTIFICATE = "ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3"
MAX_APK = 128 * 1024 * 1024
MAX_FEED = 64 * 1024
MAX_PACK = 20 * 1024 * 1024
MAX_DESCRIPTOR = 64 * 1024
MAX_PACK_ITEMS = 10000
DESCRIPTOR_FIELDS = (
    "schema_version", "versionName", "file_name", "sha256", "pack_id",
    "revision", "display_version", "question_count", "experience_count",
)


@dataclass(frozen=True)
class BoundQuestionPack:
    path: Path
    descriptor: dict
    data: bytes

    @property
    def provenance(self):
        return {"file_name": self.descriptor["file_name"],
                "sha256": self.descriptor["sha256"]}


def load_version(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_version(value)
    return value


def validate_version(value):
    if not isinstance(value, dict) or set(value) != {"versionName", "versionCode", "channel"}:
        raise ValueError("invalid version fields")
    code, name, channel = value["versionCode"], value["versionName"], value["channel"]
    if type(code) is not int or not 1 <= code <= 2100000000:
        raise ValueError("invalid versionCode")
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-beta\.[0-9]+)?", name):
        raise ValueError("invalid versionName")
    if channel not in ("stable", "beta") or ("-beta." in name) != (channel == "beta"):
        raise ValueError("versionName and channel disagree")


def apk_name(version):
    validate_version(version)
    return f"bagu-{version['versionName']}-public-arm64-v8a.apk"


def file_hash(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _unique_object(pairs, label):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate {label} field")
        value[key] = item
    return value


def parse_question_pack_descriptor(data, version):
    """Parse the exact public nine-field binding, including its canonical bytes."""
    validate_version(version)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DESCRIPTOR:
        raise ValueError("question-pack descriptor exceeds 64 KiB or is empty")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=lambda pairs: _unique_object(pairs, "descriptor"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")),
        )
    except (UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid question-pack descriptor JSON") from exc
    if (not isinstance(value, dict) or tuple(value) != DESCRIPTOR_FIELDS
            or json_bytes(value) != data):
        raise ValueError("question-pack descriptor is not canonical or has invalid fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid question-pack descriptor schema")
    if value["versionName"] != version["versionName"]:
        raise ValueError("question-pack descriptor version differs")
    name = value["file_name"]
    device_stem = name.split(".", 1)[0].upper() if isinstance(name, str) else ""
    reserved_device = (device_stem in {"CON", "PRN", "AUX", "NUL"}
                       or re.fullmatch(r"(?:COM|LPT)[1-9]", device_stem) is not None)
    if (not isinstance(name, str) or not name.isascii() or len(name) > 220
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.bagu-pack", name)
            or ".." in name or Path(name).name != name or reserved_device):
        raise ValueError("invalid question-pack filename")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError("invalid question-pack SHA-256")
    if (not isinstance(value["pack_id"], str) or not value["pack_id"].strip()
            or len(value["pack_id"]) > 128):
        raise ValueError("invalid question-pack ID")
    if (not isinstance(value["display_version"], str) or not value["display_version"].strip()
            or len(value["display_version"]) > 128):
        raise ValueError("invalid question-pack display version")
    for field, high in (("revision", 2100000000), ("question_count", MAX_PACK_ITEMS),
                        ("experience_count", MAX_PACK_ITEMS)):
        if type(value[field]) is not int or not 1 <= value[field] <= high:
            raise ValueError(f"invalid question-pack {field}")
    return value


def question_pack_descriptor_path(root, version):
    validate_version(version)
    return Path(root) / "docs" / "releases" / f"{version['versionName']}-question-pack.json"


def _read_bounded_regular_file(path, maximum, label):
    path = Path(path)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} is not a regular file") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a non-symlink regular file")
    if not 0 < before.st_size <= maximum:
        raise ValueError(f"{label} size exceeds its limit")
    try:
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            data = source.read(maximum + 1)
        after = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} could not be read") from exc
    identities = {(item.st_dev, item.st_ino, item.st_size) for item in (before, opened, after)}
    if (len(identities) != 1 or stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode)
            or len(data) != after.st_size or not 0 < len(data) <= maximum):
        raise ValueError(f"{label} changed while it was read")
    return data


def read_question_pack_descriptor(path, version):
    data = _read_bounded_regular_file(path, MAX_DESCRIPTOR, "question-pack descriptor")
    return parse_question_pack_descriptor(data, version), data


def load_question_pack_descriptor(root, version, required=False):
    path = question_pack_descriptor_path(root, version)
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError("version-derived question-pack descriptor is missing")
        return None
    descriptor, data = read_question_pack_descriptor(path, version)
    return descriptor, data, path


def read_bound_question_pack(path, descriptor):
    path = Path(path)
    if path.name != descriptor["file_name"]:
        raise ValueError("question-pack filename does not match descriptor")
    data = _read_bounded_regular_file(path, MAX_PACK, "question pack (20 MiB maximum)")
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError("question-pack hash differs from descriptor")
    try:
        payload = bagu.parse_interview_pack(data)
    except bagu.PackValidationError as exc:
        raise ValueError("question-pack runtime validation failed") from exc
    manifest = payload.manifest
    comparisons = (
        ("pack_id", "pack_id"), ("revision", "revision"),
        ("display_version", "display version"),
        ("question_count", "questions"), ("experience_count", "experiences"),
    )
    for field, label in comparisons:
        if manifest[field] != descriptor[field]:
            raise ValueError(f"question-pack {label} differs from descriptor")
    return BoundQuestionPack(path=path, descriptor=descriptor, data=data)


def _pack_install_text(version, descriptor):
    name = apk_name(version)
    text = ("# 八股助手 public 安装说明\n\n"
        f"首次安装：`adb install \"{name}\"`；覆盖升级：`adb install -r \"{name}\"`。\n\n"
        "仅支持 Android 10+ ARM64。公开 APK 使用空题库，并且不内置题包。\n"
        "升级前建议导出题库＋进度；同包名同签名升级保留私有数据，卸载会清空数据。\n"
        "旧 internal 包首次需手动安装本 public 包。自动检查只查询 APK 版本，下载和安装须点击确认。\n"
        "备份不包含模型配置、密钥、草稿、会话或评分分析；图片仅保存链接。\n")
    if descriptor is not None:
        text += ("\n## 导入面经题包\n\n"
            f"从同一 Release 单独下载 `{descriptor['file_name']}`，打开八股助手的题库管理，选择“导入题包”，"
            "先检查预览，再明确确认安装。题包不会自动下载或自动更新。\n\n"
            "题包仅供个人学习及在八股助手中使用；题包内容权利保留，应用源码的 MIT 许可证不适用于题包内容。\n")
    else:
        text += "\n应用源码使用 MIT；字体、运行时和第三方材料保留各自许可证，题库不在 MIT 授权范围内。\n"
    return text


def _validate_pack_disclosures(install, notes, descriptor):
    if descriptor is None:
        return
    if (descriptor["file_name"] not in install or "个人学习" not in install
            or "八股助手" not in install or "内容权利保留" not in install
            or "MIT 许可证不适用于题包内容" not in install):
        raise ValueError("installation notes lack question-pack import or rights disclosure")
    if ("AI" not in notes or "维护者" not in notes or "参考答案" not in notes
            or "原帖" not in notes or "面试公司" not in notes or "不是" not in notes):
        raise ValueError("release notes lack AI reference-answer disclosure")


def _atomic_create(path, data):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ValueError("release destination appeared during atomic copy") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_feed(feed, channel):
    """Validate the complete Android feed contract without trusting a local APK."""
    if (channel not in ("beta", "stable") or not isinstance(feed, dict)
            or set(feed) != {"schema_version", "channel", "release"}
            or type(feed["schema_version"]) is not int or feed["schema_version"] != 1
            or feed["channel"] != channel):
        raise ValueError("invalid feed envelope")
    item = feed["release"]
    if item is None:
        return feed
    if not isinstance(item, dict) or set(item) != {
            "versionName", "versionCode", "distribution", "packageName", "minSdk", "abi",
            "apkUrl", "size", "sha256", "releaseUrl", "publishedAt", "notes"}:
        raise ValueError("invalid feed release fields")
    version = {"versionName": item["versionName"], "versionCode": item["versionCode"], "channel": channel}
    validate_version(version)
    for key, low, high in (("size", 1, MAX_APK), ("minSdk", 29, 10000)):
        if type(item[key]) is not int or not low <= item[key] <= high:
            raise ValueError("invalid feed release integer")
    if (item["distribution"] != "public" or item["packageName"] != PACKAGE
            or item["abi"] != "arm64-v8a" or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
        raise ValueError("invalid feed release identity")
    tag = "v" + version["versionName"]
    if (item["apkUrl"] != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{apk_name(version)}"
            or item["releaseUrl"] != f"https://github.com/{REPOSITORY}/releases/tag/{tag}"):
        raise ValueError("invalid feed release URL")
    if (not isinstance(item["publishedAt"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", item["publishedAt"])):
        raise ValueError("invalid feed release timestamp")
    try:
        datetime.strptime(item["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
        notes = item["notes"]
        if not isinstance(notes, str) or not notes.strip() or len(notes.encode("utf-16-le")) // 2 > 12000:
            raise ValueError("invalid feed release notes")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid feed release timestamp or notes") from exc
    return feed


def parse_feed(data, channel):
    if not isinstance(data, bytes) or len(data) > MAX_FEED:
        raise ValueError("feed exceeds 64 KiB")
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate feed field")
            value[key] = item
        return value
    try:
        feed = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid feed JSON") from exc
    return validate_feed(feed, channel)


def make_feed(version, apk, notes, published_at):
    validate_version(version)
    apk = Path(apk)
    if apk.name != apk_name(version) or not 0 < apk.stat().st_size <= MAX_APK:
        raise ValueError("invalid public APK name or size")
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError("invalid release notes")
    try:
        # Android String.length counts UTF-16 units, including surrogate pairs.
        notes_units = len(notes.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("invalid release notes") from exc
    if notes_units > 12000:
        raise ValueError("invalid release notes")
    datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
    tag = "v" + version["versionName"]
    result = {"schema_version": 1, "channel": version["channel"], "release": {
        "versionName": version["versionName"], "versionCode": version["versionCode"],
        "distribution": "public", "packageName": PACKAGE, "minSdk": 29,
        "abi": "arm64-v8a", "apkUrl": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{apk.name}",
        "size": apk.stat().st_size, "sha256": file_hash(apk),
        "releaseUrl": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "publishedAt": published_at, "notes": notes,
    }}
    if len(json_bytes(result)) > MAX_FEED:
        raise ValueError("update feed exceeds 64 KiB")
    return result


def write_metadata(directory, version, notes, published_at=None, *, question_pack=None, descriptor=None):
    directory = Path(directory)
    bound = None
    if descriptor is not None:
        if question_pack is None:
            raise ValueError("descriptor-bound release requires --question-pack")
        bound = read_bound_question_pack(question_pack, descriptor)
    elif question_pack is not None:
        raise ValueError("question pack has no version-derived descriptor")
    timestamp = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed = make_feed(version, directory / apk_name(version), notes, timestamp)
    name = apk_name(version)
    install = _pack_install_text(version, descriptor)
    _validate_pack_disclosures(install, notes, descriptor)
    hashes = {name: feed["release"]["sha256"]}
    if bound is not None:
        hashes[descriptor["file_name"]] = descriptor["sha256"]
    files = {
        "update.json": json_bytes(feed),
        "SHA256SUMS": "".join(
            f"{hashes[file_name]} *{file_name}\n" for file_name in sorted(hashes)
        ).encode("ascii"),
        "certificate-sha256.txt": (CERTIFICATE + "\n").encode("ascii"),
        "RELEASE_NOTES.md": (notes + "\n").encode("utf-8"),
        "INSTALL.md": install.encode("utf-8"),
    }
    if bound is not None:
        files[descriptor["file_name"]] = bound.data
    # Preflight the entire set before writing, preserving resumable identical sets.
    expected = set(files) | {apk_name(version)}
    entries = list(directory.iterdir())
    if (not {path.name for path in entries} <= expected
            or any(not path.is_file() or path.is_symlink() for path in entries)):
        raise ValueError("release directory violates public asset allowlist")
    for file_name, data in files.items():
        target = directory / file_name
        if target.exists() and target.read_bytes() != data:
            raise ValueError("existing release metadata differs; refusing overwrite")
    for file_name, data in files.items():
        target = directory / file_name
        if not target.exists():
            _atomic_create(target, data)
    return feed


def validate_directory(directory, version, *, descriptor=None):
    directory = Path(directory)
    if descriptor is None:
        loaded = load_question_pack_descriptor(ROOT, version)
        descriptor = None if loaded is None else loaded[0]
    expected = {apk_name(version), "SHA256SUMS", "certificate-sha256.txt", "update.json", "INSTALL.md", "RELEASE_NOTES.md"}
    if descriptor is not None:
        expected.add(descriptor["file_name"])
    entries = list(directory.iterdir())
    if {p.name for p in entries} != expected or any(not p.is_file() or p.is_symlink() for p in entries):
        raise ValueError("release directory violates public asset allowlist")
    for entry in entries:
        limit = MAX_APK if entry.suffix == ".apk" else MAX_PACK if entry.name.endswith(".bagu-pack") else MAX_FEED
        if not 0 < entry.stat().st_size <= limit:
            raise ValueError("release asset exceeds size limit")
    feed_bytes = (directory / "update.json").read_bytes()
    feed = parse_feed(feed_bytes, version["channel"])
    if feed_bytes != json_bytes(feed):
        raise ValueError("update metadata is not canonical")
    notes = (directory / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    if not notes.endswith("\n"):
        raise ValueError("invalid release notes")
    try:
        expected_feed = make_feed(version, directory / apk_name(version), notes[:-1], feed["release"]["publishedAt"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid update metadata") from exc
    if feed != expected_feed:
        raise ValueError("release metadata/hash/size mismatch")
    hashes = {apk_name(version): feed["release"]["sha256"]}
    if descriptor is not None:
        read_bound_question_pack(directory / descriptor["file_name"], descriptor)
        hashes[descriptor["file_name"]] = descriptor["sha256"]
    expected_sum = "".join(
        f"{hashes[file_name]} *{file_name}\n" for file_name in sorted(hashes)
    ).encode("ascii")
    if (directory / "SHA256SUMS").read_bytes() != expected_sum:
        raise ValueError("release hashes differ or are not canonical")
    if (directory / "certificate-sha256.txt").read_text(encoding="ascii") != CERTIFICATE + "\n":
        raise ValueError("untrusted signing certificate")
    install = (directory / "INSTALL.md").read_text(encoding="utf-8")
    if apk_name(version) not in install:
        raise ValueError("installation notes do not name the exact APK")
    _validate_pack_disclosures(install, notes[:-1], descriptor)
    return sorted(entries, key=lambda p: p.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify", "bind"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", type=Path, default=Path(__file__).resolve().parents[1] / "version.json")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--question-pack", type=Path)
    args = parser.parse_args()
    version = load_version(args.version)
    descriptor_root = args.version.resolve().parent
    loaded = load_question_pack_descriptor(descriptor_root, version)
    descriptor = None if loaded is None else loaded[0]
    if args.mode == "bind":
        if args.notes or args.question_pack:
            parser.error("bind accepts only its archive path and --version")
        if descriptor is None:
            parser.error("bind requires a version-derived question-pack descriptor")
        read_bound_question_pack(args.directory, descriptor)
        print("Question pack binding verified.")
        return
    if args.mode == "prepare":
        if not args.notes:
            parser.error("prepare requires --notes")
        if descriptor is not None and args.question_pack is None:
            parser.error("prepare requires --question-pack for this version")
        if descriptor is None and args.question_pack is not None:
            parser.error("this version has no question-pack descriptor")
        write_metadata(args.directory, version, args.notes.read_text(encoding="utf-8").rstrip("\n"),
                       question_pack=args.question_pack, descriptor=descriptor)
    elif args.question_pack or args.notes:
        parser.error("verify accepts no external question-pack or notes path")
    validate_directory(args.directory, version, descriptor=descriptor)
    print("Public asset metadata verified (APK cryptographic verification is separate).")


if __name__ == "__main__":
    main()
