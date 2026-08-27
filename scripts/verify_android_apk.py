#!/usr/bin/env python
"""Verify the explicitly allowed contents of an Android delivery APK.

This tool never opens the workstation database. It inspects only the APK and
its embedded seed database, and optionally asks GNU readelf to inspect native
libraries extracted from that APK.
"""
import argparse
import hashlib
import io
import json
from pathlib import PurePosixPath
import sqlite3
import subprocess
import tempfile
import zipfile


STATIC_ASSETS = {
    "assets/static/web/index.html",
    "assets/static/assets/branding/bagu-helper-icon-concept.png",
    "assets/static/assets/fonts/PlusJakartaSans.ttf",
    "assets/static/assets/fonts/FiraCode.ttf",
    "assets/static/assets/fonts/MaterialSymbolsRounded.ttf",
    "assets/static/assets/fonts/PlusJakartaSans-OFL.txt",
    "assets/static/assets/fonts/FiraCode-OFL.txt",
    "assets/static/assets/fonts/MaterialSymbolsRounded-APACHE-2.0.txt",
}
REQUIRED_ASSETS = STATIC_ASSETS | {
    "assets/seed/bagu-seed.db",
    "assets/chaquopy/app.imy",
}
ALLOWED_CHAQUOPY_ASSETS = {
    "assets/chaquopy/app.imy",
    "assets/chaquopy/bootstrap.imy",
    "assets/chaquopy/stdlib-common.imy",
    "assets/chaquopy/stdlib-arm64-v8a.imy",
    "assets/chaquopy/requirements-common.imy",
    "assets/chaquopy/requirements-arm64-v8a.imy",
    "assets/chaquopy/build.json",
    "assets/chaquopy/cacert.pem",
}
EXPECTED_PYTHON_MODULES = {"bagu.pyc", "android_runtime.pyc"}
# This is deliberately exact rather than a directory/prefix policy.  Chaquopy's
# runtime surface is part of the release contract: another native library must
# receive explicit review before it can ship, including inside .imy archives.
EXPECTED_NATIVE_LIBRARIES = frozenset({
    "lib/arm64-v8a/libchaquopy_java.so",
    "lib/arm64-v8a/libcrypto_chaquopy.so",
    "lib/arm64-v8a/libcrypto_python.so",
    "lib/arm64-v8a/libpython3.11.so",
    "lib/arm64-v8a/libsqlite3_chaquopy.so",
    "lib/arm64-v8a/libsqlite3_python.so",
    "lib/arm64-v8a/libssl_chaquopy.so",
    "lib/arm64-v8a/libssl_python.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_bz2.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_ctypes.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_datetime.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_lzma.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_random.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_sha512.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/_struct.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/binascii.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/java/chaquopy.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/math.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/mmap.cpython-311.so",
    "assets/chaquopy/bootstrap-native/arm64-v8a/zlib.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_asyncio.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_bisect.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_blake2.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_cn.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_hk.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_iso2022.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_jp.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_kr.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_codecs_tw.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_contextvars.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_csv.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_decimal.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_elementtree.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_hashlib.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_heapq.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_json.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_lsprof.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_md5.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_multibytecodec.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_multiprocessing.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_opcode.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_pickle.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_posixsubprocess.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_queue.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_sha1.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_sha256.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_sha3.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_socket.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_sqlite3.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_ssl.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_statistics.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_typing.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_xxsubinterpreters.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_xxtestfuzz.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!_zoneinfo.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!array.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!audioop.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!cmath.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!fcntl.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!ossaudiodev.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!pyexpat.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!resource.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!select.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!syslog.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!termios.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!unicodedata.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!xxlimited.cpython-311.so",
    "assets/chaquopy/stdlib-arm64-v8a.imy!xxlimited_35.cpython-311.so",
})
PRIVATE_BASENAMES = {
    ".env", "settings.json", "bagu.db", "release.jks", "keystore.properties",
    "keystore.jks", "credentials.json", "secrets.json",
}


def _fail(message):
    raise ValueError(message)


def _is_allowed_asset(name):
    return (
        name in STATIC_ASSETS
        or name == "assets/seed/bagu-seed.db"
        or name in ALLOWED_CHAQUOPY_ASSETS
        or name in EXPECTED_NATIVE_LIBRARIES
    )


def _check_names(apk):
    names = set(apk.namelist())
    missing = REQUIRED_ASSETS - names
    if missing:
        _fail(f"APK missing required assets: {sorted(missing)}")
    private = [name for name in names if PurePosixPath(name).name.lower() in PRIVATE_BASENAMES]
    if private:
        _fail(f"APK contains private state: {sorted(private)}")
    assets = {name for name in names if name.startswith("assets/") and not name.endswith("/")}
    unknown_assets = {name for name in assets if not _is_allowed_asset(name)}
    if unknown_assets:
        native_payloads = {
            name for name in unknown_assets
            if name.startswith("assets/chaquopy/bootstrap-native/") and name.endswith(".so")
        }
        if native_payloads:
            _fail(f"APK native manifest contains unexpected payload: {sorted(native_payloads)}")
        _fail(f"APK contains unapproved application assets: {sorted(unknown_assets)}")
    native = [name for name in names if name.startswith("lib/") and not name.endswith("/")]
    bad_native = [name for name in native if not name.startswith("lib/arm64-v8a/") or not name.endswith(".so")]
    if bad_native:
        _fail(f"APK contains unapproved native path: {sorted(bad_native)}")
    discovered = set(native)
    runtime_prefix = "assets/chaquopy/bootstrap-native/arm64-v8a/"
    discovered.update(name for name in names if name.startswith(runtime_prefix) and name.endswith(".so"))
    for archive_name in names:
        if not archive_name.endswith(".imy"):
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(apk.read(archive_name))) as nested:
                discovered.update(
                    f"{archive_name}!{name}"
                    for name in nested.namelist()
                    if name.endswith(".so") and not name.endswith("/")
                )
        except zipfile.BadZipFile:
            _fail(f"Chaquopy archive is not a ZIP: {archive_name}")
    if discovered != EXPECTED_NATIVE_LIBRARIES:
        _fail(
            "APK native manifest mismatch: "
            f"missing={sorted(EXPECTED_NATIVE_LIBRARIES - discovered)} "
            f"unexpected={sorted(discovered - EXPECTED_NATIVE_LIBRARIES)}"
        )
    return sorted(native)


def _seed_report(seed_bytes, flavor, expected_questions):
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(seed_bytes)
        columns = "category, question, answer, url"
        rows = conn.execute(f"SELECT {columns} FROM questions ORDER BY category, question").fetchall()
        questions = len(rows)
        dirty = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE level != 0 OR times_seen != 0 "
            "OR times_right != 0 OR next_due IS NOT NULL OR last_reviewed IS NOT NULL"
        ).fetchone()[0]
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        session_items = conn.execute("SELECT COUNT(*) FROM session_items").fetchone()[0]
    except sqlite3.Error as exc:
        _fail(f"packaged seed is not a valid mobile database: {exc}")
    finally:
        conn.close()
    if dirty or sessions or session_items:
        _fail("packaged seed contains scheduling or session history")
    if flavor == "public" and questions != 0:
        _fail(f"public seed must be empty, found {questions} questions")
    if expected_questions is not None and questions != expected_questions:
        _fail(f"seed question count {questions} does not match expected {expected_questions}")
    digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "questions": questions,
        "seed_content_sha256": digest,
        "sessions": sessions,
        "session_items": session_items,
    }


def _application_modules(apk):
    try:
        with zipfile.ZipFile(io.BytesIO(apk.read("assets/chaquopy/app.imy"))) as app:
            modules = set(app.namelist())
    except (KeyError, zipfile.BadZipFile) as exc:
        _fail(f"application Python archive is invalid: {exc}")
    if modules != EXPECTED_PYTHON_MODULES:
        _fail(f"application Python modules must be explicit: {sorted(modules)}")
    return sorted(modules)


def _embedded_elfs(apk, names):
    runtime_prefix = "assets/chaquopy/bootstrap-native/arm64-v8a/"
    libraries = [
        (name, apk.read(name)) for name in names
        if (name.startswith("lib/") or name.startswith(runtime_prefix)) and name.endswith(".so")
    ]
    for archive_name in names:
        if not archive_name.endswith(".imy"):
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(apk.read(archive_name))) as nested:
                for name in nested.namelist():
                    if name.endswith(".so") and not name.endswith("/"):
                        libraries.append((f"{archive_name}!{name}", nested.read(name)))
        except zipfile.BadZipFile:
            _fail(f"Chaquopy archive is not a ZIP: {archive_name}")
    return libraries


def verify_native_elfs(apk_path, readelf):
    with zipfile.ZipFile(apk_path) as apk:
        libraries = _embedded_elfs(apk, apk.namelist())
    reports = []
    with tempfile.TemporaryDirectory(prefix="bagu-apk-elf-") as temporary:
        temporary = PurePosixPath(temporary.replace("\\", "/"))
        # subprocess accepts a native string path; keep archive member names out of it.
        for index, (name, payload) in enumerate(libraries):
            destination = str(temporary / f"library-{index}.so")
            with open(destination, "wb") as output:
                output.write(payload)
            completed = subprocess.run(
                [str(readelf), "-lW", destination], capture_output=True, text=True, encoding="utf-8"
            )
            if completed.returncode != 0:
                _fail(f"readelf failed for {name}: {completed.stderr.strip()}")
            lines = completed.stdout.splitlines()
            load_alignments = []
            for line in lines:
                parts = line.split()
                if parts and parts[0] == "LOAD":
                    try:
                        load_alignments.append(int(parts[-1], 0))
                    except ValueError as exc:
                        _fail(f"cannot parse LOAD alignment for {name}: {line}")
            if not load_alignments:
                _fail(f"native library has no LOAD segments: {name}")
            if any(alignment < 0x4000 or alignment % 0x4000 for alignment in load_alignments):
                _fail(f"native library is not 16 KiB LOAD-aligned: {name} {load_alignments}")
            if not any("GNU_RELRO" in line for line in lines):
                _fail(f"native library lacks GNU_RELRO: {name}")
            reports.append({"name": name, "load_alignments": load_alignments, "relro": True})
    return reports


def verify_apk_contents(apk_path, flavor, expected_questions=None):
    """Check a built APK without accessing source data or signing material."""
    apk_path = str(apk_path)
    if flavor not in {"internal", "public"}:
        _fail(f"unknown flavor: {flavor}")
    try:
        with zipfile.ZipFile(apk_path) as apk:
            names = apk.namelist()
            native = _check_names(apk)
            report = _seed_report(apk.read("assets/seed/bagu-seed.db"), flavor, expected_questions)
            report["python_modules"] = _application_modules(apk)
    except zipfile.BadZipFile as exc:
        _fail(f"APK is not a ZIP: {exc}")
    report["abis"] = sorted({name.split("/")[1] for name in native})
    report["sha256"] = hashlib.sha256(open(apk_path, "rb").read()).hexdigest()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a built 八股助手 APK without source-data access")
    parser.add_argument("apk", help="APK to inspect")
    parser.add_argument("--flavor", required=True, choices=("internal", "public"))
    parser.add_argument("--expected-questions", type=int)
    parser.add_argument("--readelf", help="GNU readelf path; verifies every outer/nested ELF")
    args = parser.parse_args(argv)
    report = verify_apk_contents(args.apk, args.flavor, args.expected_questions)
    if args.readelf:
        report["native_elfs"] = verify_native_elfs(args.apk, args.readelf)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
