# 八股抽问

本地刷面试八股：从小林 coding 抓题，SQLite + 简化 SM-2 复习。Hermes 聊天和本机网页共用同一套抽题、打分和**会话锁**。

无第三方运行时依赖（标准库即可）。HTTP 只绑 `127.0.0.1`，不做账号、不做公网部署。

## 功能

- 从 [小林 coding 面试专题](https://xiaolincoding.com/) 抓题入库（MySQL / Redis / 网络 / OS / MQ / 并发 / 系统设计 / CAP）
- 到期复习优先，不足再补新题
- 全局最多 1 条进行中会话；同一题只认第一次评分
- CLI：`draw` / `grade` / `skip` / `stats`
- 本机网页：作答 + OpenAI 兼容模型评卷
- 题库管理：搜索、分类筛选、新增、修改、删除未使用题目、CSV 批量导入
- Hermes 聊天：由 Hermes 自己的 AI 评卷，再调用本仓库 CLI 落库

## 环境

- Python 3（建议 3.9+）
- 运行时无 pip 依赖
- 跑测试需要：`pip install pytest`

## 快速开始

在本目录执行：

```bash
python bagu.py init
python bagu.py import          # 抓取题目和对应正文，无损补全已有库
python bagu.py stats
python bagu.py serve           # 打开 http://127.0.0.1:8765
```

网页评卷前，先点击作答页顶部的当前模型条进入配置库，新建或选用一条可用配置。

## 命令

| 命令 | 作用 |
| --- | --- |
| `python bagu.py init` | 初始化 SQLite（`bagu.db`） |
| `python bagu.py import` | 按章节抓取题目与答案；已有题更新正文/锚点且保留复习进度 |
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

`session_id` 格式：`s_` + 日期 `YYYYMMDD` + `_` + 8 位小写十六进制，例如 `s_20260826_a3f2c91b`。

## 调度（简化 SM-2）

| 评级 | 基础间隔（天） |
| --- | --- |
| again | 重置 level=0，间隔 1 天 |
| hard | 1 × 连续答对倍率 |
| good | 3 × 倍率 |
| easy | 7 × 倍率 |

`level` 0–3，倍率 `1 / 1 / 2 / 4`。`good`/`easy` 计 `times_right`。`level >= 3` 视为已掌握。

抽题：`next_due IS NULL`（新题）或 `next_due <= 今天`；到期复习优先。

## 本地网页

```bash
python bagu.py serve --port 8765
```

打开 http://127.0.0.1:8765 （只监听本机）。

- 空闲：抽 5 题；点分类名 = `draw --cat`
- 作答：用自己的话描述 → 显示分析动画与耗时 → 流式展示评判内容 → 自动 `grade`
- 非 `easy` 才展开完整答案
- 模型失败、断流或结果解析失败都不落库，可保留草稿重试
- 「结束本轮 skip」关闭会话

## 题库管理与 CSV 导入

点击页面右上角「题库管理」进入管理页。支持按题目、答案、分类或 URL 搜索，可直接展开答案正文，正文中的图片会安全渲染并可点击打开原图；也可打开带题目锚点的来源链接。为保留复习历史，已经进入过会话的题目不能删除，但仍可修改题干、答案、分类和来源 URL。

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

## 模型配置（仅网页评卷）

作答页顶部展示当前评卷模型（显示名、模型 ID、Base URL）。点进去进入配置库：点卡片切换当前模型；可新建、修改、复制、删除。同一厂商可存多条，每条自己的 Base URL 和 Key。

Key 写入项目 `.env` 的 `BAGU_KEY_<模型id>`，模型列表在 `settings.json`，都不会进 git。保存前必须测试通过，失败不写盘。作答失败时输入框草稿会保留。

旧版单模型配置会在首次读取时自动迁移：`settings.json` 的 `{provider, model, base_url}` 会变成一条模型记录，`.env` 中的 `BAGU_API_KEY` 会改写为对应的 `BAGU_KEY_<模型id>`。迁移完成后不再使用旧键。

下拉预设（新建时预填，可改）：

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

## Hermes 聊天

你用自己的话作答 → **Hermes 自己的 AI** 分析并给出 `again|hard|good|easy` → 再 `grade` 一次。不是 easy 时对照题目 URL 讲完整答案。一次只发一题。

Hermes 路径**不**调用本仓库配置的 LLM。禁止：不带 session 的 grade、同一题再 grade、用户未作答就自选评级、本轮未结束再 draw、把 Nous token 写入本项目。

## HTTP API（仅本机）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 单页 `web/index.html` |
| GET | `/api/stats` | 看板；含 `open_session_id` |
| GET | `/api/session` | 当前 open 会话及题目 |
| GET | `/api/questions` | 分页查询；支持 `q`、`cat`、`page`、`page_size` |
| POST | `/api/questions` | 新增题目 |
| PUT | `/api/questions/:id` | 修改题目，不重置复习进度 |
| DELETE | `/api/questions/:id` | 删除未进入过会话的题目 |
| POST | `/api/questions/import` | 解析并导入 UTF-8 CSV 文本 |
| POST | `/api/draw` | `{n, cat?}`；已有会话返回 409 |
| POST | `/api/answer` | `{session_id, question_id, text}` → 调模型 → grade |
| POST | `/api/answer/stream` | 同上；SSE 推送 `start` / `delta` / `done` / `error`，完整解析后才 grade |
| POST | `/api/skip` | 关闭本轮 |
| GET | `/api/models` | 模型列表 + `active_id` + 掩码 Key + 预设 |
| POST | `/api/models` | 新建（服务端先测再写） |
| POST | `/api/models/test` | 用草稿 ping，不写盘 |
| PUT | `/api/models/:id` | 修改（先测再写；Key 空则沿用） |
| POST | `/api/models/:id/activate` | 设为当前评卷模型 |
| POST | `/api/models/:id/copy` | 复制，不改当前项 |
| DELETE | `/api/models/:id` | 删除 |
| GET | `/api/settings` | 兼容：当前 active 的字段 + 掩码 Key |

旧写接口 `POST /api/settings`、`POST /api/settings/test`、`POST /api/settings/import-hermes` 已停用，统一返回 404；模型连接测试改用 `POST /api/models/test`。

## 仓库结构

```
bagu.py                 # CLI + HTTP + 抽题/评分/评卷
web/index.html          # 本机单页
test/test_bagu.py       # pytest，使用临时库
docs/superpowers/       # 设计稿与实现计划
.gitignore
README.md
AGENTS.md               # 给 Agent 的项目规则
```

本地生成、不要提交：`.env`、`settings.json`、`bagu.db`。

## 测试

```bash
python -m pytest test/test_bagu.py -v
```

测试使用临时库，不会写真实 `bagu.db`。多模型条目见 `docs/superpowers/specs/2026-08-26-model-profiles-design.md`（已实现）。

## 安全

- API Key 只放 `.env`，已写入 `.gitignore`
- 接口不回明文 Key，只回掩码
- 服务只监听 `127.0.0.1`
- 不要把 `.env`、`bagu.db`、`settings.json` 拷进聊天、截图或公开仓库
- 若 Key 曾离开本机，去对应供应商控制台轮换
