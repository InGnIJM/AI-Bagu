# 八股助手（八股抽问）

本地面试复习工具：题库管理、间隔复习、背题自评与 AI 评卷。桌面 CLI、网页和 Android Beta 复用 `bagu.py` 核心及 SQLite 存储。

桌面的 Hermes/CLI 与网页共用同一份题库和**会话锁**；Android 使用应用私有目录中的独立题库、配置和进度，不自动同步电脑。HTTP 只监听 `127.0.0.1`，不做账号或公网部署。

> 截至 2026-08-28，源码基线 `997fe91` 已合入 `main`，包含“答案渲染异常不计分、错误链接按安全文本显示”的修复。现有 `0.1.0-beta.1` APK 尚未重新构建，不包含该修复。构建与验收边界见 [Android Beta 文档](docs/android-beta.md)。

## 功能

- 从 [小林 coding 面试专题](https://xiaolincoding.com/) 抓题入库（MySQL / Redis / 网络 / OS / MQ / 并发 / 系统设计 / CAP）
- 到期复习优先，不足再补新题
- 每份数据库最多 1 条进行中会话；同一会话同一题只认第一次评分
- CLI：`draw` / `grade` / `skip` / `stats`
- 本机网页：答题/背题两种模式、流式模型评卷、多模型配置库
- 草稿持久保存，评分响应丢失后可按 submission ID 恢复，不重复计分
- 题库管理：搜索、分类筛选、新增、修改、删除未使用题目、CSV 批量导入
- Hermes 聊天：由 Hermes 自己的 AI 评卷，再调用本仓库 CLI 落库
- Android Beta：内嵌 Python 与 WebView，无需电脑常驻；支持原生文件选择及 `.bagu-backup` 备份恢复
- 本地打包字体与图标；查看已保存的答案和自评复习无需模型服务

## 环境

| 使用方式 | 所需环境 |
| --- | --- |
| 桌面 CLI / 网页 | Python；当前验证使用 Python 3.11.7，核心仅依赖标准库 |
| 核心及网页回归测试 | Python、pytest、Node.js（测试会执行网页 JavaScript） |
| Android 应用使用 | Android 10+、arm64-v8a；手机无需自行安装 Python |
| Android 本地构建/完整项目测试 | 另需 JDK 17、Android SDK、Gradle 与 Chaquopy 相关缓存，详见 [构建前置条件](docs/android-beta.md#前置条件) |

抓取公开题库、AI 评卷和远程图片加载需要网络；模型预设不是内置服务，需要自行配置可用地址和 Key。Android 的原生宿主和构建插件并非 Python 标准库的一部分。

## 快速开始

在本目录执行：

```bash
python bagu.py init
python bagu.py import          # 抓取题目和对应正文，无损补全已有库
python bagu.py stats
python bagu.py serve           # 启动后在浏览器打开 http://127.0.0.1:8765
```

网页评卷前，点击侧栏「模型配置库」或作答页当前模型条，新建或选用一条可用配置。背题、自评和「不会，直接看答案」无需配置模型。

不想抓取公开题库时，可以只执行 `init` 和 `serve`，再从「题库管理」新增题目或导入 CSV。

## Android Beta（本地构建）

内部 Beta 首次安装自带 408 道清洁种子题；已有应用数据不会被种子覆盖。`public` 构建使用空种子，仅用于验证，不代表已公开发布。

应用底部有「练习 / 题库 / 概览 / 设置」。草稿存入原生私有存储，备份和 CSV 操作使用系统文件选择器；AI 评卷仅允许 HTTPS 模型地址。

完整的构建、校验、安装、更新和迁移说明见 [docs/android-beta.md](docs/android-beta.md)。构建产物位于被 Git 忽略的 `dist/android/`，不会随源码提交，也不会自动发布到 GitHub 或应用商店。

## 命令

| 命令 | 作用 |
| --- | --- |
| `python bagu.py init` | 初始化 SQLite（`bagu.db`） |
| `python bagu.py import` | 按章节抓取题目与答案；已有题更新正文/锚点且保留复习进度 |
| `python bagu.py import --code-only` | 自动备份后，仅恢复旧答案中与来源匹配的代码块和缩进；不新增题目、不替换正文、不改复习记录 |
| `python bagu.py stats` | 总题 / 今日到期 / 已掌握 / 分类进度 |
| `python bagu.py list` | 列出全部题目 |
| `python bagu.py draw -n 5 [--cat MySQL]` | 开一轮会话并打印题目 |
| `python bagu.py grade <session_id> <题id> <again\|hard\|good\|easy>` | 对本轮一题打分 |
| `python bagu.py skip [session_id]` | 结束本轮，未判题不改调度 |
| `python bagu.py serve [--port 8765]` | 本机网页，默认 `http://127.0.0.1:8765` |

评分含义：`again` = 不会，`hard` = 勉强，`good` = 会了，`easy` = 秒答。

## 会话规则（必须遵守）

1. 每次 `draw` 开一轮，打印 `session: s_YYYYMMDD_xxxxxxxx`。
2. 有未关闭会话时**禁止再 draw**。先把题判完，或 `skip` 结束本轮。
3. `grade` 必须带本轮 `session_id`。同一题只认**第一次**，重复调用失败且不改库。
4. `skip` 只关会话，未判的题仍留在池里（不改 `next_due` / `level`）。
5. 全部题判完后会话自动 `closed`。
6. 旧写法 `grade <id> <result>`（不带 session）已废除。
7. 网页和 Hermes 抢同一把锁：任一方持有 open 会话，另一方 `draw` 失败。
8. 会话锁和评分使用 SQLite 原子事务；并发请求也只允许一条 open、同题只计分一次。
9. 网页评分及答案 HTML 构造成功后才提交事务；渲染异常会回滚评分、调度和 submission，可以重试。
10. 同一 submission ID 的重试返回原判定，不再次计分；不同 ID 重复评分、跨题复用 ID 都会失败。

`session_id` 格式：`s_` + 日期 `YYYYMMDD` + `_` + 8 位小写十六进制，例如 `s_20260826_a3f2c91b`。

## 调度（简化 SM-2）

| 评级 | 基础间隔（天） |
| --- | --- |
| again | 重置 level=0，间隔 1 天 |
| hard | 1 × 等级倍率 |
| good | 3 × 倍率 |
| easy | 7 × 倍率 |

`again` 将 `level` 重置为 0；`hard`、`good`、`easy` 均将等级升一级（最多 3），按**升级后的等级**取倍率：level 1/2/3 对应 1/2/4。只有 `good`/`easy` 增加 `times_right`，因此等级并不等同于连续答对次数。`level >= 3` 视为已掌握。

抽题：`next_due IS NULL`（新题）或 `next_due <= 今天`；到期复习优先。

## 本地网页

```bash
python bagu.py serve --port 8765
```

打开 [本机网页](http://127.0.0.1:8765)（只监听本机）。桌面采用 Arcade Bento 布局，通过侧栏进入练习、题库管理和模型配置。

- 空闲：可选「答题模式」或「背题模式」后抽 5 题；点分类名会沿用当前模式
- 作答：用自己的话描述 → 显示分析动画与耗时 → 流式展示评判内容 → 自动 `grade`
- 不会：点「不会，直接看答案」，立即展示题库答案并按 `again` 记分，不调用模型
- 背题：每题直接展示题库答案，看完后自行选择 `again / hard / good / easy`
- AI 评卷结果仅在非 `easy` 时展开完整答案；背题模式本身会直接展示题库答案
- 模型失败、断流或结果解析失败都不落库，可保留草稿重试
- 草稿保存在本机浏览器 `localStorage`；关闭标签页或重启浏览器后仍可恢复
- 网页提交带 submission ID；若评分已成功但响应丢失，刷新后会恢复原判定、点评和完整答案
- 清除浏览器站点数据会丢失本地草稿；更换主机名或端口也不会自动迁移浏览器存储
- 「结束本轮 skip」关闭会话

## 题库管理与 CSV 导入

旧版本抓取的答案可能没有代码围栏，导致 SQL、Java 等代码显示为普通正文。
可执行 `python bagu.py import --code-only`：先在数据库旁创建
`*.before-code-format-*.sqlite3` 备份，再从公开来源恢复能逐行匹配的代码块格式。
已有围栏、内容有改动或匹配数量不一致的答案/片段会保持原样；不会更新历史评卷文本。
修复后刷新网页并重新打开答案即可；此命令不需要模型 Key，也不调用模型。

桌面从侧栏「题库管理」进入管理页，Android 从底部「题库」进入。支持按题目、答案、分类或 URL 搜索，可直接展开答案正文；答案支持标题、粗体、链接、图片、列表、引用、代码块和表格等常用 Markdown，并会经过安全转义。无法解析的链接显示为普通文本，不执行题库 HTML。为保留复习历史，已经进入过会话的题目不能删除；修改题目不会重置复习进度。

批量导入文件必须是 UTF-8 CSV，固定表头如下：

```csv
category,question,answer,url
MySQL,什么是事务？,事务是一组不可分割的数据库操作,https://example.com/mysql#transaction
Redis,什么是缓存穿透？,,
```

- `category`：分类，必填，最多 100 个字符
- `question`：题目，必填，最多 2000 个字符
- `answer`：参考答案正文，可空，最多 100000 个字符
- `url`：来源链接，可空，最多 2048 个字符
- 仍兼容旧表头 `category,question,url`，导入后答案留空
- 支持带 BOM、双引号和字段内逗号
- 单文件不超过 2 MiB，一次最多 5000 道题
- 先校验整份文件；任一行错误则整批不写入
- 相同 `category + question` 视为重复并跳过，不覆盖已有题目

管理页可直接下载 CSV 模板。

## 备份与恢复

`.bagu-backup` 是包含 `manifest.json` 和 `questions.json` 的 ZIP 档案：

- 包含题目、答案、来源 URL 和复习进度；**不包含评分分析、模型配置、API Key、草稿或会话历史**。
- 恢复按 `category + question` 合并：已有题的答案、URL 和进度会被备份覆盖，新题新增，不删除备份外的题目。
- 有进行中的会话时禁止恢复；导出不受此限制，但不会导出该会话。
- 最多 10000 题、压缩文件 20 MiB、两个 JSON 解压后合计 50 MiB。损坏、超限或校验失败时整批拒绝，不部分写入。
- Android 在「设置」中导出/恢复；桌面已提供对应 HTTP API，当前桌面界面没有备份按钮。CSV 导入仅导入题目，不迁移进度。

恢复会覆盖同名题的相关数据，操作前先备份。跨卸载迁移无法找回被排除的配置、草稿或评分分析，详情见 [安装、更新与备份](docs/android-beta.md#安装更新与备份)。

## 模型配置（桌面网页 / Android 评卷）

作答页顶部展示当前评卷模型（显示名、模型 ID、Base URL）。点进去进入配置库：点卡片切换当前模型；可新建、修改、复制、删除。同一厂商可存多条，每条自己的 Base URL 和 Key。

桌面 Key 写入项目 `.env` 的 `BAGU_KEY_<模型id>`，模型列表在 `settings.json`；Android 使用应用私有 `config/` 下同名文件，不读取电脑配置。新建和修改会先完整读取一次流式测试响应，通过后才写盘；激活或复制已有条目不重新测试。作答失败时输入框草稿会保留。

旧版单模型配置会在首次读取时自动迁移：`settings.json` 的 `{provider, model, base_url}` 会变成一条模型记录，`.env` 中的 `BAGU_API_KEY` 会改写为对应的 `BAGU_KEY_<模型id>`。迁移完成后不再使用旧键。

下拉预设（以下仅描述代码中的预填值，可修改，不保证供应商当前仍提供对应模型）：

| 供应商 | 默认 Base URL | 默认模型 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openrouter/auto` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 智谱 GLM / z.ai | `https://api.z.ai/api/paas/v4` | `glm-4-flash` |
| Kimi | `https://api.moonshot.cn/v1` | `kimi-k2-turbo-preview` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` |
| Ollama（本地） | `http://127.0.0.1:11434/v1` | `llama3.1` |
| 自定义 | 自填 | 自填 |

Android 拒绝 HTTP 模型地址，因此不能直接使用表中的默认 Ollama 地址。桌面保留本机 HTTP 模型支持。同步与流式请求默认均不指定 `temperature`；截断、拒答、空内容或不完整流式响应不会作为成功评分落库。

## Hermes 聊天

你用自己的话作答 → **Hermes 自己的 AI** 分析并给出 `again|hard|good|easy` → 再 `grade` 一次。不是 easy 时对照题目 URL 讲完整答案。一次只发一题。

Hermes 路径**不**调用本仓库配置的 LLM。禁止：不带 session 的 grade、同一题再 grade、用户未作答就自选评级、本轮未结束再 draw、把 Nous token 写入本项目。

## HTTP API（仅本机）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/`、`/index.html` | 单页 `web/index.html` |
| GET | `/assets/...` | 仅提供允许列表内的本地字体与品牌图标，不是任意文件服务 |
| GET | `/api/stats` | 看板；含 `open_session_id` |
| GET | `/api/session` | 当前 open 会话及题目 |
| GET | `/api/questions` | 分页查询；支持 `q`、`cat`、`page`、`page_size` |
| POST | `/api/questions` | 新增题目 |
| PUT | `/api/questions/:id` | 修改题目，不重置复习进度 |
| DELETE | `/api/questions/:id` | 删除未进入过会话的题目 |
| POST | `/api/questions/import` | `{content}`；解析并导入 UTF-8 CSV 文本 |
| POST | `/api/draw` | `{n, cat?}`；已有会话返回 409 |
| POST | `/api/answer` | `{session_id, question_id, text, submission_id?}` → 调模型 → grade |
| POST | `/api/answer/stream` | 同上；SSE 推送 `start` / `delta` / `done` / `error`，完整生成、解析和渲染成功后才提交评分 |
| POST | `/api/reveal` | `{session_id, question_id}`；只查看题库答案，不评分 |
| POST | `/api/review` | `{session_id, question_id, result, submission_id?}` → 自评并持久化题库答案 |
| GET | `/api/submissions/:id` | 查询已完成 submission；会话关闭后仍可恢复结果 |
| GET | `/api/backup/export` | 返回 ZIP 字节，保存为 `.bagu-backup` |
| POST | `/api/backup/restore` | `{archive_base64}`；返回 `{added, updated, total}`，有 open 会话时 409 |
| POST | `/api/skip` | 关闭本轮 |
| GET | `/api/models` | 模型列表 + `active_id` + 掩码 Key + 预设 |
| POST | `/api/models` | 新建（服务端先测再写） |
| POST | `/api/models/test` | 测试草稿配置的完整流式响应，不写盘 |
| PUT | `/api/models/:id` | 修改（先测再写；Key 空则沿用） |
| POST | `/api/models/:id/activate` | 设为当前评卷模型 |
| POST | `/api/models/:id/copy` | 复制，不改当前项 |
| DELETE | `/api/models/:id` | 删除 |
| GET | `/api/settings` | 兼容：当前 active 的字段 + 掩码 Key |

旧写接口 `POST /api/settings`、`POST /api/settings/test`、`POST /api/settings/import-hermes` 已停用，统一返回 404；模型连接测试改用 `POST /api/models/test`。

除 HTML、字体/图标、ZIP 和 SSE 外，接口使用 JSON；POST/PUT 请求体必须是 JSON 对象，最多 32 MiB（CSV 和备份还有各自更严格的上限）。Android 的本机 HTTP 服务使用每进程生成的访问令牌，API 请求携带 `X-Bagu-Token`；令牌不应复制到文档或日志。

## 仓库结构

```
bagu.py                      # CLI、HTTP、抽题/评分/评卷、备份及配置
web/index.html               # 桌面与 Android 共用的唯一页面
android/                     # Android 宿主、原生桥接与仪器测试
assets/fonts/                # 离线字体及许可证
assets/branding/             # 应用品牌图标
scripts/android.ps1          # 本地签名准备、构建与交付物校验
scripts/build_android_seed.py # 从只读题库生成清洁种子，或生成空种子
scripts/verify_android_apk.py # APK 内容与原生库校验
test/test_bagu.py             # 核心、HTTP、网页行为回归
test/test_android_project.py  # Android 项目、桥接和打包契约回归
docs/android-beta.md          # Android 构建、迁移与验收边界
docs/superpowers/             # 历史设计、实现计划及协议补充
AGENTS.md                    # 当前项目协作规则；AGENT.md 仅作指向
```

本地生成、不要提交：`.env`、`settings.json`、`bagu.db`、`.signing/`、Android SDK/Gradle 缓存和 `dist/`。桌面服务日志默认写入 `.superpowers/bagu-server.log`，Android 写入私有 `logs/`；日志也不属于源码。

## 测试

安装 pytest，并确保 `node` 可从命令行调用：

```bash
python -m pip install pytest
python -m pytest test/test_bagu.py -q
```

Android 工具链及缓存就绪后，运行完整回归：

```bash
python -m pytest test/test_bagu.py test/test_android_project.py -q
```

完整项目测试包含 Windows PowerShell、JDK 和离线 Gradle 检查，不是只安装 pytest 就能在任意环境运行。测试使用临时数据库、隔离的临时签名材料及模拟网络；不会写真实 `bagu.db`，也不调用真实模型。Android 的 Java 单元测试、lint、APK 校验与模拟器验收是另外的检查，不由上述 pytest 命令全部覆盖。

2026-08-28，源码基线 `997fe91` 的完整回归为 **328 项通过**；另行编译执行的 `HostPolicyTest` **6 项通过**。这是源码测试记录，不代表现有 APK 已更新或真机验收完成。

协议与设计入口：[会话恢复与并发保护](docs/superpowers/specs/2026-08-27-session-fault-recovery-design.md)、[多模型配置库](docs/superpowers/specs/2026-08-26-model-profiles-design.md)、[Android Beta 设计](docs/superpowers/specs/2026-08-27-android-beta-design.md)。早期设计中的已替代接口，以当前代码和后续补充为准。

## 安全

- API Key 只放桌面或 Android 私有配置目录中的 `.env`，不得提交
- 接口不回明文 Key，只回掩码
- 服务只监听 `127.0.0.1`
- Android 仅加载受控本机页面；模型地址必须是 HTTPS，原生桥接限制存储键与文件大小
- 畸形链接按转义文本显示；无法完成的答案渲染不会提交新评分
- `.bagu-backup` 含题目正文与学习进度，请自行妥善保管
- 不要把 `.env`、`bagu.db`、`settings.json` 拷进聊天、截图或公开仓库
- 若 Key 曾离开本机，去对应供应商控制台轮换
