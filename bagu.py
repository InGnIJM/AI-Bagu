#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""八股抽问系统 - SQLite + 艾宾浩斯复习调度"""
import argparse
import datetime as dt
import json
import os
import re
import secrets
import sqlite3
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path(__file__).parent / "bagu.db"

PAGES = {
    "MySQL": "https://xiaolincoding.com/interview/mysql.html",
    "Redis": "https://xiaolincoding.com/interview/redis.html",
    "计算机网络": "https://xiaolincoding.com/interview/network.html",
    "操作系统": "https://xiaolincoding.com/interview/os.html",
    "消息队列": "https://xiaolincoding.com/interview/mq.html",
    "并发": "https://xiaolincoding.com/interview/juc.html",
    "微服务/系统设计": "https://xiaolincoding.com/interview/systemdesign.html",
    "分布式CAP": "https://xiaolincoding.com/interview/cap.html",
}

# SM-2简化版：等级 -> 下次间隔天数
GRADE_INTERVALS = {"again": 0, "hard": 1, "good": 3, "easy": 7}
LEVEL_MULT = {0: 1, 1: 1, 2: 2, 3: 4}  # 连续答对倍率


def get_conn(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            question TEXT NOT NULL,
            url TEXT DEFAULT '',
            level INTEGER DEFAULT 0,
            times_seen INTEGER DEFAULT 0,
            times_right INTEGER DEFAULT 0,
            next_due DATE,
            last_reviewed DATE,
            UNIQUE(category, question)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('open','closed')),
            created_at TEXT NOT NULL,
            n INTEGER NOT NULL,
            cat TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_items (
            session_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            grade TEXT,
            graded_at TEXT,
            PRIMARY KEY (session_id, question_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (question_id) REFERENCES questions(id)
        )"""
    )
    conn.commit()


def new_session_id():
    day = dt.date.today().strftime("%Y%m%d")
    return f"s_{day}_{secrets.token_hex(4)}"


def get_open_session(conn):
    return conn.execute("SELECT * FROM sessions WHERE status='open' LIMIT 1").fetchone()


def fetch_questions(cat, url):
    """抓取页面，提取 h2 小节名 + h3 问题标题"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    questions = []
    section = ""
    pending_h2 = None  # 记录上一个h2，若无h3子题则h2本身即题目
    # VuePress 渲染后的正文区
    for m in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', html, re.S):
        tag = m.group(0)
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = text.replace("\\#", "").strip().lstrip("#").strip()
        if not text:
            continue
        if tag.startswith("<h2"):
            if pending_h2:
                questions.append((cat, pending_h2, url))
            pending_h2 = text
            section = text
        else:
            q = f"{section}｜{text}" if section else text
            q = q.replace("｜#", "｜")
            questions.append((cat, q, url))
            pending_h2 = None
    if pending_h2:
        questions.append((cat, pending_h2, url))
    return questions


def import_all(conn):
    total_new = 0
    for cat, url in PAGES.items():
        try:
            qs = fetch_questions(cat, url)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {cat} 抓取失败: {e}")
            continue
        for c, q, u in qs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO questions(category, question, url) VALUES(?,?,?)",
                (c, q, u),
            )
            total_new += cur.rowcount
        print(f"[OK] {cat}: 累计新增 {total_new}")
    conn.commit()
    return total_new


class SessionOpenError(Exception):
    def __init__(self, session_id, pending_ids):
        self.session_id = session_id
        self.pending_ids = pending_ids
        super().__init__(f"已有未关闭会话 {session_id}，未判题: {pending_ids}")


def draw(conn, n=5, cat=None):
    """优先到期复习题，不足则补新题。成功返回 (session_id, rows)。"""
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
    today = dt.date.today().isoformat()
    where = "WHERE (next_due IS NULL OR next_due <= ?)"
    params = [today]
    if cat:
        where += " AND category = ?"
        params.append(cat)
    rows = conn.execute(
        f"""SELECT * FROM questions {where}
            ORDER BY (next_due IS NOT NULL) DESC,
                     CASE WHEN next_due IS NULL THEN RANDOM() ELSE 0 END
            LIMIT ?""",
        params + [n],
    ).fetchall()
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


class GradeRejected(Exception):
    pass


def grade(conn, session_id, qid, result):
    """result: again/hard/good/easy。同一会话同一题只认第一次。"""
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
    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise LookupError(f"题目不存在: id={qid}")
    today = dt.date.today()
    if result == "again":
        new_level = 0
        interval = 1
    else:
        new_level = min(row["level"] + 1, 3)
        interval = GRADE_INTERVALS[result] * LEVEL_MULT[new_level]
    next_due = (today + dt.timedelta(days=interval)).isoformat()
    right = 1 if result in ("good", "easy") else 0
    conn.execute(
        """UPDATE questions SET level=?, times_seen=times_seen+1,
           times_right=times_right+?, next_due=?, last_reviewed=?
           WHERE id=?""",
        (new_level, right, next_due, today.isoformat(), qid),
    )
    conn.execute(
        "UPDATE session_items SET grade=?, graded_at=? WHERE session_id=? AND question_id=?",
        (result, today.isoformat(), session_id, qid),
    )
    left = conn.execute(
        "SELECT COUNT(*) c FROM session_items WHERE session_id=? AND grade IS NULL",
        (session_id,),
    ).fetchone()[0]
    if left == 0:
        conn.execute("UPDATE sessions SET status='closed' WHERE id=?", (session_id,))
    conn.commit()
    return next_due


class SkipRejected(Exception):
    pass


class JudgeError(Exception):
    pass


def skip_session(conn, session_id=None):
    if session_id:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    else:
        sess = get_open_session(conn)
    if not sess or sess["status"] != "open":
        raise SkipRejected("没有进行中的会话")
    conn.execute("UPDATE sessions SET status='closed' WHERE id=?", (sess["id"],))
    conn.commit()
    return sess["id"]


PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_envs": ["DEEPSEEK_API_KEY"],
        "base_envs": ["DEEPSEEK_BASE_URL"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/auto",
        "key_envs": ["OPENROUTER_API_KEY"],
        "base_envs": ["OPENROUTER_BASE_URL"],
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_envs": ["OPENAI_API_KEY"],
        "base_envs": ["OPENAI_BASE_URL"],
    },
    "glm": {
        "label": "智谱 GLM / z.ai",
        "base_url": "https://api.z.ai/api/paas/v4",
        "model": "glm-4-flash",
        "key_envs": ["GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY", "ZHIPUAI_API_KEY"],
        "base_envs": ["GLM_BASE_URL", "ZHIPUAI_BASE_URL"],
    },
    "kimi": {
        "label": "Kimi / 月之暗面",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2-turbo-preview",
        "key_envs": ["KIMI_CN_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY"],
        "base_envs": ["KIMI_BASE_URL", "MOONSHOT_BASE_URL"],
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "key_envs": ["SILICONFLOW_API_KEY"],
        "base_envs": ["SILICONFLOW_BASE_URL"],
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "key_envs": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "base_envs": ["GEMINI_BASE_URL"],
    },
    "ollama": {
        "label": "Ollama（本地）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.1",
        "key_envs": ["OLLAMA_API_KEY"],
        "base_envs": ["OLLAMA_BASE_URL"],
    },
    "custom": {
        "label": "自定义 OpenAI 兼容",
        "base_url": "",
        "model": "",
        "key_envs": [],
        "base_envs": [],
    },
}

def list_provider_presets():
    out = []
    for pid, p in PROVIDER_PRESETS.items():
        out.append(
            {
                "id": pid,
                "label": p["label"],
                "base_url": p["base_url"],
                "model": p["model"],
            }
        )
    return out


def new_model_id():
    return "m_" + secrets.token_hex(4)


def default_model_name(provider, model):
    label = (PROVIDER_PRESETS.get(provider) or {}).get("label") or (provider or "自定义")
    model = (model or "").strip()
    return f"{label} · {model}" if model else label


def _settings_root(root=None):
    return Path(root) if root else Path(__file__).parent


def mask_api_key(key):
    if not key:
        return ""
    if len(key) < 8:
        return "•" * len(key)
    return key[:3] + "•" * (len(key) - 5) + key[-2:]


def persist_store(active_id, models, root=None):
    root = _settings_root(root)
    payload_models = []
    env_lines = []
    for m in models:
        mid = m["id"]
        payload_models.append(
            {
                "id": mid,
                "name": m.get("name") or default_model_name(m.get("provider", ""), m.get("model", "")),
                "provider": m.get("provider", ""),
                "model": m.get("model", ""),
                "base_url": m.get("base_url", ""),
            }
        )
        key = m.get("api_key") or ""
        if key:
            env_lines.append(f"BAGU_KEY_{mid}={key}")
    (root / "settings.json").write_text(
        json.dumps({"active_id": active_id or "", "models": payload_models}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / ".env").write_text(("\n".join(env_lines) + ("\n" if env_lines else "")), encoding="utf-8")


def _attach_keys(models, env_map):
    out = []
    for m in models:
        item = dict(m)
        item["api_key"] = (env_map.get("BAGU_KEY_" + item["id"]) or "").strip()
        item["api_key_masked"] = mask_api_key(item["api_key"])
        item["configured"] = bool(item["api_key"])
        out.append(item)
    return out


def _empty_settings():
    return {
        "active_id": "",
        "models": [],
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key": "",
        "api_key_masked": "",
    }


def _with_active(active_id, models):
    data = _empty_settings()
    data["active_id"] = active_id or ""
    data["models"] = models
    active = next((m for m in models if m["id"] == active_id), None)
    if active:
        data["provider"] = active.get("provider", "")
        data["model"] = active.get("model", "")
        data["base_url"] = active.get("base_url", "")
        data["api_key"] = active.get("api_key", "")
        data["api_key_masked"] = active.get("api_key_masked", "")
    return data


def load_settings(root=None):
    root = _settings_root(root)
    sp = root / "settings.json"
    raw = {}
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty_settings()
    env_map = _parse_env_map(root / ".env")
    if isinstance(raw.get("models"), list):
        models = _attach_keys(raw.get("models") or [], env_map)
        active_id = raw.get("active_id") or ""
        if active_id and not any(m["id"] == active_id for m in models):
            active_id = models[0]["id"] if models else ""
        return _with_active(active_id, models)
    if raw.get("provider") or raw.get("model") or raw.get("base_url"):
        mid = new_model_id()
        item = {
            "id": mid,
            "name": default_model_name(raw.get("provider", ""), raw.get("model", "")),
            "provider": raw.get("provider", ""),
            "model": raw.get("model", ""),
            "base_url": raw.get("base_url", ""),
            "api_key": (env_map.get("BAGU_API_KEY") or "").strip(),
        }
        persist_store(mid, [item], root=root)
        return load_settings(root)
    return _empty_settings()


def save_settings(data, api_key=None, root=None):
    mid = new_model_id()
    if api_key is None:
        api_key = load_settings(root).get("api_key") or ""
    item = {
        "id": mid,
        "name": default_model_name(data.get("provider", ""), data.get("model", "")),
        "provider": data.get("provider", ""),
        "model": data.get("model", ""),
        "base_url": data.get("base_url", ""),
        "api_key": api_key,
    }
    persist_store(mid, [item], root=root)


def _public_item(m):
    return {
        "id": m["id"],
        "name": m.get("name") or "",
        "provider": m.get("provider") or "",
        "model": m.get("model") or "",
        "base_url": m.get("base_url") or "",
        "api_key_masked": m.get("api_key_masked") or mask_api_key(m.get("api_key") or ""),
        "configured": bool(m.get("api_key")),
    }


def public_models_payload(root=None):
    s = load_settings(root)
    return {
        "active_id": s["active_id"],
        "models": [_public_item(m) for m in s["models"]],
        "presets": list_provider_presets(),
    }


def _draft_settings(body, root=None):
    key = (body.get("api_key") or "").strip()
    if not key and body.get("id"):
        s = load_settings(root)
        src = next((m for m in s["models"] if m["id"] == body["id"]), None)
        key = (src or {}).get("api_key") or ""
    return {
        "model": body.get("model") or "",
        "base_url": body.get("base_url") or "",
        "api_key": key,
    }


def test_model_draft(body, root=None, chat_fn=None):
    settings = _draft_settings(body, root=root)
    if not settings["api_key"]:
        raise JudgeError("未配置模型")
    fn = chat_fn or _openai_chat
    fn("ping", settings)


def create_model(body, root=None, chat_fn=None):
    test_model_draft(body, root=root, chat_fn=chat_fn)
    s = load_settings(root)
    mid = new_model_id()
    name = (body.get("name") or "").strip() or default_model_name(body.get("provider", ""), body.get("model", ""))
    item = {
        "id": mid,
        "name": name,
        "provider": body.get("provider", ""),
        "model": body.get("model", ""),
        "base_url": body.get("base_url", ""),
        "api_key": (body.get("api_key") or "").strip(),
    }
    models = s["models"] + [item]
    persist_store(mid, models, root=root)
    return _public_item(load_settings(root)["models"][-1])


def update_model(model_id, body, root=None, chat_fn=None):
    s = load_settings(root)
    src = next((m for m in s["models"] if m["id"] == model_id), None)
    if not src:
        raise LookupError(f"模型不存在: {model_id}")
    draft = dict(body)
    draft["id"] = model_id
    test_model_draft(draft, root=root, chat_fn=chat_fn)
    key = (body.get("api_key") or "").strip() or src.get("api_key") or ""
    name = (body.get("name") or "").strip() or default_model_name(body.get("provider", ""), body.get("model", ""))
    updated = []
    for m in s["models"]:
        if m["id"] != model_id:
            updated.append(m)
            continue
        updated.append(
            {
                "id": model_id,
                "name": name,
                "provider": body.get("provider", ""),
                "model": body.get("model", ""),
                "base_url": body.get("base_url", ""),
                "api_key": key,
            }
        )
    persist_store(s["active_id"], updated, root=root)
    return _public_item(next(m for m in load_settings(root)["models"] if m["id"] == model_id))


def activate_model(model_id, root=None):
    s = load_settings(root)
    if not any(m["id"] == model_id for m in s["models"]):
        raise LookupError(f"模型不存在: {model_id}")
    persist_store(model_id, s["models"], root=root)
    return public_models_payload(root)


def copy_model(model_id, root=None):
    s = load_settings(root)
    src = next((m for m in s["models"] if m["id"] == model_id), None)
    if not src:
        raise LookupError(f"模型不存在: {model_id}")
    nid = new_model_id()
    item = {
        "id": nid,
        "name": (src.get("name") or default_model_name(src.get("provider", ""), src.get("model", ""))) + " 副本",
        "provider": src.get("provider", ""),
        "model": src.get("model", ""),
        "base_url": src.get("base_url", ""),
        "api_key": src.get("api_key") or "",
    }
    persist_store(s["active_id"], s["models"] + [item], root=root)
    return _public_item(item)


def delete_model(model_id, root=None):
    s = load_settings(root)
    if not any(m["id"] == model_id for m in s["models"]):
        raise LookupError(f"模型不存在: {model_id}")
    left = [m for m in s["models"] if m["id"] != model_id]
    if s["active_id"] == model_id:
        active = left[0]["id"] if left else ""
    else:
        active = s["active_id"]
    persist_store(active, left, root=root)
    return public_models_payload(root)


def _parse_env_map(path):
    out = {}
    if not path or not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def parse_judge_output(text):
    m = re.search(r"GRADE:\s*(again|hard|good|easy)\b", text, re.I)
    if not m:
        raise JudgeError("无法解析评级")
    grade_v = m.group(1).lower()
    cm = re.search(r"COMMENT:\s*(.*?)(?:\n\s*ANSWER:|\Z)", text, re.I | re.S)
    comment = cm.group(1).strip() if cm else ""
    am = re.search(r"ANSWER:\s*(.*)\Z", text, re.I | re.S)
    full = am.group(1).strip() if am else ""
    return {"grade": grade_v, "comment": comment, "full_answer": full}


def _openai_chat(prompt, settings):
    key = (settings or {}).get("api_key") or ""
    if not key:
        raise JudgeError("未配置模型")
    url = (settings.get("base_url") or "").rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": settings.get("model") or "deepseek-chat",
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:  # noqa: BLE001
        raise JudgeError(f"模型调用失败: {e}") from e
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise JudgeError("模型返回无法解析") from e


def fetch_reference_text(url, limit=4000):
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def judge_answer(conn, session_id, qid, user_text, chat_fn=None, root=None):
    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise GradeRejected(f"题目不存在: id={qid}")
    settings = load_settings(root)
    if chat_fn is None:
        if not settings.get("api_key"):
            raise JudgeError("未配置模型")
        chat_fn = lambda prompt: _openai_chat(prompt, settings)
    ref = fetch_reference_text(row["url"] or "")
    prompt = (
        "你是面试官。根据用户回答给出评级，必须严格用下面格式，不要输出其它内容：\n"
        "GRADE: again|hard|good|easy\n"
        "COMMENT: 一句话点评\n"
        "ANSWER:\n"
        "完整答案（仅当评级不是 easy 时必填；easy 可空）\n\n"
        f"题目：{row['question']}\n"
        f"用户回答：{user_text}\n"
    )
    if ref:
        prompt += f"\n参考资料（可能不完整）：{ref}\n"
    else:
        prompt += "\n（未拉到参考页，按面试口径作答）\n"
    raw = chat_fn(prompt)
    parsed = parse_judge_output(raw)
    if parsed["grade"] == "easy":
        parsed["full_answer"] = ""
    grade(conn, session_id, qid, parsed["grade"])
    return parsed


def _q_public(row, grade_v=None):
    keys = row.keys()
    return {
        "id": row["id"],
        "category": row["category"],
        "question": row["question"],
        "url": row["url"] or "",
        "times_seen": row["times_seen"],
        "grade": grade_v if grade_v is not None else (row["grade"] if "grade" in keys else None),
    }


def _session_payload(conn):
    open_s = get_open_session(conn)
    if not open_s:
        return {"session_id": None, "items": [], "pending": []}
    items = conn.execute(
        """SELECT q.*, i.grade AS item_grade
           FROM session_items i JOIN questions q ON q.id = i.question_id
           WHERE i.session_id=? ORDER BY i.question_id""",
        (open_s["id"],),
    ).fetchall()
    pub = [_q_public(r, r["item_grade"]) for r in items]
    pending = [x for x in pub if x["grade"] is None]
    return {
        "session_id": open_s["id"],
        "n": open_s["n"],
        "cat": open_s["cat"],
        "items": pub,
        "pending": pending,
    }


def handle_http(method, path, body, conn, root=None):
    root = _settings_root(root)
    body = body or {}
    json_ct = "application/json"
    if method == "GET" and path in ("/", "/index.html"):
        html_path = Path(__file__).parent / "web" / "index.html"
        if not html_path.is_file():
            return 404, "missing index", "text/plain; charset=utf-8"
        return 200, html_path.read_text(encoding="utf-8"), "text/html; charset=utf-8"
    if method == "GET" and path == "/api/stats":
        s = stats(conn)
        open_s = get_open_session(conn)
        s["open_session_id"] = open_s["id"] if open_s else None
        return 200, s, json_ct
    if method == "GET" and path == "/api/session":
        return 200, _session_payload(conn), json_ct
    if method == "POST" and path == "/api/draw":
        try:
            n = int(body.get("n") or 5)
            cat = body.get("cat") or None
            sid, rows = draw(conn, n, cat)
        except SessionOpenError as e:
            return 409, {
                "error": str(e),
                "session_id": e.session_id,
                "pending_ids": e.pending_ids,
            }, json_ct
        except Exception as e:  # noqa: BLE001
            return 400, {"error": str(e)}, json_ct
        if not rows:
            return 200, {"session_id": None, "questions": []}, json_ct
        return 200, {"session_id": sid, "questions": [_q_public(r) for r in rows]}, json_ct
    if method == "POST" and path == "/api/answer":
        sid = body.get("session_id")
        qid = body.get("question_id")
        text = (body.get("text") or "").strip()
        if not sid or qid is None or not text:
            return 400, {"error": "缺少 session_id / question_id / text"}, json_ct
        try:
            out = judge_answer(conn, sid, int(qid), text, root=root)
        except JudgeError as e:
            msg = str(e)
            code = 400 if "未配置" in msg else 502
            return code, {"error": msg}, json_ct
        except (GradeRejected, ValueError, LookupError) as e:
            return 400, {"error": str(e)}, json_ct
        return 200, out, json_ct
    if method == "POST" and path == "/api/skip":
        try:
            sid = skip_session(conn, body.get("session_id"))
        except SkipRejected as e:
            return 400, {"error": str(e)}, json_ct
        return 200, {"session_id": sid, "status": "closed"}, json_ct
    if method == "GET" and path == "/api/settings":
        s = load_settings(root)
        return 200, {
            "provider": s["provider"],
            "model": s["model"],
            "base_url": s["base_url"],
            "api_key_masked": s["api_key_masked"],
            "configured": bool(s["api_key"]),
            "presets": list_provider_presets(),
        }, json_ct
    if method == "GET" and path == "/api/models":
        return 200, public_models_payload(root), json_ct
    if method == "POST" and path == "/api/models/test":
        try:
            test_model_draft(body, root=root)
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
        return 200, {"ok": True}, json_ct
    if method == "POST" and path == "/api/models":
        try:
            created = create_model(body, root=root)
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
        return 200, created, json_ct

    def _mid_action(p):
        if not p.startswith("/api/models/"):
            return None, None
        rest = p[len("/api/models/"):]
        if not rest or rest == "test":
            return None, None
        parts = rest.split("/")
        return parts[0], (parts[1] if len(parts) > 1 else "")

    mid, action = _mid_action(path)
    if mid:
        try:
            if method == "PUT" and action == "":
                return 200, update_model(mid, body, root=root), json_ct
            if method == "POST" and action == "activate":
                return 200, activate_model(mid, root=root), json_ct
            if method == "POST" and action == "copy":
                return 200, copy_model(mid, root=root), json_ct
            if method == "DELETE" and action == "":
                return 200, delete_model(mid, root=root), json_ct
        except LookupError as e:
            return 400, {"error": str(e)}, json_ct
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
    return 404, {"error": "not found"}, json_ct


def serve(host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _write(self, code, payload, ctype):
            if ctype.startswith("application/json"):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            elif isinstance(payload, bytes):
                raw = payload
            else:
                raw = str(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _dispatch(self, method, body):
            path = self.path.split("?", 1)[0]
            conn = get_conn()
            try:
                init_db(conn)
                code, payload, ctype = handle_http(method, path, body, conn)
            finally:
                conn.close()
            self._write(code, payload, ctype)

        def do_GET(self):
            self._dispatch("GET", None)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._write(400, {"error": "JSON 无法解析"}, "application/json")
                return
            self._dispatch("POST", body)

        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._write(400, {"error": "JSON 无法解析"}, "application/json")
                return
            self._dispatch("PUT", body)

        def do_DELETE(self):
            self._dispatch("DELETE", None)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"八股抽问: http://{host}:{port}")
    httpd.serve_forever()


def stats(conn):
    total = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    due = conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE next_due IS NULL OR next_due <= ?",
        (dt.date.today().isoformat(),),
    ).fetchone()["c"]
    mastered = conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE level >= 3"
    ).fetchone()["c"]
    by_cat = conn.execute(
        """SELECT category, COUNT(*) total,
                  SUM(CASE WHEN times_seen > 0 THEN 1 ELSE 0 END) seen,
                  SUM(CASE WHEN next_due IS NULL OR next_due <= date('now') THEN 1 ELSE 0 END) due_n
           FROM questions GROUP BY category ORDER BY total DESC"""
    ).fetchall()
    return {"total": total, "due": due, "mastered": mastered, "by_cat": [dict(r) for r in by_cat]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="八股抽问系统")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p_imp = sub.add_parser("import")
    p_draw = sub.add_parser("draw")
    p_draw.add_argument("-n", type=int, default=5)
    p_draw.add_argument("--cat", default=None)
    p_grade = sub.add_parser("grade")
    p_grade.add_argument("session_id")
    p_grade.add_argument("id", type=int)
    p_grade.add_argument("result", choices=list(GRADE_INTERVALS))
    p_skip = sub.add_parser("skip")
    p_skip.add_argument("session_id", nargs="?", default=None)
    sub.add_parser("stats")
    sub.add_parser("list")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        serve(port=args.port)
        return
    conn = get_conn()
    try:
        init_db(conn)
        if args.cmd == "init":
            init_db(conn)
            print("数据库已初始化")
        elif args.cmd == "import":
            init_db(conn)
            n = import_all(conn)
            print(f"导入完成，新题 {n} 道")
        elif args.cmd == "draw":
            try:
                sid, rows = draw(conn, args.n, args.cat)
            except SessionOpenError as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)
            if not rows:
                print("今天没有到期的题！用 `draw -n X` 继续学新题或 `stats` 查看进度")
                return
            print(f"session: {sid}")
            print(f"📋 抽到 {len(rows)} 道题，凭记忆作答后用 grade 打分：")
            print("   评分: again=不会 hard=勉强 good=会了 easy=秒答\n")
            for r in rows:
                tag = "🔄复习" if r["times_seen"] else "🆕新题"
                print(f"#{r['id']} [{r['category']}] {tag}")
                print(f"   {r['question'].split('｜')[-1]}")
                if r["url"]:
                    print(f"   参考: {r['url']}")
                print()
        elif args.cmd == "grade":
            try:
                nd = grade(conn, args.session_id, args.id, args.result)
            except GradeRejected as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)
            print(f"✅ 已记录，下次复习: {nd}")
        elif args.cmd == "skip":
            try:
                sid = skip_session(conn, args.session_id)
            except SkipRejected as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)
            print(f"已结束会话 {sid}")
        elif args.cmd == "stats":
            s = stats(conn)
            print(f"总题数: {s['total']} | 今日到期: {s['due']} | 已掌握(level>=3): {s['mastered']}")
            print(f"{'类别':<10}{'总数':>6}{'已刷':>6}{'到期':>6}")
            for r in s["by_cat"]:
                print(f"{r['category']:<10}{r['total']:>6}{r['seen'] or 0:>6}{r['due_n'] or 0:>6}")
        elif args.cmd == "list":
            for r in conn.execute("SELECT id, category, question FROM questions"):
                print(f"#{r['id']} [{r['category']}] {r['question']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
