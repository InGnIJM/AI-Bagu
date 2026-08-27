"""Deterministic, allowlisted public release artifacts (standard library only).

This module validates metadata; android.ps1 separately verifies the real APK
signature, manifest, alignment, empty seed and explicit payload allowlist.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

REPOSITORY = "InGnIJM/AI-Bagu"
PACKAGE = "io.github.ingnijm.baguhelper"
CERTIFICATE = "ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3"
MAX_APK = 128 * 1024 * 1024
MAX_FEED = 64 * 1024


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
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"\d+\.\d+\.\d+(?:-beta\.\d+)?", name):
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


def write_metadata(directory, version, notes, published_at=None):
    directory = Path(directory)
    timestamp = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed = make_feed(version, directory / apk_name(version), notes, timestamp)
    name = apk_name(version)
    files = {
        "update.json": json_bytes(feed),
        "SHA256SUMS": f"{feed['release']['sha256']} *{name}\n".encode("ascii"),
        "certificate-sha256.txt": (CERTIFICATE + "\n").encode("ascii"),
        "RELEASE_NOTES.md": (notes + "\n").encode("utf-8"),
        "INSTALL.md": ("# 八股助手 public 安装说明\n\n"
            f"首次安装：`adb install \"{name}\"`；覆盖升级：`adb install -r \"{name}\"`。\n\n"
            "仅支持 Android 10+ ARM64。公开包不含题库，请从设置导入自己的文件。\n"
            "升级前建议导出题库＋进度；同包名同签名升级保留私有数据，卸载会清空数据。\n"
            "旧 internal 包首次需手动安装本 public 包。自动检查只查询公开版本，下载和安装须点击确认。\n"
            "备份不包含模型配置、密钥、草稿、会话或评分分析；图片仅保存链接。\n"
            "应用源码使用 MIT；字体、运行时和第三方材料保留各自许可证，题库不在 MIT 授权范围内。\n"
        ).encode("utf-8"),
    }
    # Preflight the entire set before writing, preserving resumable identical sets.
    for name, data in files.items():
        target = directory / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError("existing release metadata differs; refusing overwrite")
    for name, data in files.items():
        target = directory / name
        if not target.exists():
            with target.open("xb") as output:
                output.write(data)
    return feed


def validate_directory(directory, version):
    directory = Path(directory)
    expected = {apk_name(version), "SHA256SUMS", "certificate-sha256.txt", "update.json", "INSTALL.md", "RELEASE_NOTES.md"}
    entries = list(directory.iterdir())
    if {p.name for p in entries} != expected or any(not p.is_file() or p.is_symlink() for p in entries):
        raise ValueError("release directory violates public asset allowlist")
    for entry in entries:
        limit = MAX_APK if entry.suffix == ".apk" else MAX_FEED
        if not 0 < entry.stat().st_size <= limit:
            raise ValueError("release asset exceeds size limit")
    feed = json.loads((directory / "update.json").read_text(encoding="utf-8"))
    notes = (directory / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    if not notes.endswith("\n"):
        raise ValueError("invalid release notes")
    try:
        expected_feed = make_feed(version, directory / apk_name(version), notes[:-1], feed["release"]["publishedAt"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid update metadata") from exc
    if feed != expected_feed:
        raise ValueError("release metadata/hash/size mismatch")
    expected_sum = f"{feed['release']['sha256']} *{apk_name(version)}\n"
    if (directory / "SHA256SUMS").read_text(encoding="ascii") != expected_sum:
        raise ValueError("APK hash mismatch")
    if (directory / "certificate-sha256.txt").read_text(encoding="ascii") != CERTIFICATE + "\n":
        raise ValueError("untrusted signing certificate")
    if apk_name(version) not in (directory / "INSTALL.md").read_text(encoding="utf-8"):
        raise ValueError("installation notes do not name the exact APK")
    return sorted(entries, key=lambda p: p.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", type=Path, default=Path(__file__).resolve().parents[1] / "version.json")
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    version = load_version(args.version)
    if args.mode == "prepare":
        if not args.notes:
            parser.error("prepare requires --notes")
        write_metadata(args.directory, version, args.notes.read_text(encoding="utf-8").rstrip("\n"))
    validate_directory(args.directory, version)
    print("Public asset metadata verified (APK cryptographic verification is separate).")


if __name__ == "__main__":
    main()
