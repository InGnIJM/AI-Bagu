# 面经题包与专题模拟 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, and `superpowers:verification-before-completion`. 用户已授权当前任务实施；不得提交、推送、迁移 `E:/秋招/面经库`、生成或发布真实首包。

**Goal:** 在复用现有题目、评分、调度和唯一会话锁的前提下，引入私有 `.bagu-pack`、有序面经模拟、准备题自评、桌面/Android 显式导入和备份 v3。

**Architecture:** `bagu.py` 继续作为唯一业务核心。题包题与本地题共用 `questions` 和进度字段，以题包稳定 ID 保持升级后的主键与进度；专题结构独立建表并通过有序关系组装 `experience` 会话。网页仍只有 `web/index.html`；Android 只负责安全持有文件字节与确认生命周期。构建器只使用 Python 标准库和显式私有清单。

**Tech Stack:** Python 标准库、SQLite、现有 HTTP/SSE、单页 HTML/CSS/JavaScript、Android Java、pytest/Node/JUnit。

**Spec:** 用户在 2026-08-29 当前任务批准的《面经题包与专题模拟设计》。本文件固定其实现契约与执行顺序。

**Status (2026-08-30):** Tasks 1–6 已按当前未提交工作树实现并完成分任务独立审查；Task 7 的现行文档已同步。最终整树验证/总审查、真实首包整理和设备 instrumentation 不在本状态中冒充完成；精确证据与限制见 [validation.md](../../validation.md#2026-08-30面经题包与专题模拟当前开发源码)。

## Global Constraints

- 不读取、修改或迁移 `E:/秋招/面经库`；当前 `748` 项和指纹仅是审计基线，不能冒充冻结首包。
- 不新增第三方 Python 依赖，不新增第二个 HTML，不改变 Hermes `grade <session_id> <id> <result>` 协议。
- 保持每库最多一个 open 会话、首次评分、skip 不调度、失败不落库、submission 重放、Android localhost/token/HTTPS/WebView 边界。
- public/internal 种子行为不变；仓库不加入源面经、私有清单、生成的 `.bagu-pack` 或真实数据库。
- CSV 仍只创建普通 `review` 题；不同专题中的同文题不做语义去重并独立进度。
- 题包题只读；升级保留题目主键、进度和历史答案快照。类型实质变化必须换稳定 ID；遗漏旧 ID不删除，只有显式 `retired` 停止新抽题。
- `.bagu-pack` 是严格 ZIP，只含 `manifest.json`、`questions.json`、`experiences.json`；压缩 20 MiB、解压 50 MiB、最多 10,000 题。
- manifest 精确包含 `format="bagu-pack"/schema_version=1/pack_id/name/revision/display_version/source_snapshot_sha256/question_count/experience_count/questions_sha256/experiences_sha256`。同 revision 同内容幂等；同 revision 不同内容、降级均拒绝。
- 题目使用稳定 `stable_id`、`question/category/kind`（`review|prepare`）、`answer` 或 `preparation_prompt`、`review_status=reviewed`、`retired`、至少一个安全 HTTP(S) `sources` 条目。产物禁止 `answer_ref*` 或未解析引用字段。
- 专题使用稳定 `stable_id`、`kind`（`interview|topic_set`）、方向/公司/岗位/阶段、按序章节和按序题目稳定 ID；同一专题不得重复引用同一题。
- 包安装/升级、包备份恢复存在 open 会话时返回 409。导入必须先检查并确认同一份字节。
- 本地 `include_in_review` 偏好默认开启，包升级不得覆盖；关闭后不进入日常抽题/分类统计，但仍可模拟且进度保留。
- 准备题只允许 `prepared|skipped`，不调用模型且不修改 `level/times_seen/times_right/next_due/last_reviewed`。
- 任何实现任务必须先写能因功能缺失而失败的测试，记录 RED，再写最小实现并记录 GREEN；不为流程自动创建 Git commit。

### Task 1: 纯标准库扫描、审校与确定性题包构建工具

**Files:** 新增 `scripts/build_interview_pack.py`、`test/test_interview_pack_builder.py`；必要时补 `.gitignore` 的生成物模式，但不得加入真实题包或私有清单。

- [ ] 产物严格只含 `manifest.json/questions.json/experiences.json`；manifest 精确字段为 `format="bagu-pack"/schema_version=1/pack_id/name/revision/display_version/source_snapshot_sha256/question_count/experience_count/questions_sha256/experiences_sha256`。题目字段为 `stable_id/question/category/kind/answer|preparation_prompt/review_status/retired/sources`；专题为稳定 ID、`interview|topic_set` 元数据、按序章节和题目 ID。最多 10,000 题、压缩 20 MiB、解压 50 MiB。
- [ ] 先用临时 Markdown 源目录和私有 JSON 清单写失败测试，覆盖稳定构建、目录/字节漂移、未登记 Markdown、README/清单计数漂移、未复核题、重复稳定 ID、坏类型/分类/顺序/来源、缺答/提示、未解析引用、孤儿题、循环/未知引用、非法 URL。
- [ ] CLI 参数显式接收 `--source-root --catalog --output`，不硬编码盘符、不联网、不调用模型。
- [ ] 构建前后分别按规范化相对路径和原始字节 SHA-256 计算源快照；两次不一致即失败。
- [ ] 清单记录每个源文件哈希、所有专题/章节/题目及冻结数量；扫描到新增/未登记文件或清单哈希不符即失败。
- [ ] 将私有清单内允许的答案引用确定性展开；循环、未知目标、非完整结果阻止构建，产物不包含引用字段。
- [ ] JSON 使用 UTF-8、稳定键序/紧凑分隔符；ZIP 成员固定、时间戳和属性固定，使相同输入逐字节相同。
- [ ] 输出前复用共享题包校验器（若 Task 2 尚未存在，则保持独立纯函数并由 Task 2 复用），验证 manifest 数量与内容哈希。

### Task 2: SQLite v3、题包模型、严格检查与安装升级

**Files:** 修改 `bagu.py`、`test/test_bagu.py`。

- [ ] 先写 v2→v3 迁移失败测试：题目 ID/进度/答案、会话、session_items 评分与 submission、唯一 open 锁均保留；旧评分项迁移为 `completion_type=graded`；更高 user_version 仍拒绝。
- [ ] `questions` 增加 `pack_id/stable_question_id/question_type/answer_review_status/retired`，重建唯一性为本地 `(category,question)` 与题包 `(pack_id,stable_question_id)` 两个部分唯一索引。
- [ ] 新增 `question_packs`、`question_sources`、`experiences`、`experience_sections`、`experience_items`，以稳定键 upsert 并保留 SQLite 主键。
- [ ] `sessions` 增加 `session_type=review|experience` 及专题/章节上下文；`session_items` 增加持久化 `position` 和 `completion_type`，旧行顺序确定性回填。
- [ ] 实现严格 ZIP/JSON 校验及预览模型，覆盖成员名/重复项/路径、大小、SHA、稳定 ID、文本长度、来源 URL、结构引用和计数。
- [ ] 实现事务化安装/升级：首次安装、幂等、升 revision、退役；拒绝降级和同 revision 冲突；新版遗漏旧题/专题保持原样；升级不覆盖 `include_in_review`。
- [ ] 题包题在通用 PUT/DELETE/CSV 覆盖路径保持只读；题目管理响应显示题包来源、类型、审校/退役状态。
- [ ] 新增 `GET /api/packs`、`POST /api/packs/inspect`、`POST /api/packs/install`、`PUT /api/packs/:id`，安装接收与 inspect 相同的 `archive_base64`；open 会话返回 409。
- [ ] 日常 draw 和分类/统计只纳入未退役 `review` 题及已开启题包；CLI/Hermes 请求与输出协议保持兼容。

### Task 3: 有序面经会话与准备题完成语义

**Files:** 修改 `bagu.py`、`test/test_bagu.py`。

- [ ] 先写失败测试覆盖专题列表/详情、整套与章节顺序、推荐章节标记、123 题长专题、跨启动恢复、唯一 open 锁、全完成自动关闭。
- [ ] 新增 `GET /api/experiences`、`GET /api/experiences/:id`、`POST /api/experiences/:id/start`；start 可选 `section_id`，整套按章节序+题序生成持久化 session_items.position。
- [ ] `GET /api/session` 增加会话类型、专题/章节上下文、题目类型/题包来源、顺序和 completion_type；所有取题路径按 position，不按 question_id。
- [ ] 专题中的 `review` 题完全复用既有 answer/reveal/review/grade/重放逻辑及间隔进度，仍只接受首次评分。
- [ ] 新增 `POST /api/session/complete`，仅接受当前专题会话的 prepare 题与 `prepared|skipped`；重复同结果幂等，冲突/错题/错会话拒绝；不调用模型、不修改调度。
- [ ] prepare 题不得进入日常 draw、模型 answer 或普通 self-review；通用 skip 关闭会话且所有未完成题不调度。

### Task 4: `.bagu-backup` schema v3 与题包往返

**Files:** 修改 `bagu.py`、`test/test_bagu.py`。

- [ ] 先写失败测试覆盖 v3 questions/progress 导出检查恢复、v1/v2 兼容、题包快照/专题/偏好/稳定 ID 进度、revision 冲突、open 会话阻止恢复。
- [ ] v3 严格保存题包元数据、题包题、来源、专题章节顺序、`include_in_review` 和可选进度；仍不含会话、评卷历史、配置、Key 或草稿。
- [ ] questions 模式保留目标库已有进度，progress 模式按稳定键覆盖调度；普通题仍按 `(category,question)` 合并。
- [ ] 题包恢复沿用安装升级语义：同 revision/同哈希幂等，降级或同 revision 冲突整批拒绝；错误整批回滚。
- [ ] 保持既有成员/字段/大小/哈希安全检查，并让 v1/v2 继续按历史 progress 语义读取。

### Task 5: 共享网页的面经模拟与题包管理

**Files:** 修改 `web/index.html`、现有 Node/pytest 网页测试，必要时新增单一聚焦的 `.test.cjs`，不得新增页面文件。

- [ ] 先写失败测试覆盖两种练习入口、专题筛选/详情、推荐章节默认值、整套/章节启动、按序恢复、prepare 自评、题包列表/开关、只读提示和同字节预览确认。
- [ ] 练习页明确分为“日常复习”和“面经模拟”；日常复习保留分类和默认五题，面经按方向/公司/岗位选择专题再选整套或章节。
- [ ] review 题沿用现有作答/背题与流式评卷；prepare 题展示已复核准备提示及“已准备/跳过”按钮。
- [ ] 题包答案统一显示“题包参考答案 · 已复核”，不标成原帖标准答案；题库管理显示 pack/revision/题数/专题数/来源并禁止包题修改删除。
- [ ] 桌面选择 `.bagu-pack` 后将字节只提交 inspect；用户在预览上明确确认后对相同缓存 base64 调 install，取消即清空。
- [ ] 保持 Arcade Bento token、无 emoji、触控目标、reduced-motion、旧 WebView 兼容和现有模型/备份/草稿行为。

### Task 6: Android 原生同字节 `.bagu-pack` 导入生命周期

**Files:** 修改最小范围 Android bridge/controller/state Java、`web/index.html` Android 分支、Java/仪器测试和 `test/test_android_project.py`。

- [ ] 先写失败测试覆盖文件选择、同字节预览/确认、取消、Activity 重建、进程死亡不重放、正文不进 JS、过期回调、返回键和与备份/更新/诊断操作互斥。
- [ ] 原生层读取并限额持有题包字节，JS 仅接收安全预览/状态；确认时原生用同一缓存字节调用本地 API 或受限桥接，不允许 JS 替换正文。
- [ ] Activity 重建可恢复“待用户确认”的安全状态但不得隐式确认；进程死亡后要求重新选择文件。
- [ ] 复用现有文件操作互斥、token、随机 localhost、生命周期和安全事件分发；不新增宽泛 WebView/文件访问能力。
- [ ] public/internal 种子、APK 打包允许列表和版本不变；不得把 `.bagu-pack` 或私有清单打入 APK。

### Task 7: 文档、集成验收与独立审查

**Files:** 更新 `README.md`、`docs/README.md`、`docs/user-guide.md`、`docs/api.md`、`docs/architecture.md`、`docs/data-transfer-and-updates.md`、`docs/development.md`、`docs/validation.md` 及必要的项目规则说明。

- [ ] 文档区分日常 category 与独立面经模拟、题包显式导入/升级/关闭、prepare 语义、私有构建清单、备份 v3 和“不迁移/不公开/不自动更新”的边界。
- [ ] 聚焦测试通过后运行 `python -m pytest test/test_bagu.py test/test_interview_pack_builder.py -q`。
- [ ] 运行完整 `python -m pytest test/test_bagu.py test/test_android_project.py -q`，并按项目脚本运行 Android Java 单元测试、public release lint 和打包允许列表/空种子验证。
- [ ] 运行任何新增 Node 测试；读取所有完整输出、退出码与失败数。未能运行的设备/APK 验收必须明确列为未验证。
- [ ] 独立最终代码审查覆盖迁移数据安全、严格 ZIP、事务、session 锁/重放、prepare 不调度、Android 字节边界及 public 空种子；重要发现补 RED/GREEN 修复和聚焦复审。
