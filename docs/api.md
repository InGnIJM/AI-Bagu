# HTTP API 参考

[文档导航](README.md) · [CLI](cli.md) · [用户指南](user-guide.md) · [架构与数据](architecture.md)

本文基础 HTTP 说明沿用源码基线 `71fbbfd`；诊断和原生更新章节另行说明当前源码扩展，新的双模式备份契约见[数据迁移与更新](data-transfer-and-updates.md)。桌面默认地址为 `http://127.0.0.1:8765`，可用 `python bagu.py serve --port 8765` 启动；Android 使用独立私有数据库和随机本机端口。这不是账号服务或公网 API。

## 通用约定与安全

- 除页面、允许的字体/图片、ZIP 和 SSE 外，响应为 JSON。已处理的业务错误通常为 `{"error":"原因"}`；客户端仍需处理网络中断和非 JSON 异常响应。
- POST / PUT 发送 UTF-8 JSON 对象，推荐 `Content-Type: application/json`；空请求体按 `{}` 处理。数组、非法 JSON 返回 400；请求体超过 32 MiB 返回 413。CSV 和备份另有更严格上限。
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
| GET | `/api/questions` | `q`、`cat`、`page`、`page_size` 分页查询 |
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
| GET | `/api/backup/export` | ZIP 字节，保存为 `.bagu-backup` |
| POST | `/api/backup/restore` | `{archive_base64}`，校验后合并题目与进度 |
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

`GET /api/stats` 返回 `total`、`due`、`mastered`、`by_cat`、`open_session_id`。`due` 包含到期题和新题，`mastered` 为 `level >= 3`；`by_cat` 每项为 `{category, total, seen, due_n}`，其中 `seen` 是复习过的题数。

`POST /api/draw` 的 `n` 建议传正整数，省略时为 5；`cat` 按完整分类名过滤，省略/空值表示全部分类。响应为 `{session_id, questions}`；没有可选题时返回 `{"session_id":null,"questions":[]}`，不会创建会话。

`GET /api/session` 有会话时返回 `{session_id, n, cat, items, pending}`，`pending` 是未判题对象数组，不是 ID 数组。没有会话时为 `{"session_id":null,"items":[],"pending":[]}`。这里的题目对象与抽题响应一致：`id`、`category`、`question`、`url`、`times_seen`、`grade`；未评分的 `grade` 为 `null`，不包含答案正文。

已有 open 会话时，`draw` 返回 409 和 `{error, session_id, pending_ids}`，不另开一轮。所有本轮题评分成功后自动关闭；`POST /api/skip` 返回 `{session_id, status:"closed"}`，只关闭会话，不改未判题调度，也不撤销已完成评分。无可关闭会话返回 400。

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

`PUT` 是提交完整题目字段，不是局部补丁；省略的 `answer`、`url` 会成为空字符串。`category + question` 唯一；新增/修改重复题返回 400。返回题目对象包含上述四字段及 `id`、`answer_html`、`level`、`times_seen`、`times_right`、`next_due`、`last_reviewed`。修改仅更改题目内容，不重置调度。

查询参数：`q` 在题干、答案、分类、URL 中做文本搜索，`cat` 精确匹配分类；`page` 默认 1 且至少 1，`page_size` 默认 20，范围 1–100。响应为 `{items, total, page, page_size, pages, categories}`；题目按 ID 倒序，`categories` 是全题库分类列表。非法分页返回 400。

删除成功返回 `{"deleted":true,"id":12}`；已有任意会话引用的题目返回 409，即使该会话已经关闭。不存在的数字题目 ID 在修改/删除时返回 400。

CSV 请求为 `{"content":"category,question,answer,url\n..."}`，不是 multipart 文件上传。支持 UTF-8 BOM、双引号和字段内逗号；表头必须按顺序为 `category,question,answer,url`，也兼容旧表头 `category,question,url`。字段限制与单题相同，最多 2 MiB / 5000 题；整批通过校验才写入。重复题跳过、不覆盖，响应为 `{total, inserted, skipped}`；任一行非法或无可导入题目返回 400。

## 备份与恢复

`GET /api/backup/export` 返回 `application/zip` 的二进制内容，由调用方保存为 `.bagu-backup`；不是 JSON/base64 响应。有 open 会话仍可导出，但会话本身不在档案中。

`POST /api/backup/restore` 接受 `{"archive_base64":"<完整 ZIP 的 base64>"}`，成功返回 `{added, updated, total}`。档案仅含 `manifest.json` 和 `questions.json`，保存题干、答案、来源及复习进度，不含模型配置、Key、草稿、会话和评分分析。

限制为 10000 题、压缩文件 20 MiB、两份 JSON 解压后合计 50 MiB。校验成员名、重复项、字段和 SHA-256；损坏、超限、非法 base64 等返回 400，整批不写入。有 open 会话时返回 409 和 `{error, session_id, pending_ids}`。

恢复按 `category + question` 合并：新题新增，已有题的答案、URL 和进度被备份覆盖，不删除其他题，也不修改已有会话/分析历史。恢复是覆盖性操作，先备份再执行；`.bagu-backup` 不能替代数据库升级前的完整 SQLite 备份。用户操作说明见 [用户指南](user-guide.md) 和 [Android Beta](android-beta.md)。

## 诊断接口（当前工作区新增）

以下接口仅用于桌面页面，Android 对 `/api/diagnostics/*` 返回 404，改走受限原生桥接。接口在数据库连接之前分流，不读取题库、配置或密钥，不执行数据库初始化/迁移。

请求必须携带 `X-Bagu-Diagnostics: 1`；Host 必须为实际监听的 `127.0.0.1:端口`，存在 Origin 时必须精确匹配该 HTTP origin。不开放 CORS。普通 HTTP 和 SSE 响应提供 `X-Bagu-Request-Id`，可用于关联错误与日志。

| 接口 | 请求与结果 |
| --- | --- |
| `GET /api/diagnostics/export` | 返回 `application/zip`，带 `Content-Disposition` 下载文件名及 `Cache-Control: no-store`；每来源最多 2 MiB，ZIP 最多 8 MiB |
| `POST /api/diagnostics/events` | `application/json` 对象 `{"events":[{"event":"web.error","error_type":"TypeError","line":42}]}`；返回 `{accepted,dropped}` |

事件请求最多 32 KiB、每批最多 20 条、单事件最多 2 KiB；每分钟最多接收 120 条，超限或非法单条计入 dropped。请求体超限返回 413，批次格式错误返回 400，访问校验失败返回 403。仅接收已定义的 `web.*` 事件及白名单字段，自由文本不会被保存。

ZIP 固定包含 `manifest.json`、`server.jsonl`、`web.jsonl`、`native.jsonl`、`README.txt`；不可用来源为空并在 manifest 标记。历史日志同样重新过滤，不提供原始目录或任意文件下载。格式版本为 1，与 SQLite/题库备份版本无关。

## Android 更新状态与诊断（当前源码扩展）

沿用受限桥接 `getUpdateState()` 与 `bagu-update` 事件，不新增下载 URL、路径或日志读取方法。既有 `operationId` 用于请求与过期回调控制，`revision` 用于状态顺序；它们不等于供反馈的诊断编号。旧字段保留，新增 `lastCheck`：

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
| 安装来源权限 / 安装器启动 | 1301 / 1302 |
| 未分类错误 | 1999 |

主动取消是 `cancelled` 结果，不生成网络错误。操作只有被接受才分配新诊断编号，取消沿用原编号；节流跳过、忙时拒绝或重复点击不清除已有失败编号。工作线程与回调捕获所属操作上下文，过期结果不改变新操作的编号或状态。

`AndroidDiagnostics` 接收不可变 `UpdateDiagnostic`，通过已有 `native.update` 写入 `operation_id`（诊断编号）、`stage`、`outcome`、可选 `channel`、`error_code`、可选 `status`（HTTP）及 `duration_ms`。只对 native.update 开放通道与结果白名单，落盘和导出使用同一过滤器；不记录每块下载进度或任意异常消息，日志失败不改变更新结果。自动检查失败只在设置页显示，网页不重复上报原生错误；用户可通过原有诊断导出入口反馈。

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
