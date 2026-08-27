# 八股抽问：会话协议 + 本地网页

日期：2026-08-26  
状态：已实现（2026-08-26）

> 说明：本文仍是会话协议、CLI 与本地网页基础行为的依据；其中单模型配置、`BAGU_API_KEY` 和 Hermes 导入接口已被 `2026-08-26-model-profiles-design.md` 的多模型配置库设计取代。当前模型行为以该设计和代码为准。
>
> 2026-08-27 补充：数据库级唯一会话、原子评分、submission 幂等和中断恢复以 `2026-08-27-session-fault-recovery-design.md` 为准。

## 目标

1. 每次 `draw` 开一轮会话，带 `session_id`。同一会话同一题只接受第一次 `grade`，杜绝 Hermes 对一题判两遍。
2. 有未关闭会话时禁止再 `draw`；`skip` 只关会话，不改调度。
3. 写 README，并改 Hermes Skill 的工具调用规则。
4. 提供本地网页：看板 + 作答 + 模型配置。抽题/打分与 CLI 共用同一套函数和会话锁。

抽题优先级、SM-2 间隔、题库表结构保持不变。

## 非目标

- 不做账号、不做公网部署。HTTP 只绑 `127.0.0.1`。
- 不把 Nous OAuth token 拷进本项目（会过期）。
- `bagu.py` 在 Hermes 路径上不调用任何 LLM；评卷由 Hermes 自己完成。
- 不引入第三方 Python 依赖。网页评卷用标准库发 OpenAI 兼容 HTTP。

## 架构

```
Hermes 聊天          浏览器
  draw/grade/skip      /api/* + 单页
         \              /
          bagu.py 函数
               |
        SQLite bagu.db
        questions + sessions + session_items
               |
        本地 .env（网页评卷 Key，gitignore）
```

两条入口抢同一把会话锁：全局最多 1 条 `sessions.status = open`。

## 会话协议

### 生命周期

1. `draw` 成功 → 插入 `sessions`（open）和 `session_items`（grade 全 NULL），打印 `session_id` 和题目。
2. `grade <session_id> <question_id> <again|hard|good|easy>`：仅当会话 open、题目属于本轮、该项 `grade IS NULL` 时写入，并按现有 SM-2 更新 `questions`。
3. 本轮所有 item 都已 grade → 会话自动 `closed`。
4. `skip [session_id]`：把会话标 `closed`。未判题的 `questions.next_due` / `level` 一律不改，下轮仍可抽到。
5. 再 `draw` 时若已有 open 会话 → 失败，打印该 `session_id` 和未判题 id。不创建新会话。

### `session_id` 格式

`s_` + 日期 `YYYYMMDD` + `_` + 8 位小写十六进制，例如 `s_20260826_a3f2c91b`。主键 TEXT。

### CLI 命令

| 命令 | 行为 |
|------|------|
| `draw [-n N] [--cat X]` | 抽题逻辑与现在相同；成功多打印一行 `session: <id>`。有 open 会话则非 0 退出。 |
| `grade <session_id> <id> <result>` | 必须带 session。重复/越权/未知评级：非 0 退出且不改库。 |
| `skip [session_id]` | 省略 id 时关闭当前唯一 open 会话。无 open 会话：非 0，提示无需 skip。 |
| `stats` / `list` / `init` / `import` | 不变。 |
| `serve [--port 8765]` | 本机网页，默认 `http://127.0.0.1:8765`。 |

`grade` 不再接受无 session 的两位参数。这是刻意破坏性变更，用以堵住旧的批量误判。

### 报错

所有协议失败：`sys.exit(1)`，stderr 一行中文原因。Hermes 应把该行原样告诉用户。

- 已有 open 会话再 draw
- grade 缺 session / 会话不存在或已关闭 / 题不在本轮 / 已经判过
- skip 时没有 open 会话
- 网页未配置模型就提交作答
- 模型 HTTP 失败：不写 grade，可重试

## 数据表

`questions` 字段不改。新增：

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('open','closed')),
  created_at TEXT NOT NULL,
  n INTEGER NOT NULL,
  cat TEXT
);

CREATE TABLE session_items (
  session_id TEXT NOT NULL,
  question_id INTEGER NOT NULL,
  grade TEXT,
  graded_at TEXT,
  submission_id TEXT,
  result_comment TEXT,
  result_full_answer TEXT,
  PRIMARY KEY (session_id, question_id),
  FOREIGN KEY (session_id) REFERENCES sessions(id),
  FOREIGN KEY (question_id) REFERENCES questions(id)
);
```

启动时 `CREATE TABLE IF NOT EXISTS`，与现有 `init_db` 一起做。

「最多一条 open」由部分唯一索引在数据库层保证；代码仍保留友好的冲突提示。

## 谁来评卷

### Hermes（QQ / 聊天）

1. 可选 `stats`。
2. `draw`，记下 `session_id`，一次只发一题（题干，不先给答案）。
3. 用户用自己的话描述 → **Hermes 自己的 AI** 分析，映射到 `again|hard|good|easy`。
4. 调用 `grade <session_id> <id> <result>` **恰好一次**。
5. 若结果不是 `easy`：对照题目 `url` 给出完整答案和面试加分点。
6. 下一题。全部判完后会话自动关，可再 `stats`。
7. 用户中止：`skip`。禁止在 open 会话上再 `draw`。

Hermes **不得**：不带 session 调用 grade；同一题再 grade；用户未作答就自选 again/hard；本轮未结束再 draw；把 Nous token 写入 bagu 配置。

### 本地网页

用户在作答框描述 → 后端用配置页里的 OpenAI 兼容模型评卷 → 内部走同一套 `grade()`。非 `easy` 才在页面展开完整答案（模型根据题干 + 抓取的参考 URL 生成）。模型失败不落库。

网页与 Hermes 互斥：任一方持有 open 会话，另一方 draw 失败并提示先结束本轮。

## 网页

单页，标准库 `http.server` 提供 HTML 和 JSON API。只监听 `127.0.0.1`。

### 信息架构

- 顶栏：品牌 + 作答 / 模型配置
- 统计：总题、今日到期、已掌握、本轮进度
- 作答主区：无会话时「抽 5 题」；有会话时一次一道，作答框，「发给模型评判」，「结束本轮 skip」
- 分类进度条在主区下方；点击分类名 = `draw --cat`
- 模型配置：供应商、模型 ID、Base URL、API Key（回显掩码）、「从 Hermes 导入」、「保存并测试」

### 视觉（ui-ux-pro-max）

- 风格：单栏、学习紫、微交互
- Primary `#7C3AED` / On-primary `#FFFFFF` / Accent `#059669` / Background `#FAF5FF` / Foreground `#0F172A`
- 字体：Fira Sans（正文）+ Fira Code（数字、session id）
- 圆角：按钮 20px、卡片 12px、chip 8px
- 图标：SVG，不用 emoji
- 触控目标 ≥ 44px；对比度 ≥ 4.5:1；`prefers-reduced-motion` 减弱动画
- 主 CTA 每屏一个：空闲是「抽 5 题」，作答是「发给模型评判」

### API（均本机）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/stats` | 看板数据 |
| GET | `/api/session` | 当前 open 会话及未判/已判题 |
| POST | `/api/draw` | `{n, cat?}` |
| POST | `/api/answer` | `{session_id, question_id, text, submission_id?}` → 调模型 → grade；返回判定和可选完整答案 |
| POST | `/api/answer/stream` | 同上；SSE 流式评判，完整解析后才 grade |
| POST | `/api/review` | `{session_id, question_id, result, submission_id?}` → 自评并持久化题库答案 |
| GET | `/api/submissions/:id` | 查询已完成 submission 的持久化结果 |
| POST | `/api/skip` | 关闭本轮 |
| GET/POST | `/api/settings` | 读/写模型配置；GET 的 key 为掩码 |
| POST | `/api/settings/import-hermes` | 从本机 Hermes `.env` 导入 API Key 类供应商 |
| POST | `/api/settings/test` | 发一条最小 chat 请求 |

### 模型配置存储

- 非密钥：`settings.json`（provider、model、base_url）
- 密钥：项目根目录 `.env` 的 `BAGU_API_KEY`，写入 `.gitignore`
- 供应商预设：DeepSeek `https://api.deepseek.com/v1` + `deepseek-chat`；OpenRouter `https://openrouter.ai/api/v1`；自定义（用户填 base_url 和 model）
- 「从 Hermes 导入」：按顺序读 `%LOCALAPPDATA%/hermes/.env`、`~/.hermes/.env`。优先采用已填写的 `DEEPSEEK_API_KEY`（base `https://api.deepseek.com/v1`，模型 `deepseek-chat`），否则 `OPENROUTER_API_KEY`。不读取、不复制 Nous OAuth。
- 保存后测试失败仍允许保存，但作答前提示未通过

评卷 prompt 要求模型只输出：评级、简短点评、完整答案（非 easy 时必填）。用约束格式解析（例如首行 `GRADE: hard`），解析失败则不 grade，提示重试。

参考答案来源：对该题 `url` 做 HTTP GET，截取正文前若干千字作为上下文。抓取失败时仍可凭题干评，完整答案由模型按面试口径生成，并注明「未拉到参考页」。

## 文档

### README.md（仓库根目录）

包含：项目是什么、命令表、会话规则、网页 `serve`、模型配置、与 Hermes 的关系、测试命令。

### Hermes Skill

更新 `C:/Users/jm050/AppData/Local/hermes/skills/automation/spaced-repetition-quiz/SKILL.md`：

- 删除「全部答完再批量 grade」
- 写入本 spec 的 Hermes 调用顺序和禁止项
- 注明网页与 CLI 共用会话锁

## 测试

继续放在 `test/test_bagu.py`，用临时库，禁止写真实 `bagu.db`。

必测：

1. `draw` 创建 open 会话；第二次 `draw` 失败且题目集合不变
2. `grade` 第一次成功；第二次失败且 `times_seen` 不变
3. 题不属于本轮 / 错误 session → 失败
4. `skip` 后 `next_due` 仍为 NULL；随后可以再 `draw`
5. 全部判完会话 `closed`，可以再 `draw`
6. 网页评卷：mock 模型失败不落库；非 easy 响应含完整答案；easy 不含完整答案正文
7. 无模型配置时 `/api/answer` 失败
8. 旧命令 `grade <id> <result>`（无 session）应失败

## 实现边界

- 主逻辑仍在 `bagu.py`（会话函数 + serve 处理函数）。HTML/CSS 可同文件字符串或 `web/` 下静态文件，不新增框架。
- 现有 408 题数据库只加表，不重新 import。
- 不提交 `.env`、`bagu.db`、`.superpowers/`。
