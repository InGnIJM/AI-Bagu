# 架构与数据约束

本文面向维护者，说明当前源码的共享核心、会话、评分、存储和平台安全边界。2026-08-30 的开发树已升为 SQLite v3，并加入题包、面经专题和备份 v3；这些改动尚未进入公开 beta.4（`ac53f34`，SQLite/备份 v2）。使用方法见[用户指南](user-guide.md)，请求字段见 [HTTP API](api.md)，源码与产物验收分开记录在[验证记录](validation.md)。

## 共享核心与入口

```text
桌面 Hermes / CLI ──┐
                   ├─ bagu.py ── 桌面 SQLite + 配置
桌面浏览器 ── HTTP ┘

Android Activity / WebView ── 受令牌保护的 loopback HTTP
                              └─ 同一份 bagu.py ── 应用私有 SQLite + 配置
```

- [bagu.py](../bagu.py) 承担 SQLite、抽题、调度、评分、模型配置、HTTP、题库/题包、专题和备份；CLI 与 HTTP 是同一套业务函数的入口，不维护两套评分规则。
- [web/index.html](../web/index.html) 是桌面和 Android 共用的唯一页面。Android 增加移动布局和受限原生桥接，不复制业务状态机。
- Hermes 自行评卷，只调用原有 CLI `grade` 落库；桌面网页和 Android 的 AI 评卷才调用本项目配置的模型。普通自评不调用模型，准备项则只用专用完成状态。
- 桌面 CLI / 网页共享数据库和会话锁；Android 私有数据库有自己的锁，不读取电脑题库、配置或进度，也不自动同步。
- Python 核心只使用标准库。Android 的 Java、Chaquopy 与构建工具链另见 [Android Beta](android-beta.md)。

## 会话与事务

每份数据库最多一条 `sessions.status = 'open'`，不是只靠页面禁用按钮保证。数据库部分唯一索引 `uq_sessions_one_open` 约束 `sessions(status) WHERE status='open'`。

| 操作 | 核心行为 |
| --- | --- |
| `draw` | 在 `BEGIN IMMEDIATE` 内检查 open 会话、按日常资格选题并写入 `review` 会话及有序 item。已有会话则失败且不新建；无符合条件的题目返回空结果、不建会话。 |
| `start_experience` | 在同一 open 锁下按章节位置、题目位置创建 `experience` 会话；可启动整套或单章，过滤退役题但不受题包日常开关影响。 |
| `grade` | 目标必须是 `review`，要求会话 open、题目属于本轮且 `completion_type IS NULL`；第一次评分才写 `grade/completion_type='graded'` 并更新调度。重复、错会话或 prepare 均失败且不改库。 |
| `complete_prepare_question` | 目标必须是专题中的 `prepare`，只把 item 标为 `prepared|skipped`；同值重放幂等，异值拒绝，不创建 grade/submission，也不修改题目调度。 |
| `skip` | 在短写事务内只把会话置为 closed；未判题的 `next_due`、`level`、`times_seen` 等均不改，不视作一次复习。 |
| 自动结束 | 最后一项评分或准备完成后，在同一事务中关闭会话。 |

`session_id` 由 `new_session_id()` 生成，格式为 `s_YYYYMMDD_` 加 8 位小写十六进制。CLI `grade` 必须带 session；旧的两参数写法不再接受。命令和错误处理见 [CLI](cli.md)。

`session_items.position` 是所有会话的恢复顺序；pending 统一按 `completion_type IS NULL ORDER BY position`，不再依赖 question ID。旧评分行迁移为 `graded`。`_record_grade` 是 review 评分写入边界：使用 `grade IS NULL AND completion_type IS NULL` 条件更新占有本题，再更新题目进度、判断会话是否完成；最终答案 HTML 和返回结果必须在新评分事务提交前构造成功。任何失败均回滚评分、调度及 submission。模型网络请求不持有写锁：调用前只读预检，写入时再在事务内校验，不能用预检代替最终校验。

## 调度公式

日常抽题限定 `next_due IS NULL` 或 `next_due <= 今天`，并要求 `question_type='review'`、未退役；题包题还要求对应包开启 `include_in_review`，可再按知识分类过滤。到期复习题优先，再随机补新题；没有保证到期题按逾期天数排序。面经模拟按专题顺序选题，不检查 due，也不受日常开关影响，但 review 项评分后仍更新同一套调度。

| 评级 | 新 level | 下次间隔（天） | `times_right` |
| --- | --- | --- | --- |
| `again` | 0 | 1 | 不增加 |
| `hard` | `min(旧 level + 1, 3)` | `1 × 新等级倍率` | 不增加 |
| `good` | 同上 | `3 × 新等级倍率` | +1 |
| `easy` | 同上 | `7 × 新等级倍率` | +1 |

新 level 1 / 2 / 3 的倍率分别为 1 / 2 / 4。每次成功评分都令 `times_seen + 1`，设置 `last_reviewed = 今天`、`next_due = 今天 + 间隔`。统计中的“已掌握”为 `level >= 3`；因为 hard 也升级，level 不等同于连续答对次数。调度是本项目的简化规则，不是完整 SM-2 算法。

统计接口保留兼容字段 `due`，表示新题与到期复习题的总和；`review_due` 只包含 `next_due` 非空且不晚于本地今天的题，`new_count` 只包含 `next_due` 为空的题。分类统计同时返回 `seen` 与 `mastered`，看板“分类掌握度”使用 `mastered / total`，不再用“至少刷过一次”冒充掌握度。

`prepare` 不属于间隔复习题；标记 `prepared|skipped` 不修改 level、次数或日期。不同题包/面经中的同文题使用各自稳定身份与题目主键，独立累计进度，不做运行时语义去重。

## SQLite 与版本迁移

| 表 | 持久数据与约束 |
| --- | --- |
| `question_packs` | 稳定 `pack_id`、revision/display version、源/内容/manifest hash、计数、`include_in_review` 和安装时间。 |
| `questions` | 本地/题包共用题干、分类、答案/准备提示、类型、审校/退役状态和调度；本地身份为 `(category,question)`，题包身份为 `(pack_id,stable_question_id)`，由两个部分唯一索引分别约束。 |
| `question_sources` | 题包题的有序私有源路径引用与安全 HTTP(S) URL；不替代题目主 URL。 |
| `experiences` | 题包内稳定专题 ID、`interview|topic_set`、方向/公司/岗位/阶段与包内位置。 |
| `experience_sections` | 专题内稳定章节 ID、标题、推荐标记和位置。每个新题包专题恰有一个推荐章节。 |
| `experience_items` | 章节与题目的有序关系；同一章节内题目和位置均唯一。 |
| `sessions` | session ID、open/closed、创建时间、请求题数/分类、`review|experience` 类型及可选专题/章节上下文；部分唯一索引限制一条 open。 |
| `session_items` | `(session_id,question_id)` 主键；持久化 `position`、`completion_type`，以及原有 grade/submission/结果快照。position 在会话内唯一，非空 submission ID 全库唯一。 |

`DATABASE_VERSION = 3`，以 `PRAGMA user_version` 标识；每个连接启用外键。`init_db` 在保存点/事务内迁移，失败回滚；遇到更高版本直接拒绝，不降级。v1→v2 的答案来源语义保持不变；v2→v3 重建需要新增约束的父子表，保留题目整数 ID、内容/进度、会话、评分、submission 和历史结果快照，旧已评分 item 回填 `completion_type='graded'`，旧顺序按原 question ID 确定性回填。迁移结束检查外键与唯一索引。

迁移若发现多条 open，保留按 `created_at DESC, rowid DESC` 排序的第一条，只关闭其余会话，不修改未判题调度。正常重复初始化不会重写未变化的版本号。

升级真实数据库前应关闭使用它的程序，另行备份完整 SQLite；升级后的库不能直接交给旧程序使用。`.bagu-backup` 是题目与进度交换格式，不是完整数据库备份，也不支持回滚数据库版本。

## 题包契约与升级

`.bagu-pack` 是私有、本地显式导入的严格 ZIP，schema 1 只允许 canonical `manifest.json`、`questions.json`、`experiences.json` 三个 DEFLATED 成员。公共 validator 同时被运行时和 [build_interview_pack.py](../scripts/build_interview_pack.py) 调用，统一校验 20 MiB 压缩、50 MiB 解压、10000 题、精确字段/哈希、ASCII 稳定 ID、review/prepare 互斥内容、reviewed 状态、安全 HTTP(S) 来源及无孤儿/重复的专题引用。

构建器的额外职责是审计显式 `--source-root` 与不提交的 `--catalog`：冻结 README、Markdown 和 catalog 原始字节，构建前后比较目录快照，展开仅存在于私有清单的答案/提示引用，并生成确定性 ZIP。它不联网、不调用模型、不硬编码盘符。仓库不会保存面经源、私有清单或生成的 `.bagu-pack`；历史 748 项只是一份未达到产物门禁的审计基线。

安装先完成全包解析，再在 `BEGIN IMMEDIATE` 中复查 open 会话、revision/manifest 身份、孤立归属和稳定题型。首次安装默认 `include_in_review=1`；同 revision/同 manifest 幂等，高 revision 只更新可修订内容及结构并保留题目主键/进度/历史快照/用户开关。未出现在新版中的旧行保持不变，只有包内显式 `retired` 停止新的抽题；题型变化必须换稳定 ID。包题通用编辑/删除为只读冲突，不提供物理卸载。

## Submission 与结果恢复

网页为 AI 评卷和自评发送 `submission_id`（`sub_<UUID>`）；后端校验格式，字段对旧客户端可选。CLI `grade` 不使用 submission，重复调用仍失败。

- 同一 ID、同一会话和题目已完成：直接返回第一次持久化的评级、点评、答案与来源，不再调用模型或更新调度；会话已 closed 也可恢复。
- 同一 ID 用于其他题目，或用不同 ID 提交已评分题：失败且不改库。
- 只在评分成功事务内保存 submission；模型 HTTP、断流、空输出、协议解析或答案渲染失败不保存完成结果。
- `GET /api/submissions/:id` 可查询已完成结果；未知 ID 为 404。SSE 重放只发 `start` 和 `done`。

这里是“完成后幂等”，没有 processing 状态、租约或在途请求去重。两个同时在途的重试仍可能调用模型两次，但数据库只接受第一次成功评分。

`result_full_answer` 保存答案文本，HTML 在返回时安全渲染，不直接存题库 HTML。题库以后修改不会替换已存答案或来源；查询中的题干等公开字段仍取当前 `questions`，不是完整历史题目快照。

维护命令 `import --format-only --include-history` 是显式的格式恢复例外：先完整备份 SQLite，只对与同一来源的旧版解析结果整篇匹配的 `stored` 快照恢复 Markdown 结构。它不改变点评、评级、来源或调度，不生成新历史答案；正常查询/重放仍然不读取当前题库答案替换快照。默认不带 `--include-history` 时只恢复题库答案。详见 [CLI 格式修复](cli.md#修复旧答案表格与其他格式)。

## AI 评卷与答案来源

### 消息和解析

同步与流式评卷共用 `_judge_context`、请求构造和 `_finish_judge`：

1. system 消息放固定评分标准、反馈要求，以及两题各四档的校准示例。
2. user 消息是 JSON，字段为 `question`、`user_answer`、`reference_text`、`has_stored_answer`。题目、作答和参考资料是待评数据，不能改写规则或指定评级。
3. 请求发往配置 Base URL 下的 `/chat/completions`；默认不传 `temperature`。连通性测试仍支持字符串 `ping`。
4. 输出必须依次且唯一包含 `GRADE:`、`COMMENT:`、`ANSWER:`；兼容大小写和换行，拒绝额外前言、重复/乱序字段、未知评级和空点评。
5. 完整输出解析、最终答案选择及渲染成功后才计分；不因收到首个 chunk 就认定成功。

AI 按语义区分核心错误（again）、关键缺漏（hard）、次要缺漏（good）和完整准确（easy），不按篇幅、术语数量或速度评分。同步与流式响应遇到拒答、截断、内容过滤、不支持的工具调用、空内容或不完整流会失败；流中只有推理字段而无可见答案也不算成功。

### 最终答案

| 情况 | 保存的答案 | `answer_source` |
| --- | --- | --- |
| AI 评卷，有非空题库答案 | 题库原文，模型不能覆盖 | `stored` |
| AI 评卷，无题库答案 | 模型提供的非空完整答案；包括 easy 在内均必需 | `model` |
| 自评，有非空题库答案 | 题库答案 | `stored` |
| 自评无答案，或旧记录 | 自评可为空；旧记录保持原值 | `null` |

没有题库答案时，会尝试读取参考 URL 作为模型上下文；抓取失败可继续凭题干评卷。最终模型答案缺失则整次失败，不自动再次请求补答。用户回答正文不写入 SQLite，但题目、作答与参考资料会发送到用户选用的模型服务。

页面结果按“评级 → 学习反馈 → 标准答案”展示。easy 只是在页面默认折叠答案，不代表不保存；其他等级默认展开。来源为空的历史记录标注“参考答案 · 历史记录”；旧 easy 没有答案时提示历史未保存，不追溯生成。

## 配置、草稿与备份

- `settings.json` 保存 `{active_id, models:[{id,name,provider,model,base_url}]}`，不含 Key；每个模型对应 `.env` 中的 `BAGU_KEY_<id>`。HTTP 公开配置仅返回 `api_key_masked`，不返回明文 Key。
- `load_settings` 提供模型列表与 active 的顶层字段。旧单模型配置及 `BAGU_API_KEY` 在首次读写时迁移；没有 Hermes 配置导入功能，更不能复制 Nous OAuth。
- 新建/修改配置先完整读取流式测试响应，通过后才写盘；单独测试不写盘。激活与复制不重新测试，复制不切换 active。这里不应推断 `settings.json` 与 `.env` 具有 SQLite 式跨文件事务保证。
- 桌面草稿使用 `localStorage`；当前提交状态只存 submission、session、question 和 flow，不重复存作答正文。恢复时先查会话和 submission；查询网络失败不能清除本地状态。成功 skip 清除该会话草稿；确认结果或确认失效后清理相应状态。
- Android 通过 `appStorage` 使用受限原生私有存储，跨随机端口重启保留草稿；不能依靠 WebView origin 的 `localStorage` 实现跨启动恢复。
- `.bagu-backup` 新导出为 schema v3，格式版本与 SQLite 版本独立。v3 的四成员累计快照保存本地题、题包题/来源、题包 identity/revision、专题结构与 `include_in_review`；questions 保留目标进度，progress 按稳定身份覆盖调度，prepare 强制零调度。仍读取历史两成员 v1/v2，v1 按 progress 处理。任何版本都不含会话、submission、评分分析、配置、Key 或草稿。v3 导出在一个 SQLite 读事务中取得一致快照；恢复在一个 `BEGIN IMMEDIATE` 中预检 open 会话和题包冲突，再整体合并/回滚。核心档案预览不访问数据库，但 HTTP 入口仍执行公共数据库初始化，不能当作数据库只读预演。格式上限与接口见 [HTTP API](api.md#备份与恢复)。

## 平台与内容安全

- 桌面服务默认绑定 `127.0.0.1:8765`，端口可配置；不绑定公网地址。页面、字体与品牌资源采用显式允许列表，不是任意目录文件服务。POST/PUT 接收 JSON 对象，最大 32 MiB；CSV 和备份另有更严格上限。
- 答案经 `render_answer_html` 渲染，原始 HTML 和属性必须转义；只允许受支持的 HTTP(S) 图片/链接，畸形链接退化为普通文本。不得绕过此层直接插入不可信 HTML。
- Android 用 `AppPaths` 区分 data/config/static/logs。只在数据库不存在时复制清洁种子；已有数据只迁移结构，不被种子覆盖。internal 种子仅保留授权本地题并清空进度/会话，public 为空种子；两种 flavor 的种子都禁止题包题、题包/专题/来源/关系行，不直接打包工作站数据库。
- Android 每进程仅启动一个 `127.0.0.1` 随机端口服务。页面入口携带随机令牌，API 通过 `X-Bagu-Token` 校验；令牌不得持久化到用户配置、写入日志或文档。
- Android 模型 URL 必须 HTTPS，Python 重定向策略阻止 HTTPS 降级，并在跨源重定向时移除认证、Cookie 等敏感请求头；桌面保留自定义 HTTP 模型支持。
- Android 更新先在私有缓存完成长度、哈希、包名、版本、ABI 与证书信息校验，再由 `PackageInstallDriver` 将字节复制到系统 `PackageInstaller.Session` 并 `fsync`。提交回调只进入非导出的 `UpdateInstallActivity`，需要确认时仅转交系统提供的 `Intent.EXTRA_INTENT`；网页接口不暴露 session ID、安装器包名、路径或原始错误正文。提交后 `ready=false`，session ID、目标版本与租约持久化；重启先核对实际版本，再查询遗留 session，不自动重试。旧的 APK Provider／通用 `ACTION_VIEW` 不再属于当前架构。
- WebView 禁止任意文件访问、mixed content 和非受控顶层导航；CSP 限制远程可执行内容与 frames，并使用 no-referrer 防止启动 URL 外泄。原生存储桥只接受限定的 `bagu-` 状态键与有界数据，不传输 API Key；文件操作走系统选择器，不申请全盘存储权限。
- Android 的 `.bagu-pack` 导入由原生层一次读取并持有不可变字节，canonical Python inspect 的 allowlist 预览只留在原生状态并仅显示于原生确认框；JS/WebView 不接收预览，也不接收 raw/body/archive/base64/hash、题目、答案或准备提示。确认时仍由原生对同一缓存副本 install；完成后网页只收到 allowlist 过滤、脱敏的结果状态/字段。配置重建可保留内存中的待确认状态但不能隐式确认，进程死亡只留下取消标记并要求重选。返回/取消/过期回调不得消费新操作。
- 文件导入导出、诊断和更新安装共用进程级 `NativeOperationArbiter`；租约按精确 token/operation ID 释放，更新 handoff 需观察同一操作的 active→terminal（或精确同步终态），避免跨 Activity 的 check-then-act。发布预检拒绝跟踪的 `.bagu-pack`/精确私有 catalog，APK verifier 扫描所有 ZIP member，不能把题包藏到非 assets 路径。
- 语音只填入草稿，不自动评分；Android 系统识别与桌面浏览器识别的可用性、网络和隐私取决于相应服务。不能把源码回归通过等同于真实语音或模型服务已连通。

当前页面使用 Arcade Bento 的深蓝/黄/奶油色、本地 Plus Jakarta Sans / Fira Code 和 Material Symbols；旧紫色视觉不是当前设计依据。保持触控目标至少 44px、提交中禁用按钮、减少动态效果偏好，以及 Android 返回键、键盘、安全区和旧 WebView 兼容处理。

## 维护与历史设计

行为变更应同时更新对应测试与本页/API 文档；开发和验证入口见[开发指南](development.md)。[文档索引](README.md)区分用户说明、技术参考、验收证据和历史设计。

[会话设计](superpowers/specs/2026-08-26-session-web-design.md)、[模型配置设计](superpowers/specs/2026-08-26-model-profiles-design.md)、[故障恢复设计](superpowers/specs/2026-08-27-session-fault-recovery-design.md)和 [Android 设计](superpowers/specs/2026-08-27-android-beta-design.md)保留决策背景与仍有效的约束。历史计划不是执行待办清单，也不是当前 APK 验收结论；其中单模型写接口、`sessionStorage`、easy 无答案及紫色 UI 等已被替代的段落不得照搬。
