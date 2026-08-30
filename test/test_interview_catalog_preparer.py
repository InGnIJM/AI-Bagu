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


def test_insertion_cannot_hide_a_wording_change_without_an_alias(tmp_path):
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
    catalog_path = workspace / "catalog" / "private-catalog.json"
    map_path = workspace / "catalog" / "stable-ids.json"
    catalog_before = catalog_path.read_bytes()
    map_before = map_path.read_bytes()

    (source_root / "Agent面经" / "01_Agent基础" / "01_问题版.md").write_text(
        (
            "## Section\n"
            "- [ ] Inserted question\n"
            "- [ ] Alpha question\n"
            "- [ ] Beta question revised\n"
        ),
        encoding="utf-8",
    )
    del overrides["questions"]["q.agent.01.002"]
    overrides["questions"].update(
        {
            "q.agent.01.003": {
                "category": "general",
                "kind": "review",
                "retired": False,
                "answer": "Inserted answer.",
            },
            "q.agent.01.004": {
                "category": "general",
                "kind": "review",
                "retired": False,
                "answer": "Revised answer.",
            },
        }
    )

    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=3,
        )

    assert "identity_changed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert catalog_path.read_bytes() == catalog_before
    assert map_path.read_bytes() == map_before


@pytest.mark.parametrize(
    ("stored_id", "next_number"),
    [
        ("q.backend.99.777", 778),
        ("q.agent.01.1", 2),
        ("q.agent.01.001", 1),
        ("q.agent.01.001", 3),
    ],
)
def test_rejects_cross_topic_malformed_or_inconsistent_stable_map_entries(
    tmp_path, stored_id, next_number
):
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
    catalog_path = workspace / "catalog" / "private-catalog.json"
    map_path = workspace / "catalog" / "stable-ids.json"
    catalog_before = catalog_path.read_bytes()
    stable_map = read_json(map_path)
    stable_map["topics"]["agent.01"]["entries"][0]["stable_id"] = stored_id
    stable_map["topics"]["agent.01"]["next_question_number"] = next_number
    write_json(map_path, stable_map)
    map_before = map_path.read_bytes()
    if stored_id != "q.agent.01.001":
        overrides["questions"][stored_id] = overrides["questions"].pop(
            "q.agent.01.001"
        )

    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )

    assert "invalid_stable_map" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert catalog_path.read_bytes() == catalog_before
    assert map_path.read_bytes() == map_before


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


def test_blocker_coordinates_never_echo_nonportable_override_keys(tmp_path):
    preparer = load_preparer()
    builder = load_builder()
    source_root = make_source(tmp_path, "## Section\n- [ ] Question\n")
    workspace = tmp_path / "workspace"
    overrides = direct_overrides(["q.agent.01.001"])
    private_path = "C:\\PRIVATE\\secret-source.md"
    private_url = "https://private.example.test/secret-source"
    overrides["questions"][private_path] = {
        "category": "general",
        "kind": "review",
        "retired": False,
        "answer": "Secret answer.",
    }
    overrides["identity_aliases"]["a" * 64] = private_url

    with pytest.raises(preparer.CatalogPreparationError):
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )

    report_path = workspace / "reports" / "normalization-report.json"
    report_bytes = report_path.read_bytes()
    for private_value in (b"C:", b"PRIVATE", b"private.example.test", b"secret-source"):
        assert private_value not in report_bytes
    report = read_json(report_path)
    for blocker in report["blockers"]:
        if "stable_id" in blocker:
            assert len(blocker["stable_id"]) <= builder.MAX_STABLE_ID_LENGTH
            assert builder.STABLE_ID_PATTERN.fullmatch(blocker["stable_id"])
        if "path" in blocker:
            assert ":" not in blocker["path"]
            assert not blocker["path"].startswith(("/", "\\"))


def test_blocker_stable_ids_only_allow_preparer_generated_shapes(tmp_path):
    preparer = load_preparer()
    source_root = make_source(
        tmp_path,
        "## Empty section\n## Used section\n- [ ] Question\n",
    )
    workspace = tmp_path / "workspace"
    overrides = direct_overrides(
        ["q.agent.01.001"], recommended="sec.agent.01.02"
    )
    private_values = (
        "private.secret-token",
        "token:abc123",
        "C:\\PRIVATE\\secret-source.md",
        "https://private.example.test/secret-source",
    )
    for value in private_values:
        overrides["questions"][value] = {
            "category": "general",
            "kind": "review",
            "retired": False,
            "answer": "Secret answer.",
        }
    overrides["topics"]["agent.99"] = {
        "kind": "interview",
        "direction": "agent",
        "company": "Acme",
        "position": "Engineer",
        "stage": "Technical",
        "recommended_section": "sec.agent.99.01",
    }
    overrides["questions"]["q.agent.01.999"] = {
        "category": "general",
        "kind": "review",
        "retired": False,
        "answer": "Unknown answer.",
    }

    with pytest.raises(preparer.CatalogPreparationError):
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )

    report_path = workspace / "reports" / "normalization-report.json"
    report_bytes = report_path.read_bytes()
    for value in private_values:
        assert value.encode("utf-8") not in report_bytes
    stable_ids = {
        blocker["stable_id"]
        for blocker in read_json(report_path)["blockers"]
        if "stable_id" in blocker
    }
    assert {"agent.99", "q.agent.01.999", "sec.agent.01.01"} <= stable_ids


@pytest.mark.parametrize(
    "diagnostic_name",
    ["overrides-template.json", "normalization-report.json"],
)
def test_source_drift_during_diagnostic_writes_blocks_pair_promotion(
    tmp_path, monkeypatch, diagnostic_name
):
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
    catalog_path = workspace / "catalog" / "private-catalog.json"
    map_path = workspace / "catalog" / "stable-ids.json"
    catalog_before = catalog_path.read_bytes()
    map_before = map_path.read_bytes()
    original_atomic_json = preparer._atomic_json
    mutated = False

    def mutate_after_write(path, value):
        nonlocal mutated
        original_atomic_json(path, value)
        if Path(path).name == diagnostic_name and not mutated:
            mutated = True
            (source_root / "README.md").write_text(
                "# Fixture source changed during diagnostics\n", encoding="utf-8"
            )

    monkeypatch.setattr(preparer, "_atomic_json", mutate_after_write)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=1,
        )

    assert "source_changed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert read_json(workspace / "reports" / "normalization-report.json")[
        "status"
    ] == "blocked"
    assert catalog_path.read_bytes() == catalog_before
    assert map_path.read_bytes() == map_before


PAIR_JOURNAL = Path("catalog") / ".catalog-pair-transaction.json"


def setup_pair_upgrade(tmp_path, preparer, prior_state):
    source_root = make_source(tmp_path, "## Section\n- [ ] First question\n")
    seed_workspace = tmp_path / "seed-workspace"
    seed_overrides = direct_overrides(["q.agent.01.001"])
    run_prepare(
        preparer,
        source_root,
        seed_workspace,
        seed_overrides,
        expected_questions=1,
    )
    seed_map = (seed_workspace / "catalog" / "stable-ids.json").read_bytes()
    seed_catalog = (seed_workspace / "catalog" / "private-catalog.json").read_bytes()

    workspace = tmp_path / "workspace"
    catalog_dir = workspace / "catalog"
    catalog_dir.mkdir(parents=True)
    map_path = catalog_dir / "stable-ids.json"
    catalog_path = catalog_dir / "private-catalog.json"
    if prior_state in ("map-only", "both"):
        map_path.write_bytes(seed_map)
    if prior_state in ("catalog-only", "both"):
        catalog_path.write_bytes(seed_catalog)

    (source_root / "Agent面经" / "01_Agent基础" / "01_问题版.md").write_text(
        "## Section\n- [ ] First question\n- [ ] Second question\n",
        encoding="utf-8",
    )
    overrides = direct_overrides(["q.agent.01.001", "q.agent.01.002"])
    return {
        "source_root": source_root,
        "workspace": workspace,
        "overrides": overrides,
        "map_path": map_path,
        "catalog_path": catalog_path,
        "journal_path": workspace / PAIR_JOURNAL,
        "prior_map": seed_map if prior_state in ("map-only", "both") else None,
        "prior_catalog": (
            seed_catalog if prior_state in ("catalog-only", "both") else None
        ),
    }


def assert_prior_pair(fixture):
    for path_key, bytes_key in (
        ("map_path", "prior_map"),
        ("catalog_path", "prior_catalog"),
    ):
        path = fixture[path_key]
        prior = fixture[bytes_key]
        assert path.exists() is (prior is not None)
        if prior is not None:
            assert path.read_bytes() == prior


def recover_then_stop(preparer, fixture):
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
            expected_snapshot="0" * 64,
        )
    assert "source_snapshot_mismatch" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert_prior_pair(fixture)
    assert not fixture["journal_path"].exists()


@pytest.mark.parametrize("prior_state", ["none", "map-only", "catalog-only", "both"])
@pytest.mark.parametrize(
    "failed_target", ["stable-ids.json", "private-catalog.json"]
)
def test_journal_covers_all_prior_pair_states_and_promotion_failures(
    tmp_path, monkeypatch, prior_state, failed_target
):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, prior_state)
    original_replace = preparer.os.replace
    injected = False

    def fail_one_promotion(source, destination):
        nonlocal injected
        if Path(destination).name == failed_target and not injected:
            assert fixture["journal_path"].is_file()
            injected = True
            raise OSError("injected promotion failure")
        return original_replace(source, destination)

    monkeypatch.setattr(preparer.os, "replace", fail_one_promotion)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert "output_promotion_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert_prior_pair(fixture)
    assert fixture["journal_path"].is_file()

    monkeypatch.setattr(preparer.os, "replace", original_replace)
    recover_then_stop(preparer, fixture)


def test_restore_write_failure_keeps_journal_for_next_call(tmp_path, monkeypatch):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "both")
    original_replace = preparer.os.replace
    promotion_failed = False
    restore_failed = False

    def fail_promotion_then_restore(source, destination):
        nonlocal promotion_failed, restore_failed
        name = Path(destination).name
        if name == "private-catalog.json" and not promotion_failed:
            promotion_failed = True
            raise OSError("injected catalog promotion failure")
        if name == "stable-ids.json" and promotion_failed and not restore_failed:
            restore_failed = True
            raise OSError("injected map restore failure")
        return original_replace(source, destination)

    monkeypatch.setattr(preparer.os, "replace", fail_promotion_then_restore)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert "output_recovery_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert fixture["journal_path"].is_file()
    assert fixture["journal_path"].read_bytes() == canonical_bytes(
        read_json(fixture["journal_path"])
    )

    monkeypatch.setattr(preparer.os, "replace", original_replace)
    recover_then_stop(preparer, fixture)


def test_restore_unlink_failure_keeps_journal_for_next_call(tmp_path, monkeypatch):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "none")
    original_replace = preparer.os.replace
    original_unlink = Path.unlink
    promotion_failed = False
    unlink_failed = False

    def fail_catalog_promotion(source, destination):
        nonlocal promotion_failed
        if Path(destination).name == "private-catalog.json" and not promotion_failed:
            promotion_failed = True
            raise OSError("injected catalog promotion failure")
        return original_replace(source, destination)

    def fail_map_restore_unlink(path, *args, **kwargs):
        nonlocal unlink_failed
        if path == fixture["map_path"] and promotion_failed and not unlink_failed:
            unlink_failed = True
            raise OSError("injected map restore unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(preparer.os, "replace", fail_catalog_promotion)
    monkeypatch.setattr(Path, "unlink", fail_map_restore_unlink)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert "output_recovery_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert fixture["journal_path"].is_file()

    monkeypatch.setattr(preparer.os, "replace", original_replace)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    recover_then_stop(preparer, fixture)


def test_journal_cleanup_failure_is_blocked_and_recovered_next_call(
    tmp_path, monkeypatch
):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "both")
    original_unlink = Path.unlink
    injected = False

    def fail_journal_cleanup(path, *args, **kwargs):
        nonlocal injected
        if path == fixture["journal_path"] and not injected:
            injected = True
            raise OSError("injected journal cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_journal_cleanup)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert "output_recovery_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert fixture["journal_path"].is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    recover_then_stop(preparer, fixture)


def test_interruption_after_second_replace_recovers_on_next_call(tmp_path, monkeypatch):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "both")
    original_replace = preparer.os.replace

    class SimulatedInterruption(BaseException):
        pass

    interrupted = False

    def interrupt_after_catalog_replace(source, destination):
        nonlocal interrupted
        result = original_replace(source, destination)
        if Path(destination).name == "private-catalog.json" and not interrupted:
            interrupted = True
            raise SimulatedInterruption()
        return result

    monkeypatch.setattr(preparer.os, "replace", interrupt_after_catalog_replace)
    with pytest.raises(SimulatedInterruption):
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert fixture["journal_path"].is_file()

    monkeypatch.setattr(preparer.os, "replace", original_replace)
    recover_then_stop(preparer, fixture)


def test_malformed_journal_blocks_without_overwriting_pair_or_leaking(tmp_path):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "both")
    malformed = b'{"schema_version":1,"private.secret-token":"PRIVATE_BODY"}'
    fixture["journal_path"].write_bytes(malformed)

    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )

    assert "invalid_output_journal" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert_prior_pair(fixture)
    assert fixture["journal_path"].read_bytes() == malformed
    report_bytes = (
        fixture["workspace"] / "reports" / "normalization-report.json"
    ).read_bytes()
    assert b"private.secret-token" not in report_bytes
    assert b"PRIVATE_BODY" not in report_bytes


def test_journal_write_failure_is_a_redacted_controlled_blocker(tmp_path, monkeypatch):
    preparer = load_preparer()
    fixture = setup_pair_upgrade(tmp_path, preparer, "both")
    original_replace = preparer.os.replace

    def fail_journal_replace(source, destination):
        if Path(destination) == fixture["journal_path"]:
            raise OSError("C:\\PRIVATE\\journal-write-secret")
        return original_replace(source, destination)

    monkeypatch.setattr(preparer.os, "replace", fail_journal_replace)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            fixture["source_root"],
            fixture["workspace"],
            fixture["overrides"],
            expected_questions=2,
        )
    assert "output_journal_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert_prior_pair(fixture)
    assert not fixture["journal_path"].exists()
    report_bytes = (
        fixture["workspace"] / "reports" / "normalization-report.json"
    ).read_bytes()
    assert b"PRIVATE" not in report_bytes
    assert b"journal-write-secret" not in report_bytes


@pytest.mark.parametrize(
    "failed_target", ["stable-ids.json", "private-catalog.json"]
)
def test_pair_promotion_failure_restores_both_prior_outputs(
    tmp_path, monkeypatch, failed_target
):
    preparer = load_preparer()
    source_root = make_source(tmp_path, "## Section\n- [ ] First question\n")
    workspace = tmp_path / "workspace"
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
        "## Section\n- [ ] First question\n- [ ] Second question\n",
        encoding="utf-8",
    )
    overrides["questions"]["q.agent.01.002"] = {
        "category": "general",
        "kind": "review",
        "retired": False,
        "answer": "Second answer.",
    }
    original_replace = preparer.os.replace
    injected = False

    def fail_one_pair_replace(source, destination):
        nonlocal injected
        if Path(destination).name == failed_target and not injected:
            injected = True
            raise OSError("injected pair promotion failure")
        return original_replace(source, destination)

    monkeypatch.setattr(preparer.os, "replace", fail_one_pair_replace)
    with pytest.raises(preparer.CatalogPreparationError) as caught:
        run_prepare(
            preparer,
            source_root,
            workspace,
            overrides,
            expected_questions=2,
        )

    assert "output_promotion_failed" in {
        blocker["code"] for blocker in caught.value.report["blockers"]
    }
    assert read_json(workspace / "reports" / "normalization-report.json")[
        "status"
    ] == "blocked"
    assert catalog_path.read_bytes() == catalog_before
    assert map_path.read_bytes() == map_before


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
