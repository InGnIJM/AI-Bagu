# 八股抽问：按模型条目配置

日期：2026-08-26  
状态：已实现（2026-08-26）

## 目标

1. 作答页（主页）明确展示**当前评卷模型**：显示名、模型 ID、Base URL。点这条进入配置库。
2. 每个模型单独存储、单独展示。同一厂商可有多条（例如 `deepseek-chat` 与 `deepseek-reasoner`）；新建/复制时预填同厂商的 URL 和 Key，保存后各管各的。
3. 允许修改、复制。保存前必须测试通过，失败不写盘。
4. 去掉顶栏「模型配置」Tab，去掉「从 Hermes 导入」。
5. 作答失败（未配置、测试未过、模型 HTTP 失败）不落 grade，**作答草稿保留**，去改模型再回来不用重打。

抽题、会话锁、SM-2、Hermes CLI 评卷路径不变。网页仍只绑 `127.0.0.1`，无第三方 Python 依赖。

## 非目标

- 不从 Hermes `.env` 导入 Key。
- 不做厂商级共用钥匙（改一条 Key 不影响同厂商其它条目）。
- 不把 Key 写入 `settings.json`。
- 不新开第二个 HTML 文件：仍是 `web/index.html` 单页，作答 / 配置库 / 编辑 三个视图切换。

## 数据

### `settings.json`（gitignore，无 Key）

```json
{
  "active_id": "m_a1b2c3d4",
  "models": [
    {
      "id": "m_a1b2c3d4",
      "name": "DeepSeek Chat",
      "provider": "deepseek",
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1"
    }
  ]
}
```

- `id`：`m_` + 8 位小写十六进制。
- `name`：展示名。空则保存时写成 `{厂商中文名} · {model}`。
- `provider`：预设 id 或 `custom`，只用于新建时预填，不是唯一配置单元。
- `active_id`：当前评卷条目。列表为空时为 `""`。
- 评卷只使用 `active_id` 那一条的 `model`、`base_url` 及其 Key。

### `.env`（gitignore）

每条模型一行：`BAGU_KEY_<id>=...`  
例：`BAGU_KEY_m_a1b2c3d4=sk-...`

本程序重写 `.env` 时只写出当前仍存在的 `BAGU_KEY_*` 行，删模型时丢掉对应行。不再写入 `BAGU_API_KEY`。

GET 接口只回 `api_key_masked`，不明文回 Key。

### 旧格式迁移

读到旧结构 `{provider, model, base_url}`（无 `models`）时，生成一条模型：`id` 新生成，`name` 按规则默认，字段从旧值来。若 `.env` 有 `BAGU_API_KEY`，改写成 `BAGU_KEY_<id>` 并去掉旧键。`active_id` 指向这条。写回新格式。只在 `load_settings` / 首次读写时做，幂等。

厂商预设表 `PROVIDER_PRESETS` 保留，用途仅限：新建时选厂商预填默认 `model` 与 `base_url`。

## 页面

视觉沿用现有：学习紫、Fira Sans / Fira Code、按钮圆角 20px、卡片 12px。图标用 SVG，不用 emoji。

### 作答页

- 顶栏只留品牌，**删除**「作答 / 模型配置」Tab。
- 统计条下方一条可点击的当前模型条：`name`、`model`、`base_url`。未配置时文案为「未配置评卷模型」。
- 点击进入配置库视图（不在作答页改字段）。
- 作答、抽题、skip、分类进度与现在相同。

### 配置库

- 左上「返回作答」。
- 卡片列表：点**整张卡片**把该条设为 `active_id`（不重新测试），当前条目标「使用中」。
- 卡片上的「修改」「复制」「删除」是文字操作，点击不触发选用。删除前 `confirm`。
- 底部「新建配置」。无 Hermes 导入。

### 新建 / 修改表单

字段：显示名、厂商、模型 ID、Base URL、API Key（password；留空表示不改已存 Key）。

- 新建：改厂商则填入该预设的默认模型 ID 和 Base URL。
- 修改：改厂商**不**覆盖用户已填的模型 ID / Base URL。
- 「测试连接」：用表单草稿发最小 `chat/completions`（`ping`），**不写盘**。Key 留空时用该 id 已存 Key。
- 「保存」仅在本次测试通过后可点。服务端保存时再测一次；失败 502，文件不变。
- 复制：立即新 `id`，`name` 加「 副本」，Key 拷一份到新键，插入列表，**不**改 `active_id`，不要求再测。然后停在配置库。

### 作答草稿

`/api/answer` 失败（未配置、502、解析失败等）不 `grade`，前端不清 textarea。

草稿键：`sessionStorage["bagu-draft:" + session_id + ":" + question_id]`。

- 输入时写入；切到配置库 / 刷新后读回。
- 判成功、进入下一题、skip、本轮结束：删掉该键。

## HTTP API

评卷：`POST /api/answer` 改为使用当前 `active_id` 条目。无 active、无 Key → 400「未配置模型」，不 grade。模型 HTTP 失败仍 502，不 grade。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/models` | `{active_id, models:[{id,name,provider,model,base_url,api_key_masked,configured}], presets}` |
| POST | `/api/models/test` | body：草稿 `{provider,model,base_url,api_key,id?}`。不写盘。Key 空且带 id 则用已存 Key。失败 502。 |
| POST | `/api/models` | 新建。body：`{name,provider,model,base_url,api_key}`。服务端先测再写。测挂 502 且不写。成功返回该条（掩码）。 |
| PUT | `/api/models/:id` | 修改。body 同新建（`api_key` 可空）。先测再写。Key 空则沿用旧 Key。 |
| POST | `/api/models/:id/activate` | 只改 `active_id`。 |
| POST | `/api/models/:id/copy` | 复制，不改 `active_id`。 |
| DELETE | `/api/models/:id` | 删条目和对应 Key。若删的是当前项：改选列表第一条；删光则 `active_id=""`。 |

`GET /api/settings` 保留为兼容：返回当前 active 的 `provider/model/base_url/api_key_masked/configured` 以及 `presets`（无 active 则字段为空）。`POST /api/settings` 与 `POST /api/settings/test`、`POST /api/settings/import-hermes` 一律 404 `{"error":"not found"}`。测试走 `/api/models/test`。

## 错误处理

- 测试/保存失败：中文错误（未配置、连接失败、返回无法解析）。前端展示，不改本地已存配置。
- 选用/修改/删除未知 id：400。
- 复制后 Key 缺失（源未配 Key）：仍复制元数据，新条 `configured=false`；选用它可以，作答时按未配置失败并保留草稿。

## 测试（`test/test_bagu.py`，临时目录）

1. 旧 `settings.json` + `BAGU_API_KEY` 读入后变成一条模型，Key 在 `BAGU_KEY_<id>`。
2. `/api/models/test` 失败不写文件；成功也不写（直到 POST/PUT）。
3. POST/PUT：mock 测试失败 → 502 且 `settings.json` / `.env` 不变；成功才写入。
4. copy 产生新 id、名字含「副本」、`active_id` 不变、新 Key 存在。
5. activate 只改 `active_id`。
6. 删除当前项后 `active_id` 指向剩余第一条；删光为空。
7. 无模型或 Key 空：`/api/answer` 失败且不 grade。
8. `judge_answer` 使用 active 条目的 model / base_url / key（mock `_openai_chat` 断言 settings）。
9. `POST /api/settings` 不再盲存。无 import-hermes。
10. 作答失败不 grade（pytest）。草稿是浏览器 `sessionStorage`：交付时用浏览器走「提交失败 → 去配置库 → 返回」确认文字还在，不单开前端测试框架。

继续禁止写真实 `bagu.db`。

## 文档

更新根目录 `README.md`：模型改为「作答页展示当前条目 → 配置库选用/新建/修改/复制」；删除 Hermes 导入说明；写明 Key 为 `BAGU_KEY_<id>`。

## 实现边界

- 逻辑仍在 `bagu.py` + `web/index.html` + `test/test_bagu.py`。
- 不改 `questions` / 会话表。
- 不提交 `.env`、`settings.json`、`bagu.db`。
