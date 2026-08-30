# HTTP API 参考

[文档导航](README.md) · [CLI](cli.md) · [用户指南](user-guide.md) · [架构与数据](architecture.md)

本文描述当前源码的 HTTP、备份、诊断和原生更新契约，迁移操作见[数据迁移与更新](data-transfer-and-updates.md)。当前公开 beta.5（code 5）已包含题包、面经专题、SQLite/备份 v3 及相应路由；历史 beta.4（源码 `ac53f34`）为 SQLite/备份 v2，不提供这些路由。桌面默认地址为 `http://127.0.0.1:8765`，可用 `python bagu.py serve --port 8765` 启动；Android 使用独立私有数据库和随机本机端口。这不是账号服务或公网 API。

## 通用约定与安全

- 除页面、允许的字体/图片、ZIP 和 SSE 外，响应为 JSON。已处理的业务错误通常为 `{"error":"原因"}`；客户端仍需处理网络中断和非 JSON 异常响应。
- POST / PUT 发送 UTF-8 JSON 对象，推荐 `Content-Type: application/json`；空请求体按 `{}` 处理。数组、非法 JSON 返回 400；请求体超过 32 MiB 返回 413。CSV、备份和题包另有更严格上限。
- HTTP 层拒绝 `Transfer-Encoding`、重复或非法 `Content-Length`，返回 400；不要使用分块请求体。
- Android API 请求必须携带当前进程的 `X-Bagu-Token`，缺失或不匹配返回 403。令牌由原生启动层提供，不写入文档、日志或持久配置，也不能沿用上次进程的令牌。
- 桌面 CLI 默认只监听 `127.0.0.1`。不要公开映射端口或改成 `0.0.0.0`；本机绑定不等于公网认证机制。
- 只提供唯一页面及允许的静态资源，不开放任意目录。未知路径返回 404。

## 路由总览

| 方法 | 路径 | 请求 / 作用 |
| --- | --- | --- |
| GET | `/`、`/index.html` | 共享单页 `web/index.html` |
| GET | `/assets/...` | 允许的本地字体和品牌图标，不是任意文件服务 |
| GET | `/api/stats` | 统计及 `open_session_id` |
| GET | `/api/session` | 当前 open 会话、全部本轮题目和未判题 |
| GET | `/api/experiences` | 已安装专题列表、筛选元数据、题数及推荐章节 ID |
| GET | `/api/experiences/:id` | 专题详情与有序章节 |
| POST | `/api/experiences/:id/start` | `{section_id?}`，按保存顺序启动整套或章节面经 |
| GET | `/api/questions` | `q` 搜索题目、答案和分类；`cat`、`page`、`page_size` 用于筛选和分页 |
| POST | `/api/questions` | 新增题目；成功为 201 |
| PUT | `/api/questions/:id` | 修改题目，不重置进度 |
| DELETE | `/api/questions/:id` | 删除从未进入会话的题目 |
| POST | `/api/questions/import` | `{content}`，导入 CSV 文本 |
| POST | `/api/draw` | `{n, cat?}`，开启一轮 |
| POST | `/api/answer` | `{session_id, question_id, text, submission_id?}`，同步模型评卷 |
| POST | `/api/answer/stream` | 同上，SSE 流式模型评卷 |
| POST | `/api/reveal` | `{session_id, question_id}`，只看题库答案、不评分 |
| POST | `/api/review` | `{session_id, question_id, result, submission_id?}`，自评并保存答案 |
| GET | `/api/submissions/:id` | 查询已完成的 submission，关闭会话后仍可恢复 |
| POST | `/api/session/complete` | `{session_id,question_id,completion_type}`，完成 `prepare` 项 |
| GET | `/api/packs` | 已安装题包、revision、计数和日常复习开关 |
| POST | `/api/packs/inspect` | `{archive_base64}`，完整校验 `.bagu-pack` 并预览，不安装 |
| POST | `/api/packs/install` | 对同一 `archive_base64` 执行安装/幂等重导/升级 |
| PUT | `/api/packs/:id` | 仅接受 `{include_in_review:boolean}` |
| GET | `/api/backup/export` | `mode=questions` 或 `mode=progress`，默认 progress；返回 ZIP 字节 |
| POST | `/api/backup/inspect` | `{archive_base64}`，校验档案并返回类型、题数、时间、版本，不执行恢复 |
| POST | `/api/backup/restore` | `{archive_base64}`，按文件模式事务合并题目及可选进度 |
| POST | `/api/skip` | `{session_id?}`，关闭指定或当前会话 |
| GET | `/api/models` | 模型列表、`active_id`、掩码 Key 和预设 |
| POST | `/api/models` | 新建模型，先完整测试再写入 |
| POST | `/api/models/test` | 测试草稿模型完整流式响应，不保存草稿 |
| PUT | `/api/models/:id` | 修改模型，先测试再写入；空 Key 沿用旧值 |
| POST | `/api/models/:id/activate` | 设为当前模型，不重新测试 |
| POST | `/api/models/:id/copy` | 复制配置和 Key，不改变当前模型、不重新测试 |
| DELETE | `/api/models/:id` | 删除模型 |
| GET | `/api/settings` | 只读兼容接口：当前模型字段、掩码 Key、预设 |

旧写接口 `POST /api/settings`、`POST /api/settings/test`、`POST /api/settings/import-hermes` 均为 404；连通性测试使用 `/api/models/test`。

## 统计与会话

`GET /api/stats` 返回 `total`、`due`、`review_due`、`new_count`、`mastered`、`by_cat`、`open_session_id`。为兼容旧调用方，`due` 仍表示当前可抽题数（到期题加新题）；`review_due` 只统计已进入调度且不晚于本地今天的题，`new_count` 只统计 `next_due` 为空的新题，且 `due = review_due + new_count`。统计与日常 draw 使用同一资格谓词：只包含未退役 `review` 题，题包题还要求 `include_in_review=true`；`prepare` 和已关闭题包不计入日常分类。`mastered` 为 `level >= 3`；`by_cat` 每项为 `{category, total, seen, mastered, due_n}`。

`POST /api/draw` 的 `n` 建议传正整数，省略时为 5；`cat` 按完整分类名过滤，省略/空值表示全部分类。响应为 `{session_id, questions}`；没有可选题时返回 `{"session_id":null,"questions":[]}`，不会创建会话。

`GET /api/session` 有会话时返回 `{session_id,n,cat,session_type,items,pending}`；`session_type` 为 `review|experience`。`items` 和 `pending` 按持久化 `position` 排序，`pending` 是 `completion_type=null` 的对象数组，不是 ID 数组。每项在旧题目字段外增加 `position`、`completion_type`（`null|graded|prepared|skipped`）、`question_type`、`pack_id`、`pack_name`、`stable_question_id`；仅 `prepare` 项额外返回 `preparation_prompt`，`review` 项仍不返回答案正文。面经会话另带 `experience`，单章会话再带 `section`。没有会话时为 `{"session_id":null,"session_type":null,"items":[],"pending":[]}`。

已有 open 会话时，`draw` 或面经 start 返回 409 和 `{error, session_id, pending_ids}`，不另开一轮。所有本轮项完成后自动关闭；`POST /api/skip` 返回 `{session_id,status:"closed"}`，只关闭会话，不改未完成项调度，也不撤销已完成评分/准备结果。无可关闭会话返回 400。

## 题包与面经专题

### 题包检查、安装与开关

`GET /api/packs` 返回 `{packs:[...]}`。每个对象含 `pack_id`、`name`、`revision`、`display_version`、源快照/内容/manifest SHA-256、`question_count`、`experience_count`、`include_in_review`、`installed_at` 和 `updated_at`。

`POST /api/packs/inspect` 与 `/api/packs/install` 都严格只接受：

```json
{"archive_base64":"<完整 .bagu-pack ZIP 的规范 base64>"}
```

inspect 会在不安装题包的情况下完整解析固定三成员、canonical UTF-8 JSON、DEFLATED 压缩、大小、哈希、稳定 ID、文本、HTTP(S) 来源和专题引用，返回公开 manifest 字段、三成员哈希、`installed_revision` 以及 `status`：`new|upgrade|installed|downgrade|conflict`。它不返回题干、答案或准备提示正文。HTTP 入口仍会经过公共数据库初始化，所以不能当作数据库 schema 的绝对只读预演。

install 必须由客户端在预览后对同一份缓存 base64 调用。首次安装返回 201、`status="installed"`；高 revision 升级与相同 revision/相同 hash 的幂等重导返回 200，状态分别为 `upgraded` / `unchanged`。降级、同 revision 内容冲突、题型稳定 ID 冲突或 orphan 所有权冲突返回 409；坏格式返回 400；存在 open 会话返回 409 并附 `session_id/pending_ids`。升级保留题目整数主键、调度和历史评分快照，且不覆盖用户的 `include_in_review`；遗漏旧 ID 保持原状，只有显式 `retired` 停止新抽题。

`PUT /api/packs/:id` 精确接受 `{"include_in_review":true|false}`，只改变日常抽题/统计资格；关闭后仍可启动专题，进度不删除。题包 ID 不存在为 404。首版没有 HTTP 卸载、在线商店、自动下载或自动题包更新接口。

`.bagu-pack` 压缩文件最多 20 MiB、解压 JSON 合计最多 50 MiB、最多 10000 题，严格只含 `manifest.json`、`questions.json`、`experiences.json`。manifest 的 `schema_version` 当前为 1；题目类型为 `review|prepare`，均须 `review_status="reviewed"` 和完整来源，产物不接受未解析引用。

### 专题列表、启动与准备项

`GET /api/experiences` 返回 `{experiences:[...]}`。专题对象包含数据库 `id`、`stable_experience_id`、题包标识/名称、`kind`（`interview|topic_set`）、`direction/company/position/stage`、`section_count`、未退役 `question_count` 及 `recommended_section_id`。`GET /api/experiences/:id` 返回 `{experience,sections}`；章节按 `position` 排序，每项为 `{id,stable_section_id,position,title,recommended,question_count}`。

`POST /api/experiences/:id/start` 用 `{}` 启动整套，或用 `{"section_id":12}` 启动该专题下的一个章节。响应含 `session_id`、有序 `questions`、`session_type:"experience"`、`experience`，单章时另有 `section`。题目顺序由章节位置和题目位置写入 `session_items.position`；仅过滤 `retired`，不受 `include_in_review` 开关影响。未知专题/章节为 404，非法 ID/请求字段为 400。

`POST /api/session/complete` 精确接受：

```json
{
  "session_id": "s_20260830_a3f2c91b",
  "question_id": 42,
  "completion_type": "prepared"
}
```

`completion_type` 只允许 `prepared|skipped`，目标必须是该专题会话里的 `prepare` 项。成功返回 `{session_id,question_id,completion_type,replayed,status}`；同题同结果重放幂等，改用另一结果或把 review 题交给本接口会拒绝。它不创建 submission/grade，不调用模型，也不修改 `level/times_seen/times_right/next_due/last_reviewed`；末项完成时同事务关闭会话。

## 评分、看答案与自评

同步和流式评卷接受同一请求对象，例如：

```json
{
  "session_id": "s_20260826_a3f2c91b",
  "question_id": 12,
  "text": "这里是用户自己的回答",
  "submission_id": "sub_12345678-1234-4123-8123-123456789abc"
}
```

示例 ID 仅演示格式；实际使用抽题返回的 ID 和新生成的 submission ID。`text` 必须为非空作答字符串。成功结果对象如下，AI 评分响应本身不含 `next_due`：

```json
{
  "submission_id": "sub_12345678-1234-4123-8123-123456789abc",
  "grade": "good",
  "comment": "学习反馈",
  "full_answer": "保存的参考答案正文",
  "full_answer_html": "<p>保存的参考答案正文</p>",
  "answer_source": "stored"
}
```

`answer_source` 由服务端决定：题库答案为 `stored`，模型补充为 `model`，旧记录或无答案自评为 `null`。题库答案优先，包括 `easy` 在内均保存最终答案；后续修改题库不会替换已保存的历史答案。原始答案文本不能直接当 HTML 插入，使用服务端安全渲染字段。

`POST /api/reveal` 只允许查看当前会话未判题，返回 `{question_id, answer, answer_html, url}`；无题库答案时正文为空，不调用模型、不计分。

`POST /api/review` 的 `result` 为 `again` / `hard` / `good` / `easy`。它不调用模型，返回上述看答案字段，再加 `next_due` 和评分结果对象字段；自评无答案时仍可评分，`answer_source` 为 `null`。页面的“不会，直接看答案”使用 `again` 自评，不等于只调用 `/api/reveal`。

以上 `/api/answer`、流式 answer、`/api/reveal` 和 `/api/review` 只接受 `review` 题；`prepare` 在模型调用或调度写入前即拒绝，必须使用 `/api/session/complete`。题包 review 的 `answer_source` 仍是 `stored`；页面借助题目对象中的 `pack_id` 将它标为「题包参考答案 · 已复核」。submission 恢复只增加这个公开归属字段，不返回题包答案、准备提示、稳定 ID 或来源清单。

同步评卷未配置模型返回 400；模型请求失败、截断、拒答、空响应、解析失败或缺少必需模型答案返回 502。目标会话/题目非法、已评分、submission 格式错误返回 400。完整答案与结果 HTML 构造成功后才提交评分；失败不留下新评分、调度或 submission 记录。

### SSE 协议

`/api/answer/stream` 响应为 `text/event-stream; charset=utf-8`。每条消息是 `data: <JSON>` 加空行，事件种类在 JSON 的 `type` 字段中，**不是** SSE 的 `event:` 字段：

```text
data: {"type":"start"}

data: {"type":"delta","text":"GRADE: good\n"}

data: {"type":"done","result":{"submission_id":"sub_12345678-1234-4123-8123-123456789abc","grade":"good","comment":"学习反馈","full_answer":"答案正文","full_answer_html":"<p>答案正文</p>","answer_source":"stored"}}

```

失败消息形如 `data: {"type":"error","error":"原因"}`。`delta.text` 是模型原始协议文本片段，不能作为最终评分；只有 `done.result` 是已持久化结果。无内容、缺点评、协议畸形、无题库答案且模型未补答案等情况不会计分，也不会自动再次调用模型补答。

HTTP 请求体、认证或 Android 地址校验失败可能先返回普通 JSON 错误。开始 SSE 后 HTTP 状态已是 200，业务失败用 `error` 消息表达；甚至可能没有 `start` 就收到 `error`。所以 HTTP 200、收到片段或连接结束均不等于评分成功。若没有收到 `done`，保留草稿及 submission ID，先查询完成结果再决定重试。

### submission 幂等与恢复

- 可选 ID 采用 `sub_` 加标准 UUID（当前校验版本 1–5，推荐 UUID v4）；服务端去除两端空白并转小写。`null` / 空字符串等同未提供 ID。
- 同一 ID、会话和题目已成功评分后，再次提交只返回已保存结果，不再次调用模型或计分；SSE 重放直接返回 `start`、`done`，没有 `delta`。
- 不同 ID 对同一已评分题再次评分，或跨题/跨会话复用同一 ID，均失败。客户端应为一次作答生成 ID，并在网络重试时保留它。
- `GET /api/submissions/:id` 成功返回 `{submission_id, session_id, question, result}`；未找到已完成记录返回 404，格式错误返回 400。它不是运行中任务查询接口，404 不证明另一个请求永远不会完成。
- 首次 `grade`、点评、答案正文和来源是历史结果；查询中 `question` 的题干等元数据来自当前题库。自评重放的 `next_due` 同样是查询时的当前调度，不是单独保存的历史快照。
- 不传 ID 仍可评分，但无法通过此查询恢复响应；不能把重发无 ID 的请求当幂等操作。

服务端不保存用户作答正文；草稿由桌面浏览器或 Android 私有存储管理。并发和断流边界见 [架构与数据](architecture.md)。

## 题库管理与 CSV

新增和修改共用字段：

| 字段 | 类型与限制 |
| --- | --- |
| `category` | 必填字符串，去首尾空白后非空，最多 100 字符 |
| `question` | 必填字符串，去首尾空白后非空，最多 2000 字符 |
| `answer` | 可空字符串，最多 100000 字符 |
| `url` | 可空字符串，最多 2048 字符 |

`PUT` 是提交完整题目字段，不是局部补丁；省略的 `answer`、`url` 会成为空字符串。普通本地题按 `category + question` 唯一；新增/修改重复题返回 400。CSV 和 POST 始终创建本地 `review` 题，不接受题包身份或专题结构。

题目列表对象在原内容/进度字段外增加 `pack_id`、`pack_name`、`stable_question_id`、`question_type`、`preparation_prompt`、`answer_review_status`、`retired`、有序 `sources`。本地题的题包字段为空，审校状态为 `local`；题包题由包管理，通用 PUT/DELETE 在任何写入前返回 409，不能用这些入口改变归属或覆盖内容。题包答案/准备提示仍只以安全文本/Markdown 渲染，不直接信任 HTML。

查询参数：`q` 在题干、答案、分类中做文本搜索，`cat` 精确匹配分类；`page` 默认 1 且至少 1，`page_size` 默认 20，范围 1–100。响应为 `{items, total, page, page_size, pages, categories}`；题目按 ID 倒序，`categories` 是全题库分类列表。非法分页返回 400。

删除成功返回 `{"deleted":true,"id":12}`；已有任意会话引用的本地题返回 409，即使该会话已经关闭；题包题也返回 409 只读冲突。不存在的数字题目 ID 在修改/删除时返回 400。

CSV 请求为 `{"content":"category,question,answer,url\n..."}`，不是 multipart 文件上传。支持 UTF-8 BOM、双引号和字段内逗号；表头必须按顺序为 `category,question,answer,url`，也兼容旧表头 `category,question,url`。字段限制与单题相同，最多 2 MiB / 5000 题；整批通过校验才写入。重复题跳过、不覆盖，响应为 `{total, inserted, skipped}`；任一行非法或无可导入题目返回 400。

## 备份与恢复

`GET /api/backup/export?mode=questions` 导出纯题库，`?mode=progress` 导出含进度备份，省略 mode 默认 progress；空值、非法值或重复 mode 返回 400。成功返回 `application/zip` 二进制，由调用方保存为 `.bagu-backup`，不是 JSON/base64 响应。有 open 会话仍可导出，但会话本身不在档案中。

`POST /api/backup/inspect` 与 `/api/backup/restore` 都只接受 `{"archive_base64":"<完整 ZIP 的规范 base64>"}`。inspect 完整校验所有成员和题目后返回 `{schema_version,mode,question_count,local_question_count?,pack_question_count?,pack_count?,experience_count?,created_at,app_version}`；这些额外计数字段只在 v3 出现。它不执行合并或取得恢复资格的锁。restore 在写事务中重新检查 open 会话，成功返回 `{added,updated,total}`。

核心 `inspect_backup(data)` 和 Android 原生档案预览不访问数据库；但 HTTP inspect 沿用普通 API 的公共入口，处理前会调用 `get_conn`／`init_db`，缺失的数据库可能被创建、旧库可能被迁移。因此 HTTP 预览不是“绝对不写库”的数据库迁移预演，也不具备下方诊断接口的数据库故障隔离保证。

beta.5 新导出的备份为 schema v3，严格只含 `manifest.json`、`questions.json`、`packs.json`、`experiences.json`：

- `questions` 模式保存本地题、题包题/来源、题包 revision/hash 元数据、专题/章节顺序和 `include_in_review`，不保存调度；目标已有进度保留，新题从零开始。
- `progress` 在同一内容快照上保存 review 题调度，并按稳定身份覆盖目标进度；prepare 的调度固定为零/null。
- v1/v2 两成员档案继续按历史契约读取，v1 按 progress 处理；历史 beta.4 只认识这两个旧版本，不能读取 v3。

两种 v3 模式均为一个 SQLite 读快照，不含模型配置、Key、草稿、会话、submission、评分分析或历史答案快照。备份 schema 与 SQLite `user_version=3` 是两个独立版本号。

限制为 10000 题、压缩文件 20 MiB、全部 JSON 解压后合计 50 MiB。v3 还要求 DEFLATED，并校验固定字段、重复 JSON key、题包原始 canonical manifest 身份、来源 URL、稳定引用和累计结构；损坏、超限、非法 base64 等返回 400，错误消息受限且不回显任意题包来源正文。restore 遇到 open 会话时返回 409 和 `{error,session_id,pending_ids}`；inspect 不因有会话而拒绝预览。

本地题按 `category + question` 合并；题包题按 `pack_id + stable_question_id`，专题/章节按各自稳定 ID 合并。恢复会还原题包内容、结构和日常开关，不删除目标独有行；同 revision/同 manifest 幂等，高 revision 可升级，降级、同 revision 冲突、题型变化或 orphan 所有权冲突返回 409，整批回滚。已有会话/分析历史不改。恢复是覆盖性操作，先备份再执行；`.bagu-backup` 不能替代数据库升级前的完整 SQLite 备份。用户操作说明见 [用户指南](user-guide.md) 和 [Android Beta](android-beta.md)。

## 诊断接口

以下接口仅用于桌面页面，Android 对 `/api/diagnostics/*` 返回 404，改走受限原生桥接。接口在数据库连接之前分流，不读取题库、配置或密钥，不执行数据库初始化/迁移。

请求必须携带 `X-Bagu-Diagnostics: 1`；Host 必须为实际监听的 `127.0.0.1:端口`，存在 Origin 时必须精确匹配该 HTTP origin。不开放 CORS。普通 HTTP 和 SSE 响应提供 `X-Bagu-Request-Id`，可用于关联错误与日志。

| 接口 | 请求与结果 |
| --- | --- |
| `GET /api/diagnostics/export` | 返回 `application/zip`，带 `Content-Disposition` 下载文件名及 `Cache-Control: no-store`；每来源最多 2 MiB，ZIP 最多 8 MiB |
| `POST /api/diagnostics/events` | `application/json` 对象 `{"events":[{"event":"web.error","error_type":"TypeError","line":42}]}`；返回 `{accepted,dropped}` |

事件请求最多 32 KiB、每批最多 20 条、单事件最多 2 KiB；每分钟最多接收 120 条，超限或非法单条计入 dropped。请求体超限返回 413，批次格式错误返回 400，访问校验失败返回 403。仅接收已定义的 `web.*` 事件及白名单字段，自由文本不会被保存。

ZIP 固定包含 `manifest.json`、`server.jsonl`、`web.jsonl`、`native.jsonl`、`README.txt`；不可用来源为空并在 manifest 标记。历史日志同样重新过滤，不提供原始目录或任意文件下载。格式版本为 1，与 SQLite/题库备份版本无关。

## Android 更新状态与诊断

沿用受限桥接 `getUpdateState()`、`installUpdate(candidateId, operationId)`、`cancelUpdate(operationId)` 与 `bagu-update` 事件，不新增下载 URL、路径或日志读取方法。既有 `operationId` 用于请求与过期回调控制，`revision` 用于状态顺序；它们不等于供反馈的诊断编号。`ready`、`installerLease` 和 `recovery` 继续保留；系统 PackageInstaller session ID、安装器包名、APK 路径及原始错误正文不进入状态 JSON。旧字段保留，新增 `lastCheck`：

| 字段 | 含义 |
| --- | --- |
| `diagnosticId` | 真正接受检查后生成的 `n_` 加 32 位小写十六进制；未知为 null，两通道共用 |
| `startedAt` / `completedAt` | 毫秒时间戳，未完成为 0；中断保留开始时间、完成时间为 0 |
| `status` | `unknown`、`checking`、`latest`、`available`、`partial-error`、`error`、`interrupted` |
| `errorCode` | 总体固定错误码，无错误为 0 |
| `channels` | 以 `beta`／`stable` 为键的对象；stable 安装只包含 stable，未知摘要为空对象 |
| `channels.<通道>.status` | `pending`、`checking`、`empty`、`available`、`no-update`、`incompatible`、`error`、`interrupted`、`not-checked` |
| `channels.<通道>.errorCode` | 通道错误码，无错误为 0 |
| `channels.<通道>.httpStatus` | 已获知的实际 HTTP 状态，未获知或该阶段不适用为 null，不用应用错误码冒充 |
| `channels.<通道>.durationMs` | 该通道的耗时，非负毫秒 |

`latest` 仅表示所有应检查通道均成功且“当前没有兼容的新版本”；空通道 `empty` 也是成功。部分失败可同时保留旧字段中的已验证候选，因此页面必须分别呈现当前下载／安装状态和 `lastCheck`，不能用某个通道成功推断不存在更高版本。

最近摘要以单条、最多 4096 UTF-8 字节持久化，不含 URL、清单正文、更新说明或异常消息。开始检查先记 `checking`；进程重启把残留 checking 恢复为 interrupted，不重放旧请求。摘要缺失／非法按 unknown，写入失败仅降级为内存状态，不放宽安装交接的持久化要求。无新字段的旧宿主继续显示原有消息。

错误码在发生位置按异常类型或校验边界生成，不匹配异常消息：

| 类别 | 固定错误码 |
| --- | --- |
| HTTP / DNS / 超时 / TLS / 其他连接 | 1001 / 1002 / 1003 / 1004 / 1005 |
| JSON 或 UTF-8 / 清单校验 / 大小超限 / 重定向拒绝 | 1101 / 1102 / 1103 / 1104 |
| 本地存储 / 长度 / SHA-256 / APK 身份、签名或兼容性 | 1201 / 1202 / 1203 / 1204 |
| 安装来源权限或设备策略 / 安装器协议或超时 / 开发者验证或高级保护 | 1301 / 1302 / 1303 |
| 未分类错误 | 1999 |

主动取消下载或系统安装确认是 `cancelled` 结果，不生成网络错误；安装取消会废弃对应 session、解除租约并保留校验缓存。无效、冲突或不兼容 APK 统一为 1204，session 写入／空间失败为 1201，策略阻止为 1301；Android 16.1+ 的开发者验证 extra 为 1303。操作只有被接受才分配新诊断编号，取消沿用原编号；节流跳过、忙时拒绝、重复或错误 session 回调不清除已有失败编号。工作线程与回调捕获所属操作上下文，过期结果不改变新操作的编号或状态。

`AndroidDiagnostics` 接收不可变 `UpdateDiagnostic`，通过已有 `native.update` 写入 `operation_id`（诊断编号）、`stage`、`outcome`、可选 `channel`、`error_code`、可选 `status`（HTTP）及 `duration_ms`。白名单包含安装确认专用的 `confirm` 阶段，用 `started/ok/error` 区分确认回调到达、启动请求成功或失败；不记录确认 Intent、路径、Session ID 或系统错误正文。只对 native.update 开放通道与结果白名单，落盘和导出使用同一过滤器；不记录每块下载进度或任意异常消息，日志失败不改变更新结果。自动检查失败只在设置页显示，网页不重复上报原生错误；用户可通过原有诊断导出入口反馈。

## 模型配置

新建、测试和修改的配置字段为 `provider`、`model`、`base_url`、`api_key`，新建/修改另可带 `name`。提交完整连接配置，不要把 `PUT` 当作仅修改名称的补丁；空名称会使用默认显示名。供应商预设从 `GET /api/models` 的 `presets` 获取，只是代码预填值，不保证服务商实际可用性。

公共模型对象为 `{id, name, provider, model, base_url, api_key_masked, configured}`，不会返回明文 Key：

| 操作 | 成功响应与副作用 |
| --- | --- |
| 列表 | `{active_id, models, presets}` |
| 新建 | 新模型公共对象，并自动设为 active |
| 测试 | `{"ok":true}`；测试草稿，若带已有 `id` 且 Key 为空则借用该条目 Key，不保存草稿 |
| 修改 | 修改后的公共对象；空/省略 Key 沿用旧 Key，保持当前 active 选择 |
| 激活 | 完整模型列表响应，切换 `active_id` |
| 复制 | 新公共对象，名称附加“副本”；复制 Key，不切换 active |
| 删除 | 完整模型列表响应；删除 active 时选剩余第一条，无剩余时 `active_id` 为空字符串 |

新建、修改和测试会读取完整流式响应，不能只收到首个片段就成功；未配置 Key、连接或响应验证失败返回 502，失败不保存新配置。修改/激活/复制/删除的模型不存在返回 400。激活和复制不做网络测试。

桌面连接配置保存在 `settings.json` 的 `{active_id, models:[{id,name,provider,model,base_url}]}`，Key 只放 `.env` 的 `BAGU_KEY_<id>`；Android 使用应用私有配置目录。旧单模型配置首次读取会迁移到多模型格式，因此旧配置的首次 GET 不应被当作完全无写入的迁移预演。不要把真实 Key 放入源码、日志、文档或聊天。

Android 仅接受有效 HTTPS 模型地址且禁止 URL 用户名/密码；不符合时返回 400，激活/复制已有条目也校验。桌面保留本机 HTTP 模型支持。同步和流式请求共用构造规则，默认不发送 `temperature`；截断、拒答、空输出和不完整流均不作为成功评分。`GET /api/settings` 只返回 active 的 `provider`、`model`、`base_url`、`api_key_masked`、`configured` 和 `presets`，不再提供写入入口。
