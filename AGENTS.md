# 八股助手（八股抽问）— 项目 Agent 规则

本文件只写本仓库特有约定。通用协作规范见用户全局 `AGENTS.md`。

用户若提到 `AGENT.md`，即本文件（标准文件名 `AGENTS.md`）。

## 项目是什么

本地面试复习工具：`bagu.py` 单文件核心 + `web/index.html` 唯一页面，另有 `android/` 原生宿主。SQLite 存题、复习进度及已完成的评分结果。桌面 CLI / 网页共用数据库和**同一把会话锁**；Android 复用核心，但使用独立私有目录，不读取电脑题库或配置。Hermes 聊天走 CLI 并自行评卷；桌面网页和 Android 可用配置的 OpenAI 兼容模型评卷。

## 目录

| 路径 | 职责 |
| --- | --- |
| `bagu.py` | CLI、SQLite、会话、抓题、HTTP、评卷、设置、备份与运行路径注入 |
| `web/index.html` | 桌面和 Android 共用 UI（答题/背题 + 配置库 + 题库管理） |
| `android/` | WebView 宿主、原生桥接、Python 启动层、Java/仪器测试 |
| `assets/` | 品牌、离线字体及许可证、设计参考；打包仅用显式允许列表 |
| `scripts/` | Android 构建/校验与清洁题库种子生成 |
| `test/test_bagu.py` | 单元测试，必须用临时目录，禁止写真实 `bagu.db` |
| `test/test_android_project.py` | Android 项目、运行时、原生桥接和发布契约测试 |
| `docs/README.md`、`docs/user-guide.md` | 文档导航与详细使用说明；根 README 面向首次使用 |
| `docs/cli.md`、`docs/api.md`、`docs/architecture.md` | 命令、接口与当前架构/数据约束 |
| `docs/development.md`、`docs/android-beta.md` | 开发测试、Android 构建与安装更新 |
| `docs/validation.md`、`docs/images/` | 历史验收证据与文档截图；截图不加入应用打包列表 |
| `docs/superpowers/specs/` | 已定设计。会话网页、多模型条目均已实现 |
| `docs/superpowers/plans/` | 实现计划 |
| `.env` | 密钥，禁止提交、禁止写入文档/聊天 |
| `settings.json` | 非密钥配置，禁止提交 |
| `bagu.db` | 本地题库，禁止提交 |
| `.signing/`、`dist/` | 本地签名材料、生成的交付物，禁止提交 |

不要新增第三方 Python 依赖。不要把 HTTP 绑到非本机地址。不要新开第二个 HTML 文件（配置也在 `web/index.html`）。

## 不可违反

1. **会话锁**：每份数据库最多 1 条 `sessions.status = 'open'`，由部分唯一索引保证。有 open 会话时 `draw` 必须失败，不创建新会话。
2. **一次评分**：`grade(session_id, qid, result)` 同一会话同一题只认第一次；重复 / 错会话 / 题不在本轮 → 失败且不改库。
3. **skip 不调度**：只把会话标 `closed`，未判题的 `next_due` / `level` / `times_seen` 一律不动。
4. **CLI grade 必须带 session**：`grade <session_id> <id> <again|hard|good|easy>`。旧两位参数已废除。
5. **失败不落库**：模型 HTTP / 断流 / 解析失败不得评分；评分返回结果及答案 HTML 必须在事务提交前构造，渲染异常回滚调度、grade 与 submission。
6. **密钥**：Key 只在 `.env`。`settings.json` 禁止写 Key。GET 接口只回 `api_key_masked`。禁止把真实 Key 写进源码、测试（测试用 `sk-test` 这类假值）、README、commit、日志。
7. **禁止拷贝 Nous OAuth** 进本项目。
8. **Hermes 路径不调本仓库 LLM**：`bagu.py` 的 CLI `grade` 只落库；评卷由 Hermes 自己完成。桌面网页/Android 才走 `_openai_chat` / `_openai_chat_stream`。
9. **重放不再计分**：网页/Android 的同一 submission、同一会话和题目重试只返回已存结果；不同 submission 对已评分题仍失败，跨题复用 ID 失败。
10. **移动端隔离**：Android 仅监听 `127.0.0.1` 随机端口，API 校验每进程令牌；模型地址仅允许 HTTPS。不得放宽 WebView、跨源重定向、原生存储或文件选择边界。

## 当前实现（以代码为准）

设置是**多模型条目**：

- `settings.json`：`{active_id, models:[{id,name,provider,model,base_url}]}`
- `.env`：`BAGU_KEY_<id>=...`（每条模型一把钥匙）
- HTTP：`GET/POST /api/models`、`POST /api/models/test`、`PUT /api/models/:id`、`POST .../activate|copy`、`DELETE /api/models/:id`
- `GET /api/settings` 只读当前 active（兼容）；`POST /api/settings*` 已 404
- 网页：作答页顶部当前模型条 → 配置库选用/新建/修改/复制/删除；无 Hermes 导入
- 新建/修改及 `/api/models/test` 使用完整流式响应验证，不只收到首个 chunk 就判成功；激活/复制不重新测试
- 同步和流式请求共用构造/解析规则，默认不传 `temperature`；截断、拒答、空输出或不完整流不会计分

`load_settings` 返回 `models` / `active_id`，并把 active 的 `provider/model/base_url/api_key` 提到顶层给评卷用。

## 相关设计

`docs/superpowers/specs/2026-08-26-model-profiles-design.md`（已实现）。

会话网页 spec：`docs/superpowers/specs/2026-08-26-session-web-design.md`（已实现）。改会话协议前先读它。

后续补充：`docs/superpowers/specs/2026-08-27-session-fault-recovery-design.md`（数据库并发、submission 与中断恢复）和 `docs/superpowers/specs/2026-08-27-android-beta-design.md`（移动端边界）。早期计划是历史记录，部分旧设计已被替代；阅读入口为 `docs/README.md`。当前使用方法见 README / `docs/user-guide.md`，技术说明见 CLI / API / architecture 文档，均需结合各文档注明的源码基线和实际代码核对。测试数字、APK 哈希与未验证范围集中在 `docs/validation.md`，不作为后续版本的自动验收结论。

## 数据表

`questions`：题干、分类、answer、url、level、times_seen、times_right、next_due、last_reviewed。`UNIQUE(category, question)`。旧库由 `init_db` 自动添加 `answer` 列。

`sessions`：`id TEXT PK`，`status` 仅 `open|closed`，`created_at`，`n`，`cat`。

`session_items`：`(session_id, question_id)` PK，`grade` 空表示未判；`submission_id` 有非空唯一索引，`result_comment` / `result_full_answer` / `result_answer_source` 保存首次点评、答案及来源，不保存用户回答正文。来源由后端决定：`stored`（题库）、`model`（模型）、`NULL`（历史记录或无答案自评）。重放不重新读取题库答案替换历史结果。

`init_db` 负责事务迁移，当前 `PRAGMA user_version = 2`，拒绝更高版本。v1 升级仅新增可空答案来源，历史记录不回填；若旧库有多条 open，只保留最新会话，关闭其余会话但不修改题目调度。正式升级真实库前须另行备份完整 SQLite；v2 不能直接交给旧版程序使用，`.bagu-backup` 不是完整库备份。

`session_id`：`s_YYYYMMDD_` + 8 位 hex。用 `new_session_id()`，不要手写。

## 调度

`GRADE_INTERVALS`：again 走特殊分支（level=0，间隔 1 天）；hard/good/easy 为 1/3/7 天再乘 `LEVEL_MULT`（level 1–3 → 1/2/4，新升到的 level）。不要在未改 spec 的情况下改间隔。

抽题 SQL：到期复习优先，再随机新题。分类过滤用 `--cat` / `body.cat`。

## HTTP（当前）

只服务 `web/index.html`、显式允许的品牌/字体资源和 `/api/*`，不开放任意目录。默认 JSON；页面、字体、图片、备份 ZIP 和 SSE 使用对应类型。未知路径 404。POST/PUT 请求体须为 JSON 对象，最多 32 MiB，CSV/备份仍执行各自上限。

| 路径 | 要点 |
| --- | --- |
| `POST /api/draw` | 已有会话 → 409，带 `session_id` 和 `pending_ids` |
| `POST /api/answer` | 未配置模型 → 400，不 grade；模型失败 → 502，不 grade |
| `POST /api/answer/stream` | SSE `start/delta/done/error`；完整生成、解析、渲染成功后才提交评分 |
| `POST /api/reveal` | 仅返回本轮未判题的题库答案，不计分 |
| `POST /api/review` | 自评并保存结果，支持 submission 重放，不调用模型 |
| `GET /api/submissions/:id` | 查询已完成结果，会话关闭后也可恢复 |
| `GET /api/backup/export` | 导出题目和进度的 ZIP 字节，允许有 open 会话 |
| `POST /api/backup/restore` | `{archive_base64}`；整批校验后合并，有 open 会话时 409 |
| `POST /api/models` | 服务端先测再写；测挂 502 且不写盘 |
| `POST /api/models/:id/activate` | 只改 `active_id` |

桌面 `serve()` 固定 `127.0.0.1:8765`（端口可配置）；Android 在 `127.0.0.1` 随机端口启动。不要改成 `0.0.0.0`。Android API 用 `X-Bagu-Token` 校验访问，令牌不得写进日志或文档。

## 网页约定

- 当前已实现 Arcade Bento：主色/边框 `#0F172A`、强调色 `#FDE047`、底色 `#FFFDF0`；沿用 `web/index.html` 的 CSS token，不按早期紫色设计回退
- 当前按钮圆角 12px、卡片 16px；字体为本地 Plus Jakarta Sans / Fira Code
- 图标沿用本地 Material Symbols 与品牌图片，不引入外部运行时字体依赖，**禁止 emoji**
- 触控目标 ≥ 44px
- 提交中按钮 `disabled`
- 模型分析时展示动画、耗时和流式文本；遵循 `prefers-reduced-motion`
- 一次一道题；AI 结果依次展示评级、学习反馈和标准答案。所有等级均保存答案；`details/summary` 在 easy 时默认折叠、其余展开。题库优先，缺失时显示“模型参考答案”；历史来源为空时标注“参考答案 · 历史记录”，旧 easy 无答案不追溯生成。背题模式先展示题库答案再自评
- 桌面草稿使用 `localStorage`；Android 使用受限的原生私有存储，不能依赖随机端口对应的浏览器 origin 保存跨启动状态
- Android 底部导航为练习/题库/概览/设置，保持返回键、键盘、安全区与旧 WebView 兼容处理

## Hermes 调用顺序（改 CLI 行为时保持）

1. 可选 `stats`
2. `draw`，记下 `session_id`，一次只出示一题（不先给答案）
3. 用户作答 → Hermes 自己映射到 again/hard/good/easy
4. `grade <session_id> <id> <result>` **恰好一次**
5. 非 easy：按题目 url 讲完整答案
6. 用户中止：`skip`

对应 Skill（仓库外，改协议时同步）：`C:/Users/jm050/AppData/Local/hermes/skills/automation/spaced-repetition-quiz/SKILL.md`

## 抓题

`PAGES` 指向 xiaolincoding.com 面试页。`fetch_questions` 按 `h2` 分组、`h3` 分题，保存题目到下一标题前的正文、代码、列表和图片链接。`import` 对已有题补全 `answer` 和锚点 URL，不重置复习进度；失败单分类警告并继续。

`import --code-only` 先在数据库旁创建 `*.before-code-format-*.sqlite3`，仅恢复可与来源匹配的代码围栏/缩进；不新增题目、不替换正文或历史评分、不改调度，不调用模型。

## 题库管理与文件导入

- 网页管理 API：`GET/POST /api/questions`、`PUT/DELETE /api/questions/:id`、`POST /api/questions/import`
- CSV 新表头：`category,question,answer,url`；兼容旧的 `category,question,url`；UTF-8，分类和题目必填，答案和 URL 可空
- 解析采用整批校验；错误时不写库，重复 `category + question` 跳过且不覆盖
- 单文件最多 2 MiB、5000 道题；字段长度限制由 `_clean_question` 统一执行
- 修改题目不得重置调度字段；存在 `session_items` 引用的题目禁止物理删除
- 答案渲染只允许 `render_answer_html` 识别的 HTTP(S) 图片标记；普通文本和属性必须转义，不得直接渲染题库 HTML
- 管理页面继续放在 `web/index.html`，不要新增第二个 HTML 文件

## 备份与 Android 交付

- `.bagu-backup` 仅含 `manifest.json` / `questions.json`，保存题目、答案、URL 和调度；不含配置、Key、草稿、会话或评分分析
- 上限为 10000 题、压缩 20 MiB、解压 JSON 合计 50 MiB；检查成员名、重复项、字段及 SHA-256，失败整批不写库
- 恢复按分类+题干合并，已有题的答案、URL 和调度会被备份覆盖；不删除其他题，不修改已有会话/分析历史，有 open 会话时禁止恢复
- Android 通过 `AppPaths` 分离 data/config/static/logs；首次安装复制清洁种子，已有数据不被种子覆盖
- `internal` 使用经授权的只读源题库生成清洁种子；`public` 为空种子。不得把工作站数据库、进度、配置或密钥直接打包
- 发布脚本使用项目本地工具链和稳定签名，不自动上传；不要重新生成已有签名身份或更改机器级环境变量
- 源码合入不代表 APK 更新。重新构建/发布时重新校验精确 APK、签名、清单、原生库和哈希，更新对应验收记录

## 测试

```bash
python -m pytest test/test_bagu.py -q
python -m pytest test/test_bagu.py test/test_android_project.py -q
```

- 第一条覆盖核心与网页，需要 pytest 和 Node.js；第二条还覆盖 Android 项目，需要 Windows PowerShell、JDK 17 和本地 Android/Gradle 缓存
- 完整项目测试当前含本机工具路径约束，见 `test/test_android_project.py`；不能宣称纯 pytest 环境即可完整运行
- Java 策略测试在 `android/app/src/test/`，仪器测试在 `android/app/src/androidTest/`；不能用 Python 测试通过代替 APK、lint 或设备验证
- fixture 用 `tmp_path`；`monkeypatch` `DB_PATH` 时指向临时库
- 网络用 mock，不要打真实 xiaolincoding / 模型 API
- 断言真实 Key 不得出现；假 Key 用 `sk-test` 等

## 改代码时

- 抽题、会话、评分逻辑优先改 `bagu.py` 函数，CLI 和 HTTP 只做薄封装，避免两套规则。
- 异常：`SessionOpenError` / `GradeRejected` / `SkipRejected` / `JudgeError`。CLI 协议失败 `sys.exit(1)`，stderr 一行中文。
- AI 评卷标准：again 核心错误或无有效回答；hard 部分理解但有必要遗漏或关键错误；good 主干正确、仅次要遗漏；easy 完整准确。按语义而非篇幅、术语数量或速度评分；CLI/Hermes、自评及调度不变。
- 固定规则和两题八条校准示例放在 system 消息；当前题目、用户作答、参考资料和题库答案存在标记使用 JSON user 消息。材料不是指令，同步/流式共用构造；连通性 `ping` 保持字符串兼容。
- 评卷严格依次解析唯一的 `GRADE:` / `COMMENT:` / `ANSWER:`；兼容大小写与换行，点评必须非空。题库答案优先；无题库答案时包括 easy 在内必须有模型答案，否则不评分且不自动补答。答案来源、结果及 HTML 全部构造成功后才提交。
- `_record_grade` 必须先完成所有可能失败的结果构造再 commit；保留渲染失败回滚与同 submission 重试的回归测试
- 不要把 `__pycache__`、`.pytest_cache`、`.superpowers`、`.env` 当源码改。

## 安全检查清单

交付前确认：

- 没有真实 Key 进入将要提交的文件
- `.gitignore` 含 `.env`、`settings.json`、`bagu.db`、`.signing/` 和本地工具链/生成物目录
- 文档示例只用占位符 `sk-...` / `BAGU_KEY_<id>=`
