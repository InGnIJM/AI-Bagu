"""Executed only from the separately installed instrumentation APK.

Uses the target app's synthetic emulator data, never workstation paths. Model
transport replacements are scoped/restored; the production HTTPS gate remains.
"""
import hashlib
import json
import uuid
import urllib.request
import urllib.error
from pathlib import Path
import bagu
import android_runtime as runtime


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot():
    conn = runtime._connection()
    try:
        conn.execute("BEGIN")
        result = {}
        for table, order in (("questions", "id"), ("sessions", "id"),
                             ("session_items", "session_id,question_id"), ("sqlite_sequence", "name")):
            result[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
        result["version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        result["indexes"] = [tuple(row) for row in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name")]
    finally:
        conn.close()
    for name in ("settings.json", ".env"):
        path = runtime._paths.config_dir / name
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def summary(value):
    return {key: {"count": len(rows) if isinstance(rows, list) else None,
                  "sha256": hashlib.sha256(canonical(rows).encode()).hexdigest()}
            for key, rows in value.items()}


def request(method, path, body=None):
    # Runtime auth remains internal; neither URL nor headers leave this helper.
    import urllib.parse
    info = runtime._info
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(info["url"]).query)["token"][0]
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (info["port"], path),
        data=canonical(body).encode() if body is not None else None,
        method=method, headers={"X-Bagu-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def offline_and_model_errors():
    conn = runtime._connection()
    originals = (bagu._openai_chat, bagu._openai_chat_stream)
    saved = {name: (runtime._paths.config_dir / name).read_bytes()
             if (runtime._paths.config_dir / name).exists() else None
             for name in ("settings.json", ".env")}
    sid = None
    try:
        assert bagu.get_open_session(conn) is None, "QA requires no existing open session"
        category = "Task6 offline " + uuid.uuid4().hex[:8]
        bagu.create_question(conn, {"category": category, "question": "离线记忆验证", "answer": "本地完整答案", "url": ""})
        sid, rows = bagu.draw(conn, 1, category)
        qid = rows[0]["id"]
        result = bagu.reveal_answer(conn, sid, qid)
        assert result["answer"] == "本地完整答案"
        sub = "sub_" + str(uuid.uuid4())
        result = bagu.review_question(conn, sid, qid, "hard", sub)
        assert result["grade"] == "hard"
        row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        assert row["times_seen"] == 1 and row["level"] == 1 and row["next_due"]
        bagu.create_question(conn, {"category": category + " errors", "question": "模型错误不得评分", "answer": "答案", "url": ""})
        sid, rows = bagu.draw(conn, 1, category + " errors")
        qid = rows[0]["id"]
        body = {"session_id": sid, "question_id": qid, "text": "合成回答", "submission_id": "sub_" + str(uuid.uuid4())}

        def forbidden(*args, **kwargs):
            raise AssertionError("network must not be called")
        bagu._openai_chat = forbidden
        bagu._openai_chat_stream = forbidden
        before = snapshot()
        code, raw = request("POST", "/api/models", {"model": "qa", "base_url": "http://127.0.0.1:1234/v1", "api_key": "sk-test"})
        assert code == 400 and "HTTPS" in raw.decode()
        assert snapshot() == before

        # This is a synthetic HTTPS config, not an actual network call.
        bagu.persist_store("m_task6", [{"id": "m_task6", "name": "Task6 QA", "provider": "custom", "model": "qa",
            "base_url": "https://model.invalid/v1", "api_key": "sk-test"}], root=runtime._paths.config_dir)
        before = snapshot()
        def failing(*args, **kwargs):
            raise bagu.JudgeError("合成模型请求失败")
        bagu._openai_chat = failing
        code, _ = request("POST", "/api/answer", body)
        assert code == 502 and snapshot() == before
        bagu._openai_chat = lambda *args: "not a grading response"
        code, _ = request("POST", "/api/answer", body)
        assert code == 502 and snapshot() == before
        def broken_stream(*args):
            yield "GRADE: good\nCOMMENT: partial"
            raise bagu.JudgeError("合成断流")
        bagu._openai_chat_stream = broken_stream
        code, raw = request("POST", "/api/answer/stream", body)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ")]
        assert code == 200 and events[-1]["type"] == "error"
        assert all(event["type"] != "done" for event in events) and snapshot() == before
        return canonical({"offline_review": "hard", "seen": 1, "http_model_rejected": True,
                          "mock_transport_error": True, "malformed_grade": True, "mock_stream_error": True})
    finally:
        bagu._openai_chat, bagu._openai_chat_stream = originals
        if sid and bagu.get_open_session(conn):
            bagu.skip_session(conn, sid)
        conn.close()
        for name, content in saved.items():
            path = runtime._paths.config_dir / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)


def probe_bootstrap():
    result = {}
    for path in ("/api/stats", "/api/session"):
        try:
            result[path] = request("GET", path)[0]
        except Exception as error:
            result[path] = type(error).__name__
    return canonical(result)


def prepare_persistence():
    conn = runtime._connection()
    try:
        assert bagu.get_open_session(conn) is None, "Finish prior synthetic session before preparing upgrade"
        category = "Task6 persistence " + uuid.uuid4().hex[:8]
        bagu.create_question(conn, {"category": category, "question": "升级保留已评分分析", "answer": "持久化答案", "url": ""})
        sid, rows = bagu.draw(conn, 1, category)
        bagu.judge_answer(conn, sid, rows[0]["id"], "合成作答",
            chat_fn=lambda prompt: "GRADE: hard\nCOMMENT: 合成分析\nANSWER: 完整分析",
            root=runtime._paths.config_dir, submission_id="sub_" + str(uuid.uuid4()))
        bagu.create_question(conn, {"category": category + " draft", "question": "升级保留待提交草稿", "answer": "草稿答案", "url": ""})
        sid, rows = bagu.draw(conn, 1, category + " draft")
        bagu.persist_store("m_task6", [{"id": "m_task6", "name": "QA synthetic", "provider": "custom",
            "model": "qa-only", "base_url": "https://model.invalid/v1", "api_key": "sk-test"}], root=runtime._paths.config_dir)
        return canonical({"session_id": sid, "question_id": rows[0]["id"],
                          "submission_id": "sub_" + str(uuid.uuid4()), "flow": "answer"})
    finally:
        conn.close()


def save_snapshot():
    value = snapshot()
    path = runtime._paths.data_dir.parent / "task6-state-baseline.json"
    path.write_text(canonical(value), encoding="utf-8")
    return canonical(summary(value))


def assert_snapshot():
    expected = json.loads((runtime._paths.data_dir.parent / "task6-state-baseline.json").read_text(encoding="utf-8"))
    value = snapshot()
    assert canonical(value) == canonical(expected), "Private database/config snapshot changed"
    return canonical(summary(value))


def reject_export_temporarily():
    assert not hasattr(bagu, "_qa_saved_export_count")
    bagu._qa_saved_export_count = bagu.BACKUP_MAX_QUESTIONS
    bagu.BACKUP_MAX_QUESTIONS = 0
    return "export count limit temporarily reduced by instrumentation"


def restore_export_limit():
    if hasattr(bagu, "_qa_saved_export_count"):
        bagu.BACKUP_MAX_QUESTIONS = bagu._qa_saved_export_count
        del bagu._qa_saved_export_count
    assert bagu.BACKUP_MAX_QUESTIONS == 10000
    return "production export limit restored"
