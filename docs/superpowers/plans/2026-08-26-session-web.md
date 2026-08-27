# 会话协议 + 本地网页 Implementation Plan

> 历史实施记录：保留原步骤与勾选状态，不是当前待执行清单。会话协议仍有参考价值；“没有 .git”、单模型写接口、Hermes 导入、easy 无答案及紫色 UI 均属历史，不应照搬。现行约束见[架构与数据约束](../../architecture.md)和 [HTTP API](../../api.md)，开发入口见[开发指南](../../development.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **状态说明（2026-08-26）：** 本计划对应功能已实现。文中的单模型配置、`BAGU_API_KEY` 和 `/api/settings` 写接口是当时的实现记录，后来已由 `2026-08-26-model-profiles.md` 取代；不要据此恢复旧接口。

**Goal:** 给 bagu-quiz 加上一轮一会话（每题只判一次），并提供本机网页作答/配置，Hermes 与网页共用同一把会话锁。

**Architecture:** 抽题与 SM-2 仍在 `bagu.py`。新增 `sessions` / `session_items`；`draw` 开会话，`grade` 必须带 `session_id` 且每题只写一次，`skip` 只关会话。网页用标准库 HTTP 调同一套函数；评卷 HTTP 只给网页用，Hermes 自己分析后再调 CLI `grade`。

**Tech Stack:** Python 3 标准库（sqlite3、urllib、http.server）、pytest、单页 HTML/CSS。无第三方运行时依赖。

**Spec:** `docs/superpowers/specs/2026-08-26-session-web-design.md`

**Git:** 本仓库目前没有 `.git`。跳过所有 commit 步骤，除非用户之后要求初始化仓库。

---

## 文件结构

| 路径 | 职责 |
|------|------|
| `bagu.py` | 题库、会话、抽题、打分、skip、settings、评卷、HTTP serve |
| `web/index.html` | 看板 + 作答 + 模型配置单页 |
| `test/test_bagu.py` | 会话/CLI/评卷/API 单测（临时库） |
| `settings.json` | 运行时生成：provider/model/base_url |
| `.env` | 运行时生成：`BAGU_API_KEY` |
| `.gitignore` | 忽略 db、env、缓存、`.superpowers` |
| `README.md` | 用法与工具规则 |
| Hermes Skill | `C:/Users/jm050/AppData/Local/hermes/skills/automation/spaced-repetition-quiz/SKILL.md` |

公开函数（后续任务必须用这些名字）：

```python
class SessionOpenError(Exception): ...
class GradeRejected(Exception): ...
class SkipRejected(Exception): ...
class JudgeError(Exception): ...

def new_session_id() -> str
def get_open_session(conn) -> sqlite3.Row | None
def draw(conn, n=5, cat=None) -> tuple[str, list]  # (session_id, rows)；有 open 则 SessionOpenError
def grade(conn, session_id, qid, result) -> str  # next_due；失败 GradeRejected / ValueError / LookupError
def skip_session(conn, session_id=None) -> str  # 返回关闭的 session_id
def load_settings(root: Path | None = None) -> dict
def save_settings(data: dict, api_key: str | None, root: Path | None = None) -> None
def import_hermes_settings(root: Path | None = None, hermes_env_paths: list | None = None) -> dict
def parse_judge_output(text: str) -> dict  # {grade, comment, full_answer}
def judge_answer(conn, session_id, qid, user_text, chat_fn=None) -> dict
```

---

### Task 1: 会话表与 session_id

**Files:**
- Modify: `bagu.py`（`init_db` 后追加两张表；新增 `new_session_id` / `get_open_session`）
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_bagu.py` 追加：

```python
import re

def test_init_db_creates_session_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sessions" in names and "session_items" in names


def test_new_session_id_format():
    sid = bagu.new_session_id()
    assert re.fullmatch(r"s_\d{8}_[0-9a-f]{8}", sid)


def test_get_open_session_none(conn):
    assert bagu.get_open_session(conn) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_init_db_creates_session_tables test/test_bagu.py::test_new_session_id_format test/test_bagu.py::test_get_open_session_none -v`

Expected: FAIL（`new_session_id` 未定义，或 sessions 表不存在）

- [ ] **Step 3: 最小实现**

`bagu.py` 增加 `import secrets`。`init_db` 在 questions 表之后执行 spec 中的两段 `CREATE TABLE IF NOT EXISTS`。

```python
def new_session_id():
    day = dt.date.today().strftime("%Y%m%d")
    return f"s_{day}_{secrets.token_hex(4)}"


def get_open_session(conn):
    return conn.execute("SELECT * FROM sessions WHERE status='open' LIMIT 1").fetchone()
```

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2  
Expected: PASS

- [ ] **Step 5: Commit** — 无 git 则跳过

---

### Task 2: draw 开会话，禁止第二轮

**Files:**
- Modify: `bagu.py` `draw`
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试**

```python
def test_draw_creates_open_session(conn):
    _seed(conn, 3)
    sid, rows = bagu.draw(conn, 2)
    assert re.fullmatch(r"s_\d{8}_[0-9a-f]{8}", sid)
    assert len(rows) == 2
    open_s = bagu.get_open_session(conn)
    assert open_s["id"] == sid
    n = conn.execute("SELECT COUNT(*) FROM session_items WHERE session_id=?", (sid,)).fetchone()[0]
    assert n == 2


def test_draw_second_raises_and_keeps_items(conn):
    _seed(conn, 4)
    sid, rows = bagu.draw(conn, 2)
    ids = {r["id"] for r in rows}
    with pytest.raises(bagu.SessionOpenError) as ei:
        bagu.draw(conn, 2)
    assert sid in str(ei.value)
    again = conn.execute("SELECT question_id FROM session_items WHERE session_id=?", (sid,)).fetchall()
    assert {r[0] for r in again} == ids
```

把现有 `test_draw_prefers_due_and_new` / `test_draw_with_cat_filter` 改为 `sid, rows = bagu.draw(...)` 再断言 `rows`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_draw_creates_open_session test/test_bagu.py::test_draw_second_raises_and_keeps_items -v`

Expected: FAIL（`draw` 仍只返回 rows）

- [ ] **Step 3: 最小实现**

```python
class SessionOpenError(Exception):
    def __init__(self, session_id, pending_ids):
        self.session_id = session_id
        self.pending_ids = pending_ids
        super().__init__(f"已有未关闭会话 {session_id}，未判题: {pending_ids}")


def draw(conn, n=5, cat=None):
    open_s = get_open_session(conn)
    if open_s:
        pending = [
            r[0]
            for r in conn.execute(
                "SELECT question_id FROM session_items WHERE session_id=? AND grade IS NULL",
                (open_s["id"],),
            )
        ]
        raise SessionOpenError(open_s["id"], pending)
    # 保留现有 SELECT ... LIMIT n 逻辑得到 rows
    if not rows:
        return None, []
    sid = new_session_id()
    conn.execute(
        "INSERT INTO sessions(id, status, created_at, n, cat) VALUES (?,?,?,?,?)",
        (sid, "open", dt.datetime.now().isoformat(timespec="seconds"), n, cat),
    )
    for r in rows:
        conn.execute(
            "INSERT INTO session_items(session_id, question_id) VALUES (?,?)",
            (sid, r["id"]),
        )
    conn.commit()
    return sid, rows
```

空结果：不创建会话，返回 `(None, [])`，CLI 仍打印「今天没有到期的题」。

- [ ] **Step 4: 跑相关测试确认通过**

Run: `pytest test/test_bagu.py -k draw -v`  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 3: grade 必须带 session，每题只判一次

**Files:**
- Modify: `bagu.py` `grade`
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试并改旧测试**

所有旧调用 `bagu.grade(conn, 1, "good")` 改为：先 `draw` 拿到 `sid`，再 `bagu.grade(conn, sid, qid, "good")`。`test_stats_counts` 等同理。

新增：

```python
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
```

SM-2 行为断言保持：`again` 清 level、`good` 把 next_due 推到未来。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_grade_first_ok_second_rejected test/test_bagu.py::test_grade_wrong_session_or_question -v`

Expected: FAIL（`grade` 还是两参数）

- [ ] **Step 3: 最小实现**

```python
class GradeRejected(Exception):
    pass


def grade(conn, session_id, qid, result):
    if result not in GRADE_INTERVALS:
        raise ValueError(f"未知评级: {result}")
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not sess or sess["status"] != "open":
        raise GradeRejected(f"会话不可用: {session_id}")
    item = conn.execute(
        "SELECT * FROM session_items WHERE session_id=? AND question_id=?",
        (session_id, qid),
    ).fetchone()
    if not item:
        raise GradeRejected(f"题目不属于本轮: id={qid}")
    if item["grade"] is not None:
        raise GradeRejected(f"本题已评判: id={qid}")
    # 现有 SM-2 更新 questions 的代码不变
    conn.execute(
        "UPDATE session_items SET grade=?, graded_at=? WHERE session_id=? AND question_id=?",
        (result, dt.date.today().isoformat(), session_id, qid),
    )
    left = conn.execute(
        "SELECT COUNT(*) c FROM session_items WHERE session_id=? AND grade IS NULL",
        (session_id,),
    ).fetchone()[0]
    if left == 0:
        conn.execute("UPDATE sessions SET status='closed' WHERE id=?", (session_id,))
    conn.commit()
    return next_due
```

- [ ] **Step 4: 跑 grade/stats 相关测试**

Run: `pytest test/test_bagu.py -k "grade or stats" -v`  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 4: skip 不改调度，关会话后可再 draw

**Files:**
- Modify: `bagu.py`
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试**

```python
def test_skip_closes_without_scheduling(conn):
    _seed(conn, 2)
    sid, rows = bagu.draw(conn, 2)
    qid = rows[0]["id"]
    bagu.skip_session(conn, sid)
    row = conn.execute("SELECT next_due, level, times_seen FROM questions WHERE id=?", (qid,)).fetchone()
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_skip_closes_without_scheduling test/test_bagu.py::test_skip_none_raises test/test_bagu.py::test_all_graded_auto_closes -v`

Expected: FAIL

- [ ] **Step 3: 最小实现**

```python
class SkipRejected(Exception):
    pass


def skip_session(conn, session_id=None):
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone() if session_id else get_open_session(conn)
    if not sess or sess["status"] != "open":
        raise SkipRejected("没有进行中的会话")
    conn.execute("UPDATE sessions SET status='closed' WHERE id=?", (sess["id"],))
    conn.commit()
    return sess["id"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 5: CLI 参数与退出码

**Files:**
- Modify: `bagu.py` `main`
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试**

改 `test_main_full_flow`：`draw` 输出含 `session:`；从 stdout 解析 session id；`main(["grade", sid, "1", "good"])`。

改 `test_main_grade_invalid` 保持 SystemExit。

新增：

```python
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
    with pytest.raises(SystemExit) as ei:
        bagu.main(["draw", "-n", "2"])
    assert ei.value.code == 1
    assert "未关闭" in capsys.readouterr().err
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_main_grade_without_session_exits test/test_bagu.py::test_main_draw_when_open_exits -v`

Expected: FAIL

- [ ] **Step 3: 最小实现**

`grade` 子命令改为：`session_id`、`id`、`result` 三个位置参数。

新增 `skip` 子命令，可选 `session_id`。

`main` 中：

- `draw`：打印 `session: {sid}`；捕获 `SessionOpenError` → `print(..., file=sys.stderr)` + `sys.exit(1)`
- `grade`：捕获 `GradeRejected` 同样处理
- `skip`：捕获 `SkipRejected`

空 draw：`sid` 为 None 时保持现有「没有到期」提示，exit 0。

- [ ] **Step 4: 跑 main 相关测试**

Run: `pytest test/test_bagu.py -k main -v`  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 6: settings 读写与 Hermes 导入

**Files:**
- Modify: `bagu.py`
- Test: `test/test_bagu.py`

存储：`root/settings.json` = `{provider, model, base_url}`；`root/.env` 一行 `BAGU_API_KEY=...`。默认 `root = Path(__file__).parent`。

预设：

```python
PROVIDER_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto"},
    "custom": {"base_url": "", "model": ""},
}
```

- [ ] **Step 1: 写失败测试**

```python
def test_save_and_load_settings(tmp_path):
    bagu.save_settings(
        {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        api_key="sk-test",
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    assert s["model"] == "deepseek-chat" and s["api_key"] == "sk-test"
    assert s["api_key_masked"].startswith("sk-") and "test" not in s["api_key_masked"]


def test_import_hermes_prefers_deepseek(tmp_path):
    env = tmp_path / "hermes.env"
    env.write_text("DEEPSEEK_API_KEY=sk-from-hermes\nOPENROUTER_API_KEY=sk-or\n", encoding="utf-8")
    out = bagu.import_hermes_settings(root=tmp_path, hermes_env_paths=[env])
    assert out["provider"] == "deepseek" and out["api_key"] == "sk-from-hermes"
    loaded = bagu.load_settings(tmp_path)
    assert loaded["api_key"] == "sk-from-hermes"
```

掩码规则：保留前 3 与后 2，中间 `•`，长度不足 8 则全部 `•`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_save_and_load_settings test/test_bagu.py::test_import_hermes_prefers_deepseek -v`

Expected: FAIL

- [ ] **Step 3: 最小实现**

`load_settings` 缺文件时返回空字符串字段。`import_hermes_settings` 解析 KEY=VAL，忽略 `NOUS` / 空值；先 DeepSeek 再 OpenRouter；调用 `save_settings`。不要读 `auth.json`。

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 7: 解析评卷输出 + judge_answer

**Files:**
- Modify: `bagu.py`
- Test: `test/test_bagu.py`

约定模型输出：

```
GRADE: hard
COMMENT: 只讲了 Read View
ANSWER:
完整答案正文
```

`easy` 时 `ANSWER` 可空。`parse_judge_output` 找不到合法 GRADE 则 `JudgeError`。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py -k "parse_judge or judge_" -v`

Expected: FAIL

- [ ] **Step 3: 最小实现**

`judge_answer`：查题干；`chat_fn(prompt)` 得到文本；`parse_judge_output`；`grade(...)`；返回 `{grade, comment, full_answer}`。easy 把 `full_answer` 强制 `""`。`chat_fn` 默认走 `_openai_chat`（Task 8 接上）；本任务可用占位 `raise JudgeError("未配置模型")` 当 settings 无 key。

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 8: HTTP API + 单页

**Files:**
- Create: `web/index.html`
- Modify: `bagu.py`（`handle_http` / `serve` / `_openai_chat`）
- Test: `test/test_bagu.py`

Handler 签名：`handle_http(method, path, body: dict, conn, root: Path) -> tuple[int, dict | str, str]`  
返回 `(status, payload, content_type)`。JSON API 的 payload 为 dict；`GET /` 读 `web/index.html` 为 str。

- [ ] **Step 1: 写失败测试**

```python
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
        {"session_id": drawn["session_id"], "question_id": drawn["questions"][0]["id"], "text": "x"},
        conn,
        tmp_path,
    )
    assert code == 400 and conn.execute("SELECT times_seen FROM questions").fetchone()[0] == 0
```

`/api/stats` 的 JSON 与现有 `stats()` 字段对齐，并加上 `open_session_id`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest test/test_bagu.py::test_api_draw_and_session test/test_bagu.py::test_api_answer_requires_settings -v`

Expected: FAIL

- [ ] **Step 3: 实现 handler + 页面 + serve**

路由：

- `GET /api/stats` → 200
- `GET /api/session` → 无 open 则 `{"session_id": null, "items": []}`
- `POST /api/draw` → 409 + `{error, session_id, pending_ids}` 当 `SessionOpenError`
- `POST /api/answer` → 调 `judge_answer`；无 key → 400；`JudgeError` → 502 不落库
- `POST /api/skip` → 400 当 `SkipRejected`
- `GET/POST /api/settings`
- `POST /api/settings/import-hermes`
- `POST /api/settings/test`：`chat_fn` 发 `ping`，可 mock

`serve`：`http.server.BaseHTTPRequestHandler` 绑 `127.0.0.1`，默认端口 8765。`GET /` 返回 `web/index.html`。

`web/index.html` 必须落实 spec 视觉 token（紫 `#7C3AED`、绿 `#059669`、底 `#FAF5FF`、Fira Sans/Code、无 emoji 图标）。交互：空闲抽题；作答框提交 `/api/answer`；非 easy 展开 `full_answer`；配置页保存/导入/测试。提交中按钮 `disabled`。

- [ ] **Step 4: 跑 API 测试 + 全量**

Run: `pytest test/test_bagu.py -v`  
Expected: 全部 PASS

- [ ] **Step 5: Commit** — 跳过

---

### Task 9: gitignore、README、Hermes Skill

**Files:**
- Create: `.gitignore`、`README.md`
- Modify: `C:/Users/jm050/AppData/Local/hermes/skills/automation/spaced-repetition-quiz/SKILL.md`

- [ ] **Step 1: `.gitignore`**

```
bagu.db
.env
settings.json
__pycache__/
.pytest_cache/
.coverage
.superpowers/
```

- [ ] **Step 2: README.md**

写清：项目用途；`init/import/draw/grade/skip/stats/serve`；会话规则与禁止项；网页评卷 vs Hermes 评卷；模型配置；`pytest test/test_bagu.py`。

- [ ] **Step 3: 替换 Skill「使用流程」**

按 spec「Hermes 允许的调用顺序」全文替换「全部答完统一跑 grade」。注明与网页共用会话锁、一次一题、非 easy 才讲完整答案。

- [ ] **Step 4: 全量测试再确认**

Run: `pytest test/test_bagu.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** — 跳过

---

## Spec 覆盖核对

| Spec 项 | 任务 |
|---------|------|
| 会话表 / session_id | T1 |
| draw 开轮、二次拒绝 | T2 |
| grade 带 session、只判一次、自动关闭 | T3–T4 |
| skip 不改调度 | T4 |
| CLI 破坏性变更与退出码 | T5 |
| settings / Hermes 导入 / 不碰 OAuth | T6 |
| 网页评卷解析、失败不落库、easy 无完整答案 | T7 |
| HTTP API + 单页视觉 | T8 |
| README + Skill | T9 |
| 抽题 SM-2 不变 | T2–T3 保留原 SQL / 间隔公式 |
| 仅 127.0.0.1 | T8 serve |
| 不重新 import 题库 | 无对应写库任务 |
