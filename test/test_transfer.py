"""Portable migration contracts; all archives and databases are synthetic."""
import base64
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import bagu


@pytest.fixture
def conn(tmp_path):
    connection = bagu.get_conn(tmp_path / "migration.db")
    bagu.init_db(connection)
    yield connection
    connection.close()


def archive(rows, *, version=2, mode="questions", **overrides):
    data = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    manifest = {"format": "bagu-backup", "schema_version": version,
                "created_at": "2026-08-28T01:02:03Z", "app_version": "synthetic-test",
                "question_count": len(rows), "questions_sha256": hashlib.sha256(data).hexdigest()}
    if version == 2:
        manifest["mode"] = mode
    manifest.update(overrides)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("manifest.json", json.dumps(manifest))
        zipped.writestr("questions.json", data)
    return output.getvalue()


def question(**changes):
    row = {"category": "A", "question": "synthetic", "answer": "", "url": ""}
    row.update(changes)
    return row


def progress(**changes):
    row = question(level=2, times_seen=4, times_right=3,
                   next_due="2026-09-01", last_reviewed="2026-08-28")
    row.update(changes)
    return row


@pytest.mark.parametrize("mode", ["questions", "progress"])
def test_export_v3_modes_pack_aware_members_and_real_version(conn, mode):
    conn.execute("INSERT INTO questions(category,question,level,times_seen,times_right) VALUES('A','synthetic',2,4,3)")
    conn.commit()
    payload = bagu.export_backup(conn, mode=mode)
    with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert set(zipped.namelist()) == {
            "manifest.json", "questions.json", "packs.json", "experiences.json",
        }
    assert manifest["schema_version"] == 3 and manifest["mode"] == mode
    assert {
        key: manifest[key] for key in (
            "question_count", "local_question_count", "pack_question_count",
            "pack_count", "experience_count",
        )
    } == {
        "question_count": 1, "local_question_count": 1,
        "pack_question_count": 0, "pack_count": 0, "experience_count": 0,
    }
    assert manifest["app_version"] == json.loads((Path(bagu.__file__).parent / "version.json").read_text())["versionName"]
    rows = bagu.parse_backup(payload)
    assert set(rows[0]) == (set(question()) if mode == "questions" else set(progress()))
    if mode == "progress":
        assert rows[0]["level"] == 2


@pytest.mark.parametrize("version,mode", [(1, "progress"), (2, "progress"), (2, "questions")])
def test_inspect_fully_validates_without_database_and_retains_list_parser(version, mode):
    rows = [question()] if mode == "questions" else [progress()]
    payload = archive(rows, version=version, mode=mode)
    assert bagu.inspect_backup(payload) == {"schema_version": version, "mode": mode,
        "question_count": 1, "created_at": "2026-08-28T01:02:03Z", "app_version": "synthetic-test"}
    assert bagu.parse_backup(payload) == rows


def test_v1_nullable_optional_content_remains_readable_as_progress():
    payload = archive([progress(answer=None, url=None)], version=1)
    assert bagu.parse_backup(payload) == [progress()]
    assert bagu.inspect_backup(payload)["mode"] == "progress"


@pytest.mark.parametrize("mode", ["questions", "progress"])
def test_export_canonicalizes_nullable_legacy_database_content(conn, mode):
    conn.execute("INSERT INTO questions(category,question,answer,url) VALUES('A','synthetic',NULL,NULL)")
    conn.commit()
    row = bagu.parse_backup(bagu.export_backup(conn, mode=mode))[0]
    assert row["answer"] == "" and row["url"] == ""
    assert tuple(conn.execute("SELECT answer,url FROM questions").fetchone()) == (None, None)


@pytest.mark.parametrize("rows,mode", [([progress()], "questions"), ([question()], "progress"),
    ([question(level=0)], "questions"), ([question()], ""), ([question()], True),
    ([question(answer=None)], "questions"), ([question(), question()], "questions")])
def test_inspect_rejects_mode_field_type_and_duplicate_errors(rows, mode):
    with pytest.raises(ValueError):
        bagu.inspect_backup(archive(rows, mode=mode))


def test_pure_restore_overwrites_empty_content_preserving_progress_history_and_local_rows(conn):
    conn.execute("INSERT INTO questions(category,question,answer,url,level,times_seen,times_right,next_due,last_reviewed) VALUES('A','synthetic','old','https://example.test',2,4,3,'2026-09-01','2026-08-28')")
    conn.execute("INSERT INTO questions(category,question) VALUES('A','local-only')")
    conn.commit()
    sid, drawn = bagu.draw(conn, 1)
    bagu.grade(conn, sid, drawn[0]["id"], "good")
    history = [tuple(row) for row in conn.execute("SELECT * FROM session_items")]
    scheduling = tuple(conn.execute("SELECT level,times_seen,times_right,next_due,last_reviewed FROM questions WHERE question='synthetic'").fetchone())
    result = bagu.restore_backup(conn, archive([question(), question(question="new")]))
    assert result == {"added": 1, "updated": 1, "total": 2}
    assert tuple(conn.execute("SELECT answer,url,level,times_seen,times_right,next_due,last_reviewed FROM questions WHERE question='synthetic'").fetchone()) == ("", "", *scheduling)
    assert tuple(conn.execute("SELECT level,times_seen,times_right,next_due,last_reviewed FROM questions WHERE question='new'").fetchone()) == (0, 0, 0, None, None)
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 3
    assert [tuple(row) for row in conn.execute("SELECT * FROM session_items")] == history
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v2_progress_restore_overwrites_schedule_and_default_export_is_progress(conn):
    conn.execute("INSERT INTO questions(category,question,answer) VALUES('A','synthetic','old')")
    conn.commit()
    assert bagu.inspect_backup(bagu.export_backup(conn, app_version="synthetic"))["mode"] == "progress"
    assert bagu.restore_backup(conn, archive([progress()], mode="progress")) == {"added": 0, "updated": 1, "total": 1}
    assert tuple(conn.execute("SELECT answer,url,level,times_seen,times_right,next_due,last_reviewed FROM questions").fetchone()) == ("", "", 2, 4, 3, "2026-09-01", "2026-08-28")


@pytest.mark.parametrize("mode", ["questions", "progress"])
def test_restore_transaction_guard_and_rollback(conn, monkeypatch, mode):
    conn.execute("INSERT INTO questions(category,question,answer) VALUES('A','synthetic','old')")
    conn.execute("CREATE TRIGGER reject_import BEFORE INSERT ON questions WHEN NEW.question='new' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    conn.commit()
    before = list(conn.iterdump())
    row = question if mode == "questions" else progress
    payload = archive([row(answer="changed"), row(question="new")], mode=mode)
    check = bagu._backup_open_session_error
    def inside_transaction(connection):
        assert connection.in_transaction
        return check(connection)
    monkeypatch.setattr(bagu, "_backup_open_session_error", inside_transaction)
    with pytest.raises(sqlite3.IntegrityError):
        bagu.restore_backup(conn, payload)
    assert list(conn.iterdump()) == before
    assert not conn.in_transaction


@pytest.mark.parametrize("query", ["?mode=", "?mode=bad", "?mode=questions&mode=progress", "?mode=questions&mode="])
def test_export_http_rejects_invalid_empty_or_duplicate_mode(conn, tmp_path, query):
    assert bagu.handle_http("GET", "/api/backup/export" + query, None, conn, tmp_path)[0] == 400


def test_http_inspect_is_read_only_open_session_and_restore_is_conflict(conn, tmp_path):
    conn.execute("INSERT INTO questions(category,question) VALUES('A','synthetic')")
    conn.commit()
    sid, _ = bagu.draw(conn, 1)
    before = list(conn.iterdump())
    code, payload, _ = bagu.handle_http("GET", "/api/backup/export?mode=questions", None, conn, tmp_path)
    assert code == 200
    body = {"archive_base64": base64.b64encode(payload).decode("ascii")}
    code, summary, _ = bagu.handle_http("POST", "/api/backup/inspect", body, conn, tmp_path)
    assert code == 200 and summary["mode"] == "questions"
    assert bagu.handle_http("POST", "/api/backup/restore", body, conn, tmp_path)[0] == 409
    assert list(conn.iterdump()) == before and bagu.get_open_session(conn)["id"] == sid


@pytest.mark.parametrize("body", [{}, {"archive_base64": "%%%"}, {"archive_base64": "é"}, {"archive_base64": 1},
    {"archive_base64": "", "extra": True}, {"archive_base64": base64.b64encode(archive([], questions_sha256="0" * 64)).decode()}])
@pytest.mark.parametrize("route", ["inspect", "restore"])
def test_http_archive_rejects_malformed_payload(conn, tmp_path, body, route):
    before = list(conn.iterdump())
    assert bagu.handle_http("POST", "/api/backup/" + route, body, conn, tmp_path)[0] == 400
    assert list(conn.iterdump()) == before
