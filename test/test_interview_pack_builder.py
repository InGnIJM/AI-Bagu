# -*- coding: utf-8 -*-
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "build_interview_pack.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_interview_pack", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_catalog(root, *, mutate=None):
    (root / "interviews").mkdir(parents=True)
    (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (root / "interviews" / "acme.md").write_text("# Acme\n", encoding="utf-8")
    catalog = {
        "pack": {
            "pack_id": "interview-fixture",
            "name": "Fixture interview pack",
            "revision": 1,
            "display_version": "1.0.0",
        },
        "source_files": {
            "README.md": sha256(root / "README.md"),
            "interviews/acme.md": sha256(root / "interviews" / "acme.md"),
        },
        "readme": {
            "path": "README.md",
            "question_count": 2,
            "experience_count": 1,
        },
        "frozen_counts": {"questions": 2, "experiences": 1},
        "questions": [
            {
                "stable_id": "acme-review-1",
                "question": "Explain a transaction.",
                "category": "database",
                "kind": "review",
                "answer": "A transaction is atomic.",
                "review_status": "reviewed",
                "retired": False,
                "sources": [{"path": "interviews/acme.md", "url": "https://example.test/acme"}],
            },
            {
                "stable_id": "acme-prepare-1",
                "question": "Prepare an incident example.",
                "category": "system-design",
                "kind": "prepare",
                "preparation_prompt": "Describe context, action, and result.",
                "review_status": "reviewed",
                "retired": False,
                "sources": [{"path": "interviews/acme.md", "url": "https://example.test/acme"}],
            },
        ],
        "experiences": [
            {
                "stable_id": "acme-backend-2026",
                "kind": "interview",
                "direction": "backend",
                "company": "Acme",
                "position": "engineer",
                "stage": "technical",
                "sections": [
                    {
                        "stable_id": "acme-backend-2026-round-1",
                        "order": 1,
                        "title": "Round one",
                        "recommended": True,
                        "question_ids": ["acme-review-1", "acme-prepare-1"],
                    }
                ],
            }
        ],
    }
    if mutate:
        mutate(catalog)
    catalog_path = root.parent / "private-catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return catalog_path


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["manifest.json", "questions.json", "experiences.json"]
        return {
            name: json.loads(archive.read(name).decode("utf-8"))
            for name in archive.namelist()
        }


def build(tmp_path, *, mutate=None):
    root = tmp_path / "source"
    catalog = write_catalog(root, mutate=mutate)
    output = tmp_path / "fixture.bagu-pack"
    builder = load_builder()
    builder.build_pack(root, catalog, output)
    return builder, root, catalog, output


def make_reference_cycle(catalog):
    catalog["questions"].append(
        {
            "stable_id": "acme-review-2",
            "question": "Explain an isolation level.",
            "category": "database",
            "kind": "review",
            "answer": "Isolation constrains concurrent effects.",
            "review_status": "reviewed",
            "retired": False,
            "sources": [{"path": "interviews/acme.md", "url": "https://example.test/acme"}],
        }
    )
    catalog["experiences"][0]["sections"][0]["question_ids"].append("acme-review-2")
    catalog["frozen_counts"]["questions"] = 3
    catalog["readme"]["question_count"] = 3
    catalog["questions"][0].pop("answer")
    catalog["questions"][0]["answer_ref"] = "acme-review-2.answer"
    catalog["questions"][2].pop("answer")
    catalog["questions"][2]["answer_ref"] = "acme-review-1.answer"


def test_build_is_byte_stable_and_contains_only_canonical_json(tmp_path):
    builder, root, catalog, first = build(tmp_path)
    second = tmp_path / "again.bagu-pack"
    builder.build_pack(root, catalog, second)

    assert first.read_bytes() == second.read_bytes()
    contents = read_archive(first)
    manifest = contents["manifest.json"]
    assert manifest == {
        "format": "bagu-pack",
        "schema_version": 1,
        "pack_id": "interview-fixture",
        "name": "Fixture interview pack",
        "revision": 1,
        "display_version": "1.0.0",
        "source_snapshot_sha256": manifest["source_snapshot_sha256"],
        "question_count": 2,
        "experience_count": 1,
        "questions_sha256": manifest["questions_sha256"],
        "experiences_sha256": manifest["experiences_sha256"],
    }
    assert {question["stable_id"] for question in contents["questions.json"]} == {
        "acme-review-1", "acme-prepare-1"
    }
    assert all("answer_ref" not in question for question in contents["questions.json"])
    builder.validate_pack(manifest, contents["questions.json"], contents["experiences.json"])


def test_answer_reference_is_expanded_without_leaking_reference_field(tmp_path):
    def mutate(catalog):
        catalog["questions"].append(
            {
                "stable_id": "acme-review-2",
                "question": "Explain a savepoint.",
                "category": "database",
                "kind": "review",
                "answer": "A savepoint marks a partial rollback point.",
                "review_status": "reviewed",
                "retired": False,
                "sources": [{"path": "interviews/acme.md", "url": "https://example.test/acme"}],
            }
        )
        catalog["experiences"][0]["sections"][0]["question_ids"].append("acme-review-2")
        catalog["frozen_counts"]["questions"] = 3
        catalog["readme"]["question_count"] = 3
        catalog["questions"][0].pop("answer")
        catalog["questions"][0]["answer_ref"] = "acme-review-2.answer"

    _, _, _, output = build(tmp_path, mutate=mutate)
    question = read_archive(output)["questions.json"][0]
    assert question["answer"] == "A savepoint marks a partial rollback point."
    assert not any(key.endswith("_ref") for key in question)


def test_answer_reference_supports_dotted_stable_ids_deterministically(tmp_path):
    def mutate(catalog):
        catalog["questions"].append({
            "stable_id": "java.gc",
            "question": "Explain garbage collection.",
            "category": "java",
            "kind": "review",
            "answer": "Garbage collection reclaims unreachable objects.",
            "review_status": "reviewed",
            "retired": False,
            "sources": [{
                "path": "interviews/acme.md",
                "url": "https://example.test/acme",
            }],
        })
        catalog["questions"][0].pop("answer")
        catalog["questions"][0]["answer_ref"] = "java.gc.answer"
        catalog["experiences"][0]["sections"][0]["question_ids"].append("java.gc")
        catalog["frozen_counts"]["questions"] = 3
        catalog["readme"]["question_count"] = 3

    builder, root, catalog, output = build(tmp_path, mutate=mutate)
    questions = {
        question["stable_id"]: question
        for question in read_archive(output)["questions.json"]
    }
    assert questions["acme-review-1"]["answer"] == (
        "Garbage collection reclaims unreachable objects."
    )
    assert "answer_ref" not in questions["acme-review-1"]

    second = tmp_path / "dotted-reference-again.bagu-pack"
    builder.build_pack(root, catalog, second)
    assert output.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        ("unreviewed", lambda c: c["questions"][0].update(review_status="draft"), "reviewed"),
        ("duplicate-question", lambda c: c["questions"].append(dict(c["questions"][0])), "duplicate"),
        ("bad-type", lambda c: c["questions"][0].update(kind="essay"), "kind"),
        ("blank-category", lambda c: c["questions"][0].update(category=""), "category"),
        ("missing-answer", lambda c: c["questions"][0].pop("answer"), "answer"),
        ("missing-prompt", lambda c: c["questions"][1].pop("preparation_prompt"), "preparation_prompt"),
        ("bad-source", lambda c: c["questions"][0].update(sources=[]), "sources"),
        ("bad-url", lambda c: c["questions"][0]["sources"][0].update(url="file:///private"), "URL"),
        ("bad-order", lambda c: c["experiences"][0]["sections"][0].update(order=2), "order"),
        ("orphan", lambda c: c["questions"].append({**c["questions"][0], "stable_id": "orphan"}), "orphan"),
        ("unknown-reference", lambda c: c["experiences"][0]["sections"][0].update(question_ids=["missing"]), "unknown"),
        ("duplicate-reference", lambda c: c["experiences"][0]["sections"][0].update(question_ids=["acme-review-1", "acme-review-1"]), "duplicate"),
        ("unknown-answer-reference", lambda c: c["questions"][0].update(answer_ref="missing.answer") or c["questions"][0].pop("answer"), "unknown"),
        ("reference-cycle", make_reference_cycle, "cycle"),
    ],
)
def test_rejects_invalid_catalog_contracts(tmp_path, name, mutate, message):
    root = tmp_path / name / "source"
    catalog = write_catalog(root, mutate=mutate)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match=message):
        builder.build_pack(root, catalog, tmp_path / name / "out.bagu-pack")


def test_rejects_unregistered_markdown_and_catalog_hash_or_count_drift(tmp_path):
    root = tmp_path / "source"
    catalog = write_catalog(root)
    builder = load_builder()
    (root / "unregistered.md").write_text("new", encoding="utf-8")
    with pytest.raises(builder.PackBuildError, match="unregistered"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")

    (root / "unregistered.md").unlink()
    data = json.loads(catalog.read_text(encoding="utf-8"))
    data["source_files"]["README.md"] = "0" * 64
    catalog.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(builder.PackBuildError, match="hash"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")

    data["source_files"]["README.md"] = sha256(root / "README.md")
    data["readme"]["question_count"] = 9
    catalog.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(builder.PackBuildError, match="count"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")


def test_rejects_source_bytes_that_change_during_build(tmp_path, monkeypatch):
    root = tmp_path / "source"
    catalog = write_catalog(root)
    builder = load_builder()
    original = builder._write_archive

    def drift(*args, **kwargs):
        (root / "README.md").write_text("changed", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_write_archive", drift)
    with pytest.raises(builder.PackBuildError, match="source snapshot"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")


def test_validator_rejects_manifest_content_hash_drift(tmp_path):
    builder, _, _, output = build(tmp_path)
    contents = read_archive(output)
    contents["manifest.json"]["question_count"] = 99
    with pytest.raises(builder.PackBuildError, match="question_count"):
        builder.validate_pack(
            contents["manifest.json"], contents["questions.json"], contents["experiences.json"]
        )


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        ("missing-section-id", lambda c: c["experiences"][0]["sections"][0].pop("stable_id"), "section"),
        ("missing-recommended", lambda c: c["experiences"][0]["sections"][0].update(recommended=False), "recommended"),
        ("wrong-recommended-type", lambda c: c["experiences"][0]["sections"][0].update(recommended=1), "recommended"),
        ("bad-section-id", lambda c: c["experiences"][0]["sections"][0].update(stable_id="no space"), "stable_id"),
        ("review-extra-prepare-ref", lambda c: c["questions"][0].update(preparation_prompt_ref="acme-prepare-1.preparation_prompt"), "reference"),
        ("prepare-extra-answer-ref", lambda c: c["questions"][1].update(answer_ref="acme-review-1.answer"), "reference"),
        ("long-category", lambda c: c["questions"][0].update(category="x" * 101), "category"),
        ("long-question", lambda c: c["questions"][0].update(question="x" * 2001), "question"),
        ("long-answer", lambda c: c["questions"][0].update(answer="x" * 100001), "answer"),
        ("long-url", lambda c: c["questions"][0]["sources"][0].update(url="https://example.test/" + "x" * 2048), "URL"),
        ("invalid-question-id", lambda c: c["questions"][0].update(stable_id="bad id"), "stable_id"),
        ("long-pack-id", lambda c: c["pack"].update(pack_id="x" * 129), "pack_id"),
        ("long-pack-name", lambda c: c["pack"].update(name="x" * 201), "name"),
        ("long-display-version", lambda c: c["pack"].update(display_version="x" * 201), "display_version"),
        ("long-direction", lambda c: c["experiences"][0].update(direction="x" * 201), "direction"),
        ("long-section-title", lambda c: c["experiences"][0]["sections"][0].update(title="x" * 201), "title"),
    ],
)
def test_rejects_missing_section_contracts_cross_kind_refs_and_oversized_text(tmp_path, name, mutate, message):
    root = tmp_path / name / "source"
    catalog = write_catalog(root, mutate=mutate)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match=message):
        builder.build_pack(root, catalog, tmp_path / name / "out.bagu-pack")


def test_rejects_duplicate_section_stable_id_within_experience(tmp_path):
    def mutate(catalog):
        section = dict(catalog["experiences"][0]["sections"][0])
        section["order"] = 2
        section["recommended"] = False
        catalog["experiences"][0]["sections"].append(section)

    root = tmp_path / "source"
    catalog = write_catalog(root, mutate=mutate)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match="duplicate section stable_id"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")


def test_topic_set_allows_blank_company_position_and_stage_but_interview_does_not(tmp_path):
    def topic_set(catalog):
        catalog["experiences"][0].update(kind="topic_set", company="", position="", stage="")

    _, _, _, output = build(tmp_path, mutate=topic_set)
    assert read_archive(output)["experiences.json"][0]["kind"] == "topic_set"

    def interview(catalog):
        catalog["experiences"][0].update(company="")

    root = tmp_path / "interview" / "source"
    catalog = write_catalog(root, mutate=interview)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match="company"):
        builder.build_pack(root, catalog, tmp_path / "interview" / "out.bagu-pack")


@pytest.mark.parametrize(
    "url",
    [
        " https://example.test",
        "https://example.test ",
        "https://exa\nmple.test",
        "https://user@example.test/path",
        "https://@example.test/path",
        "https:///path",
        "https://example.test:bad",
        "https://example.test:99999",
        "https://-bad.example.test",
        "https://bad_.example.test",
        "https://\ud800.example.test",
    ],
)
def test_url_validator_rejects_unsafe_or_malformed_http_urls(url):
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match="URL"):
        builder._validate_url(url)


@pytest.mark.parametrize("url", ["http://example.test/a", "https://example.test:443/a?x=1#part", "https://xn--bcher-kva.example/"])
def test_url_validator_allows_safe_http_urls(url):
    load_builder()._validate_url(url)


def review_to_prepare_reference(catalog):
    catalog["questions"][0].pop("answer")
    catalog["questions"][0]["answer_ref"] = "acme-prepare-1.preparation_prompt"


def prepare_to_review_reference(catalog):
    catalog["questions"][1].pop("preparation_prompt")
    catalog["questions"][1]["preparation_prompt_ref"] = "acme-review-1.answer"


@pytest.mark.parametrize("mutate", [review_to_prepare_reference, prepare_to_review_reference])
def test_rejects_result_references_to_a_different_question_kind(tmp_path, mutate):
    root = tmp_path / "source"
    catalog = write_catalog(root, mutate=mutate)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match="reference"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")


def test_rejects_catalog_bytes_that_change_during_build(tmp_path, monkeypatch):
    root = tmp_path / "source"
    catalog = write_catalog(root)
    builder = load_builder()
    original = builder._write_archive

    def drift(*args, **kwargs):
        catalog.write_bytes(catalog.read_bytes() + b"\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_write_archive", drift)
    with pytest.raises(builder.PackBuildError, match="catalog"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")


@pytest.mark.parametrize("field", ["source_snapshot_sha256", "questions_sha256", "experiences_sha256"])
@pytest.mark.parametrize("bad_hash", ["a" * 63, "A" * 64, "g" * 64])
def test_validator_requires_lowercase_64_character_hashes(tmp_path, field, bad_hash):
    builder, _, _, output = build(tmp_path)
    contents = read_archive(output)
    contents["manifest.json"][field] = bad_hash
    with pytest.raises(builder.PackBuildError, match=field):
        builder.validate_pack(
            contents["manifest.json"], contents["questions.json"], contents["experiences.json"]
        )


def test_enforces_documented_question_and_archive_size_limits(tmp_path, monkeypatch):
    builder, _, _, output = build(tmp_path)
    assert builder.MAX_QUESTIONS == 10_000
    assert builder.MAX_COMPRESSED_SIZE == 20 * 1024 * 1024
    assert builder.MAX_UNCOMPRESSED_SIZE == 50 * 1024 * 1024

    questions = read_archive(output)["questions.json"]
    monkeypatch.setattr(builder, "MAX_QUESTIONS", 1)
    with pytest.raises(builder.PackBuildError, match="questions exceeds"):
        builder._validate_questions(questions)

    monkeypatch.setattr(builder, "MAX_COMPRESSED_SIZE", 1)
    with pytest.raises(builder.PackBuildError, match="compressed"):
        builder._validate_archive_size(output)

    monkeypatch.setattr(builder, "MAX_COMPRESSED_SIZE", output.stat().st_size)
    monkeypatch.setattr(builder, "MAX_UNCOMPRESSED_SIZE", 1)
    with pytest.raises(builder.PackBuildError, match="uncompressed"):
        builder._validate_archive_size(output)


def test_builder_uses_runtime_public_payload_validator_for_portable_source_paths(tmp_path):
    def mutate(catalog):
        catalog["questions"][0]["sources"][0]["path"] = "interviews\\acme.md"

    root = tmp_path / "source"
    catalog = write_catalog(root, mutate=mutate)
    builder = load_builder()
    with pytest.raises(builder.PackBuildError, match="source path"):
        builder.build_pack(root, catalog, tmp_path / "out.bagu-pack")
