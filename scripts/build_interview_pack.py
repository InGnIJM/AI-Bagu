#!/usr/bin/env python3
"""Build a deterministic, locally audited ``.bagu-pack`` from a private catalog.

The catalog is deliberately private: it freezes source file hashes, reviewed
questions, and ordered experiences.  This module uses only the standard library
so it can be run on an offline workstation.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import bagu as bagu_runtime


MEMBERS = ("manifest.json", "questions.json", "experiences.json")
MAX_QUESTIONS = 10_000
MAX_COMPRESSED_SIZE = 20 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_STABLE_ID_LENGTH = 128
MAX_CATEGORY_LENGTH = 100
MAX_QUESTION_LENGTH = 2_000
MAX_RESULT_LENGTH = 100_000
MAX_URL_LENGTH = 2_048
MAX_DISPLAY_TEXT_LENGTH = 200
MAX_EXPERIENCE_TEXT_LENGTH = 200
STABLE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
MANIFEST_FIELDS = {
    "format", "schema_version", "pack_id", "name", "revision", "display_version",
    "source_snapshot_sha256", "question_count", "experience_count",
    "questions_sha256", "experiences_sha256",
}
QUESTION_BASE_FIELDS = {
    "stable_id", "question", "category", "kind", "review_status", "retired", "sources",
}
EXPERIENCE_FIELDS = {
    "stable_id", "kind", "direction", "company", "position", "stage", "sections",
}


class PackBuildError(ValueError):
    """Raised when the private catalog or the generated package is unsafe."""


def _fail(message):
    raise PackBuildError(message)


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")


def _limited_text(value, label, maximum, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"{label} must be a {'string' if allow_empty else 'non-empty string'}")
    if len(value) > maximum:
        _fail(f"{label} exceeds {maximum} characters")


def _stable_id(value, label):
    _limited_text(value, label, MAX_STABLE_ID_LENGTH)
    if not STABLE_ID_PATTERN.fullmatch(value):
        _fail(f"{label} must be a portable ASCII stable_id")


def _sha256(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail(f"{label} must be a lowercase 64-character SHA-256")


def _normal_relative(path):
    if not isinstance(path, str) or not path:
        _fail("source path must be a non-empty relative path")
    normal = unicodedata.normalize("NFC", path.replace("\\", "/"))
    if normal.startswith("/") or ":" in normal or any(part in ("", ".", "..") for part in normal.split("/")):
        _fail(f"invalid source path: {path!r}")
    return normal


def _scan_sources(root):
    if not root.is_dir():
        _fail("--source-root must be an existing directory")
    files = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() == ".md":
            relative = _normal_relative(path.relative_to(root).as_posix())
            files[relative] = _sha256_bytes(path.read_bytes())
    return dict(sorted(files.items()))


def _source_snapshot(files):
    digest = hashlib.sha256()
    for relative, file_hash in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_catalog_bytes(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read catalog: {exc}")


def _read_catalog(raw_bytes):
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read catalog JSON: {exc}")
    if not isinstance(data, dict):
        _fail("catalog must be a JSON object")
    return data


def _validate_url(value):
    _limited_text(value, "source URL", MAX_URL_LENGTH)
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(f"invalid source URL: {value!r}")
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        _fail(f"invalid source URL: {value!r}")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or "@" in parsed.netloc
        or not hostname
        or parsed.username
        or parsed.password
    ):
        _fail(f"invalid source URL: {value!r}")
    if port is not None and not 0 <= port <= 65535:
        _fail(f"invalid source URL: {value!r}")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        _fail(f"invalid source URL: {value!r}")
    labels = ascii_hostname.rstrip(".").split(".")
    if not ascii_hostname.rstrip(".") or len(ascii_hostname.rstrip(".")) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        _fail(f"invalid source URL: {value!r}")


def _validate_sources(sources, known_sources=None):
    if not isinstance(sources, list) or not sources:
        _fail("sources must contain at least one source")
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "url"}:
            _fail("sources entries must contain path and URL")
        relative = _normal_relative(source["path"])
        if known_sources is not None and relative not in known_sources:
            _fail(f"source path is not registered: {relative}")
        _validate_url(source["url"])


def _validate_questions(questions, known_sources=None):
    if not isinstance(questions, list) or not questions:
        _fail("questions must be a non-empty list")
    if len(questions) > MAX_QUESTIONS:
        _fail(f"questions exceeds limit of {MAX_QUESTIONS}")
    seen = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            _fail(f"question {index} must be an object")
        kind = question.get("kind")
        result_field = "answer" if kind == "review" else "preparation_prompt" if kind == "prepare" else None
        if result_field is None:
            _fail(f"question {index} has invalid kind")
        if result_field not in question:
            _fail(f"question {index} is missing {result_field}")
        if set(question) != QUESTION_BASE_FIELDS | {result_field}:
            _fail(f"question {index} has invalid fields")
        _stable_id(question["stable_id"], f"question {index} stable_id")
        _limited_text(question["question"], f"question {index} question", MAX_QUESTION_LENGTH)
        _limited_text(question["category"], f"question {index} category", MAX_CATEGORY_LENGTH)
        _limited_text(question[result_field], f"question {index} {result_field}", MAX_RESULT_LENGTH)
        stable_id = question["stable_id"]
        if stable_id in seen:
            _fail(f"duplicate question stable_id: {stable_id}")
        seen.add(stable_id)
        if question["review_status"] != "reviewed":
            _fail(f"question {stable_id} must be reviewed")
        if type(question["retired"]) is not bool:
            _fail(f"question {stable_id} retired must be a boolean")
        _validate_sources(question["sources"], known_sources)
    return seen


def _validate_experiences(experiences, question_ids):
    if not isinstance(experiences, list):
        _fail("experiences must be a list")
    seen_experiences = set()
    referenced = set()
    for experience in experiences:
        if not isinstance(experience, dict) or set(experience) != EXPERIENCE_FIELDS:
            _fail("experience has invalid fields")
        _stable_id(experience["stable_id"], "experience stable_id")
        _limited_text(experience["direction"], "experience direction", MAX_EXPERIENCE_TEXT_LENGTH)
        if experience["kind"] == "interview":
            for field in ("company", "position", "stage"):
                _limited_text(experience[field], f"experience {field}", MAX_EXPERIENCE_TEXT_LENGTH)
        elif experience["kind"] == "topic_set":
            for field in ("company", "position", "stage"):
                _limited_text(experience[field], f"experience {field}", MAX_EXPERIENCE_TEXT_LENGTH, allow_empty=True)
        else:
            _fail("experience has invalid kind")
        if experience["stable_id"] in seen_experiences:
            _fail(f"duplicate experience stable_id: {experience['stable_id']}")
        seen_experiences.add(experience["stable_id"])
        sections = experience["sections"]
        if not isinstance(sections, list) or not sections:
            _fail("experience sections must be a non-empty list")
        in_experience = set()
        section_ids = set()
        recommended_count = 0
        for expected, section in enumerate(sections, start=1):
            if not isinstance(section, dict) or set(section) != {"stable_id", "order", "title", "recommended", "question_ids"}:
                _fail("section has invalid fields")
            _stable_id(section["stable_id"], "section stable_id")
            if section["stable_id"] in section_ids:
                _fail(f"duplicate section stable_id: {section['stable_id']}")
            section_ids.add(section["stable_id"])
            if type(section["order"]) is not int or section["order"] != expected:
                _fail("section order must be consecutive starting at 1")
            _limited_text(section["title"], "section title", MAX_EXPERIENCE_TEXT_LENGTH)
            if type(section["recommended"]) is not bool:
                _fail("section recommended must be a boolean")
            recommended_count += section["recommended"]
            ids = section["question_ids"]
            if not isinstance(ids, list) or not ids:
                _fail("section question_ids must be a non-empty list")
            for stable_id in ids:
                if stable_id not in question_ids:
                    _fail(f"unknown question reference: {stable_id}")
                if stable_id in in_experience:
                    _fail(f"duplicate question reference: {stable_id}")
                in_experience.add(stable_id)
                referenced.add(stable_id)
        if recommended_count != 1:
            _fail("experience must contain exactly one recommended section")
    orphaned = question_ids - referenced
    if orphaned:
        _fail(f"orphan questions: {', '.join(sorted(orphaned))}")


def _expand_references(questions):
    for question in questions:
        if not isinstance(question, dict):
            _fail("question must be an object")
        kind = question.get("kind")
        if kind not in ("review", "prepare"):
            continue
        result_field = "answer" if kind == "review" else "preparation_prompt"
        opposite_ref = "preparation_prompt_ref" if kind == "review" else "answer_ref"
        if opposite_ref in question:
            _fail(f"question {question.get('stable_id', '')} has cross-kind reference")
        allowed = QUESTION_BASE_FIELDS | {result_field, f"{result_field}_ref"}
        if not set(question).issubset(allowed):
            _fail(f"question {question.get('stable_id', '')} has invalid reference fields")
    by_id = {question.get("stable_id"): question for question in questions if isinstance(question, dict)}
    if len(by_id) != len(questions) or None in by_id:
        _fail("duplicate or missing question stable_id")
    resolving = set()
    resolved = {}

    def resolve(stable_id, field):
        key = (stable_id, field)
        if key in resolved:
            return resolved[key]
        if key in resolving:
            _fail("answer reference cycle")
        question = by_id.get(stable_id)
        if question is None or field not in ("answer", "preparation_prompt"):
            _fail(f"unknown answer reference: {stable_id}.{field}")
        expected_kind = "review" if field == "answer" else "prepare"
        if question.get("kind") != expected_kind:
            _fail(f"answer reference type mismatch: {stable_id}.{field}")
        direct = question.get(field)
        reference = question.get(f"{field}_ref")
        if (direct is None) == (reference is None):
            _fail(f"question {stable_id} must contain exactly one {field} result")
        resolving.add(key)
        try:
            if reference is not None:
                if not isinstance(reference, str) or "." not in reference:
                    _fail(f"invalid answer reference: {reference!r}")
                target_id, target_field = reference.rsplit(".", 1)
                if not target_id:
                    _fail(f"invalid answer reference: {reference!r}")
                if target_field != field:
                    _fail(f"answer reference type mismatch: {reference}")
                direct = resolve(target_id, target_field)
            _nonempty(direct, f"question {stable_id} {field}")
            resolved[key] = direct
            return direct
        finally:
            resolving.discard(key)

    output = []
    for original in questions:
        question = copy.deepcopy(original)
        kind = question.get("kind")
        result_field = "answer" if kind == "review" else "preparation_prompt" if kind == "prepare" else None
        if result_field is not None and f"{result_field}_ref" in question:
            question[result_field] = resolve(question.get("stable_id"), result_field)
        question.pop("answer_ref", None)
        question.pop("preparation_prompt_ref", None)
        output.append(question)
    return output


def _validate_catalog(catalog, files):
    expected = {"pack", "source_files", "readme", "frozen_counts", "questions", "experiences"}
    if set(catalog) != expected:
        _fail("catalog has invalid fields")
    pack = catalog["pack"]
    if not isinstance(pack, dict) or set(pack) != {"pack_id", "name", "revision", "display_version"}:
        _fail("catalog pack has invalid fields")
    _stable_id(pack["pack_id"], "pack pack_id")
    _limited_text(pack["name"], "pack name", MAX_DISPLAY_TEXT_LENGTH)
    _limited_text(pack["display_version"], "pack display_version", MAX_DISPLAY_TEXT_LENGTH)
    if type(pack["revision"]) is not int or pack["revision"] < 1:
        _fail("pack revision must be a positive integer")
    declared = catalog["source_files"]
    if not isinstance(declared, dict) or declared != files:
        _fail("catalog source file hash mismatch or unregistered Markdown")
    readme = catalog["readme"]
    frozen = catalog["frozen_counts"]
    if not isinstance(readme, dict) or set(readme) != {"path", "question_count", "experience_count"}:
        _fail("catalog readme has invalid fields")
    if _normal_relative(readme["path"]) != "README.md" or "README.md" not in files:
        _fail("catalog README must be the registered README.md")
    if not isinstance(frozen, dict) or set(frozen) != {"questions", "experiences"}:
        _fail("catalog frozen_counts has invalid fields")
    questions = _expand_references(catalog["questions"]) if isinstance(catalog["questions"], list) else catalog["questions"]
    if not isinstance(questions, list):
        _fail("questions must be a list")
    question_ids = _validate_questions(questions, files)
    _validate_experiences(catalog["experiences"], question_ids)
    counts = {"questions": len(questions), "experiences": len(catalog["experiences"])}
    if frozen != counts or {"question_count": counts["questions"], "experience_count": counts["experiences"]} != {
        "question_count": readme["question_count"], "experience_count": readme["experience_count"]
    }:
        _fail("README or frozen count mismatch")
    return pack, questions, catalog["experiences"]


def validate_pack(manifest, questions, experiences):
    """Wrap the runtime-owned validator while preserving the builder error API."""
    try:
        bagu_runtime.validate_interview_pack_payload(manifest, questions, experiences)
    except bagu_runtime.PackValidationError as exc:
        raise PackBuildError(str(exc)) from exc


def _write_archive(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _validate_archive_size(path):
    if path.stat().st_size > MAX_COMPRESSED_SIZE:
        _fail("pack compressed size exceeds 20 MiB")
    with zipfile.ZipFile(path) as archive:
        if tuple(archive.namelist()) != MEMBERS:
            _fail("pack contains unexpected ZIP members")
        if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_SIZE:
            _fail("pack uncompressed size exceeds 50 MiB")


def build_pack(source_root, catalog_path, output_path):
    """Audit private source/catalog inputs and atomically write one package."""
    root = Path(source_root)
    catalog_path = Path(catalog_path)
    output_path = Path(output_path)
    before = _scan_sources(root)
    catalog_before = _read_catalog_bytes(catalog_path)
    catalog = _read_catalog(catalog_before)
    pack, questions, experiences = _validate_catalog(catalog, before)
    questions_bytes = _canonical_json(questions)
    experiences_bytes = _canonical_json(experiences)
    manifest = {
        "format": "bagu-pack",
        "schema_version": 1,
        "pack_id": pack["pack_id"],
        "name": pack["name"],
        "revision": pack["revision"],
        "display_version": pack["display_version"],
        "source_snapshot_sha256": _source_snapshot(before),
        "question_count": len(questions),
        "experience_count": len(experiences),
        "questions_sha256": _sha256_bytes(questions_bytes),
        "experiences_sha256": _sha256_bytes(experiences_bytes),
    }
    validate_pack(manifest, questions, experiences)
    members = {
        "manifest.json": _canonical_json(manifest),
        "questions.json": questions_bytes,
        "experiences.json": experiences_bytes,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bagu-pack-", suffix=".tmp", dir=output_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        _write_archive(temporary_path, members)
        _validate_archive_size(temporary_path)
        after = _scan_sources(root)
        if before != after or _source_snapshot(before) != _source_snapshot(after):
            _fail("source snapshot changed during build")
        if catalog_before != _read_catalog_bytes(catalog_path):
            _fail("catalog changed during build")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a deterministic audited .bagu-pack")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        build_pack(args.source_root, args.catalog, args.output)
    except PackBuildError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    main()
