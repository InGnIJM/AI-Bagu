#!/usr/bin/env python3
"""Prepare a strict private interview-pack catalog from frozen Markdown.

The preparer deliberately has no knowledge of the workstation's private source
location or contents.  Every input path and release expectation is supplied by
the caller, and every generated file is confined to the private workspace.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BUILDER_PATH = SCRIPT_DIR / "build_interview_pack.py"
_BUILDER_SPEC = importlib.util.spec_from_file_location(
    "build_interview_pack_for_catalog_preparer", BUILDER_PATH
)
builder = importlib.util.module_from_spec(_BUILDER_SPEC)
_BUILDER_SPEC.loader.exec_module(builder)


DOMAIN_SPECS = (("Agent面经", "agent"), ("后端面经", "backend"))
TOPIC_FILES = (
    "00_原文存档.md",
    "01_问题版.md",
    "02_AI回答版.md",
    "03_总结评价.md",
)
QUESTION_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)\[ \]\s+(\S.*)$")
H2_PATTERN = re.compile(r"^\s*##(?!#)\s+(\S.*)$")
TOPIC_NUMBER_PATTERN = re.compile(r"^(\d+)(?:[_ .-].*)?$")
MARKDOWN_LINK_PATTERN = re.compile(r"\]\(([^)\s]+)")
SCHEME_URL_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>)\]]+")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_BLOCKERS = 1_000

OVERRIDE_FIELDS = {"schema_version", "identity_aliases", "topics", "questions"}
TOPIC_OVERRIDE_FIELDS = {
    "kind",
    "direction",
    "company",
    "position",
    "stage",
    "recommended_section",
}
QUESTION_OVERRIDE_BASE_FIELDS = {"category", "kind", "retired"}
QUESTION_OVERRIDE_OPTIONAL_FIELDS = {"question", "reason", "verification_urls"}
RESULT_FIELDS = {
    "answer",
    "answer_ref",
    "answer_span",
    "preparation_prompt",
    "preparation_prompt_ref",
}
STABLE_MAP_FIELDS = {"schema_version", "topics"}
STABLE_TOPIC_FIELDS = {"next_question_number", "entries"}
STABLE_ENTRY_FIELDS = {
    "stable_id",
    "question_sha256",
    "source_path",
    "last_ordinal",
    "section_identity",
}


class CatalogPreparationError(ValueError):
    """Raised after a redacted blocker report has been written."""

    def __init__(self, report):
        super().__init__(
            f"catalog preparation blocked: {report['counts']['blockers']} blocker(s)"
        )
        self.report = report


class _DuplicateKeyError(ValueError):
    pass


class _Blockers:
    def __init__(self):
        self.items = []
        self.total = 0

    def add(self, code, *, path=None, stable_id=None):
        self.total += 1
        if len(self.items) >= MAX_BLOCKERS:
            return
        item = {"code": code}
        if path is not None:
            safe_path = _safe_report_path(path)
            if safe_path:
                item["path"] = safe_path
        if stable_id is not None and isinstance(stable_id, str):
            item["stable_id"] = stable_id[: builder.MAX_STABLE_ID_LENGTH]
        if item not in self.items:
            self.items.append(item)


def _safe_report_path(value):
    if isinstance(value, Path):
        value = value.as_posix()
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if not value or value.startswith("/") or ":" in value:
        return None
    if any(part in ("", ".", "..") for part in value.split("/")):
        return None
    return value[:1_024]


def _canonical_json(value):
    return builder._canonical_json(value)


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(path):
    raw = Path(path).read_bytes()
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def _normalized_question(value):
    return unicodedata.normalize("NFC", value.strip())


def _question_hash(value):
    return hashlib.sha256(_normalized_question(value).encode("utf-8")).hexdigest()


def _relative(path, root):
    return builder._normal_relative(path.relative_to(root).as_posix())


def _discover_urls(text, blockers, relative_path):
    candidates = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        candidates.append((match.start(1), match.group(1)))
    for match in SCHEME_URL_PATTERN.finditer(text):
        candidates.append((match.start(), match.group(0).rstrip(".,;!?")))
    candidates.sort(key=lambda pair: pair[0])
    urls = []
    seen = set()
    unsafe = False
    for _, candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            builder._validate_url(candidate)
        except builder.PackBuildError:
            unsafe = True
            continue
        urls.append(candidate)
    if unsafe:
        blockers.add("unsafe_url", path=relative_path)
    if not urls:
        blockers.add("missing_url", path=relative_path)
    return urls


def _parse_questions(path, root, domain_key, topic_number, blockers):
    relative = _relative(path, root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        blockers.add("unreadable_source", path=relative)
        return [], []
    sections = []
    current = None
    ordinal = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        heading = H2_PATTERN.match(line)
        if heading:
            current = {
                "stable_id": f"sec.{domain_key}.{topic_number:02d}.{len(sections) + 1:02d}",
                "title": unicodedata.normalize("NFC", heading.group(1).strip()),
                "questions": [],
            }
            sections.append(current)
            continue
        matched = QUESTION_PATTERN.match(line)
        if not matched:
            continue
        ordinal += 1
        question = _normalized_question(matched.group(1))
        if current is None:
            blockers.add("missing_section", path=relative)
            section_identity = None
        else:
            section_identity = current["stable_id"]
        item = {
            "question": question,
            "question_sha256": _question_hash(question),
            "source_path": relative,
            "line": line_number,
            "last_ordinal": ordinal,
            "section_identity": section_identity,
        }
        if current is not None:
            current["questions"].append(item)
    for section in sections:
        if not section["questions"]:
            blockers.add("empty_section", path=relative, stable_id=section["stable_id"])
    if not ordinal:
        blockers.add("missing_questions", path=relative)
    return sections, [item for section in sections for item in section["questions"]]


def _discover_topics(root, blockers):
    topics = []
    registered = {"README.md"} if (root / "README.md").is_file() else set()
    if "README.md" not in registered:
        blockers.add("missing_readme", path="README.md")
    seen_keys = set()
    for domain_directory, domain_key in DOMAIN_SPECS:
        domain_root = root / domain_directory
        if not domain_root.exists():
            continue
        try:
            children = list(domain_root.iterdir())
        except OSError:
            blockers.add("unreadable_source", path=domain_directory)
            continue
        numbered = []
        for child in children:
            if not child.is_dir():
                if child.suffix.casefold() == ".md":
                    blockers.add("unregistered_markdown", path=_relative(child, root))
                continue
            match = TOPIC_NUMBER_PATTERN.fullmatch(child.name)
            if not match:
                blockers.add("invalid_topic_directory", path=_relative(child, root))
                continue
            numbered.append((int(match.group(1)), child.name, child))
        for topic_number, _, topic_root in sorted(numbered):
            topic_key = f"{domain_key}.{topic_number:02d}"
            if topic_key in seen_keys:
                blockers.add("duplicate_topic", path=_relative(topic_root, root))
                continue
            seen_keys.add(topic_key)
            markdown_names = {
                child.name
                for child in topic_root.iterdir()
                if child.is_file() and child.suffix.casefold() == ".md"
            }
            if markdown_names != set(TOPIC_FILES):
                blockers.add("topic_files", path=_relative(topic_root, root))
            for filename in TOPIC_FILES:
                candidate = topic_root / filename
                if candidate.is_file():
                    registered.add(_relative(candidate, root))
            question_path = topic_root / TOPIC_FILES[1]
            if question_path.is_file():
                sections, questions = _parse_questions(
                    question_path, root, domain_key, topic_number, blockers
                )
            else:
                sections, questions = [], []
            archive_path = topic_root / TOPIC_FILES[0]
            urls = []
            if archive_path.is_file():
                try:
                    archive_text = archive_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    blockers.add("unreadable_source", path=_relative(archive_path, root))
                else:
                    urls = _discover_urls(
                        archive_text, blockers, _relative(archive_path, root)
                    )
            topics.append(
                {
                    "key": topic_key,
                    "domain": domain_key,
                    "number": topic_number,
                    "source_path": _relative(question_path, root),
                    "answer_path": _relative(topic_root / TOPIC_FILES[2], root),
                    "archive_path": _relative(archive_path, root),
                    "sections": sections,
                    "questions": questions,
                    "urls": urls,
                }
            )
    return topics, registered


def _load_overrides(path, blockers):
    try:
        value = _strict_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError):
        blockers.add("invalid_overrides_json")
        return {
            "schema_version": None,
            "identity_aliases": {},
            "topics": {},
            "questions": {},
        }
    if not isinstance(value, dict) or set(value) != OVERRIDE_FIELDS:
        blockers.add("invalid_override_fields")
    if not isinstance(value, dict):
        return {
            "schema_version": None,
            "identity_aliases": {},
            "topics": {},
            "questions": {},
        }
    if value.get("schema_version") != 1:
        blockers.add("invalid_override_schema")
    for field in ("identity_aliases", "topics", "questions"):
        if not isinstance(value.get(field), dict):
            blockers.add("invalid_override_fields")
            value[field] = {}
    return value


def _empty_stable_map():
    return {"schema_version": 1, "topics": {}}


def _load_stable_map(path, blockers):
    if not path.exists():
        return _empty_stable_map()
    try:
        value = _strict_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError):
        blockers.add("invalid_stable_map")
        return _empty_stable_map()
    valid = isinstance(value, dict) and set(value) == STABLE_MAP_FIELDS
    valid = valid and value.get("schema_version") == 1 and isinstance(value.get("topics"), dict)
    if valid:
        for topic_key, topic in value["topics"].items():
            if (
                not isinstance(topic_key, str)
                or not isinstance(topic, dict)
                or set(topic) != STABLE_TOPIC_FIELDS
                or type(topic.get("next_question_number")) is not int
                or topic["next_question_number"] < 1
                or not isinstance(topic.get("entries"), list)
            ):
                valid = False
                break
            ids = set()
            for entry in topic["entries"]:
                if (
                    not isinstance(entry, dict)
                    or set(entry) != STABLE_ENTRY_FIELDS
                    or not isinstance(entry.get("stable_id"), str)
                    or not HASH_PATTERN.fullmatch(entry.get("question_sha256", ""))
                    or not _safe_report_path(entry.get("source_path"))
                    or type(entry.get("last_ordinal")) is not int
                    or entry["last_ordinal"] < 1
                    or not isinstance(entry.get("section_identity"), str)
                    or entry["stable_id"] in ids
                ):
                    valid = False
                    break
                ids.add(entry["stable_id"])
            if not valid:
                break
    if not valid:
        blockers.add("invalid_stable_map")
        return _empty_stable_map()
    return value


def _validate_aliases(aliases, prior_map, blockers):
    prior_by_id = {}
    for topic_key, topic in prior_map["topics"].items():
        for entry in topic["entries"]:
            prior_by_id[entry["stable_id"]] = (topic_key, entry)
    valid = {}
    for question_sha256, stable_id in aliases.items():
        if (
            not isinstance(question_sha256, str)
            or not HASH_PATTERN.fullmatch(question_sha256)
            or not isinstance(stable_id, str)
            or stable_id not in prior_by_id
        ):
            blockers.add("invalid_identity_alias", stable_id=stable_id)
            continue
        if stable_id in valid.values():
            blockers.add("invalid_identity_alias", stable_id=stable_id)
            continue
        valid[question_sha256] = stable_id
    return valid, prior_by_id


def _numeric_id(stable_id):
    try:
        return int(stable_id.rsplit(".", 1)[1])
    except (ValueError, IndexError):
        return 0


def _assign_stable_ids(topics, prior_map, aliases, prior_by_id, blockers):
    candidate_topics = {
        key: {
            "next_question_number": value["next_question_number"],
            "entries": [dict(entry) for entry in value["entries"]],
        }
        for key, value in prior_map["topics"].items()
    }
    deleted_ids = []
    alias_suggestions = {}
    for topic in topics:
        topic_key = topic["key"]
        old_topic = prior_map["topics"].get(
            topic_key, {"next_question_number": 1, "entries": []}
        )
        old_entries = [dict(entry) for entry in old_topic["entries"]]
        by_hash = {}
        for entry in old_entries:
            by_hash.setdefault(entry["question_sha256"], []).append(entry)
        duplicate_current_hashes = {
            digest
            for digest in {item["question_sha256"] for item in topic["questions"]}
            if sum(item["question_sha256"] == digest for item in topic["questions"]) > 1
        }
        for digest in sorted(duplicate_current_hashes):
            blockers.add("ambiguous_identity", path=topic["source_path"])

        assigned_ids = set()
        unmatched = []
        for item in topic["questions"]:
            matches = by_hash.get(item["question_sha256"], [])
            if len(matches) == 1 and matches[0]["stable_id"] not in assigned_ids:
                item["stable_id"] = matches[0]["stable_id"]
                assigned_ids.add(item["stable_id"])
            else:
                unmatched.append(item)

        still_unmatched = []
        for item in unmatched:
            aliased_id = aliases.get(item["question_sha256"])
            prior = prior_by_id.get(aliased_id)
            if (
                aliased_id is not None
                and prior is not None
                and prior[0] == topic_key
                and aliased_id not in assigned_ids
            ):
                item["stable_id"] = aliased_id
                assigned_ids.add(aliased_id)
            elif aliased_id is not None:
                blockers.add("invalid_identity_alias", stable_id=aliased_id)
                still_unmatched.append(item)
            else:
                still_unmatched.append(item)

        next_number = max(
            old_topic["next_question_number"],
            max((_numeric_id(entry["stable_id"]) + 1 for entry in old_entries), default=1),
        )
        for item in still_unmatched:
            changed = next(
                (
                    entry
                    for entry in old_entries
                    if entry["stable_id"] not in assigned_ids
                    and entry["source_path"] == item["source_path"]
                    and entry["last_ordinal"] == item["last_ordinal"]
                ),
                None,
            )
            if changed is not None:
                item["stable_id"] = changed["stable_id"]
                assigned_ids.add(item["stable_id"])
                blockers.add("identity_changed", stable_id=item["stable_id"])
                alias_suggestions[item["question_sha256"]] = item["stable_id"]
                continue
            while f"q.{topic_key}.{next_number:03d}" in assigned_ids:
                next_number += 1
            item["stable_id"] = f"q.{topic_key}.{next_number:03d}"
            assigned_ids.add(item["stable_id"])
            next_number += 1

        updated = {entry["stable_id"]: entry for entry in old_entries}
        for item in topic["questions"]:
            if item.get("stable_id") is None:
                continue
            updated[item["stable_id"]] = {
                "stable_id": item["stable_id"],
                "question_sha256": item["question_sha256"],
                "source_path": item["source_path"],
                "last_ordinal": item["last_ordinal"],
                "section_identity": item["section_identity"] or "unresolved",
            }
        deleted_ids.extend(
            entry["stable_id"]
            for entry in old_entries
            if entry["stable_id"] not in assigned_ids
        )
        candidate_topics[topic_key] = {
            "next_question_number": next_number,
            "entries": sorted(updated.values(), key=lambda entry: entry["stable_id"]),
        }
    return (
        {"schema_version": 1, "topics": dict(sorted(candidate_topics.items()))},
        sorted(deleted_ids),
        dict(sorted(alias_suggestions.items())),
    )


def _validate_topic_overrides(topics, values, blockers):
    valid = set()
    known = {topic["key"] for topic in topics}
    for unknown in sorted(set(values) - known):
        blockers.add("unknown_topic_override", stable_id=unknown)
    for topic in topics:
        topic_key = topic["key"]
        value = values.get(topic_key)
        if value is None:
            blockers.add("missing_topic_override", stable_id=topic_key)
            continue
        if not isinstance(value, dict) or set(value) != TOPIC_OVERRIDE_FIELDS:
            blockers.add("invalid_override_fields", stable_id=topic_key)
            continue
        kind = value.get("kind")
        expected_kind = (
            "topic_set" if topic_key in {"agent.01", "agent.09"} else "interview"
        )
        string_fields = ("direction", "company", "position", "stage", "recommended_section")
        if kind not in ("interview", "topic_set") or any(
            not isinstance(value.get(field), str) for field in string_fields
        ):
            blockers.add("invalid_topic_override", stable_id=topic_key)
            continue
        if kind != expected_kind or value["direction"] != topic["domain"]:
            blockers.add("invalid_topic_override", stable_id=topic_key)
            continue
        if not value["direction"].strip() or not value["recommended_section"].strip():
            blockers.add("invalid_topic_override", stable_id=topic_key)
            continue
        if kind == "interview" and any(
            not value[field].strip() for field in ("company", "position", "stage")
        ):
            blockers.add("invalid_topic_override", stable_id=topic_key)
            continue
        section_ids = {section["stable_id"] for section in topic["sections"]}
        if value["recommended_section"] not in section_ids:
            blockers.add("invalid_recommended_section", stable_id=topic_key)
            continue
        valid.add(topic_key)
    return valid


def _resolve_answer_span(value, topic, source_root, blockers, stable_id):
    if not isinstance(value, dict) or set(value) != {"path", "start_line", "end_line"}:
        blockers.add("invalid_answer_span", stable_id=stable_id)
        return None
    path = value.get("path")
    start = value.get("start_line")
    end = value.get("end_line")
    if (
        path != topic["answer_path"]
        or type(start) is not int
        or type(end) is not int
        or start < 1
        or end < start
    ):
        blockers.add("invalid_answer_span", stable_id=stable_id)
        return None
    try:
        lines = (source_root / Path(path)).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        blockers.add("invalid_answer_span", stable_id=stable_id)
        return None
    if end > len(lines):
        blockers.add("invalid_answer_span", stable_id=stable_id)
        return None
    answer = "\n".join(lines[start - 1 : end]).strip()
    if not answer:
        blockers.add("invalid_answer_span", stable_id=stable_id)
        return None
    return answer


def _validate_question_overrides(topics, values, source_root, blockers):
    current = {
        item["stable_id"]: (topic, item)
        for topic in topics
        for item in topic["questions"]
        if item.get("stable_id")
    }
    valid = set()
    resolved = {}
    audit = []
    for unknown in sorted(set(values) - set(current)):
        blockers.add("unknown_question_override", stable_id=unknown)
    for stable_id, (topic, item) in current.items():
        value = values.get(stable_id)
        if value is None:
            blockers.add("missing_question_override", stable_id=stable_id)
            continue
        if not isinstance(value, dict):
            blockers.add("invalid_override_fields", stable_id=stable_id)
            continue
        allowed = QUESTION_OVERRIDE_BASE_FIELDS | QUESTION_OVERRIDE_OPTIONAL_FIELDS | RESULT_FIELDS
        if not QUESTION_OVERRIDE_BASE_FIELDS.issubset(value) or not set(value).issubset(allowed):
            blockers.add("invalid_override_fields", stable_id=stable_id)
            continue
        category = value.get("category")
        kind = value.get("kind")
        if not isinstance(category, str) or not category.strip():
            blockers.add("invalid_category", stable_id=stable_id)
            continue
        if kind not in ("review", "prepare") or type(value.get("retired")) is not bool:
            blockers.add("invalid_question_override", stable_id=stable_id)
            continue
        present_results = RESULT_FIELDS & set(value)
        expected_results = (
            {"answer", "answer_ref", "answer_span"}
            if kind == "review"
            else {"preparation_prompt", "preparation_prompt_ref"}
        )
        if len(present_results) != 1:
            blockers.add("missing_result", stable_id=stable_id)
            continue
        if not present_results.issubset(expected_results):
            blockers.add("invalid_override_fields", stable_id=stable_id)
            continue
        display_question = value.get("question", item["question"])
        if not isinstance(display_question, str) or not display_question.strip():
            blockers.add("invalid_question_override", stable_id=stable_id)
            continue
        question = {
            "stable_id": stable_id,
            "question": display_question,
            "category": category,
            "kind": kind,
            "review_status": "reviewed",
            "retired": value["retired"],
            "sources": [
                {"path": topic["archive_path"], "url": url} for url in topic["urls"]
            ],
        }
        result_field = next(iter(present_results))
        if result_field == "answer_span":
            answer = _resolve_answer_span(
                value[result_field], topic, source_root, blockers, stable_id
            )
            if answer is None:
                continue
            question["answer"] = answer
        else:
            result = value[result_field]
            if not isinstance(result, str) or not result.strip():
                blockers.add("missing_result", stable_id=stable_id)
                continue
            question[result_field] = result
        reason = value.get("reason")
        verification_urls = value.get("verification_urls")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            blockers.add("invalid_question_override", stable_id=stable_id)
            continue
        if verification_urls is not None:
            if not isinstance(verification_urls, list) or not verification_urls:
                blockers.add("invalid_question_override", stable_id=stable_id)
                continue
            verified = []
            invalid_url = False
            for url in verification_urls:
                try:
                    builder._validate_url(url)
                except builder.PackBuildError:
                    invalid_url = True
                    break
                verified.append(url)
            if invalid_url:
                blockers.add("invalid_question_override", stable_id=stable_id)
                continue
        else:
            verified = []
        if reason is not None or verified:
            audit.append(
                {
                    "stable_id": stable_id,
                    "reason": reason or "",
                    "verification_urls": verified,
                }
            )
        resolved[stable_id] = question
        valid.add(stable_id)
    return valid, resolved, sorted(audit, key=lambda item: item["stable_id"])


def _make_template(topics, valid_topics, valid_questions, alias_suggestions):
    topic_template = {}
    question_template = {}
    for topic in topics:
        if topic["key"] not in valid_topics:
            topic_template[topic["key"]] = {
                "kind": "",
                "direction": topic["domain"],
                "company": "",
                "position": "",
                "stage": "",
                "recommended_section": (
                    topic["sections"][0]["stable_id"] if topic["sections"] else ""
                ),
            }
        for item in topic["questions"]:
            stable_id = item.get("stable_id")
            if not stable_id or stable_id in valid_questions:
                continue
            coordinate = f"source {item['source_path']}:{item['line']}"
            if item["section_identity"]:
                coordinate += f" section {item['section_identity']}"
            question_template[stable_id] = {
                "category": "",
                "kind": "",
                "retired": False,
                "reason": coordinate,
            }
    return {
        "schema_version": 1,
        "identity_aliases": alias_suggestions,
        "topics": dict(sorted(topic_template.items())),
        "questions": dict(sorted(question_template.items())),
    }


def _make_catalog(
    files,
    topics,
    topic_overrides,
    questions,
    pack_id,
    name,
    revision,
    display_version,
):
    ordered_questions = []
    experiences = []
    for topic in topics:
        override = topic_overrides[topic["key"]]
        section_payloads = []
        for order, section in enumerate(topic["sections"], start=1):
            question_ids = [item["stable_id"] for item in section["questions"]]
            section_payloads.append(
                {
                    "stable_id": section["stable_id"],
                    "order": order,
                    "title": section["title"],
                    "recommended": (
                        section["stable_id"] == override["recommended_section"]
                    ),
                    "question_ids": question_ids,
                }
            )
            ordered_questions.extend(questions[stable_id] for stable_id in question_ids)
        experiences.append(
            {
                "stable_id": f"exp.{topic['key']}",
                "kind": override["kind"],
                "direction": override["direction"],
                "company": override["company"],
                "position": override["position"],
                "stage": override["stage"],
                "sections": section_payloads,
            }
        )
    counts = {"questions": len(ordered_questions), "experiences": len(experiences)}
    return {
        "pack": {
            "pack_id": pack_id,
            "name": name,
            "revision": revision,
            "display_version": display_version,
        },
        "source_files": files,
        "readme": {
            "path": "README.md",
            "question_count": counts["questions"],
            "experience_count": counts["experiences"],
        },
        "frozen_counts": counts,
        "questions": ordered_questions,
        "experiences": experiences,
    }


def prepare_catalog(
    *,
    source_root,
    workspace,
    pack_id,
    name,
    revision,
    display_version,
    overrides_path,
    expected_source_snapshot,
    expected_questions,
    expected_experiences,
):
    """Prepare the private catalog or raise after writing a redacted report."""
    root = Path(source_root)
    workspace = Path(workspace)
    catalog_path = workspace / "catalog" / "private-catalog.json"
    stable_map_path = workspace / "catalog" / "stable-ids.json"
    template_path = workspace / "overrides" / "overrides-template.json"
    report_path = workspace / "reports" / "normalization-report.json"
    blockers = _Blockers()

    try:
        files_before = builder._scan_sources(root)
        snapshot = builder._source_snapshot(files_before)
    except builder.PackBuildError:
        files_before = {}
        snapshot = hashlib.sha256(b"").hexdigest()
        blockers.add("source_scan")

    if expected_source_snapshot != snapshot:
        blockers.add("source_snapshot_mismatch")
    topics, registered = _discover_topics(root, blockers)
    for relative in sorted(set(files_before) - registered):
        blockers.add("unregistered_markdown", path=relative)
    if set(files_before) != registered:
        blockers.add("source_file_set_mismatch")

    question_count = sum(len(topic["questions"]) for topic in topics)
    if question_count != expected_questions:
        blockers.add("question_count_mismatch")
    if len(topics) != expected_experiences:
        blockers.add("experience_count_mismatch")

    overrides = _load_overrides(overrides_path, blockers)
    prior_map = _load_stable_map(stable_map_path, blockers)
    aliases, prior_by_id = _validate_aliases(
        overrides.get("identity_aliases", {}), prior_map, blockers
    )
    candidate_map, deleted_ids, alias_suggestions = _assign_stable_ids(
        topics, prior_map, aliases, prior_by_id, blockers
    )
    valid_topics = _validate_topic_overrides(
        topics, overrides.get("topics", {}), blockers
    )
    valid_questions, resolved_questions, audit = _validate_question_overrides(
        topics,
        overrides.get("questions", {}),
        root,
        blockers,
    )

    catalog = None
    if (
        len(valid_topics) == len(topics)
        and len(valid_questions) == question_count
        and all(topic["sections"] for topic in topics)
    ):
        catalog = _make_catalog(
            files_before,
            topics,
            overrides["topics"],
            resolved_questions,
            pack_id,
            name,
            revision,
            display_version,
        )
        try:
            builder._validate_catalog(catalog, files_before)
        except builder.PackBuildError:
            blockers.add("invalid_catalog")

    try:
        files_after = builder._scan_sources(root)
    except builder.PackBuildError:
        files_after = None
    if files_after != files_before:
        blockers.add("source_changed")

    template = _make_template(
        topics, valid_topics, valid_questions, alias_suggestions
    )
    report = {
        "schema_version": 1,
        "status": "ready" if blockers.total == 0 else "blocked",
        "source_snapshot_sha256": snapshot,
        "counts": {
            "source_files": len(files_before),
            "topics": len(topics),
            "questions": question_count,
            "experiences": len(topics),
            "blockers": blockers.total,
        },
        "blockers": blockers.items,
        "deleted_ids": deleted_ids,
        "audit": audit,
    }
    _atomic_json(template_path, template)
    _atomic_json(report_path, report)
    if blockers.total:
        raise CatalogPreparationError(report)

    _atomic_json(stable_map_path, candidate_map)
    _atomic_json(catalog_path, catalog)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare a deterministic private interview-pack catalog"
    )
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--display-version", required=True)
    parser.add_argument("--overrides", required=True, type=Path)
    parser.add_argument("--expected-source-snapshot", required=True)
    parser.add_argument("--expected-questions", required=True, type=int)
    parser.add_argument("--expected-experiences", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        prepare_catalog(
            source_root=args.source_root,
            workspace=args.workspace,
            pack_id=args.pack_id,
            name=args.name,
            revision=args.revision,
            display_version=args.display_version,
            overrides_path=args.overrides,
            expected_source_snapshot=args.expected_source_snapshot,
            expected_questions=args.expected_questions,
            expected_experiences=args.expected_experiences,
        )
    except CatalogPreparationError as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    main()
