#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""八股抽问系统 - SQLite + 艾宾浩斯复习调度"""
import argparse
import csv
import datetime as dt
import html as html_lib
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
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
QUESTION_IMPORT_FIELDS = ["category", "question", "answer", "url"]
LEGACY_QUESTION_IMPORT_FIELDS = ["category", "question", "url"]
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
ANSWER_IMAGE_RE = re.compile(
    r"\[图片[：:](?P<alt>[^\]\r\n]*)\]\((?P<url>https?://[^\s)]+)\)", re.I
)


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
            answer TEXT DEFAULT '',
            url TEXT DEFAULT '',
            level INTEGER DEFAULT 0,
            times_seen INTEGER DEFAULT 0,
            times_right INTEGER DEFAULT 0,
            next_due DATE,
            last_reviewed DATE,
            UNIQUE(category, question)
        )"""
    )
    question_columns = {row[1] for row in conn.execute("PRAGMA table_info(questions)")}
    if "answer" not in question_columns:
        conn.execute("ALTER TABLE questions ADD COLUMN answer TEXT DEFAULT ''")
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


class _AnswerTextParser(HTMLParser):
    """把题目章节 HTML 转成可读纯文本，并保留列表、代码和图片来源。"""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts = []
        self.in_pre = False

    def _break(self, count=1):
        self.parts.append("\n" * count)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_map = dict(attrs)
        if tag in {"p", "div", "blockquote", "table", "tr", "h4", "h5", "h6"}:
            self._break(2)
        elif tag == "br":
            self._break()
        elif tag == "li":
            self._break()
            self.parts.append("- ")
        elif tag == "pre":
            self._break(2)
            self.in_pre = True
        elif tag == "img":
            src = urllib.parse.urljoin(self.base_url, attr_map.get("src", ""))
            alt = (attr_map.get("alt") or "图片").strip()
            if src:
                self.parts.append(f"[图片：{alt}]({src})")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "pre":
            self.in_pre = False
            self._break(2)
        elif tag in {"p", "div", "blockquote", "table", "tr", "h4", "h5", "h6"}:
            self._break(2)

    def handle_data(self, data):
        if self.in_pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self):
        raw = html_lib.unescape("".join(self.parts)).replace("\xa0", " ")
        lines = [line.strip() for line in raw.splitlines()]
        cleaned = []
        for line in lines:
            if line:
                cleaned.append(line)
            elif cleaned and cleaned[-1] != "":
                cleaned.append("")
        return "\n".join(cleaned).strip()


def _html_text(fragment, base_url):
    parser = _AnswerTextParser(base_url)
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _heading_text(fragment, base_url):
    return _html_text(fragment, base_url).replace("\\#", "").strip().lstrip("#").strip()


def _anchored_url(url, attrs):
    match = re.search(r"\bid\s*=\s*(['\"])(.*?)\1", attrs, re.I | re.S)
    if not match or not match.group(2).strip():
        return url
    base, _ = urllib.parse.urldefrag(url)
    return f"{base}#{urllib.parse.quote(match.group(2).strip(), safe='-._~')}"


def parse_question_page(cat, url, html):
    """按 h2 分组、h3 分题，返回每道题对应的正文和锚点链接。"""
    heading_re = re.compile(
        r"<h(?P<level>[23])\b(?P<attrs>[^>]*)>(?P<title>.*?)</h(?P=level)\s*>",
        re.I | re.S,
    )
    headings = list(heading_re.finditer(html))
    if not headings:
        return []
    footer = re.search(r"<footer\b[^>]*class=['\"][^'\"]*page-edit", html, re.I)
    content_end = footer.start() if footer else len(html)
    questions = []
    section = ""
    section_intro = ""
    first_h3_in_section = False
    for index, heading in enumerate(headings):
        level = heading.group("level")
        title = _heading_text(heading.group("title"), url)
        if not title:
            continue
        next_start = headings[index + 1].start() if index + 1 < len(headings) else content_end
        body_html = html[heading.end() : next_start]
        if level == "2":
            section = title
            next_h2 = next(
                (i for i in range(index + 1, len(headings)) if headings[i].group("level") == "2"),
                len(headings),
            )
            has_h3 = any(headings[i].group("level") == "3" for i in range(index + 1, next_h2))
            if has_h3:
                section_intro = _html_text(body_html, url)
                first_h3_in_section = True
            else:
                questions.append(
                    (cat, title, _html_text(body_html, url), _anchored_url(url, heading.group("attrs")))
                )
                section_intro = ""
                first_h3_in_section = False
            continue
        question = f"{section}｜{title}" if section else title
        question = question.replace("｜#", "｜")
        answer = _html_text(body_html, url)
        if first_h3_in_section and section_intro:
            answer = f"{section_intro}\n\n{answer}".strip()
        first_h3_in_section = False
        questions.append(
            (cat, question, answer, _anchored_url(url, heading.group("attrs")))
        )
    return questions


def fetch_questions(cat, url):
    """抓取页面，提取题目、对应正文与标题锚点。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    return parse_question_page(cat, url, html)


def _question_identity(category, question):
    normalized = html_lib.unescape(question)
    return category.casefold(), re.sub(r"\s+", "", normalized).casefold()


def import_all(conn):
    total_new = 0
    total_updated = 0
    existing = {}
    for row in conn.execute("SELECT id, category, question, answer, url FROM questions"):
        existing.setdefault(_question_identity(row["category"], row["question"]), []).append(row)
    for cat, url in PAGES.items():
        try:
            qs = fetch_questions(cat, url)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {cat} 抓取失败: {e}")
            continue
        category_new = 0
        category_updated = 0
        for c, q, answer, u in qs:
            matches = existing.get(_question_identity(c, q), [])
            exact = [row for row in matches if row["category"] == c and row["question"] == q]
            old = exact[0] if len(exact) == 1 else matches[0] if len(matches) == 1 else None
            if old:
                next_answer = answer or old["answer"] or ""
                if (
                    q != old["question"]
                    or next_answer != (old["answer"] or "")
                    or u != (old["url"] or "")
                ):
                    conn.execute(
                        "UPDATE questions SET question=?, answer=?, url=? WHERE id=?",
                        (q, next_answer, u, old["id"]),
                    )
                    category_updated += 1
                    total_updated += 1
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO questions(category, question, answer, url)
                   VALUES(?,?,?,?)""",
                (c, q, answer, u),
            )
            total_new += cur.rowcount
            category_new += cur.rowcount
            if cur.rowcount:
                row = conn.execute(
                    "SELECT id, category, question, answer, url FROM questions WHERE id=?",
                    (cur.lastrowid,),
                ).fetchone()
                existing.setdefault(_question_identity(c, q), []).append(row)
        print(f"[OK] {cat}: 新增 {category_new}，补全/更新 {category_updated}")
    conn.commit()
    print(f"[OK] 合计: 新增 {total_new}，补全/更新 {total_updated}")
    return total_new


class QuestionValidationError(Exception):
    pass


class QuestionInUseError(Exception):
    pass


def _clean_question(data):
    if not isinstance(data, dict):
        raise QuestionValidationError("题目数据必须是对象")
    values = {}
    for key in QUESTION_IMPORT_FIELDS:
        value = data.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise QuestionValidationError(f"{key} 必须是文本")
        values[key] = value.strip()
    if not values["category"]:
        raise QuestionValidationError("分类不能为空")
    if not values["question"]:
        raise QuestionValidationError("题目不能为空")
    if len(values["category"]) > 100:
        raise QuestionValidationError("分类不能超过 100 个字符")
    if len(values["question"]) > 2000:
        raise QuestionValidationError("题目不能超过 2000 个字符")
    if len(values["answer"]) > 100000:
        raise QuestionValidationError("答案不能超过 100000 个字符")
    if len(values["url"]) > 2048:
        raise QuestionValidationError("URL 不能超过 2048 个字符")
    return values


def parse_question_csv(text):
    """解析 UTF-8 CSV；全部行通过校验后才返回。"""
    if not isinstance(text, str):
        raise QuestionValidationError("导入内容必须是 UTF-8 文本")
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise QuestionValidationError("导入文件不能超过 2 MiB")
    source = text.lstrip("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(source, newline=""), strict=True)
        if reader.fieldnames not in (QUESTION_IMPORT_FIELDS, LEGACY_QUESTION_IMPORT_FIELDS):
            expected = ",".join(QUESTION_IMPORT_FIELDS)
            raise QuestionValidationError(f"CSV 表头必须是 {expected}")
        rows = []
        for raw in reader:
            if raw is None or all(not str(v or "").strip() for v in raw.values()):
                continue
            if None in raw:
                raise QuestionValidationError(f"第 {reader.line_num} 行列数超过表头")
            try:
                if "answer" not in raw:
                    raw["answer"] = ""
                rows.append(_clean_question(raw))
            except QuestionValidationError as e:
                raise QuestionValidationError(f"第 {reader.line_num} 行：{e}") from e
            if len(rows) > MAX_IMPORT_ROWS:
                raise QuestionValidationError(f"一次最多导入 {MAX_IMPORT_ROWS} 道题")
    except csv.Error as e:
        raise QuestionValidationError(f"CSV 无法解析：{e}") from e
    if not rows:
        raise QuestionValidationError("没有可导入的题目")
    return rows


def import_question_csv(conn, text):
    rows = parse_question_csv(text)
    inserted = 0
    try:
        for item in rows:
            cur = conn.execute(
                """INSERT OR IGNORE INTO questions(category, question, answer, url)
                   VALUES(?,?,?,?)""",
                (item["category"], item["question"], item["answer"], item["url"]),
            )
            inserted += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"total": len(rows), "inserted": inserted, "skipped": len(rows) - inserted}


def render_answer_html(answer):
    """只渲染抓取器生成的 HTTP(S) 图片标记，其余内容全部转义。"""
    source = str(answer or "")
    parts = []
    cursor = 0
    for match in ANSWER_IMAGE_RE.finditer(source):
        parsed = urllib.parse.urlsplit(match.group("url"))
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        parts.append(html_lib.escape(source[cursor : match.start()]))
        url = html_lib.escape(match.group("url"), quote=True)
        alt_text = match.group("alt").strip() or "参考图片"
        alt = html_lib.escape(alt_text, quote=True)
        parts.append(
            '<figure class="answer-media">'
            f'<a class="answer-image-link" href="{url}" target="_blank" '
            f'rel="noreferrer" aria-label="打开原图：{alt}">'
            f'<img data-answer-image src="{url}" alt="{alt}" loading="lazy" '
            'decoding="async" referrerpolicy="no-referrer">'
            '<span class="image-fallback hidden" data-image-fallback>'
            '图片加载失败，点击打开原图</span></a>'
            f'<figcaption>{alt}</figcaption></figure>'
        )
        cursor = match.end()
    parts.append(html_lib.escape(source[cursor:]))
    return "".join(parts)


def _question_public(row):
    answer = row["answer"] or ""
    return {
        "id": row["id"],
        "category": row["category"],
        "question": row["question"],
        "answer": answer,
        "answer_html": render_answer_html(answer),
        "url": row["url"] or "",
        "level": row["level"],
        "times_seen": row["times_seen"],
        "times_right": row["times_right"],
        "next_due": row["next_due"],
        "last_reviewed": row["last_reviewed"],
    }


def _like_literal(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_questions(conn, query="", category="", page=1, page_size=20):
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError) as e:
        raise QuestionValidationError("分页参数必须是整数") from e
    if page < 1 or page_size < 1 or page_size > 100:
        raise QuestionValidationError("page 必须至少为 1，page_size 必须在 1 到 100 之间")
    query = str(query or "").strip()
    category = str(category or "").strip()
    where = []
    params = []
    if query:
        needle = f"%{_like_literal(query)}%"
        where.append(
            """(question LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\'
                OR answer LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\')"""
        )
        params.extend([needle, needle, needle, needle])
    if category:
        where.append("category = ?")
        params.append(category)
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM questions{clause}", params).fetchone()[0]
    offset = (page - 1) * page_size
    items = conn.execute(
        f"SELECT * FROM questions{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()
    categories = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT category FROM questions ORDER BY category COLLATE NOCASE"
        )
    ]
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [_question_public(r) for r in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "categories": categories,
    }


def create_question(conn, data):
    item = _clean_question(data)
    try:
        cur = conn.execute(
            "INSERT INTO questions(category, question, answer, url) VALUES(?,?,?,?)",
            (item["category"], item["question"], item["answer"], item["url"]),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise QuestionValidationError("同一分类下已存在相同题目") from e
    row = conn.execute("SELECT * FROM questions WHERE id=?", (cur.lastrowid,)).fetchone()
    return _question_public(row)


def update_question(conn, qid, data):
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        raise LookupError(f"题目不存在: id={qid}")
    item = _clean_question(data)
    try:
        conn.execute(
            "UPDATE questions SET category=?, question=?, answer=?, url=? WHERE id=?",
            (item["category"], item["question"], item["answer"], item["url"], qid),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise QuestionValidationError("同一分类下已存在相同题目") from e
    return _question_public(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())


def delete_question(conn, qid):
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        raise LookupError(f"题目不存在: id={qid}")
    used = conn.execute(
        "SELECT 1 FROM session_items WHERE question_id=? LIMIT 1", (qid,)
    ).fetchone()
    if used:
        raise QuestionInUseError("题目已有复习记录，不能删除；可以修改题目内容")
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    conn.commit()
    return True


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


def _openai_chat_stream(prompt, settings):
    """调用 OpenAI 兼容 SSE 接口，逐段产出文本。"""
    key = (settings or {}).get("api_key") or ""
    if not key:
        raise JudgeError("未配置模型")
    url = (settings.get("base_url") or "").rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": settings.get("model") or "deepseek-chat",
            "temperature": 0.2,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            fallback_lines = []
            saw_sse = False
            for raw_line in resp:
                line = raw_line.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                if not line.startswith("data:"):
                    fallback_lines.append(line)
                    continue
                saw_sse = True
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                    if payload.get("error"):
                        error = payload["error"]
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise JudgeError(f"模型调用失败: {message}")
                    choices = payload.get("choices")
                    if choices == [] and payload.get("usage") is not None:
                        continue
                    if not isinstance(choices, list) or not choices:
                        raise KeyError("choices")
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        raise KeyError("delta")
                    content = delta.get("content")
                except JudgeError:
                    raise
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                    raise JudgeError("模型流式返回无法解析") from e
                if isinstance(content, str) and content:
                    yield content
            if not saw_sse and fallback_lines:
                try:
                    payload = json.loads("".join(fallback_lines))
                    content = payload["choices"][0]["message"]["content"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                    raise JudgeError("模型流式返回无法解析") from e
                if isinstance(content, str) and content:
                    yield content
    except JudgeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise JudgeError(f"模型调用失败: {e}") from e


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


def _judge_context(conn, session_id, qid, user_text, root=None, require_model=True):
    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise GradeRejected(f"题目不存在: id={qid}")
    settings = load_settings(root)
    if require_model and not settings.get("api_key"):
        raise JudgeError("未配置模型")
    stored_answer = row["answer"] or ""
    ref = stored_answer or fetch_reference_text(row["url"] or "")
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
    return settings, stored_answer, prompt


def _finish_judge(conn, session_id, qid, raw, stored_answer):
    parsed = parse_judge_output(raw)
    if parsed["grade"] == "easy":
        parsed["full_answer"] = ""
    elif stored_answer:
        parsed["full_answer"] = stored_answer
    parsed["full_answer_html"] = render_answer_html(parsed["full_answer"])
    grade(conn, session_id, qid, parsed["grade"])
    return parsed


def judge_answer(conn, session_id, qid, user_text, chat_fn=None, root=None):
    settings, stored_answer, prompt = _judge_context(
        conn, session_id, qid, user_text, root=root, require_model=chat_fn is None
    )
    if chat_fn is None:
        raw = _openai_chat(prompt, settings)
    else:
        raw = chat_fn(prompt)
    return _finish_judge(conn, session_id, qid, raw, stored_answer)


def stream_answer_events(conn, body, root=None, stream_fn=None):
    body = body or {}
    session_id = body.get("session_id")
    qid = body.get("question_id")
    user_text = (body.get("text") or "").strip()
    if not session_id or qid is None or not user_text:
        raise ValueError("缺少 session_id / question_id / text")
    try:
        qid = int(qid)
    except (TypeError, ValueError) as e:
        raise ValueError("question_id 必须是整数") from e
    settings, stored_answer, prompt = _judge_context(
        conn,
        session_id,
        qid,
        user_text,
        root=root,
        require_model=stream_fn is None,
    )
    yield {"type": "start"}
    chunks = []
    fn = stream_fn or _openai_chat_stream
    for chunk in fn(prompt, settings):
        if not isinstance(chunk, str) or not chunk:
            continue
        chunks.append(chunk)
        yield {"type": "delta", "text": chunk}
    if not chunks:
        raise JudgeError("模型未返回内容")
    result = _finish_judge(conn, session_id, qid, "".join(chunks), stored_answer)
    yield {"type": "done", "result": result}


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
    parsed_url = urllib.parse.urlsplit(path)
    query_args = urllib.parse.parse_qs(parsed_url.query)
    path = parsed_url.path
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
    if method == "GET" and path == "/api/questions":
        try:
            payload = list_questions(
                conn,
                query=(query_args.get("q") or [""])[0],
                category=(query_args.get("cat") or [""])[0],
                page=(query_args.get("page") or [1])[0],
                page_size=(query_args.get("page_size") or [20])[0],
            )
        except QuestionValidationError as e:
            return 400, {"error": str(e)}, json_ct
        return 200, payload, json_ct
    if method == "POST" and path == "/api/questions/import":
        try:
            payload = import_question_csv(conn, body.get("content", ""))
        except QuestionValidationError as e:
            return 400, {"error": str(e)}, json_ct
        return 200, payload, json_ct
    if method == "POST" and path == "/api/questions":
        try:
            payload = create_question(conn, body)
        except QuestionValidationError as e:
            return 400, {"error": str(e)}, json_ct
        return 201, payload, json_ct

    question_match = re.fullmatch(r"/api/questions/(\d+)", path)
    if question_match:
        qid = int(question_match.group(1))
        try:
            if method == "PUT":
                return 200, update_question(conn, qid, body), json_ct
            if method == "DELETE":
                delete_question(conn, qid)
                return 200, {"deleted": True, "id": qid}, json_ct
        except QuestionInUseError as e:
            return 409, {"error": str(e)}, json_ct
        except (QuestionValidationError, LookupError) as e:
            return 400, {"error": str(e)}, json_ct
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


def make_http_handler(root=None, stream_fn=None):
    root = _settings_root(root)

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
            conn = get_conn()
            try:
                init_db(conn)
                code, payload, ctype = handle_http(
                    method, self.path, body, conn, root=root
                )
            finally:
                conn.close()
            self._write(code, payload, ctype)

        def _write_sse(self, payload):
            raw = (
                "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            ).encode("utf-8")
            try:
                self.wfile.write(raw)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return False
            return True

        def _stream_answer(self, body):
            conn = get_conn()
            try:
                init_db(conn)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    events = stream_answer_events(
                        conn, body, root=root, stream_fn=stream_fn
                    )
                    for event in events:
                        if not self._write_sse(event):
                            return
                except (JudgeError, GradeRejected, ValueError, LookupError) as e:
                    self._write_sse({"type": "error", "error": str(e)})
                except Exception:  # noqa: BLE001
                    self._write_sse({"type": "error", "error": "评卷失败"})
            finally:
                conn.close()

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
            if urllib.parse.urlsplit(self.path).path == "/api/answer/stream":
                self._stream_answer(body)
            else:
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

    return Handler


def serve(host="127.0.0.1", port=8765):
    httpd = ThreadingHTTPServer((host, port), make_http_handler())
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
