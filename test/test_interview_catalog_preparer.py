# -*- coding: utf-8 -*-
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[1]
MODULE_PATH = PROJECT_ROOT / "scripts" / "prepare_interview_catalog.py"
BUILDER_PATH = PROJECT_ROOT / "scripts" / "build_interview_pack.py"
ANSWER_PATH = "Agent面经/01_Agent基础/02_AI回答版.md"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_preparer():
    if not MODULE_PATH.is_file():
        pytest.skip("production preparer has not been implemented yet")
    return load_module(MODULE_PATH, "prepare_interview_catalog")


def load_builder():
    return load_module(BUILDER_PATH, "build_interview_pack_for_preparer_tests")


def canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
    return path


def add_topic(
    source_root,
    domain,
    directory,
    question_markdown,
    *,
    answer_markdown="# Answers\nA line one\nA line two\nShared line one\nShared line two\n",
    archive_markdown="Source: https://example.test/interview\n",
    summary_markdown="# Summary\n",
):
    topic = source_root / domain / directory
    topic.mkdir(parents=True, exist_ok=True)
    files = {
        "00_原文存档.md": archive_markdown,
        "01_问题版.md": question_markdown,
        "02_AI回答版.md": answer_markdown,
        "03_总结评价.md": summary_markdown,
    }
    for name, contents in files.items():
        (topic / name).write_text(contents, encoding="utf-8")
    return topic


def make_source(tmp_path, question_markdown=None, **topic_kwargs):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("# Fixture source\n", encoding="utf-8")
    if question_markdown is None:
        question_markdown = (
            "# Topic\n"
            "## Fundamentals\n"
            "- [ ] Explain A -> B / C, including the follow-up chain?\n"
            "1. [ ] Explain the numbered item.\n"
            "## Practice\n"
            "* [ ] Prepare a production incident story.\n"
            "2) [ ] Explain the first shared-answer item.\n"
            "+ [ ] Explain the second shared-answer item.\n"
            "- [ ] Explain an item through a same-kind reference.\n"
        )
    add_topic(
        source_root,
        "Agent面经",
        "01_Agent基础",
        question_markdown,
        **topic_kwargs,
    )
    return source_root


def complete_overrides():
    return {
        "schema_version": 1,
        "identity_aliases": {},
        "topics": {
            "agent.01": {
                "kind": "topic_set",
                "direction": "agent",
                "company": "",
                "position": "",
                "stage": "",
                "recommended_section": "sec.agent.01.01",
            }
        },
        "questions": {
            "q.agent.01.001": {
                "category": "agent",
                "kind": "review",
                "retired": False,
                "answer_span": {
                    "path": ANSWER_PATH,
                    "start_line": 2,
                    "end_line": 3,
                },
            },
            "q.agent.01.002": {
                "category": "agent",
                "kind": "review",
                "retired": False,
                "answer": "A direct reviewed answer.",
                "question": "Pack-only wording for the numbered item.",
                "reason": "Corrected after manual review.",
                "verification_urls": ["https://docs.example.test/review"],
            },
            "q.agent.01.003": {
                "category": "behavioral",
                "kind": "prepare",
                "retired": False,
                "preparation_prompt": "Use context, action, and result.",
            },
            "q.agent.01.004": {
                "category": "agent",
                "kind": "review",
                "retired": False,
                "answer_span": {
                    "path": ANSWER_PATH,
                    "start_line": 4,
                    "end_line": 5,
                },
            },
            "q.agent.01.005": {
                "category": "agent",
                "kind": "review",
                "retired": False,
                "answer_span": {
                    "path": ANSWER_PATH,
                    "start_line": 4,
                    "end_line": 5,
                },
            },
            "q.agent.01.006": {
                "category": "agent",
                "kind": "review",
                "retired": False,
                "answer_ref": "q.agent.01.002.answer",
            },
        },
    }


def source_snapshot(source_root):
    builder = load_builder()
    return builder._source_snapshot(builder._scan_sources(source_root))


def run_prepare(
    preparer,
    source_root,
    workspace,
    overrides,
    *,
    expected_questions=6,
    expected_experiences=1,
    expected_snapshot=None,
):
    overrides_path = write_json(workspace / "input-overrides.json", overrides)
    return preparer.prepare_catalog(
        source_root=source_root,
        workspace=workspace,
        pack_id="fixture-interviews",
        name="Fixture interviews",
        revision=1,
        display_version="2026.08.30-r1",
        overrides_path=overrides_path,
        expected_source_snapshot=(
            expected_snapshot
            if expected_snapshot is not None
            else source_snapshot(source_root)
        ),
        expected_questions=expected_questions,
        expected_experiences=expected_experiences,
    )


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def direct_overrides(question_ids, *, topic_key="agent.01", recommended="sec.agent.01.01"):
    topic_set = topic_key in {"agent.01", "agent.09"}
    return {
        "schema_version": 1,
        "identity_aliases": {},
        "topics": {
            topic_key: {
                "kind": "topic_set" if topic_set else "interview",
                "direction": topic_key.split(".")[0],
                "company": "" if topic_set else "Acme",
                "position": "" if topic_set else "Engineer",
                "stage": "" if topic_set else "Technical",
                "recommended_section": recommended,
            }
        },
        "questions": {
            stable_id: {
                "category": "general",
                "kind": "review",
                "retired": False,
                "answer": f"Reviewed answer {index}.",
            }
            for index, stable_id in enumerate(question_ids, start=1)
        },
    }


def test_module_exposes_prepare_catalog_interface():
    assert MODULE_PATH.is_file(), "prepare_interview_catalog.py is required"
    assert callable(load_preparer().prepare_catalog)


def test_extracts_checkbox_units_sections_answers_and_builder_catalog(tmp_path):
    preparer = load_preparer()
    source_root = make_source(
        tmp_path,
        archive_markdown=(
            "Source: https://example.test/interview and "
            "[duplicate](https://example.test/interview)\n"
        ),
    )
    workspace = tmp_path / "workspace"
    report = run_prepare(preparer, source_root, workspace, complete_overrides())

    assert report["status"] == "ready"
    catalog_path = workspace / "catalog" / "private-catalog.json"
    catalog = read_json(catalog_path)
    assert catalog_path.read_bytes() == canonical_bytes(catalog)
    assert [question["stable_id"] for question in catalog["questions"]] == [
        "q.agent.01.001",
        "q.agent.01.002",
        "q.agent.01.003",
        "q.agent.01.004",
        "q.agent.01.005",
        "q.agent.01.006",
    ]
    assert catalog["questions"][0]["question"] == (
        "Explain A -> B / C, including the follow-up chain?"
    )
    assert catalog["questions"][0]["answer"] == "A line one\nA line two"
    assert catalog["questions"][1]["question"] == (
        "Pack-only wording for the numbered item."
    )
    assert catalog["questions"][2]["preparation_prompt"] == (
        "Use context, action, and result."
    )
    assert catalog["questions"][3]["answer"] == "Shared line one\nShared line two"
    assert catalog["questions"][4]["answer"] == "Shared line one\nShared line two"
    assert catalog["questions"][5]["answer_ref"] == "q.agent.01.002.answer"
    assert all(question["review_status"] == "reviewed" for question in catalog["questions"])
    assert all(
        question["sources"] == [
            {
                "path": "Agent面经/01_Agent基础/00_原文存档.md",
                "url": "https://example.test/interview",
            }
        ]
        for question in catalog["questions"]
    )
    assert catalog["experiences"] == [
        {
            "stable_id": "exp.agent.01",
            "kind": "topic_set",
            "direction": "agent",
            "company": "",
            "position": "",
            "stage": "",
            "sections": [
                {
                    "stable_id": "sec.agent.01.01",
                    "order": 1,
                    "title": "Fundamentals",
                    "recommended": True,
                    "question_ids": ["q.agent.01.001", "q.agent.01.002"],
                },
                {
                    "stable_id": "sec.agent.01.02",
                    "order": 2,
                    "title": "Practice",
                    "recommended": False,
                    "question_ids": [
                        "q.agent.01.003",
                        "q.agent.01.004",
                        "q.agent.01.005",
                        "q.agent.01.006",
                    ],
                },
            ],
        }
    ]
    assert not any(
        key in question
        for question in catalog["questions"]
        for key in ("answer_span", "reason", "verification_urls")
    )

    builder = load_builder()
    files = builder._scan_sources(source_root)
    builder._validate_catalog(catalog, files)
    output = tmp_path / "fixture.bagu-pack"
    builder.build_pack(source_root, catalog_path, output)
    assert output.is_file()

    audit = read_json(workspace / "reports" / "normalization-report.json")["audit"]
    assert audit == [
        {
            "stable_id": "q.agent.01.002",
            "reason": "Corrected after manual review.",
            "verification_urls": ["https://docs.example.test/review"],
        }
    ]
    template_bytes = (workspace / "overrides" / "overrides-template.json").read_bytes()
    for private_body in (b"Explain A", b"A line one", b"A direct reviewed answer"):
        assert private_body not in template_bytes


def test_orders_domains_topics_and_sections_by_contract(tmp_path):
    preparer = load_preparer()
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    for domain, directory, question in (
        ("后端面经", "02_Backend later", "Backend two"),
        ("Agent面经", "09_Agent later", "Agent nine"),
        ("Agent面经", "02_Agent first", "Agent two"),
    ):
        add_topic(
            source_root,
            domain,
            directory,
            f"## First\n- [ ] {question}\n## Second\n1) [ ] {question} follow-up\n",
        )
    overrides = {
        "schema_version": 1,
        "identity_aliases": {},
        "topics": {},
        "questions": {},
    }
    for topic_key in ("agent.02", "agent.09", "backend.02"):
        overrides["topics"][topic_key] = {
            "kind": "topic_set" if topic_key == "agent.09" else "interview",
            "direction": topic_key.split(".")[0],
            "company": "" if topic_key == "agent.09" else "Acme",
            "position": "" if topic_key == "agent.09" else "Engineer",
            "stage": "" if topic_key == "agent.09" else "Technical",
            "recommended_section": f"sec.{topic_key}.02",
        }
        overrides["questions"].update(
            direct_overrides(
                [f"q.{topic_key}.001", f"q.{topic_key}.002"],
                topic_key=topic_key,
                recommended=f"sec.{topic_key}.02",
            )["questions"]
        )
    workspace = tmp_path / "workspace"
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=6,
        expected_experiences=3,
    )
    catalog = read_json(workspace / "catalog" / "private-catalog.json")
    assert [experience["stable_id"] for experience in catalog["experiences"]] == [
        "exp.agent.02",
        "exp.agent.09",
        "exp.backend.02",
    ]
    assert [
        section["stable_id"]
        for experience in catalog["experiences"]
        for section in experience["sections"]
    ] == [
        "sec.agent.02.01",
        "sec.agent.02.02",
        "sec.agent.09.01",
        "sec.agent.09.02",
        "sec.backend.02.01",
        "sec.backend.02.02",
    ]


@pytest.mark.parametrize(
    ("directory", "topic_key", "mutate"),
    [
        (
            "01_Agent基础",
            "agent.01",
            lambda value: value.update(
                kind="interview", company="Acme", position="Engineer", stage="Technical"
            ),
        ),
        (
            "02_Agent基础",
            "agent.02",
            lambda value: value.update(kind="topic_set", company="", position="", stage=""),
        ),
        (
            "01_Agent基础",
            "agent.01",
            lambda value: value.update(direction="backend"),
        ),
    ],
)
def test_rejects_topic_kind_or_direction_that_conflicts_with_discovered_identity(
    tmp_path, directory, topic_key, mutate
):
    preparer = load_preparer()
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    add_topic(
        source_root,
        "Agent面经",
        directory,
        "## Section\n- [ ] Question\n",
    )
    overrides = direct_overrides(
        [f"q.{topic_key}.001"],
        topic_key=topic_key,
        recommended=f"sec.{topic_key}.01",
    )
    mutate(overrides["topics"][topic_key])
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            tmp_path / "workspace",
            overrides,
            expected_questions=1,
        )
    assert "invalid_topic_override" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }


def test_reuses_ids_across_insertion_aliases_changes_and_never_reuses_deleted(tmp_path):
    preparer = load_preparer()
    source_root = make_source(
        tmp_path,
        "## Section\n- [ ] Alpha question\n- [ ] Beta question\n",
    )
    workspace = tmp_path / "workspace"
    overrides = direct_overrides(["q.agent.01.001", "q.agent.01.002"])
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=2,
    )

    question_path = source_root / "Agent面经" / "01_Agent基础" / "01_问题版.md"
    question_path.write_text(
        "## Section\n- [ ] Inserted question\n- [ ] Alpha question\n- [ ] Beta question\n",
        encoding="utf-8",
    )
    overrides["questions"]["q.agent.01.003"] = {
        "category": "general",
        "kind": "review",
        "retired": False,
        "answer": "Inserted answer.",
    }
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=3,
    )
    catalog = read_json(workspace / "catalog" / "private-catalog.json")
    assert [question["stable_id"] for question in catalog["questions"]] == [
        "q.agent.01.003",
        "q.agent.01.001",
        "q.agent.01.002",
    ]
    stable_map_path = workspace / "catalog" / "stable-ids.json"
    catalog_before = (workspace / "catalog" / "private-catalog.json").read_bytes()
    map_before = stable_map_path.read_bytes()

    question_path.write_text(
        "## Section\n- [ ] Inserted question\n- [ ] Alpha question\n- [ ] Beta question revised\n",
        encoding="utf-8",
    )
    with pytest.raises(preparer.CatalogPreparationError):
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=3,
        )
    assert (workspace / "catalog" / "private-catalog.json").read_bytes() == catalog_before
    assert stable_map_path.read_bytes() == map_before

    revised_hash = hashlib.sha256("Beta question revised".encode("utf-8")).hexdigest()
    overrides["identity_aliases"][revised_hash] = "q.agent.01.002"
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=3,
    )
    catalog = read_json(workspace / "catalog" / "private-catalog.json")
    assert catalog["questions"][-1]["stable_id"] == "q.agent.01.002"

    question_path.write_text(
        "## Section\n- [ ] Inserted question\n- [ ] Beta question revised\n",
        encoding="utf-8",
    )
    del overrides["questions"]["q.agent.01.001"]
    report = run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=2,
    )
    assert report["deleted_ids"] == ["q.agent.01.001"]

    question_path.write_text(
        "## Section\n- [ ] Inserted question\n- [ ] Beta question revised\n- [ ] Brand new question\n",
        encoding="utf-8",
    )
    overrides["questions"]["q.agent.01.004"] = {
        "category": "general",
        "kind": "review",
        "retired": False,
        "answer": "Brand new answer.",
    }
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=3,
    )
    catalog = read_json(workspace / "catalog" / "private-catalog.json")
    assert [question["stable_id"] for question in catalog["questions"]] == [
        "q.agent.01.003",
        "q.agent.01.002",
        "q.agent.01.004",
    ]
    stable_map = read_json(stable_map_path)
    assert stable_map["topics"]["agent.01"]["next_question_number"] == 5
    assert {entry["stable_id"] for entry in stable_map["topics"]["agent.01"]["entries"]} == {
        "q.agent.01.001",
        "q.agent.01.002",
        "q.agent.01.003",
        "q.agent.01.004",
    }


@pytest.mark.parametrize(
    ("case", "archive", "questions", "remove_file", "code"),
    [
        ("missing-url", "No link here\n", "## Section\n- [ ] Question\n", None, "missing_url"),
        ("unsafe-url", "[local](file:///private/source)\n", "## Section\n- [ ] Question\n", None, "unsafe_url"),
        ("missing-h2", "https://example.test/x\n", "- [ ] PRIVATE_QUESTION\n", None, "missing_section"),
        ("missing-file", "https://example.test/x\n", "## Section\n- [ ] Question\n", "03_总结评价.md", "topic_files"),
    ],
)
def test_rejects_source_shape_and_urls_without_emitting_catalog(
    tmp_path, case, archive, questions, remove_file, code
):
    preparer = load_preparer()
    source_root = make_source(tmp_path, questions, archive_markdown=archive)
    if remove_file:
        (source_root / "Agent面经" / "01_Agent基础" / remove_file).unlink()
    workspace = tmp_path / "workspace"
    overrides = direct_overrides(["q.agent.01.001"])
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )
    assert code in {blocker["code"] for blocker in caught.value.report["blockers"]}
    assert not (workspace / "catalog" / "private-catalog.json").exists()
    assert not (workspace / "catalog" / "stable-ids.json").exists()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value["topics"].clear(), "missing_topic_override"),
        (lambda value: value["questions"].clear(), "missing_question_override"),
        (
            lambda value: value["questions"]["q.agent.01.001"].pop("answer"),
            "missing_result",
        ),
        (
            lambda value: value["questions"]["q.agent.01.001"].update(category=""),
            "invalid_category",
        ),
        (
            lambda value: value["questions"]["q.agent.01.001"].update(extra=True),
            "invalid_override_fields",
        ),
        (
            lambda value: value.update(extra=True),
            "invalid_override_fields",
        ),
        (
            lambda value: value["questions"].update(
                {
                    "q.agent.01.999": {
                        "category": "general",
                        "kind": "review",
                        "retired": False,
                        "answer": "Unknown.",
                    }
                }
            ),
            "unknown_question_override",
        ),
    ],
)
def test_rejects_missing_unknown_and_extra_override_data(tmp_path, mutate, code):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] Question\n")
    overrides = direct_overrides(["q.agent.01.001"])
    mutate(overrides)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            tmp_path / "workspace",
            overrides,
            expected_questions=1,
        )
    assert code in {blocker["code"] for blocker in caught.value.report["blockers"]}


def test_rejects_duplicate_json_keys_invalid_spans_and_reference_errors(tmp_path):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] Question\n")

    duplicate_workspace = tmp_path / "duplicate"
    duplicate_path = duplicate_workspace / "overrides.json"
    duplicate_path.parent.mkdir(parents=True)
    duplicate_path.write_text(
        '{"schema_version":1,"schema_version":1,"identity_aliases":{},"topics":{},"questions":{}}',
        encoding="utf-8",
    )
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        preparer.prepare_catalog(
            source_root=source_root,
            workspace=duplicate_workspace,
            pack_id="fixture-interviews",
            name="Fixture interviews",
            revision=1,
            display_version="1",
            overrides_path=duplicate_path,
            expected_source_snapshot=source_snapshot(source_root),
            expected_questions=1,
            expected_experiences=1,
        )
    assert caught.value.report["blockers"][0]["code"] == "invalid_overrides_json"

    for case, result, code in (
        (
            "bad-span-path",
            {"answer_span": {"path": "README.md", "start_line": 1, "end_line": 1}},
            "invalid_answer_span",
        ),
        (
            "bad-span-range",
            {"answer_span": {"path": ANSWER_PATH, "start_line": 99, "end_line": 100}},
            "invalid_answer_span",
        ),
        ("unknown-ref", {"answer_ref": "q.agent.01.999.answer"}, "invalid_catalog"),
    ):
        overrides = direct_overrides(["q.agent.01.001"])
        question_override = overrides["questions"]["q.agent.01.001"]
        question_override.pop("answer")
        question_override.update(result)
        with pytest.raises(preparer.CatalogPreparationError) as caught:
            run_prepare(
                preparer,
                source_root,
                tmp_path / case,
                overrides,
                expected_questions=1,
            )
        assert code in {blocker["code"] for blocker in caught.value.report["blockers"]}


@pytest.mark.parametrize(
    ("expected_questions", "expected_experiences", "expected_snapshot", "code"),
    [
        (2, 1, None, "question_count_mismatch"),
        (1, 2, None, "experience_count_mismatch"),
        (1, 1, "0" * 64, "source_snapshot_mismatch"),
    ],
)
def test_rejects_count_and_snapshot_drift(
    tmp_path, expected_questions, expected_experiences, expected_snapshot, code
):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] Question\n")
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            tmp_path / "workspace",
            direct_overrides(["q.agent.01.001"]),
            expected_questions=expected_questions,
            expected_experiences=expected_experiences,
            expected_snapshot=expected_snapshot,
        )
    assert code in {blocker["code"] for blocker in caught.value.report["blockers"]}


def test_failure_report_and_template_are_redacted_and_outputs_are_atomic(tmp_path):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] Initial question\n")
    workspace = tmp_path / "private-workspace"
    overrides = direct_overrides(["q.agent.01.001"])
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=1,
    )
    catalog_path = workspace / "catalog" / "private-catalog.json"
    map_path = workspace / "catalog" / "stable-ids.json"
    catalog_before = catalog_path.read_bytes()
    map_before = map_path.read_bytes()

    (source_root / "Agent面经" / "01_Agent基础" / "01_问题版.md").write_text(
        "- [ ] PRIVATE_SECRET_QUESTION_BODY\n", encoding="utf-8"
    )
    (source_root / "Agent面经" / "01_Agent基础" / "02_AI回答版.md").write_text(
        "PRIVATE_SECRET_ANSWER_BODY\n", encoding="utf-8"
    )
    (source_root / "Agent面经" / "01_Agent基础" / "00_原文存档.md").write_text(
        "https://private.example.test/secret-source\n", encoding="utf-8"
    )
    with pytest.raises(preparer.CatalogPreparationError):
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )
    assert catalog_path.read_bytes() == catalog_before
    assert map_path.read_bytes() == map_before

    report_bytes = (workspace / "reports" / "normalization-report.json").read_bytes()
    template_bytes = (workspace / "overrides" / "overrides-template.json").read_bytes()
    forbidden = (
        b"PRIVATE_SECRET_QUESTION_BODY",
        b"PRIVATE_SECRET_ANSWER_BODY",
        b"private.example.test",
        str(source_root).encode("utf-8"),
        b"E:",
    )
    for value in forbidden:
        assert value not in report_bytes
        assert value not in template_bytes
    report = json.loads(report_bytes)
    assert all(set(blocker) <= {"code", "path", "stable_id"} for blocker in report["blockers"])


def test_outputs_are_byte_deterministic_and_cli_supports_fixture_arguments(tmp_path):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] Question\n")
    workspace = tmp_path / "workspace"
    overrides = direct_overrides(["q.agent.01.001"])
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=1,
    )
    output_paths = [
        workspace / "catalog" / "private-catalog.json",
        workspace / "catalog" / "stable-ids.json",
        workspace / "overrides" / "overrides-template.json",
        workspace / "reports" / "normalization-report.json",
    ]
    before = [path.read_bytes() for path in output_paths]
    run_prepare(
        preparer,
        source_root,
        workspace,
        overrides,
        expected_questions=1,
    )
    assert [path.read_bytes() for path in output_paths] == before

    cli_workspace = tmp_path / "cli-workspace"
    overrides_path = write_json(tmp_path / "cli-overrides.json", overrides)
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--source-root",
            str(source_root),
            "--workspace",
            str(cli_workspace),
            "--pack-id",
            "fixture-interviews",
            "--name",
            "Fixture interviews",
            "--revision",
            "1",
            "--display-version",
            "1",
            "--overrides",
            str(overrides_path),
            "--expected-source-snapshot",
            source_snapshot(source_root),
            "--expected-questions",
            "1",
            "--expected-experiences",
            "1",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (cli_workspace / "catalog" / "private-catalog.json").is_file()
