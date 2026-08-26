# 八股抽问 — 项目 Agent 规则

本文件只写本仓库特有约定。通用协作规范见用户全局 `AGENTS.md`。

用户若提到 `AGENT.md`，即本文件（标准文件名 `AGENTS.md`）。

## 项目是什么

本地面试八股抽问：`bagu.py` 单文件核心 + `web/index.html` 单页。SQLite 存题和复习进度。两条入口（CLI / 本机网页）共用函数和**同一把会话锁**。Hermes 聊天走 CLI，网页走 HTTP；Hermes 自己评卷，网页用配置的 OpenAI 兼容模型评卷。

## 目录

| 路径 | 职责 |
| --- | --- |
| `bagu.py` | CLI、SQLite、会话、抓题、HTTP、评卷、设置 |
| `web/index.html` | 本机 UI（作答 + 配置库） |
| `test/test_bagu.py` | 单元测试，必须用临时目录，禁止写真实 `bagu.db` |
| `docs/superpowers/specs/` | 已定设计。会话网页、多模型条目均已实现 |
| `docs/superpowers/plans/` | 实现计划 |
| `.env` | 密钥，禁止提交、禁止写入文档/聊天 |
| `settings.json` | 非密钥配置，禁止提交 |
| `bagu.db` | 本地题库，禁止提交 |

不要新增第三方 Python 依赖。不要把 HTTP 绑到非本机地址。不要新开第二个 HTML 文件（配置也在 `web/index.html`）。

## 不可违反

1. **会话锁**：全局最多 1 条 `sessions.status = 'open'`。有 open 会话时 `draw` 必须失败，不创建新会话。
2. **一次评分**：`grade(session_id, qid, result)` 同一会话同一题只认第一次；重复 / 错会话 / 题不在本轮 → 失败且不改库。
3. **skip 不调度**：只把会话标 `closed`，未判题的 `next_due` / `level` / `times_seen` 一律不动。
4. **CLI grade 必须带 session**：`grade <session_id> <id> <again|hard|good|easy>`。旧两位参数已废除。
5. **模型失败不落库**：评卷 HTTP / 解析失败不得调用 `grade`。
6. **密钥**：Key 只在 `.env`。`settings.json` 禁止写 Key。GET 接口只回 `api_key_masked`。禁止把真实 Key 写进源码、测试（测试用 `sk-test` 这类假值）、README、commit、日志。
7. **禁止拷贝 Nous OAuth** 进本项目。
8. **Hermes 路径不调本仓库 LLM**：`bagu.py` 的 CLI `grade` 只落库；评卷由 Hermes 自己完成。网页才走 `_openai_chat`。

## 当前实现（以代码为准）

设置是**多模型条目**：

- `settings.json`：`{active_id, models:[{id,name,provider,model,base_url}]}`
- `.env`：`BAGU_KEY_<id>=...`（每条模型一把钥匙）
- HTTP：`GET/POST /api/models`、`POST /api/models/test`、`PUT /api/models/:id`、`POST .../activate|copy`、`DELETE /api/models/:id`
- `GET /api/settings` 只读当前 active（兼容）；`POST /api/settings*` 已 404
- 网页：作答页顶部当前模型条 → 配置库选用/新建/修改/复制/删除；无 Hermes 导入

`load_settings` 返回 `models` / `active_id`，并把 active 的 `provider/model/base_url/api_key` 提到顶层给评卷用。

## 相关设计

`docs/superpowers/specs/2026-08-26-model-profiles-design.md`（已实现）。

会话网页 spec：`docs/superpowers/specs/2026-08-26-session-web-design.md`（已实现）。改会话协议前先读它。

## 数据表

`questions`：题干、分类、url、level、times_seen、times_right、next_due、last_reviewed。`UNIQUE(category, question)`。

`sessions`：`id TEXT PK`，`status` 仅 `open|closed`，`created_at`，`n`，`cat`。

`session_items`：`(session_id, question_id)` PK，`grade` 空表示未判。

`session_id`：`s_YYYYMMDD_` + 8 位 hex。用 `new_session_id()`，不要手写。

## 调度

`GRADE_INTERVALS`：again 走特殊分支（level=0，间隔 1 天）；hard/good/easy 为 1/3/7 天再乘 `LEVEL_MULT`（level 1–3 → 1/2/4，新升到的 level）。不要在未改 spec 的情况下改间隔。

抽题 SQL：到期复习优先，再随机新题。分类过滤用 `--cat` / `body.cat`。

## HTTP（当前）

只服务 `web/index.html` 和 `/api/*`。JSON。未知路径 404。

| 路径 | 要点 |
| --- | --- |
| `POST /api/draw` | 已有会话 → 409，带 `session_id` 和 `pending_ids` |
| `POST /api/answer` | 未配置模型 → 400，不 grade；模型失败 → 502，不 grade |
| `POST /api/models` | 服务端先测再写；测挂 502 且不写盘 |
| `POST /api/models/:id/activate` | 只改 `active_id` |

`serve()` 固定 `127.0.0.1`。不要改成 `0.0.0.0`。

## 网页约定

- 视觉：紫 `#7C3AED`、绿 `#059669`、底 `#FAF5FF`、Fira Sans / Fira Code
- 按钮圆角 20px，卡片 12px
- 图标用 SVG，**禁止 emoji**
- 触控目标 ≥ 44px
- 提交中按钮 `disabled`
- 一次一道题；非 easy 才展示 `full_answer`

## Hermes 调用顺序（改 CLI 行为时保持）

1. 可选 `stats`
2. `draw`，记下 `session_id`，一次只出示一题（不先给答案）
3. 用户作答 → Hermes 自己映射到 again/hard/good/easy
4. `grade <session_id> <id> <result>` **恰好一次**
5. 非 easy：按题目 url 讲完整答案
6. 用户中止：`skip`

对应 Skill（仓库外，改协议时同步）：`C:/Users/jm050/AppData/Local/hermes/skills/automation/spaced-repetition-quiz/SKILL.md`

## 抓题

`PAGES` 指向 xiaolincoding.com 面试页。`fetch_questions` 抽 `h2`/`h3`。`import` 失败单分类警告并继续，不要让整个 import 因一页挂掉而中止（现有行为）。

## 测试

```bash
python -m pytest test/test_bagu.py -v
```

- 文件：`test/test_bagu.py`（与全局「每文件一个 test_*.py」一致）
- fixture 用 `tmp_path`；`monkeypatch` `DB_PATH` 时指向临时库
- 网络用 mock，不要打真实 xiaolincoding / 模型 API
- 断言真实 Key 不得出现；假 Key 用 `sk-test` 等

## 改代码时

- 抽题、会话、评分逻辑优先改 `bagu.py` 函数，CLI 和 HTTP 只做薄封装，避免两套规则。
- 异常：`SessionOpenError` / `GradeRejected` / `SkipRejected` / `JudgeError`。CLI 协议失败 `sys.exit(1)`，stderr 一行中文。
- 评卷解析：`GRADE:` / `COMMENT:` / `ANSWER:`。easy 时清空 `full_answer`。
- 不要把 `__pycache__`、`.pytest_cache`、`.superpowers`、`.env` 当源码改。

## 安全检查清单

交付前确认：

- 没有真实 Key 进入将要提交的文件
- `.gitignore` 含 `.env`、`settings.json`、`bagu.db`
- 文档示例只用占位符 `sk-...` / `BAGU_KEY_<id>=`
