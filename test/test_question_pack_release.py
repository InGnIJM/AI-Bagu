"""Release-pack contracts use only tiny deterministic synthetic archives."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

VERSION = {"versionName": "0.1.0-beta.5", "versionCode": 5, "channel": "beta"}
PACK_NAME = "ai-bagu-synthetic-interviews-r1.bagu-pack"
PACK_ID = "synthetic-interview-pack"
PACK_NOTES = (
    "题包答案由 AI 生成，并由维护者接受为已复核参考答案；"
    "不是原帖作者或面试公司的标准答案。"
)
DESCRIPTOR_FIELDS = (
    "schema_version", "versionName", "file_name", "sha256", "pack_id",
    "revision", "display_version", "question_count", "experience_count",
)


def release_metadata():
    spec = importlib.util.spec_from_file_location(
        "task4_release_metadata", SCRIPTS / "release_metadata.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_github():
    spec = importlib.util.spec_from_file_location(
        "task4_release_github", SCRIPTS / "release_github.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_pack_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def make_pack(*, pack_id=PACK_ID, revision=1, display_version="2026.08.30-r1",
              question_count=1, experience_count=1, answer="Synthetic answer."):
    questions = [{
        "stable_id": "q.synthetic.001",
        "question": "Explain the synthetic transaction fixture.",
        "category": "database",
        "kind": "review",
        "answer": answer,
        "review_status": "reviewed",
        "retired": False,
        "sources": [{"path": "fixtures/interview.md", "url": "https://example.test/interview"}],
    }]
    experiences = [{
        "stable_id": "exp.synthetic.01",
        "kind": "interview",
        "direction": "backend",
        "company": "Synthetic",
        "position": "engineer",
        "stage": "technical",
        "sections": [{
            "stable_id": "sec.synthetic.01.01",
            "order": 1,
            "title": "Round one",
            "recommended": True,
            "question_ids": ["q.synthetic.001"],
        }],
    }]
    question_bytes = canonical_pack_json(questions)
    experience_bytes = canonical_pack_json(experiences)
    manifest = {
        "format": "bagu-pack",
        "schema_version": 1,
        "pack_id": pack_id,
        "name": "Synthetic interview pack",
        "revision": revision,
        "display_version": display_version,
        "source_snapshot_sha256": "1" * 64,
        "question_count": question_count,
        "experience_count": experience_count,
        "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        "experiences_sha256": hashlib.sha256(experience_bytes).hexdigest(),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_pack_json(manifest))
        archive.writestr("questions.json", question_bytes)
        archive.writestr("experiences.json", experience_bytes)
    return output.getvalue()


def descriptor_value(pack, **changes):
    value = {
        "schema_version": 1,
        "versionName": VERSION["versionName"],
        "file_name": PACK_NAME,
        "sha256": hashlib.sha256(pack).hexdigest(),
        "pack_id": PACK_ID,
        "revision": 1,
        "display_version": "2026.08.30-r1",
        "question_count": 1,
        "experience_count": 1,
    }
    value.update(changes)
    return value


def descriptor_bytes(value):
    # Independent literal layout: a field reordering or compact encoding is invalid.
    lines = ["{"]
    for index, name in enumerate(DESCRIPTOR_FIELDS):
        comma = "," if index + 1 < len(DESCRIPTOR_FIELDS) else ""
        lines.append(f"  {json.dumps(name)}: {json.dumps(value[name], ensure_ascii=False)}{comma}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_descriptor(root, pack, **changes):
    value = descriptor_value(pack, **changes)
    path = root / "docs/releases/0.1.0-beta.5-question-pack.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(descriptor_bytes(value))
    return path, value


def make_seven_asset_directory(tmp_path, module=None):
    module = module or release_metadata()
    pack = make_pack()
    pack_path = tmp_path / "formal" / PACK_NAME
    pack_path.parent.mkdir()
    pack_path.write_bytes(pack)
    descriptor = module.parse_question_pack_descriptor(
        descriptor_bytes(descriptor_value(pack)), VERSION
    )
    directory = tmp_path / "delivery"
    directory.mkdir()
    (directory / module.apk_name(VERSION)).write_bytes(b"synthetic validated APK")
    module.write_metadata(
        directory, VERSION, PACK_NOTES, "2026-08-30T00:00:00Z",
        question_pack=pack_path, descriptor=descriptor,
    )
    return directory, pack_path, descriptor


def test_descriptor_accepts_only_exact_canonical_nine_field_bytes():
    module = release_metadata()
    pack = make_pack()
    value = descriptor_value(pack)
    raw = descriptor_bytes(value)

    assert raw == module.json_bytes(value)
    assert tuple(module.parse_question_pack_descriptor(raw, VERSION)) == DESCRIPTOR_FIELDS

    invalid = [
        raw.replace(b'  "schema_version": 1,\n', b'  "schema_version": 1,\n  "schema_version": 1,\n'),
        raw.replace(b'  "experience_count": 1\n', b''),
        raw.replace(b'  "experience_count": 1\n', b'  "experience_count": 1,\n  "extra": 1\n'),
        b"\xef\xbb\xbf" + raw,
        raw[:-1],
        raw.replace(b'  "versionName"', b' "versionName"'),
        b"\xff",
    ]
    reordered = dict(value)
    first = reordered.pop("schema_version")
    reordered["schema_version"] = first
    invalid.append(module.json_bytes(reordered))
    invalid.append(b" " * (module.MAX_DESCRIPTOR + 1))
    for candidate in invalid:
        with pytest.raises(ValueError):
            module.parse_question_pack_descriptor(candidate, VERSION)


@pytest.mark.parametrize("field,value", [
    ("file_name", "../interviews.bagu-pack"),
    ("file_name", "题包.bagu-pack"),
    ("file_name", "interviews.zip"),
    ("file_name", "interviews..bagu-pack"),
    ("file_name", "CON.bagu-pack"),
    ("sha256", "A" * 64),
    ("sha256", "0" * 63),
    ("pack_id", ""),
    ("display_version", ""),
    ("revision", True),
    ("revision", 0),
    ("question_count", True),
    ("question_count", 0),
    ("question_count", 10001),
    ("experience_count", 0),
    ("versionName", "0.1.0-beta.4"),
])
def test_descriptor_rejects_unsafe_identity_and_integer_values(field, value):
    module = release_metadata()
    candidate = descriptor_value(make_pack(), **{field: value})
    with pytest.raises(ValueError):
        module.parse_question_pack_descriptor(descriptor_bytes(candidate), VERSION)


@pytest.mark.parametrize("mismatch,pack_changes,descriptor_changes", [
    ("pack_id", {"pack_id": "another-pack"}, {}),
    ("revision", {"revision": 2}, {}),
    ("display", {"display_version": "other"}, {}),
    ("questions", {}, {"question_count": 2}),
    ("experiences", {}, {"experience_count": 2}),
])
def test_external_pack_binding_rejects_every_manifest_mismatch(
        tmp_path, mismatch, pack_changes, descriptor_changes):
    module = release_metadata()
    pack = make_pack(**pack_changes)
    path = tmp_path / PACK_NAME
    path.write_bytes(pack)
    descriptor = module.parse_question_pack_descriptor(
        descriptor_bytes(descriptor_value(pack, **descriptor_changes)), VERSION
    )
    with pytest.raises(ValueError, match=mismatch):
        module.read_bound_question_pack(path, descriptor)


def test_external_pack_binding_rejects_hash_name_size_and_symlink(tmp_path, monkeypatch):
    module = release_metadata()
    pack = make_pack()
    descriptor = module.parse_question_pack_descriptor(
        descriptor_bytes(descriptor_value(pack)), VERSION
    )
    wrong_name = tmp_path / "wrong.bagu-pack"
    wrong_name.write_bytes(pack)
    with pytest.raises(ValueError, match="name"):
        module.read_bound_question_pack(wrong_name, descriptor)

    exact = tmp_path / PACK_NAME
    exact.write_bytes(pack + b"tampered")
    with pytest.raises(ValueError, match="hash"):
        module.read_bound_question_pack(exact, descriptor)

    exact.write_bytes(b"x" * (module.MAX_PACK + 1))
    with pytest.raises(ValueError, match="20 MiB|size"):
        module.read_bound_question_pack(exact, descriptor)

    exact.write_bytes(pack)
    link = tmp_path / "link" / PACK_NAME
    link.parent.mkdir()
    link.write_bytes(pack)
    real_lstat = module.os.lstat
    target_stat = real_lstat(link)
    link_stat = os.stat_result((stat.S_IFLNK | 0o777, *tuple(target_stat)[1:]))
    monkeypatch.setattr(module.os, "lstat", lambda path: (
        link_stat if Path(path) == link else real_lstat(path)
    ))
    with pytest.raises(ValueError, match="symlink|regular"):
        module.read_bound_question_pack(link, descriptor)


def test_descriptor_loader_rejects_a_symlink_before_parsing(tmp_path, monkeypatch):
    module = release_metadata()
    pack = make_pack()
    path = tmp_path / "descriptor.json"
    path.write_bytes(descriptor_bytes(descriptor_value(pack)))
    real_lstat = module.os.lstat
    target_stat = real_lstat(path)
    link_stat = os.stat_result((stat.S_IFLNK | 0o777, *tuple(target_stat)[1:]))
    monkeypatch.setattr(module.os, "lstat", lambda candidate: (
        link_stat if Path(candidate) == path else real_lstat(candidate)
    ))
    with pytest.raises(ValueError, match="symlink|regular"):
        module.read_question_pack_descriptor(path, VERSION)


def test_seven_asset_metadata_is_exact_sorted_and_feed_remains_apk_only(tmp_path):
    module = release_metadata()
    directory, _, descriptor = make_seven_asset_directory(tmp_path, module)

    paths = module.validate_directory(directory, VERSION, descriptor=descriptor)
    assert [path.name for path in paths] == sorted([
        module.apk_name(VERSION), PACK_NAME, "SHA256SUMS", "certificate-sha256.txt",
        "update.json", "INSTALL.md", "RELEASE_NOTES.md",
    ])
    apk = directory / module.apk_name(VERSION)
    expected = (
        f"{hashlib.sha256((directory / PACK_NAME).read_bytes()).hexdigest()} *{PACK_NAME}\n"
        f"{hashlib.sha256(apk.read_bytes()).hexdigest()} *{apk.name}\n"
    ).encode("ascii")
    assert (directory / "SHA256SUMS").read_bytes() == expected
    assert not (directory / "0.1.0-beta.5-question-pack.json").exists()
    release = json.loads((directory / "update.json").read_bytes())["release"]
    assert not {"questionPack", "questionPackUrl", "packId", "packSha256"} & set(release)


def test_metadata_copies_the_validated_pack_snapshot_not_a_later_path_value(tmp_path, monkeypatch):
    module = release_metadata()
    pack = make_pack()
    replacement = make_pack(answer="later path value")
    source = tmp_path / "formal" / PACK_NAME
    source.parent.mkdir()
    source.write_bytes(pack)
    descriptor = module.parse_question_pack_descriptor(
        descriptor_bytes(descriptor_value(pack)), VERSION
    )
    directory = tmp_path / "delivery"
    directory.mkdir()
    (directory / module.apk_name(VERSION)).write_bytes(b"synthetic APK")
    original_disclosures = module._validate_pack_disclosures

    def mutate_after_binding(install, notes, candidate):
        source.write_bytes(replacement)
        return original_disclosures(install, notes, candidate)

    monkeypatch.setattr(module, "_validate_pack_disclosures", mutate_after_binding)
    module.write_metadata(
        directory, VERSION, PACK_NOTES, "2026-08-30T00:00:00Z",
        question_pack=source, descriptor=descriptor,
    )
    assert source.read_bytes() == replacement
    assert (directory / PACK_NAME).read_bytes() == pack


def test_descriptor_version_cannot_silently_downgrade_beta5_to_six_assets(tmp_path):
    module = release_metadata()
    pack = make_pack()
    descriptor = module.parse_question_pack_descriptor(
        descriptor_bytes(descriptor_value(pack)), VERSION
    )
    directory, _, _ = make_seven_asset_directory(tmp_path, module)
    (directory / PACK_NAME).unlink()
    with pytest.raises(ValueError, match="allowlist|pack"):
        module.validate_directory(directory, VERSION, descriptor=descriptor)

    legacy = {"versionName": "0.1.0-beta.4", "versionCode": 4, "channel": "beta"}
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / module.apk_name(legacy)).write_bytes(b"legacy apk")
    module.write_metadata(legacy_dir, legacy, "legacy notes", "2026-08-29T00:00:00Z")
    (legacy_dir / PACK_NAME).write_bytes(pack)
    with pytest.raises(ValueError, match="allowlist"):
        module.validate_directory(legacy_dir, legacy)


@pytest.mark.parametrize("mutation", [
    "tamper-pack", "extra-descriptor", "duplicate-row", "reverse-rows",
    "text-marker", "bad-name", "bad-encoding", "install-disclosure", "notes-disclosure",
])
def test_seven_asset_validation_rejects_tampering_and_noncanonical_metadata(tmp_path, mutation):
    module = release_metadata()
    directory, _, descriptor = make_seven_asset_directory(tmp_path, module)
    sums = directory / "SHA256SUMS"
    rows = sums.read_bytes().splitlines(keepends=True)
    if mutation == "tamper-pack":
        (directory / PACK_NAME).write_bytes(make_pack(answer="replacement"))
    elif mutation == "extra-descriptor":
        (directory / "0.1.0-beta.5-question-pack.json").write_bytes(b"{}")
    elif mutation == "duplicate-row":
        sums.write_bytes(rows[0] + rows[0] + rows[1])
    elif mutation == "reverse-rows":
        sums.write_bytes(rows[1] + rows[0])
    elif mutation == "text-marker":
        sums.write_bytes(sums.read_bytes().replace(b" *", b"  ", 1))
    elif mutation == "bad-name":
        sums.write_bytes(sums.read_bytes().replace(PACK_NAME.encode(), b"other.bagu-pack"))
    elif mutation == "bad-encoding":
        sums.write_bytes(sums.read_bytes() + b"\xff")
    elif mutation == "install-disclosure":
        path = directory / "INSTALL.md"
        path.write_text(path.read_text(encoding="utf-8").replace("内容权利保留", "内容"), encoding="utf-8")
    else:
        path = directory / "RELEASE_NOTES.md"
        path.write_text("ordinary notes\n", encoding="utf-8")
    with pytest.raises(ValueError):
        module.validate_directory(directory, VERSION, descriptor=descriptor)


def test_pack_validation_precedes_any_prepare_journal_or_output(tmp_path, monkeypatch):
    module = release_github()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    pack = make_pack()
    write_descriptor(tmp_path, pack)
    formal = tmp_path / "formal" / PACK_NAME
    formal.parent.mkdir()
    formal.write_bytes(pack + b"tampered")
    directory = tmp_path / "dist/android/0.1.0-beta.5/public"
    called = []
    monkeypatch.setattr(module, "local_preflight", lambda: called.append("preflight"))

    with pytest.raises(ValueError, match="hash"):
        module.prepare(directory, VERSION, "a" * 40, question_pack=formal)

    assert called == []
    assert not (tmp_path / "dist").exists()


def test_prepare_binds_journal_build_and_receipt_to_one_pack_snapshot(tmp_path, monkeypatch):
    module = release_github()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    pack = make_pack()
    descriptor_path, descriptor_value_ = write_descriptor(tmp_path, pack)
    formal = tmp_path / "formal" / PACK_NAME
    formal.parent.mkdir()
    formal.write_bytes(pack)
    directory = tmp_path / "dist/android/0.1.0-beta.5/public"
    monkeypatch.setattr(module, "local_preflight", lambda: (VERSION, "a" * 40))
    modes = []

    def run(args, **kwargs):
        if "-Mode" in args:
            mode = args[args.index("-Mode") + 1]
            modes.append(mode)
            if mode == "Build":
                assert args[args.index("-QuestionPack") + 1] == str(formal)
                directory.mkdir(parents=True)
                (directory / module.meta.apk_name(VERSION)).write_bytes(b"synthetic apk")
                descriptor = module.meta.parse_question_pack_descriptor(
                    descriptor_path.read_bytes(), VERSION
                )
                module.meta.write_metadata(
                    directory, VERSION, PACK_NOTES, "2026-08-30T00:00:00Z",
                    question_pack=formal, descriptor=descriptor,
                )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.prepare(directory, VERSION, "a" * 40, question_pack=formal)

    provenance = {
        "file_name": PACK_NAME,
        "sha256": hashlib.sha256(pack).hexdigest(),
        "descriptor_sha256": hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
    }
    journal = json.loads((directory.parent / "preparation.json").read_bytes())
    receipt = json.loads((directory.parent / "verification.json").read_bytes())
    assert journal["question_pack"] == provenance
    assert receipt["question_pack"] == provenance
    assert len(receipt["assets"]) == 7 and set(receipt["assets"]) == {p.name for p in directory.iterdir()}
    assert modes == ["Build"]
    module.verify_receipt(directory, "a" * 40, module._release_pack_context(VERSION))

    receipt_path = directory.parent / "verification.json"
    canonical_receipt = receipt_path.read_bytes()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="noncanonical"):
        module.verify_receipt(directory, "a" * 40, module._release_pack_context(VERSION))
    receipt_path.write_bytes(canonical_receipt)

    journal_path = directory.parent / "preparation.json"
    canonical_journal = journal_path.read_bytes()
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(ValueError, match="noncanonical"):
        module.prepare(directory, VERSION, "a" * 40, question_pack=formal)
    journal_path.write_bytes(canonical_journal)

    replacement = make_pack(answer="new provenance")
    formal.write_bytes(replacement)
    write_descriptor(tmp_path, replacement)
    with pytest.raises(ValueError, match="provenance"):
        module.prepare(directory, VERSION, "a" * 40, question_pack=formal)
    assert not list(directory.parent.glob("public.interrupted-*"))


@pytest.mark.parametrize("stage,execute", [
    ("init-feed", False), ("init-feed", True), ("preflight", False),
    ("preflight", True), ("prepare", False), ("publish", False),
    ("publish", True), ("feed", False), ("feed", True),
])
def test_question_pack_cli_option_is_prepare_execute_only(
        tmp_path, monkeypatch, capsys, stage, execute):
    module = release_github()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(module, "local_preflight", lambda: calls.append("preflight"))
    monkeypatch.setattr(module, "GitHub", lambda: pytest.fail("must reject before GitHub access"))
    argv = ["release_github.py", stage, "--question-pack", str(tmp_path / PACK_NAME)]
    if execute:
        argv.append("--execute")
    monkeypatch.setattr(sys, "argv", argv)

    assert module.main() == 1
    assert calls == []
    assert "question-pack" in capsys.readouterr().err.lower()


def test_prepare_execute_requires_pack_when_version_descriptor_exists(tmp_path, monkeypatch, capsys):
    module = release_github()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "version.json").write_text(json.dumps(VERSION), encoding="utf-8")
    write_descriptor(tmp_path, make_pack())
    calls = []
    monkeypatch.setattr(module, "local_preflight", lambda: (VERSION, "a" * 40))
    monkeypatch.setattr(module, "GitHub", lambda: calls.append("github"))
    monkeypatch.setattr(sys, "argv", ["release_github.py", "prepare", "--execute"])

    assert module.main() == 1
    assert calls == []
    assert "question-pack" in capsys.readouterr().err.lower()


def test_prepare_execute_cli_validates_and_passes_only_bound_pack_context(
        tmp_path, monkeypatch, capsys):
    module = release_github()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "version.json").write_text(json.dumps(VERSION), encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\nPermission is hereby granted", encoding="utf-8")
    pack = make_pack()
    write_descriptor(tmp_path, pack)
    formal = tmp_path / "formal" / PACK_NAME
    formal.parent.mkdir()
    formal.write_bytes(pack)
    monkeypatch.setattr(module, "local_preflight", lambda: (VERSION, "a" * 40))
    remote = SimpleNamespace(remote_preflight=lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "GitHub", lambda: remote)
    monkeypatch.setattr(module, "verify_pages_ready", lambda candidate: None)
    prepared = []
    monkeypatch.setattr(module, "prepare", lambda directory, version, commit,
                        question_pack, pack_context: prepared.append(
                            (directory, version, commit, question_pack, pack_context)))
    monkeypatch.setattr(sys, "argv", [
        "release_github.py", "prepare", "--execute", "--question-pack", str(formal),
    ])

    assert module.main() == 0
    assert len(prepared) == 1
    directory, version, commit, archive, context = prepared[0]
    assert directory == tmp_path / "dist/android/0.1.0-beta.5/public"
    assert version == VERSION and commit == "a" * 40 and archive == formal
    assert context["bound"].data == pack
    assert context["provenance"]["sha256"] == hashlib.sha256(pack).hexdigest()
    assert "prepared" in capsys.readouterr().out.lower()


class SevenAssetRemote:
    def __init__(self):
        self.release = None
        self.data = {}
        self.events = []

    def verify_tag(self, *args, **kwargs):
        self.events.append("tag")

    def find_release(self, tag):
        return self.release

    def create_draft(self, tag, commit, prerelease, notes):
        self.release = {
            "id": 1, "tag_name": tag, "target_commitish": commit,
            "prerelease": prerelease, "draft": True, "assets": [],
        }
        return self.release

    def upload(self, tag, path):
        self.data[path.name] = path.read_bytes()
        self.release["assets"].append({
            "id": len(self.data), "name": path.name, "size": path.stat().st_size,
        })
        self.events.append("upload:" + path.name)

    def download_asset(self, asset, limit):
        self.events.append("verify:" + asset["name"])
        return self.data[asset["name"]]

    def get_release(self, release_id):
        return self.release

    def publish_draft(self, release):
        assert len(self.data) == 7
        release["draft"] = False
        self.events.append("publish")


def test_publish_and_anonymous_verification_cover_exact_seven_assets(tmp_path, monkeypatch):
    module = release_github()
    directory, _, descriptor = make_seven_asset_directory(tmp_path, module.meta)
    remote = SevenAssetRemote()
    result = module.publish_release(
        remote, directory, VERSION, "a" * 40, execute=True, descriptor=descriptor
    )
    assert result["release"] == "published"
    assert len([event for event in remote.events if event.startswith("upload:")]) == 7
    assert remote.events.index("publish") > max(
        index for index, event in enumerate(remote.events) if event.startswith("verify:")
    )
    calls = []
    monkeypatch.setattr(module, "anonymous_download", lambda url, limit: (
        calls.append(url.rsplit("/", 1)[1]) or (directory / url.rsplit("/", 1)[1]).read_bytes()
    ))
    feed = json.loads((directory / "update.json").read_bytes())
    module.verify_public_assets(directory, feed)
    assert sorted(calls) == sorted(path.name for path in directory.iterdir())
    remote.data[PACK_NAME] = b"corrupt remote replacement"
    with pytest.raises(ValueError, match="asset"):
        module.publish_release(
            remote, directory, VERSION, "a" * 40, execute=True, descriptor=descriptor
        )
    assert remote.release["draft"] is False


def test_local_preflight_ignores_only_approved_plan_baseline_untracked_files(tmp_path, monkeypatch):
    module = release_github()
    (tmp_path / "version.json").write_text(json.dumps(VERSION), encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\nPermission is hereby granted", encoding="utf-8")
    status = "?? .tmp-plan-baseline/private.tmp\n"

    def command(args, cwd=tmp_path, **kwargs):
        responses = {
            ("status", "--porcelain", "--untracked-files=all"): status.encode(),
            ("remote", "get-url", "origin"): b"https://github.com/InGnIJM/AI-Bagu.git\n",
            ("ls-files",): b"LICENSE\nversion.json\n",
            ("rev-parse", "HEAD"): b"a" * 40,
        }
        return responses[tuple(args[1:])]

    monkeypatch.setattr(module, "command", command)
    assert module.local_preflight(tmp_path)[1] == "a" * 40
    status = "?? .tmp-plan-baseline/private.tmp\n?? unrelated.tmp\n"
    with pytest.raises(ValueError, match="dirty"):
        module.local_preflight(tmp_path)
