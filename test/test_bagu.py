# -*- coding: utf-8 -*-
import datetime as dt
import json
import re
import sqlite3

import pytest

import bagu


@pytest.fixture
def conn(tmp_path):
    c = bagu.get_conn(tmp_path / "t.db")
    bagu.init_db(c)
    yield c
    c.close()


def _seed(c, n=3, cat="测试"):
    for i in range(n):
        c.execute(
            "INSERT INTO questions(category, question) VALUES(?,?)", (cat, f"问题{i}")
        )
    c.commit()


def test_init_db_idempotent(conn):
    bagu.init_db(conn)  # 二次执行不报错
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0


def test_init_db_creates_session_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sessions" in names and "session_items" in names


def test_new_session_id_format():
    sid = bagu.new_session_id()
    assert re.fullmatch(r"s_\d{8}_[0-9a-f]{8}", sid)


def test_get_open_session_none(conn):
    assert bagu.get_open_session(conn) is None


def test_fetch_questions_h2_with_h3():
    html = "<h2 id='a'>\\# 索引</h2><h3 id='b'>\\# 什么是B+树</h3><h3 id='c'>为什么不用红黑树</h3><h2 id='d'>事务</h2>"
    import unittest.mock as mock

    with mock.patch.object(bagu.urllib.request, "urlopen") as mu:
        mu.return_value.read.return_value = html.encode()
        qs = bagu.fetch_questions("MySQL", "http://x")
    assert qs == [
        ("MySQL", "索引｜什么是B+树", "http://x"),
        ("MySQL", "索引｜为什么不用红黑树", "http://x"),
        ("MySQL", "事务", "http://x"),
    ]


def test_draw_prefers_due_and_new(conn):
    _seed(conn)
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    conn.execute(
        "UPDATE questions SET next_due=? WHERE id=1", (past,)
    )
    conn.commit()
    sid, rows = bagu.draw(conn, 2)
    assert re.fullmatch(r"s_\d{8}_[0-9a-f]{8}", sid)
    assert len(rows) == 2
    assert rows[0]["id"] == 1  # 到期复习题优先


def test_draw_with_cat_filter(conn):
    _seed(conn, 1, "A")
    _seed(conn, 1, "B")
    sid, rows = bagu.draw(conn, 10, cat="B")
    assert len(rows) == 1 and rows[0]["category"] == "B"


def test_draw_creates_open_session(conn):
    _seed(conn, 3)
    sid, rows = bagu.draw(conn, 2)
    assert re.fullmatch(r"s_\d{8}_[0-9a-f]{8}", sid)
    assert len(rows) == 2
    open_s = bagu.get_open_session(conn)
    assert open_s["id"] == sid
    n = conn.execute(
        "SELECT COUNT(*) FROM session_items WHERE session_id=?", (sid,)
    ).fetchone()[0]
    assert n == 2


def test_draw_second_raises_and_keeps_items(conn):
    _seed(conn, 4)
    sid, rows = bagu.draw(conn, 2)
    ids = {r["id"] for r in rows}
    with pytest.raises(bagu.SessionOpenError) as ei:
        bagu.draw(conn, 2)
    assert sid in str(ei.value)
    again = conn.execute(
        "SELECT question_id FROM session_items WHERE session_id=?", (sid,)
    ).fetchall()
    assert {r[0] for r in again} == ids


def test_grade_good_schedules_future(conn):
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    nd = bagu.grade(conn, sid, rows[0]["id"], "good")
    assert nd > dt.date.today().isoformat()
    row = conn.execute("SELECT * FROM questions WHERE id=?", (rows[0]["id"],)).fetchone()
    assert row["times_seen"] == 1 and row["times_right"] == 1 and row["level"] >= 1


def test_grade_again_resets_level(conn):
    _seed(conn, 1)
    conn.execute("UPDATE questions SET level=2 WHERE id=1")
    conn.commit()
    sid, rows = bagu.draw(conn, 1)
    bagu.grade(conn, sid, rows[0]["id"], "again")
    row = conn.execute("SELECT level FROM questions WHERE id=1").fetchone()
    assert row["level"] == 0


def test_grade_invalid_result(conn):
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    with pytest.raises(ValueError):
        bagu.grade(conn, sid, rows[0]["id"], "ok")


def test_grade_missing_question(conn):
    _seed(conn, 1)
    sid, _ = bagu.draw(conn, 1)
    with pytest.raises(bagu.GradeRejected):
        bagu.grade(conn, sid, 999, "good")


def test_grade_first_ok_second_rejected(conn):
    _seed(conn, 2)
    sid, rows = bagu.draw(conn, 2)
    qid = rows[0]["id"]
    bagu.grade(conn, sid, qid, "good")
    seen = conn.execute("SELECT times_seen FROM questions WHERE id=?", (qid,)).fetchone()[0]
    with pytest.raises(bagu.GradeRejected):
        bagu.grade(conn, sid, qid, "easy")
    seen2 = conn.execute("SELECT times_seen FROM questions WHERE id=?", (qid,)).fetchone()[0]
    assert seen2 == seen == 1


def test_grade_wrong_session_or_question(conn):
    _seed(conn, 3)
    sid, rows = bagu.draw(conn, 2)
    with pytest.raises(bagu.GradeRejected):
        bagu.grade(conn, "s_20990101_ffffffff", rows[0]["id"], "good")
    outsider = [i for i in (1, 2, 3) if i not in {r["id"] for r in rows}][0]
    with pytest.raises(bagu.GradeRejected):
        bagu.grade(conn, sid, outsider, "good")


def test_stats_counts(conn):
    _seed(conn, 4)
    sid, rows = bagu.draw(conn, 4)
    bagu.grade(conn, sid, rows[0]["id"], "easy")
    s = bagu.stats(conn)
    assert s["total"] == 4 and s["due"] == 3 and s["mastered"] <= 4
    assert s["by_cat"][0]["category"] == "测试"


def test_skip_closes_without_scheduling(conn):
    _seed(conn, 2)
    sid, rows = bagu.draw(conn, 2)
    qid = rows[0]["id"]
    bagu.skip_session(conn, sid)
    row = conn.execute(
        "SELECT next_due, level, times_seen FROM questions WHERE id=?", (qid,)
    ).fetchone()
    assert row["next_due"] is None and row["level"] == 0 and row["times_seen"] == 0
    assert bagu.get_open_session(conn) is None
    sid2, rows2 = bagu.draw(conn, 2)
    assert sid2 != sid and len(rows2) == 2


def test_skip_none_raises(conn):
    with pytest.raises(bagu.SkipRejected):
        bagu.skip_session(conn)


def test_all_graded_auto_closes(conn):
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    bagu.grade(conn, sid, rows[0]["id"], "easy")
    assert conn.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0] == "closed"
    sid2, _ = bagu.draw(conn, 1)
    assert sid2 != sid


def test_import_all_with_mock(monkeypatch, conn):
    monkeypatch.setattr(bagu, "PAGES", {"A": "http://x", "B": "http://bad"})
    import unittest.mock as mock

    def fake_urlopen(req, timeout=None):
        if "bad" in req.full_url:
            raise OSError("network down")
        r = mock.MagicMock()
        r.read.return_value = b"<h2>s</h2><h3>q1</h3>"
        return r

    with mock.patch.object(bagu.urllib.request, "urlopen", fake_urlopen):
        n = bagu.import_all(conn)
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_main_full_flow(monkeypatch, tmp_path, capsys):
    db = tmp_path / "m.db"
    monkeypatch.setattr(bagu, "DB_PATH", str(db))
    bagu.main(["init"])
    conn = bagu.get_conn(db)
    bagu.init_db(conn)
    _seed(conn, 2)
    conn.close()
    bagu.main(["draw", "-n", "5"])
    out = capsys.readouterr().out
    assert "#1" in out
    sid = None
    for line in out.splitlines():
        if line.startswith("session: "):
            sid = line.split(" ", 1)[1].strip()
    assert sid
    bagu.main(["grade", sid, "1", "good"])
    bagu.main(["stats"])
    out = capsys.readouterr().out
    assert "总题数: 2" in out
    bagu.main(["list"])
    assert "问题0" in capsys.readouterr().out


def test_main_draw_empty(monkeypatch, tmp_path, capsys):
    db = tmp_path / "empty.db"
    monkeypatch.setattr(bagu, "DB_PATH", str(db))
    bagu.main(["init"])
    # 无题时 draw 提示而非报错（draw 在无到期时输出提示）
    conn = bagu.get_conn(db)
    bagu.init_db(conn)
    _seed(conn, 1)
    conn.execute("UPDATE questions SET next_due=date('now','+30 day')")
    conn.commit()
    conn.close()
    bagu.main(["draw", "-n", "3"])
    assert "没有到期" in capsys.readouterr().out


def test_main_grade_invalid(capsys):
    with pytest.raises(SystemExit):
        bagu.main(["grade", "s_x", "1", "nope"])


def test_main_grade_without_session_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(bagu, "DB_PATH", str(tmp_path / "m.db"))
    bagu.main(["init"])
    with pytest.raises(SystemExit):
        bagu.main(["grade", "1", "good"])


def test_main_draw_when_open_exits(monkeypatch, tmp_path, capsys):
    db = tmp_path / "m.db"
    monkeypatch.setattr(bagu, "DB_PATH", str(db))
    bagu.main(["init"])
    conn = bagu.get_conn(db)
    _seed(conn, 3)
    conn.close()
    bagu.main(["draw", "-n", "2"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as ei:
        bagu.main(["draw", "-n", "2"])
    assert ei.value.code == 1
    assert "未关闭" in capsys.readouterr().err


def test_save_and_load_settings(tmp_path):
    bagu.save_settings(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
        },
        api_key="sk-test",
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    assert s["model"] == "deepseek-chat" and s["api_key"] == "sk-test"
    assert "test" not in s["api_key_masked"]
    assert s["active_id"].startswith("m_")
    assert len(s["models"]) == 1
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "models" in raw and "api_key" not in raw
    assert all("api_key" not in m for m in raw["models"])
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BAGU_KEY_{s['active_id']}=" in env
    assert "BAGU_API_KEY=" not in env


def test_save_settings_preserves_key_when_omitted(tmp_path):
    bagu.save_settings(
        {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://x"},
        api_key="sk-keep-me",
        root=tmp_path,
    )
    bagu.save_settings(
        {"provider": "deepseek", "model": "deepseek-chat-v2", "base_url": "https://x"},
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    assert s["api_key"] == "sk-keep-me"
    assert s["model"] == "deepseek-chat-v2"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BAGU_KEY_{s['active_id']}=sk-keep-me" in env


def test_migrates_legacy_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "new_model_id", lambda: "m_deadbeef")
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("BAGU_API_KEY=sk-legacy-key\n", encoding="utf-8")
    s = bagu.load_settings(tmp_path)
    assert s["active_id"] == "m_deadbeef"
    assert s["api_key"] == "sk-legacy-key"
    assert s["models"][0]["name"]
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["active_id"] == "m_deadbeef" and len(raw["models"]) == 1
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BAGU_KEY_m_deadbeef=sk-legacy-key" in env
    assert "BAGU_API_KEY=" not in env
    s2 = bagu.load_settings(tmp_path)
    assert s2["active_id"] == "m_deadbeef" and len(s2["models"]) == 1


def test_new_model_id_format():
    assert re.fullmatch(r"m_[0-9a-f]{8}", bagu.new_model_id())


def test_default_model_name():
    assert bagu.default_model_name("deepseek", "deepseek-chat") == "DeepSeek · deepseek-chat"
    assert bagu.default_model_name("custom", "") == "自定义 OpenAI 兼容"


def test_parse_judge_output_hard():
    d = bagu.parse_judge_output("GRADE: hard\nCOMMENT: 缺版本链\nANSWER:\nundo log ...")
    assert d["grade"] == "hard" and "undo log" in d["full_answer"]


def test_parse_judge_output_easy_no_answer_body():
    d = bagu.parse_judge_output("GRADE: easy\nCOMMENT: 要点齐全\nANSWER:")
    assert d["grade"] == "easy" and d["full_answer"] == ""


def test_judge_model_failure_does_not_grade(conn):
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    qid = rows[0]["id"]

    def boom(*a, **k):
        raise bagu.JudgeError("timeout")

    with pytest.raises(bagu.JudgeError):
        bagu.judge_answer(conn, sid, qid, "我的回答", chat_fn=boom)
    assert conn.execute("SELECT times_seen FROM questions WHERE id=?", (qid,)).fetchone()[0] == 0


def test_judge_easy_omits_full_answer(conn):
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    qid = rows[0]["id"]

    def fake(prompt):
        return "GRADE: easy\nCOMMENT: ok\nANSWER:"

    out = bagu.judge_answer(conn, sid, qid, "完整正确", chat_fn=fake)
    assert out["grade"] == "easy" and out["full_answer"] == ""
    assert conn.execute("SELECT times_seen FROM questions WHERE id=?", (qid,)).fetchone()[0] == 1


def test_api_draw_and_session(conn, tmp_path):
    _seed(conn, 3)
    code, data, _ = bagu.handle_http("POST", "/api/draw", {"n": 2}, conn, tmp_path)
    assert code == 200 and data["session_id"].startswith("s_")
    code, sess, _ = bagu.handle_http("GET", "/api/session", None, conn, tmp_path)
    assert code == 200 and sess["session_id"] == data["session_id"]
    code, err, _ = bagu.handle_http("POST", "/api/draw", {"n": 2}, conn, tmp_path)
    assert code == 409


def test_api_answer_requires_settings(conn, tmp_path):
    _seed(conn, 1)
    _, drawn, _ = bagu.handle_http("POST", "/api/draw", {"n": 1}, conn, tmp_path)
    code, err, _ = bagu.handle_http(
        "POST",
        "/api/answer",
        {
            "session_id": drawn["session_id"],
            "question_id": drawn["questions"][0]["id"],
            "text": "x",
        },
        conn,
        tmp_path,
    )
    assert code == 400 and conn.execute("SELECT times_seen FROM questions").fetchone()[0] == 0


def test_mask_long_key():
    assert "abcd" not in bagu.mask_api_key("sk-abcdefghij")
    assert bagu.mask_api_key("sk-abcdefghij").startswith("sk-")
    assert bagu.mask_api_key("") == ""


def test_load_settings_bad_json(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    s = bagu.load_settings(tmp_path)
    assert s["model"] == ""


def test_provider_presets_list():
    ids = {p["id"] for p in bagu.list_provider_presets()}
    assert {"deepseek", "openai", "glm", "kimi", "siliconflow", "gemini", "ollama"} <= ids


def test_parse_judge_output_invalid():
    with pytest.raises(bagu.JudgeError):
        bagu.parse_judge_output("no grade here")


def test_openai_chat_and_fetch_reference(monkeypatch):
    with pytest.raises(bagu.JudgeError):
        bagu._openai_chat("x", {"api_key": ""})

    class FakeResp:
        payload = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bagu.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    assert bagu._openai_chat("x", {"api_key": "sk", "base_url": "http://x/v1", "model": "m"}) == "hi"

    class BadResp(FakeResp):
        payload = b"{}"

    monkeypatch.setattr(bagu.urllib.request, "urlopen", lambda *a, **k: BadResp())
    with pytest.raises(bagu.JudgeError):
        bagu._openai_chat("x", {"api_key": "sk", "base_url": "http://x/v1"})

    class HtmlResp(FakeResp):
        payload = b"<script>x</script><style>y</style><p>hello world</p>"

    monkeypatch.setattr(bagu.urllib.request, "urlopen", lambda *a, **k: HtmlResp())
    assert "hello" in bagu.fetch_reference_text("http://x")
    monkeypatch.setattr(
        bagu.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
    )
    assert bagu.fetch_reference_text("http://x") == ""
    assert bagu.fetch_reference_text("") == ""


def test_judge_uses_model_and_reference(conn, tmp_path, monkeypatch):
    conn.execute("INSERT INTO questions(category, question, url) VALUES(?,?,?)", ("A", "题", "http://ref"))
    conn.commit()
    monkeypatch.setattr(bagu, "_openai_chat", lambda prompt, settings: "pong")
    bagu.create_model(
        {"name": "X", "provider": "deepseek", "model": "m", "base_url": "http://x", "api_key": "sk-longkey12"},
        root=tmp_path,
    )
    bagu.create_model(
        {"name": "Y", "provider": "openai", "model": "other", "base_url": "http://y", "api_key": "sk-otherkey99"},
        root=tmp_path,
    )
    first = bagu.load_settings(tmp_path)["models"][0]
    bagu.activate_model(first["id"], root=tmp_path)
    sid, rows = bagu.draw(conn, 1)
    seen = {}
    monkeypatch.setattr(bagu, "fetch_reference_text", lambda url, limit=4000: "参考正文")

    def chat(prompt, settings):
        seen.update(settings)
        return "GRADE: good\nCOMMENT: 过\nANSWER:"

    monkeypatch.setattr(bagu, "_openai_chat", chat)
    out = bagu.judge_answer(conn, sid, rows[0]["id"], "答", root=tmp_path)
    assert out["grade"] == "good"
    assert seen.get("model") == "m"
    assert seen.get("base_url") == "http://x"
    assert seen.get("api_key") == "sk-longkey12"


def test_http_missing_index(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(bagu.Path, "is_file", lambda self: False)
    code, payload, _ = bagu.handle_http("GET", "/", None, conn, tmp_path)
    assert code == 404
    _seed(conn, 1)
    sid, rows = bagu.draw(conn, 1)
    with pytest.raises(bagu.JudgeError):
        bagu.judge_answer(conn, sid, rows[0]["id"], "ans", root=tmp_path)


def test_http_more_routes(conn, tmp_path, monkeypatch):
    _seed(conn, 2, "A")
    code, html, ctype = bagu.handle_http("GET", "/", None, conn, tmp_path)
    assert code == 200 and "八股抽问" in html and "text/html" in ctype
    assert "未配置评卷模型" in html
    assert "从 Hermes 导入" not in html
    assert "tab-cfg" not in html
    assert "bagu-draft:" in html
    code, st, _ = bagu.handle_http("GET", "/api/stats", None, conn, tmp_path)
    assert code == 200 and st["open_session_id"] is None
    code, sess, _ = bagu.handle_http("GET", "/api/session", None, conn, tmp_path)
    assert sess["session_id"] is None
    _, drawn, _ = bagu.handle_http("POST", "/api/draw", {"n": 1, "cat": "A"}, conn, tmp_path)
    code, skipd, _ = bagu.handle_http(
        "POST", "/api/skip", {"session_id": drawn["session_id"]}, conn, tmp_path
    )
    assert code == 200 and skipd["status"] == "closed"
    code, err, _ = bagu.handle_http("POST", "/api/skip", {}, conn, tmp_path)
    assert code == 400
    code, s, _ = bagu.handle_http("GET", "/api/settings", None, conn, tmp_path)
    assert code == 200 and s["configured"] is False
    assert {"openai", "glm", "kimi", "siliconflow", "gemini", "ollama"} <= {p["id"] for p in s["presets"]}
    code, err, _ = bagu.handle_http("POST", "/api/settings", {"model": "x"}, conn, tmp_path)
    assert code == 404
    code, err, _ = bagu.handle_http("POST", "/api/settings/test", {}, conn, tmp_path)
    assert code == 404
    code, err, _ = bagu.handle_http("POST", "/api/settings/import-hermes", {}, conn, tmp_path)
    assert code == 404
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, created, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {
            "name": "T",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "http://x/v1",
            "api_key": "sk-abc",
        },
        conn,
        tmp_path,
    )
    assert code == 200 and created["id"].startswith("m_")
    code, err, _ = bagu.handle_http("GET", "/nope", None, conn, tmp_path)
    assert code == 404
    code, err, _ = bagu.handle_http("POST", "/api/draw", {"n": "bad"}, conn, tmp_path)
    assert code == 400
    code, err, _ = bagu.handle_http("POST", "/api/answer", {}, conn, tmp_path)
    assert code == 400
    code, empty, _ = bagu.handle_http("POST", "/api/draw", {"n": 1, "cat": "Z"}, conn, tmp_path)
    assert empty["questions"] == []
    _, drawn2, _ = bagu.handle_http("POST", "/api/draw", {"n": 1, "cat": "A"}, conn, tmp_path)

    def boom(*a, **k):
        raise bagu.GradeRejected("no")

    monkeypatch.setattr(bagu, "judge_answer", boom)
    code, err, _ = bagu.handle_http(
        "POST",
        "/api/answer",
        {"session_id": drawn2["session_id"], "question_id": drawn2["questions"][0]["id"], "text": "x"},
        conn,
        tmp_path,
    )
    assert code == 400


def test_api_models_crud(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert code == 200 and listed["models"] == [] and listed["active_id"] == ""
    def boom(*a, **k):
        raise bagu.JudgeError("nope")
    monkeypatch.setattr(bagu, "_openai_chat", boom)
    code, err, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "X", "provider": "deepseek", "model": "m", "base_url": "http://x", "api_key": "sk"},
        conn,
        tmp_path,
    )
    assert code == 502 and not (tmp_path / "settings.json").exists()
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, a, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "A", "provider": "deepseek", "model": "chat", "base_url": "http://x", "api_key": "sk-a"},
        conn,
        tmp_path,
    )
    code, b, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "B", "provider": "openai", "model": "gpt", "base_url": "http://y", "api_key": "sk-b"},
        conn,
        tmp_path,
    )
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert listed["active_id"] == b["id"]
    code, _, _ = bagu.handle_http("POST", f"/api/models/{a['id']}/activate", {}, conn, tmp_path)
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert listed["active_id"] == a["id"]
    code, copied, _ = bagu.handle_http("POST", f"/api/models/{a['id']}/copy", {}, conn, tmp_path)
    assert copied["name"].endswith("副本")
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    code, tested, _ = bagu.handle_http(
        "POST",
        "/api/models/test",
        {"model": "chat", "base_url": "http://x", "api_key": "sk-a"},
        conn,
        tmp_path,
    )
    assert code == 200
    code, upd, _ = bagu.handle_http(
        "PUT",
        f"/api/models/{a['id']}",
        {"name": "A2", "provider": "deepseek", "model": "r", "base_url": "http://x", "api_key": ""},
        conn,
        tmp_path,
    )
    assert code == 200 and upd["name"] == "A2"
    code, _, _ = bagu.handle_http("DELETE", f"/api/models/{a['id']}", None, conn, tmp_path)
    s = bagu.load_settings(tmp_path)
    assert a["id"] not in {m["id"] for m in s["models"]}
    code, err, _ = bagu.handle_http("POST", "/api/models/m_nope/activate", {}, conn, tmp_path)
    assert code == 400
    code, gs, _ = bagu.handle_http("GET", "/api/settings", None, conn, tmp_path)
    assert code == 200 and gs["configured"] is True and gs["model"] in {"gpt", "chat", "r"}


def test_http_answer_success(conn, tmp_path, monkeypatch):
    _seed(conn, 1)
    _, drawn, _ = bagu.handle_http("POST", "/api/draw", {"n": 1}, conn, tmp_path)

    def fake_judge(*a, **k):
        return {"grade": "hard", "comment": "缺", "full_answer": "完整"}

    monkeypatch.setattr(bagu, "judge_answer", fake_judge)
    code, out, _ = bagu.handle_http(
        "POST",
        "/api/answer",
        {
            "session_id": drawn["session_id"],
            "question_id": drawn["questions"][0]["id"],
            "text": "我的答案",
        },
        conn,
        tmp_path,
    )
    assert code == 200 and out["grade"] == "hard" and "完整" in out["full_answer"]


def test_main_skip_and_grade_rejected(monkeypatch, tmp_path, capsys):
    db = tmp_path / "m.db"
    monkeypatch.setattr(bagu, "DB_PATH", str(db))
    bagu.main(["init"])
    conn = bagu.get_conn(db)
    _seed(conn, 2)
    conn.close()
    bagu.main(["draw", "-n", "2"])
    out = capsys.readouterr().out
    sid = [ln.split(" ", 1)[1].strip() for ln in out.splitlines() if ln.startswith("session: ")][0]
    bagu.main(["skip", sid])
    assert "已结束" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        bagu.main(["skip"])
    capsys.readouterr()
    bagu.main(["draw", "-n", "2"])
    out = capsys.readouterr().out
    sid = [ln.split(" ", 1)[1].strip() for ln in out.splitlines() if ln.startswith("session: ")][0]
    with pytest.raises(SystemExit):
        bagu.main(["grade", sid, "999", "good"])


def test_serve_and_main_serve(monkeypatch):
    called = {}

    class FakeServer:
        def __init__(self, addr, handler):
            called["addr"] = addr
            called["handler"] = handler

        def serve_forever(self):
            called["run"] = True

    monkeypatch.setattr(bagu, "ThreadingHTTPServer", FakeServer)
    bagu.serve(port=8765)
    assert called["addr"] == ("127.0.0.1", 8765) and called["run"]
    ports = []
    monkeypatch.setattr(bagu, "serve", lambda port=8765: ports.append(port))
    bagu.main(["serve", "--port", "9001"])
    assert ports == [9001]


def test_create_model_tests_before_write(tmp_path, monkeypatch):
    calls = []

    def boom(prompt, settings):
        calls.append(settings)
        raise bagu.JudgeError("401")

    with pytest.raises(bagu.JudgeError):
        bagu.create_model(
            {
                "name": "A",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "base_url": "http://x/v1",
                "api_key": "sk-aaa",
            },
            root=tmp_path,
            chat_fn=boom,
        )
    assert not (tmp_path / "settings.json").exists()
    monkeypatch.setattr(bagu, "_openai_chat", lambda prompt, settings: "pong")
    out = bagu.create_model(
        {
            "name": "",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "http://x/v1",
            "api_key": "sk-aaa",
        },
        root=tmp_path,
    )
    assert out["id"].startswith("m_")
    assert "DeepSeek" in out["name"]
    assert out["configured"] is True
    s = bagu.load_settings(tmp_path)
    assert s["active_id"] == out["id"]


def test_test_model_draft_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    bagu.test_model_draft(
        {"model": "m", "base_url": "http://x/v1", "api_key": "sk-z"},
        root=tmp_path,
    )
    assert not (tmp_path / "settings.json").exists()


def test_update_copy_activate_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    a = bagu.create_model(
        {
            "name": "A",
            "provider": "deepseek",
            "model": "chat",
            "base_url": "http://x/v1",
            "api_key": "sk-a",
        },
        root=tmp_path,
    )
    b = bagu.create_model(
        {
            "name": "B",
            "provider": "openai",
            "model": "gpt",
            "base_url": "http://y/v1",
            "api_key": "sk-b",
        },
        root=tmp_path,
    )
    assert bagu.load_settings(tmp_path)["active_id"] == b["id"]
    bagu.activate_model(a["id"], root=tmp_path)
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    copied = bagu.copy_model(a["id"], root=tmp_path)
    assert copied["id"] != a["id"]
    assert copied["name"].endswith("副本")
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    assert bagu.load_settings(tmp_path)["models"]
    keys = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BAGU_KEY_{copied['id']}=sk-a" in keys
    bagu.update_model(
        a["id"],
        {
            "name": "A2",
            "provider": "deepseek",
            "model": "reasoner",
            "base_url": "http://x/v1",
            "api_key": "",
        },
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    m = next(x for x in s["models"] if x["id"] == a["id"])
    assert m["name"] == "A2" and m["model"] == "reasoner" and m["api_key"] == "sk-a"
    bagu.delete_model(a["id"], root=tmp_path)
    s = bagu.load_settings(tmp_path)
    assert a["id"] not in {x["id"] for x in s["models"]}
    assert s["active_id"] == b["id"]
    for m in list(s["models"]):
        bagu.delete_model(m["id"], root=tmp_path)
    s = bagu.load_settings(tmp_path)
    assert s["models"] == [] and s["active_id"] == ""


def test_unknown_model_ops_raise(tmp_path):
    with pytest.raises(LookupError):
        bagu.activate_model("m_nope", root=tmp_path)
    with pytest.raises(LookupError):
        bagu.copy_model("m_nope", root=tmp_path)
    with pytest.raises(LookupError):
        bagu.delete_model("m_nope", root=tmp_path)


def test_fetch_questions_skips_empty_heading():
    html = "<h2>  </h2><h3>真题</h3>"
    import unittest.mock as mock

    with mock.patch.object(bagu.urllib.request, "urlopen") as mu:
        mu.return_value.read.return_value = html.encode()
        qs = bagu.fetch_questions("OS", "http://x")
    assert qs == [("OS", "真题", "http://x")]
    html = "<h2>甲</h2><h2>乙</h2>"
    import unittest.mock as mock

    with mock.patch.object(bagu.urllib.request, "urlopen") as mu:
        mu.return_value.read.return_value = html.encode()
        qs = bagu.fetch_questions("OS", "http://x")
    assert qs == [("OS", "甲", "http://x"), ("OS", "乙", "http://x")]


def test_load_settings_bad_active_id_falls_back(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "active_id": "m_missing",
                "models": [
                    {
                        "id": "m_first",
                        "name": "A",
                        "provider": "deepseek",
                        "model": "chat",
                        "base_url": "http://x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("BAGU_KEY_m_first=sk-test\n", encoding="utf-8")
    s = bagu.load_settings(tmp_path)
    assert s["active_id"] == "m_first"
    assert s["model"] == "chat"


def test_test_model_draft_no_key_raises(tmp_path):
    with pytest.raises(bagu.JudgeError, match="未配置模型"):
        bagu.test_model_draft({"model": "m", "base_url": "http://x"}, root=tmp_path)


def test_update_model_unknown_id_raises(tmp_path):
    with pytest.raises(LookupError):
        bagu.update_model(
            "m_nope",
            {"name": "X", "provider": "deepseek", "model": "m", "base_url": "http://x"},
            root=tmp_path,
        )


def test_api_models_test_no_key_502(conn, tmp_path):
    code, err, _ = bagu.handle_http(
        "POST",
        "/api/models/test",
        {"model": "m", "base_url": "http://x", "api_key": ""},
        conn,
        tmp_path,
    )
    assert code == 502 and "未配置模型" in err["error"]


def test_api_put_model_test_fail_502(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, a, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {
            "name": "KeepMe",
            "provider": "deepseek",
            "model": "chat",
            "base_url": "http://x",
            "api_key": "sk-a",
        },
        conn,
        tmp_path,
    )
    assert code == 200

    def boom(*a, **k):
        raise bagu.JudgeError("fail")

    monkeypatch.setattr(bagu, "_openai_chat", boom)
    code, err, _ = bagu.handle_http(
        "PUT",
        f"/api/models/{a['id']}",
        {
            "name": "NewName",
            "provider": "deepseek",
            "model": "r",
            "base_url": "http://x",
            "api_key": "sk-b",
        },
        conn,
        tmp_path,
    )
    assert code == 502
    m = next(x for x in bagu.load_settings(tmp_path)["models"] if x["id"] == a["id"])
    assert m["name"] == "KeepMe"


def test_api_put_unknown_model_400(conn, tmp_path):
    code, err, _ = bagu.handle_http(
        "PUT",
        "/api/models/m_nope",
        {
            "name": "X",
            "provider": "deepseek",
            "model": "m",
            "base_url": "http://x",
            "api_key": "sk",
        },
        conn,
        tmp_path,
    )
    assert code == 400 and "模型不存在" in err["error"]


def test_delete_non_active_keeps_active_id(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    a = bagu.create_model(
        {
            "name": "A",
            "provider": "deepseek",
            "model": "chat",
            "base_url": "http://x/v1",
            "api_key": "sk-a",
        },
        root=tmp_path,
    )
    b = bagu.create_model(
        {
            "name": "B",
            "provider": "openai",
            "model": "gpt",
            "base_url": "http://y/v1",
            "api_key": "sk-b",
        },
        root=tmp_path,
    )
    bagu.activate_model(a["id"], root=tmp_path)
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    bagu.delete_model(b["id"], root=tmp_path)
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    assert b["id"] not in {m["id"] for m in bagu.load_settings(tmp_path)["models"]}


def test_get_api_models_test_404(conn, tmp_path):
    code, err, _ = bagu.handle_http("GET", "/api/models/test", None, conn, tmp_path)
    assert code == 404 and err["error"] == "not found"
