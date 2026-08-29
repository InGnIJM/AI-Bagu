# 架构与数据约束

本文面向维护者，说明共享核心、会话、评分、存储和平台安全边界。公开 beta.4 对应源码 `ac53f34`，已包含 SQLite v2、双模式备份、格式恢复、诊断、更新与安装确认修复。使用方法见[用户指南](user-guide.md)，请求字段见 [HTTP API](api.md)，源码与产物验收分开记录在[验证记录](validation.md)。

## 共享核心与入口

```text
桌面 Hermes / CLI ──┐
                   ├─ bagu.py ── 桌面 SQLite + 配置
桌面浏览器 ── HTTP ┘

Android Activity / WebView ── 受令牌保护的 loopback HTTP
                              └─ 同一份 bagu.py ── 应用私有 SQLite + 配置
```

- [bagu.py](../bagu.py) 承担 SQLite、抽题、调度、评分、模型配置、HTTP、题库和备份；CLI 与 HTTP 是同一套业务函数的入口，不维护两套评分规则。
- [web/index.html](../web/index.html) 是桌面和 Android 共用的唯一页面。Android 增加移动布局和受限原生桥接，不复制业务状态机。
- Hermes 自行评卷，只调用 CLI `grade` 落库；桌面网页和 Android 的 AI 评卷才调用本项目配置的模型。自评不调用模型。
- 桌面 CLI / 网页共享数据库和会话锁；Android 私有数据库有自己的锁，不读取电脑题库、配置或进度，也不自动同步。
- Python 核心只使用标准库。Android 的 Java、Chaquopy 与构建工具链另见 [Android Beta](android-beta.md)。

## 会话与事务

每份数据库最多一条 `sessions.status = 'open'`，不是只靠页面禁用按钮保证。数据库部分唯一索引 `uq_sessions_one_open` 约束 `sessions(status) WHERE status='open'`。

| 操作 | 核心行为 |
| --- | --- |
| `draw` | 在 `BEGIN IMMEDIATE` 内检查 open 会话、选题并写入 `sessions` 和 `session_items`。已有会话则失败且不新建；无符合条件的题目返回空结果、不建会话。 |
| `grade` | 要求会话 open、题目属于本轮、该项 `grade IS NULL`；第一次评分才更新调度。同一题重复评分、错会话或不属于本轮均失败且不改库。 |
| `skip` | 在短写事务内只把会话置为 closed；未判题的 `next_due`、`level`、`times_seen` 等均不改，不视作一次复习。 |
| 自动结束 | 最后一项评分完成后，在同一事务中关闭会话。 |

`session_id` 由 `new_session_id()` 生成，格式为 `s_YYYYMMDD_` 加 8 位小写十六进制。CLI `grade` 必须带 session；旧的两参数写法不再接受。命令和错误处理见 [CLI](cli.md)。

`_record_grade` 是评分写入边界：使用 `grade IS NULL` 条件更新占有本题，再更新题目进度、判断会话是否完成；最终答案 HTML 和返回结果必须在新评分事务提交前构造成功。任何失败均回滚评分、调度及 submission。模型网络请求不持有写锁：调用前只读预检，写入时再在事务内校验，不能用预检代替最终校验。

## 调度公式

抽题限定 `next_due IS NULL` 或 `next_due <= 今天`，可按分类过滤。到期复习题优先，再随机补新题；没有保证到期题按逾期天数排序。

| 评级 | 新 level | 下次间隔（天） | `times_right` |
| --- | --- | --- | --- |
| `again` | 0 | 1 | 不增加 |
| `hard` | `min(旧 level + 1, 3)` | `1 × 新等级倍率` | 不增加 |
| `good` | 同上 | `3 × 新等级倍率` | +1 |
| `easy` | 同上 | `7 × 新等级倍率` | +1 |

新 level 1 / 2 / 3 的倍率分别为 1 / 2 / 4。每次成功评分都令 `times_seen + 1`，设置 `last_reviewed = 今天`、`next_due = 今天 + 间隔`。统计中的“已掌握”为 `level >= 3`；因为 hard 也升级，level 不等同于连续答对次数。调度是本项目的简化规则，不是完整 SM-2 算法。

## SQLite 与版本迁移

| 表 | 持久数据与约束 |
| --- | --- |
| `questions` | 题干、分类、答案、URL、level、复习次数与日期；`UNIQUE(category, question)`。 |
| `sessions` | session ID、open/closed、创建时间、请求题数 `n` 和分类 `cat`；部分唯一索引限制一条 open。 |
| `session_items` | `(session_id, question_id)` 主键；`grade`、`graded_at`、`submission_id`、`result_comment`、`result_full_answer`、`result_answer_source`。非空 submission ID 有唯一索引。 |

`DATABASE_VERSION = 2`，以 `PRAGMA user_version` 标识。`init_db` 在保存点内创建/补齐结构与索引，失败回滚；遇到更高版本直接拒绝，不降级。v1 升 v2 增加可空 `result_answer_source`，旧记录保持 `NULL`，不推断来源、不回填答案、不重算进度。旧库缺失 `questions.answer` 时也会补列。

迁移若发现多条 open，保留按 `created_at DESC, rowid DESC` 排序的第一条，只关闭其余会话，不修改未判题调度。正常重复初始化不会重写未变化的版本号。

升级真实数据库前应关闭使用它的程序，另行备份完整 SQLite；升级后的库不能直接交给旧程序使用。`.bagu-backup` 是题目与进度交换格式，不是完整数据库备份，也不支持回滚数据库版本。

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
- `.bagu-backup` 新导出为 v2，仍读取 v1（按 progress 处理），格式版本与 SQLite 版本独立。只含 `manifest.json`、`questions.json`：questions 保存题目内容、不带进度，progress 另存调度；均不含会话、评分分析、配置、Key 或草稿。两模式按分类＋题干覆盖内容（包括空答案／URL）；questions 保留已有进度、新题零／null，progress 覆盖进度且日期不重算，目标独有题保留。核心档案预览不访问数据库，但 HTTP 入口仍执行公共数据库初始化，可能创建／迁移数据库，不能当作数据库只读预演。恢复在写事务内再次检查 open 会话，异常整批回滚。格式上限与接口见 [HTTP API](api.md#备份与恢复)。

## 平台与内容安全

- 桌面服务默认绑定 `127.0.0.1:8765`，端口可配置；不绑定公网地址。页面、字体与品牌资源采用显式允许列表，不是任意目录文件服务。POST/PUT 接收 JSON 对象，最大 32 MiB；CSV 和备份另有更严格上限。
- 答案经 `render_answer_html` 渲染，原始 HTML 和属性必须转义；只允许受支持的 HTTP(S) 图片/链接，畸形链接退化为普通文本。不得绕过此层直接插入不可信 HTML。
- Android 用 `AppPaths` 区分 data/config/static/logs。只在数据库不存在时复制清洁种子；已有数据只迁移结构，不被种子覆盖。internal 种子仅保留授权题目并清空进度/会话，public 为空种子，不直接打包工作站数据库。
- Android 每进程仅启动一个 `127.0.0.1` 随机端口服务。页面入口携带随机令牌，API 通过 `X-Bagu-Token` 校验；令牌不得持久化到用户配置、写入日志或文档。
- Android 模型 URL 必须 HTTPS，Python 重定向策略阻止 HTTPS 降级，并在跨源重定向时移除认证、Cookie 等敏感请求头；桌面保留自定义 HTTP 模型支持。
- Android 更新先在私有缓存完成长度、哈希、包名、版本、ABI 与证书信息校验，再由 `PackageInstallDriver` 将字节复制到系统 `PackageInstaller.Session` 并 `fsync`。提交回调只进入非导出的 `UpdateInstallActivity`，需要确认时仅转交系统提供的 `Intent.EXTRA_INTENT`；网页接口不暴露 session ID、安装器包名、路径或原始错误正文。提交后 `ready=false`，session ID、目标版本与租约持久化；重启先核对实际版本，再查询遗留 session，不自动重试。旧的 APK Provider／通用 `ACTION_VIEW` 不再属于当前架构。
- WebView 禁止任意文件访问、mixed content 和非受控顶层导航；CSP 限制远程可执行内容与 frames，并使用 no-referrer 防止启动 URL 外泄。原生存储桥只接受限定的 `bagu-` 状态键与有界数据，不传输 API Key；文件操作走系统选择器，不申请全盘存储权限。
- 语音只填入草稿，不自动评分；Android 系统识别与桌面浏览器识别的可用性、网络和隐私取决于相应服务。不能把源码回归通过等同于真实语音或模型服务已连通。

当前页面使用 Arcade Bento 的深蓝/黄/奶油色、本地 Plus Jakarta Sans / Fira Code 和 Material Symbols；旧紫色视觉不是当前设计依据。保持触控目标至少 44px、提交中禁用按钮、减少动态效果偏好，以及 Android 返回键、键盘、安全区和旧 WebView 兼容处理。

## 维护与历史设计

行为变更应同时更新对应测试与本页/API 文档；开发和验证入口见[开发指南](development.md)。[文档索引](README.md)区分用户说明、技术参考、验收证据和历史设计。

[会话设计](superpowers/specs/2026-08-26-session-web-design.md)、[模型配置设计](superpowers/specs/2026-08-26-model-profiles-design.md)、[故障恢复设计](superpowers/specs/2026-08-27-session-fault-recovery-design.md)和 [Android 设计](superpowers/specs/2026-08-27-android-beta-design.md)保留决策背景与仍有效的约束。历史计划不是执行待办清单，也不是当前 APK 验收结论；其中单模型写接口、`sessionStorage`、easy 无答案及紫色 UI 等已被替代的段落不得照搬。
