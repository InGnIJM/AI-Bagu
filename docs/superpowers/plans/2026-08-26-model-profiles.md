# 按模型条目配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **状态说明（2026-08-26）：** 本计划对应功能已实现，当前行为以 `2026-08-26-model-profiles-design.md`、代码和测试为准。下方未勾选项保留为原始实施步骤记录，不表示功能尚未落地。

**Goal:** 每个评卷模型单独存储和展示；作答页显示当前条目并可进入配置库；保存前测试通过才写盘；作答失败保留草稿。

**Architecture:** `settings.json` 存 `active_id` + `models[]`（无 Key）；`.env` 存 `BAGU_KEY_<id>`。读旧单槽格式时原地迁移。HTTP 新增 `/api/models*`；评卷仍走 `judge_answer`，改读 active 条目。单页三个视图：作答 / 配置库 / 编辑。

**Tech Stack:** Python 3 标准库、pytest、`web/index.html`。无第三方运行时依赖。

**Spec:** `docs/superpowers/specs/2026-08-26-model-profiles-design.md`

**Git:** 本仓库没有 `.git`。跳过所有 commit 步骤，除非用户之后要求初始化仓库。

---

## 文件结构

| 路径 | 职责 |
|------|------|
| `bagu.py` | 模型存储、迁移、CRUD、评卷、HTTP（含 PUT） |
| `web/index.html` | 作答条 + 配置库 + 编辑表单 + sessionStorage 草稿 |
| `test/test_bagu.py` | 存储 / API / 评卷单测（临时目录） |
| `README.md` | 用法；去掉 Hermes 导入和单槽配置说明 |

公开函数（后续任务必须用这些名字）：

```python
def new_model_id() -> str                    # m_ + 8 位 hex
def default_model_name(provider, model) -> str
def load_settings(root=None) -> dict         # 含 active_id/models 以及当前条的 provider/model/base_url/api_key/api_key_masked
def save_settings(data, api_key=None, root=None) -> None  # 写成「仅一条模型」的新格式（测试与迁移辅助）
def persist_store(active_id, models, root=None) -> None   # models 项可带 api_key；写出 json + .env
def public_models_payload(root=None) -> dict # GET /api/models 的 body
def test_model_draft(body, root=None, chat_fn=None) -> None
def create_model(body, root=None, chat_fn=None) -> dict
def update_model(model_id, body, root=None, chat_fn=None) -> dict
def activate_model(model_id, root=None) -> dict
def copy_model(model_id, root=None) -> dict
def delete_model(model_id, root=None) -> dict
```

`load_settings` 返回值约定：

```python
{
  "active_id": "m_...",
  "models": [{"id","name","provider","model","base_url","api_key","api_key_masked","configured"}, ...],
  "provider": "", "model": "", "base_url": "", "api_key": "", "api_key_masked": "",
}
```

后五个字段来自 active 条目；无 active 则为空字符串。`judge_answer` 继续读 `api_key` / `model` / `base_url`。

HTTP 路径解析：`handle_http` 增加 PUT。`/api/models/<id>`、`/api/models/<id>/activate`、`/api/models/<id>/copy`。

---

### Task 1: 新存储格式 + 旧配置迁移

**Files:**
- Modify: `bagu.py`（`new_model_id`、`default_model_name`、`load_settings`、`save_settings`、新增 `persist_store`）
- Test: `test/test_bagu.py`（改 `test_save_and_load_settings`，加迁移测试）

- [ ] **Step 1: 写失败测试**

把 `test_save_and_load_settings` 改成断言新结构，并追加：

```python
def test_save_and_load_settings(tmp_path):
    bagu.save_settings(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
        },
        api_key="sk-test",
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    assert s["model"] == "deepseek-chat" and s["api_key"] == "sk-test"
    assert "test" not in s["api_key_masked"]
    assert s["active_id"].startswith("m_")
    assert len(s["models"]) == 1
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "models" in raw and "api_key" not in raw
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BAGU_KEY_{s['active_id']}=" in env
    assert "BAGU_API_KEY=" not in env


def test_migrates_legacy_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "new_model_id", lambda: "m_deadbeef")
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("BAGU_API_KEY=sk-legacy-key\n", encoding="utf-8")
    s = bagu.load_settings(tmp_path)
    assert s["active_id"] == "m_deadbeef"
    assert s["api_key"] == "sk-legacy-key"
    assert s["models"][0]["name"]
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["active_id"] == "m_deadbeef" and len(raw["models"]) == 1
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BAGU_KEY_m_deadbeef=sk-legacy-key" in env
    assert "BAGU_API_KEY=" not in env
    s2 = bagu.load_settings(tmp_path)
    assert s2["active_id"] == "m_deadbeef" and len(s2["models"]) == 1


def test_new_model_id_format():
    assert re.fullmatch(r"m_[0-9a-f]{8}", bagu.new_model_id())


def test_default_model_name():
    assert bagu.default_model_name("deepseek", "deepseek-chat") == "DeepSeek · deepseek-chat"
    assert bagu.default_model_name("custom", "") == "自定义 OpenAI 兼容"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest test/test_bagu.py::test_save_and_load_settings test/test_bagu.py::test_migrates_legacy_settings test/test_bagu.py::test_new_model_id_format test/test_bagu.py::test_default_model_name -v`

Expected: FAIL（`new_model_id` 未定义，或 `.env` 仍是 `BAGU_API_KEY`）

- [ ] **Step 3: 写最小实现**

在 `bagu.py` 的 `list_provider_presets` 之后加入：

```python
def new_model_id():
    return "m_" + secrets.token_hex(4)


def default_model_name(provider, model):
    label = (PROVIDER_PRESETS.get(provider) or {}).get("label") or (provider or "自定义")
    model = (model or "").strip()
    return f"{label} · {model}" if model else label
```

替换 `_read_env_key` / `load_settings` / `save_settings` 为：

```python
def persist_store(active_id, models, root=None):
    root = _settings_root(root)
    payload_models = []
    env_lines = []
    for m in models:
        mid = m["id"]
        payload_models.append(
            {
                "id": mid,
                "name": m.get("name") or default_model_name(m.get("provider", ""), m.get("model", "")),
                "provider": m.get("provider", ""),
                "model": m.get("model", ""),
                "base_url": m.get("base_url", ""),
            }
        )
        key = m.get("api_key") or ""
        if key:
            env_lines.append(f"BAGU_KEY_{mid}={key}")
    (root / "settings.json").write_text(
        json.dumps({"active_id": active_id or "", "models": payload_models}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / ".env").write_text(("\n".join(env_lines) + ("\n" if env_lines else "")), encoding="utf-8")


def _attach_keys(models, env_map):
    out = []
    for m in models:
        item = dict(m)
        item["api_key"] = (env_map.get("BAGU_KEY_" + item["id"]) or "").strip()
        item["api_key_masked"] = mask_api_key(item["api_key"])
        item["configured"] = bool(item["api_key"])
        out.append(item)
    return out


def _empty_settings():
    return {
        "active_id": "",
        "models": [],
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key": "",
        "api_key_masked": "",
    }


def _with_active(active_id, models):
    data = _empty_settings()
    data["active_id"] = active_id or ""
    data["models"] = models
    active = next((m for m in models if m["id"] == active_id), None)
    if active:
        data["provider"] = active.get("provider", "")
        data["model"] = active.get("model", "")
        data["base_url"] = active.get("base_url", "")
        data["api_key"] = active.get("api_key", "")
        data["api_key_masked"] = active.get("api_key_masked", "")
    return data


def load_settings(root=None):
    root = _settings_root(root)
    sp = root / "settings.json"
    raw = {}
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty_settings()
    env_map = _parse_env_map(root / ".env")
    if isinstance(raw.get("models"), list):
        models = _attach_keys(raw.get("models") or [], env_map)
        active_id = raw.get("active_id") or ""
        if active_id and not any(m["id"] == active_id for m in models):
            active_id = models[0]["id"] if models else ""
        return _with_active(active_id, models)
    if raw.get("provider") or raw.get("model") or raw.get("base_url"):
        mid = new_model_id()
        item = {
            "id": mid,
            "name": default_model_name(raw.get("provider", ""), raw.get("model", "")),
            "provider": raw.get("provider", ""),
            "model": raw.get("model", ""),
            "base_url": raw.get("base_url", ""),
            "api_key": (env_map.get("BAGU_API_KEY") or "").strip(),
        }
        persist_store(mid, [item], root=root)
        return load_settings(root)
    return _empty_settings()


def save_settings(data, api_key=None, root=None):
    mid = new_model_id()
    item = {
        "id": mid,
        "name": default_model_name(data.get("provider", ""), data.get("model", "")),
        "provider": data.get("provider", ""),
        "model": data.get("model", ""),
        "base_url": data.get("base_url", ""),
        "api_key": api_key if api_key is not None else "",
    }
    persist_store(mid, [item], root=root)
```

删除已无用的 `_read_env_key`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest test/test_bagu.py::test_save_and_load_settings test/test_bagu.py::test_migrates_legacy_settings test/test_bagu.py::test_new_model_id_format test/test_bagu.py::test_default_model_name test/test_bagu.py::test_load_settings_bad_json test/test_bagu.py::test_judge_uses_model_and_reference -v`

Expected: PASS

- [ ] **Step 5: Commit** — 跳过（无 git）

---

### Task 2: 模型 CRUD（测过才写）

**Files:**
- Modify: `bagu.py`（`test_model_draft` / `create_model` / `update_model` / `activate_model` / `copy_model` / `delete_model` / `public_models_payload`）
- Test: `test/test_bagu.py`

- [ ] **Step 1: 写失败测试**

```python
def test_create_model_tests_before_write(tmp_path, monkeypatch):
    calls = []

    def boom(prompt, settings):
        calls.append(settings)
        raise bagu.JudgeError("401")

    with pytest.raises(bagu.JudgeError):
        bagu.create_model(
            {
                "name": "A",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "base_url": "http://x/v1",
                "api_key": "sk-aaa",
            },
            root=tmp_path,
            chat_fn=boom,
        )
    assert not (tmp_path / "settings.json").exists()
    monkeypatch.setattr(bagu, "_openai_chat", lambda prompt, settings: "pong")
    out = bagu.create_model(
        {
            "name": "",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "http://x/v1",
            "api_key": "sk-aaa",
        },
        root=tmp_path,
    )
    assert out["id"].startswith("m_")
    assert "DeepSeek" in out["name"]
    assert out["configured"] is True
    s = bagu.load_settings(tmp_path)
    assert s["active_id"] == out["id"]


def test_test_model_draft_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    bagu.test_model_draft(
        {"model": "m", "base_url": "http://x/v1", "api_key": "sk-z"},
        root=tmp_path,
    )
    assert not (tmp_path / "settings.json").exists()


def test_update_copy_activate_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    a = bagu.create_model(
        {
            "name": "A",
            "provider": "deepseek",
            "model": "chat",
            "base_url": "http://x/v1",
            "api_key": "sk-a",
        },
        root=tmp_path,
    )
    b = bagu.create_model(
        {
            "name": "B",
            "provider": "openai",
            "model": "gpt",
            "base_url": "http://y/v1",
            "api_key": "sk-b",
        },
        root=tmp_path,
    )
    assert bagu.load_settings(tmp_path)["active_id"] == b["id"]
    bagu.activate_model(a["id"], root=tmp_path)
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    copied = bagu.copy_model(a["id"], root=tmp_path)
    assert copied["id"] != a["id"]
    assert copied["name"].endswith("副本")
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    assert bagu.load_settings(tmp_path)["models"]
    keys = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BAGU_KEY_{copied['id']}=sk-a" in keys
    bagu.update_model(
        a["id"],
        {
            "name": "A2",
            "provider": "deepseek",
            "model": "reasoner",
            "base_url": "http://x/v1",
            "api_key": "",
        },
        root=tmp_path,
    )
    s = bagu.load_settings(tmp_path)
    m = next(x for x in s["models"] if x["id"] == a["id"])
    assert m["name"] == "A2" and m["model"] == "reasoner" and m["api_key"] == "sk-a"
    bagu.delete_model(a["id"], root=tmp_path)
    s = bagu.load_settings(tmp_path)
    assert a["id"] not in {x["id"] for x in s["models"]}
    assert s["active_id"] in {b["id"], copied["id"]}
    for m in list(s["models"]):
        bagu.delete_model(m["id"], root=tmp_path)
    s = bagu.load_settings(tmp_path)
    assert s["models"] == [] and s["active_id"] == ""


def test_unknown_model_ops_raise(tmp_path):
    with pytest.raises(LookupError):
        bagu.activate_model("m_nope", root=tmp_path)
    with pytest.raises(LookupError):
        bagu.copy_model("m_nope", root=tmp_path)
    with pytest.raises(LookupError):
        bagu.delete_model("m_nope", root=tmp_path)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest test/test_bagu.py::test_create_model_tests_before_write test/test_bagu.py::test_test_model_draft_does_not_write test/test_bagu.py::test_update_copy_activate_delete test/test_bagu.py::test_unknown_model_ops_raise -v`

Expected: FAIL with `create_model` 未定义

- [ ] **Step 3: 写最小实现**

```python
def _public_item(m):
    return {
        "id": m["id"],
        "name": m.get("name") or "",
        "provider": m.get("provider") or "",
        "model": m.get("model") or "",
        "base_url": m.get("base_url") or "",
        "api_key_masked": m.get("api_key_masked") or mask_api_key(m.get("api_key") or ""),
        "configured": bool(m.get("api_key")),
    }


def public_models_payload(root=None):
    s = load_settings(root)
    return {
        "active_id": s["active_id"],
        "models": [_public_item(m) for m in s["models"]],
        "presets": list_provider_presets(),
    }


def _draft_settings(body, root=None):
    key = (body.get("api_key") or "").strip()
    if not key and body.get("id"):
        s = load_settings(root)
        src = next((m for m in s["models"] if m["id"] == body["id"]), None)
        key = (src or {}).get("api_key") or ""
    return {
        "model": body.get("model") or "",
        "base_url": body.get("base_url") or "",
        "api_key": key,
    }


def test_model_draft(body, root=None, chat_fn=None):
    settings = _draft_settings(body, root=root)
    if not settings["api_key"]:
        raise JudgeError("未配置模型")
    fn = chat_fn or _openai_chat
    fn("ping", settings)


def create_model(body, root=None, chat_fn=None):
    test_model_draft(body, root=root, chat_fn=chat_fn)
    s = load_settings(root)
    mid = new_model_id()
    name = (body.get("name") or "").strip() or default_model_name(body.get("provider", ""), body.get("model", ""))
    item = {
        "id": mid,
        "name": name,
        "provider": body.get("provider", ""),
        "model": body.get("model", ""),
        "base_url": body.get("base_url", ""),
        "api_key": (body.get("api_key") or "").strip(),
    }
    models = s["models"] + [item]
    persist_store(mid, models, root=root)
    return _public_item(load_settings(root)["models"][-1])


def update_model(model_id, body, root=None, chat_fn=None):
    s = load_settings(root)
    src = next((m for m in s["models"] if m["id"] == model_id), None)
    if not src:
        raise LookupError(f"模型不存在: {model_id}")
    draft = dict(body)
    draft["id"] = model_id
    test_model_draft(draft, root=root, chat_fn=chat_fn)
    key = (body.get("api_key") or "").strip() or src.get("api_key") or ""
    name = (body.get("name") or "").strip() or default_model_name(body.get("provider", ""), body.get("model", ""))
    updated = []
    for m in s["models"]:
        if m["id"] != model_id:
            updated.append(m)
            continue
        updated.append(
            {
                "id": model_id,
                "name": name,
                "provider": body.get("provider", ""),
                "model": body.get("model", ""),
                "base_url": body.get("base_url", ""),
                "api_key": key,
            }
        )
    persist_store(s["active_id"], updated, root=root)
    return _public_item(next(m for m in load_settings(root)["models"] if m["id"] == model_id))


def activate_model(model_id, root=None):
    s = load_settings(root)
    if not any(m["id"] == model_id for m in s["models"]):
        raise LookupError(f"模型不存在: {model_id}")
    persist_store(model_id, s["models"], root=root)
    return public_models_payload(root)


def copy_model(model_id, root=None):
    s = load_settings(root)
    src = next((m for m in s["models"] if m["id"] == model_id), None)
    if not src:
        raise LookupError(f"模型不存在: {model_id}")
    nid = new_model_id()
    item = {
        "id": nid,
        "name": (src.get("name") or default_model_name(src.get("provider", ""), src.get("model", ""))) + " 副本",
        "provider": src.get("provider", ""),
        "model": src.get("model", ""),
        "base_url": src.get("base_url", ""),
        "api_key": src.get("api_key") or "",
    }
    persist_store(s["active_id"], s["models"] + [item], root=root)
    return _public_item(item)


def delete_model(model_id, root=None):
    s = load_settings(root)
    if not any(m["id"] == model_id for m in s["models"]):
        raise LookupError(f"模型不存在: {model_id}")
    left = [m for m in s["models"] if m["id"] != model_id]
    if s["active_id"] == model_id:
        active = left[0]["id"] if left else ""
    else:
        active = s["active_id"]
    persist_store(active, left, root=root)
    return public_models_payload(root)
```

`create_model` 把新建的设为 `active_id`（第一条配置立即能用）。复制不改 `active_id`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest test/test_bagu.py::test_create_model_tests_before_write test/test_bagu.py::test_test_model_draft_does_not_write test/test_bagu.py::test_update_copy_activate_delete test/test_bagu.py::test_unknown_model_ops_raise -v`

Expected: PASS

- [ ] **Step 5: Commit** — 跳过（无 git）

---

### Task 3: HTTP 路由；去掉旧 settings 写入和 Hermes 导入

**Files:**
- Modify: `bagu.py`（`handle_http`、`Handler.do_PUT`；删除 `import_hermes_settings` 与 `HERMES_IMPORT_ORDER`）
- Test: `test/test_bagu.py`（改 `test_http_more_routes`；删 Hermes 单测；加 models API 测）

- [ ] **Step 1: 写失败测试**

1. 删除这些测试函数整段：`test_import_hermes_prefers_deepseek`、`test_import_hermes_openrouter_and_empty`、`test_provider_presets_and_glm_import` 里对 `import_hermes_settings` 的调用。保留 presets 断言，改成：

```python
def test_provider_presets_list():
    ids = {p["id"] for p in bagu.list_provider_presets()}
    assert {"deepseek", "openai", "glm", "kimi", "siliconflow", "gemini", "ollama"} <= ids
```

2. 在 `test_http_more_routes` 中，把这段：

```python
    code, ok, _ = bagu.handle_http(
        "POST",
        "/api/settings",
        {"provider": "deepseek", "model": "deepseek-chat", "base_url": "http://x", "api_key": "sk-abc"},
        conn,
        tmp_path,
    )
    assert code == 200
    code, err, _ = bagu.handle_http("POST", "/api/settings/test", {}, conn, tmp_path)
    assert code == 502
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, ok, _ = bagu.handle_http("POST", "/api/settings/test", {}, conn, tmp_path)
    assert code == 200
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-hermes"))
    monkeypatch.setattr(bagu.Path, "home", classmethod(lambda cls: tmp_path / "no-home"))
    code, err, _ = bagu.handle_http("POST", "/api/settings/import-hermes", {}, conn, tmp_path)
    assert code == 400
```

换成：

```python
    code, err, _ = bagu.handle_http("POST", "/api/settings", {"model": "x"}, conn, tmp_path)
    assert code == 404
    code, err, _ = bagu.handle_http("POST", "/api/settings/test", {}, conn, tmp_path)
    assert code == 404
    code, err, _ = bagu.handle_http("POST", "/api/settings/import-hermes", {}, conn, tmp_path)
    assert code == 404
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, created, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {
            "name": "T",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "http://x/v1",
            "api_key": "sk-abc",
        },
        conn,
        tmp_path,
    )
    assert code == 200 and created["id"].startswith("m_")
```

3. 追加：

```python
def test_api_models_crud(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert code == 200 and listed["models"] == [] and listed["active_id"] == ""
    def boom(*a, **k):
        raise bagu.JudgeError("nope")
    monkeypatch.setattr(bagu, "_openai_chat", boom)
    code, err, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "X", "provider": "deepseek", "model": "m", "base_url": "http://x", "api_key": "sk"},
        conn,
        tmp_path,
    )
    assert code == 502 and not (tmp_path / "settings.json").exists()
    monkeypatch.setattr(bagu, "_openai_chat", lambda *a, **k: "pong")
    code, a, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "A", "provider": "deepseek", "model": "chat", "base_url": "http://x", "api_key": "sk-a"},
        conn,
        tmp_path,
    )
    code, b, _ = bagu.handle_http(
        "POST",
        "/api/models",
        {"name": "B", "provider": "openai", "model": "gpt", "base_url": "http://y", "api_key": "sk-b"},
        conn,
        tmp_path,
    )
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert listed["active_id"] == b["id"]
    code, _, _ = bagu.handle_http("POST", f"/api/models/{a['id']}/activate", {}, conn, tmp_path)
    code, listed, _ = bagu.handle_http("GET", "/api/models", None, conn, tmp_path)
    assert listed["active_id"] == a["id"]
    code, copied, _ = bagu.handle_http("POST", f"/api/models/{a['id']}/copy", {}, conn, tmp_path)
    assert copied["name"].endswith("副本")
    assert bagu.load_settings(tmp_path)["active_id"] == a["id"]
    code, tested, _ = bagu.handle_http(
        "POST",
        "/api/models/test",
        {"model": "chat", "base_url": "http://x", "api_key": "sk-a"},
        conn,
        tmp_path,
    )
    assert code == 200
    code, upd, _ = bagu.handle_http(
        "PUT",
        f"/api/models/{a['id']}",
        {"name": "A2", "provider": "deepseek", "model": "r", "base_url": "http://x", "api_key": ""},
        conn,
        tmp_path,
    )
    assert code == 200 and upd["name"] == "A2"
    code, _, _ = bagu.handle_http("DELETE", f"/api/models/{a['id']}", None, conn, tmp_path)
    s = bagu.load_settings(tmp_path)
    assert a["id"] not in {m["id"] for m in s["models"]}
    code, err, _ = bagu.handle_http("POST", "/api/models/m_nope/activate", {}, conn, tmp_path)
    assert code == 400
    code, gs, _ = bagu.handle_http("GET", "/api/settings", None, conn, tmp_path)
    assert code == 200 and gs["configured"] is True and gs["model"] in {"gpt", "chat", "r"}
```

`GET /api/settings` 仍 200，字段来自 active。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest test/test_bagu.py::test_api_models_crud test/test_bagu.py::test_http_more_routes -v`

Expected: FAIL（`/api/models` 仍 404，或 POST `/api/settings` 仍 200）

- [ ] **Step 3: 写最小实现**

在 `handle_http` 开头解析 models 路径。把旧的 POST settings / test / import-hermes 分支删掉。GET `/api/settings` 改为：

```python
    if method == "GET" and path == "/api/settings":
        s = load_settings(root)
        return 200, {
            "provider": s["provider"],
            "model": s["model"],
            "base_url": s["base_url"],
            "api_key_masked": s["api_key_masked"],
            "configured": bool(s["api_key"]),
            "presets": list_provider_presets(),
        }, json_ct
    if method == "GET" and path == "/api/models":
        return 200, public_models_payload(root), json_ct
    if method == "POST" and path == "/api/models/test":
        try:
            test_model_draft(body, root=root)
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
        return 200, {"ok": True}, json_ct
    if method == "POST" and path == "/api/models":
        try:
            created = create_model(body, root=root)
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
        return 200, created, json_ct
```

再加一段通用：

```python
    def _mid_action(p):
        if not p.startswith("/api/models/"):
            return None, None
        rest = p[len("/api/models/"):]
        if not rest or rest == "test":
            return None, None
        parts = rest.split("/")
        return parts[0], (parts[1] if len(parts) > 1 else "")

    mid, action = _mid_action(path)
    if mid:
        try:
            if method == "PUT" and action == "":
                return 200, update_model(mid, body, root=root), json_ct
            if method == "POST" and action == "activate":
                return 200, activate_model(mid, root=root), json_ct
            if method == "POST" and action == "copy":
                return 200, copy_model(mid, root=root), json_ct
            if method == "DELETE" and action == "":
                return 200, delete_model(mid, root=root), json_ct
        except LookupError as e:
            return 400, {"error": str(e)}, json_ct
        except JudgeError as e:
            return 502, {"error": str(e)}, json_ct
```

删除 `import_hermes_settings`、`HERMES_IMPORT_ORDER`。

在 `Handler` 增加：

```python
        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._write(400, {"error": "JSON 无法解析"}, "application/json")
                return
            self._dispatch("PUT", body)

        def do_DELETE(self):
            self._dispatch("DELETE", None)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest test/test_bagu.py -v`

Expected: PASS（不应再有 `import_hermes_settings` 引用）

- [ ] **Step 5: Commit** — 跳过（无 git）

---

### Task 4: 评卷使用 active 条目

**Files:**
- Modify: `test/test_bagu.py`（加强 `test_judge_uses_model_and_reference`）
- Modify: `bagu.py` 仅当 `judge_answer` 还没走 `load_settings` 的 active 字段时才改（Task 1 后应已可用）

- [ ] **Step 1: 写失败测试**

把 `test_judge_uses_model_and_reference` 的 mock 改成断言 settings：

```python
def test_judge_uses_model_and_reference(conn, tmp_path, monkeypatch):
    conn.execute("INSERT INTO questions(category, question, url) VALUES(?,?,?)", ("A", "题", "http://ref"))
    conn.commit()
    monkeypatch.setattr(bagu, "_openai_chat", lambda prompt, settings: "pong")
    bagu.create_model(
        {"name": "X", "provider": "deepseek", "model": "m", "base_url": "http://x", "api_key": "sk-longkey12"},
        root=tmp_path,
    )
    bagu.create_model(
        {"name": "Y", "provider": "openai", "model": "other", "base_url": "http://y", "api_key": "sk-otherkey99"},
        root=tmp_path,
    )
    first = bagu.load_settings(tmp_path)["models"][0]
    bagu.activate_model(first["id"], root=tmp_path)
    sid, rows = bagu.draw(conn, 1)
    seen = {}
    monkeypatch.setattr(bagu, "fetch_reference_text", lambda url, limit=4000: "参考正文")

    def chat(prompt, settings):
        seen.update(settings)
        return "GRADE: good\nCOMMENT: 过\nANSWER:"

    monkeypatch.setattr(bagu, "_openai_chat", chat)
    out = bagu.judge_answer(conn, sid, rows[0]["id"], "答", root=tmp_path)
    assert out["grade"] == "good"
    assert seen.get("model") == "m"
    assert seen.get("base_url") == "http://x"
    assert seen.get("api_key") == "sk-longkey12"
```

- [ ] **Step 2: 跑测试确认失败或通过**

Run: `python -m pytest test/test_bagu.py::test_judge_uses_model_and_reference -v`

若 FAIL：检查 `judge_answer` 是否把 `load_settings(root)` 整份传给 `_openai_chat`（应包含 active 的 `model`/`base_url`/`api_key`）。不要把 `models` 列表当 chat settings 用错字段。

- [ ] **Step 3: 如需最小修复**

`judge_answer` 保持：

```python
    settings = load_settings(root)
    if chat_fn is None:
        if not settings.get("api_key"):
            raise JudgeError("未配置模型")
        chat_fn = lambda prompt: _openai_chat(prompt, settings)
```

`_openai_chat` 已读 `settings["model"]` / `base_url` / `api_key`。不要改签名。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest test/test_bagu.py::test_judge_uses_model_and_reference test/test_bagu.py::test_api_answer_requires_settings -v`

Expected: PASS

- [ ] **Step 5: Commit** — 跳过（无 git）

---

### Task 5: 网页三视图 + 当前模型条 + 草稿

**Files:**
- Modify: `web/index.html`
- Test: `test/test_bagu.py::test_http_more_routes` 已断言首页含「八股抽问」；改为同时断言含「未配置评卷模型」、不含「从 Hermes 导入」

- [ ] **Step 1: 写失败测试**

在 `test_http_more_routes` 的 GET `/` 断言改为：

```python
    code, html, ctype = bagu.handle_http("GET", "/", None, conn, tmp_path)
    assert code == 200 and "八股抽问" in html and "text/html" in ctype
    assert "未配置评卷模型" in html
    assert "从 Hermes 导入" not in html
    assert "tab-cfg" not in html
    assert "bagu-draft:" in html
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest test/test_bagu.py::test_http_more_routes -v`

Expected: FAIL（页面还是旧 Tab）

- [ ] **Step 3: 改 `web/index.html`**

CSS 追加：

```css
    .model-bar {
      width: 100%; text-align: left; margin-bottom: 16px; min-height: 56px;
      border: 1px solid var(--color-border); background: var(--color-card);
      border-radius: var(--radius-card); padding: 12px 16px; box-shadow: var(--shadow);
    }
    .model-bar .k { font-size: 12px; font-weight: 600; color: var(--color-primary); }
    .model-bar .n { font-weight: 600; margin-top: 2px; }
    .model-bar .u { font-family: var(--font-mono); font-size: 12px; color: var(--color-muted-fg); }
    .lib-card {
      border: 1px solid var(--color-border); border-radius: var(--radius-card);
      padding: 16px; margin-bottom: 8px; background: #fff; cursor: pointer; width: 100%; text-align: left;
    }
    .lib-card.active { border: 2px solid var(--color-primary); background: var(--color-muted); }
    .lib-ops { margin-top: 8px; display: flex; gap: 12px; }
    .lib-ops button { border: 0; background: transparent; color: var(--color-primary); min-height: 44px; font-weight: 600; }
    .back { border: 0; background: transparent; color: var(--color-primary); min-height: 44px; font-weight: 600; margin-bottom: 8px; }
```

顶栏：删除 `.tabs` 整块。统计条下、`view-quiz` 前插入：

```html
    <button type="button" class="model-bar" id="model-bar" aria-label="当前评卷模型">
      <div class="k">正在评卷</div>
      <div class="n" id="model-bar-name">未配置评卷模型</div>
      <div class="u" id="model-bar-meta"></div>
    </button>
```

`view-cfg` 整段替换为两个 section：

```html
    <section id="view-lib" class="card hidden">
      <button type="button" class="back" id="btn-back-quiz">返回作答</button>
      <h2 class="q" style="font-size:22px;margin-bottom:12px">评卷模型</h2>
      <div id="lib-list"></div>
      <button class="cta" type="button" id="btn-new-model">新建配置</button>
      <p class="error hidden" id="lib-err" role="alert"></p>
    </section>

    <section id="view-edit" class="card hidden">
      <button type="button" class="back" id="btn-back-lib">返回列表</button>
      <h2 class="q" style="font-size:22px;margin-bottom:12px" id="edit-title">新建配置</h2>
      <div class="field">
        <label for="m-name">显示名</label>
        <input id="m-name">
      </div>
      <div class="field">
        <label for="prov">供应商</label>
        <select id="prov"></select>
      </div>
      <div class="row">
        <div class="field">
          <label for="model">模型 ID</label>
          <input id="model">
        </div>
        <div class="field">
          <label for="base">Base URL</label>
          <input id="base">
        </div>
      </div>
      <div class="field">
        <label for="key">API Key</label>
        <input id="key" type="password" autocomplete="off" placeholder="留空则不改已保存的 Key">
        <p class="help" id="key-hint">未配置</p>
      </div>
      <p class="error hidden" id="cfg-err" role="alert"></p>
      <div class="actions">
        <button class="secondary" type="button" id="btn-test">测试连接</button>
        <button class="cta" type="button" id="btn-save" style="margin-top:0" disabled>保存</button>
      </div>
    </section>
```

脚本关键点（替换 `showTab` 到文件末尾的配置逻辑）：

```javascript
    function draftKey(sid, qid) { return "bagu-draft:" + sid + ":" + qid; }
    function saveDraft() {
      const q = currentQuestion();
      if (!session.session_id || !q) return;
      sessionStorage.setItem(draftKey(session.session_id, q.id), $("ans").value);
    }
    function loadDraft() {
      const q = currentQuestion();
      if (!session.session_id || !q) return "";
      return sessionStorage.getItem(draftKey(session.session_id, q.id)) || "";
    }
    function clearDraft(sid, qid) {
      sessionStorage.removeItem(draftKey(sid, qid));
    }
    function showView(name) {
      ["view-quiz", "view-lib", "view-edit"].forEach((id) => {
        $(id).classList.toggle("hidden", id !== "view-" + name);
      });
    }
```

`renderQuiz`：**不要**无条件 `$("ans").value = ""`。改为 ` $("ans").value = loadDraft();`。判成功后 `clearDraft`；skip / 抽新题后旧题草稿可留着但当前题读自己的 key。

`renderQuiz` 在「下一题」刷新时会换 pending[0]，自然读新 key。成功判定后：

```javascript
        clearDraft(session.session_id, q.id);
```

提交失败：catch 里不清 textarea，并 `saveDraft()`。

`ans` 监听 `input` → `saveDraft()`。

当前模型条：`GET /api/models`，active 有则填 name / `model · base_url`，无则「未配置评卷模型」。点击 `showView("lib")` 并渲染列表。

配置库列表：每张卡 `data-id`，卡 click → `POST /api/models/:id/activate`。`.lib-ops button` 必须 `event.stopPropagation()`。删除 `confirm("删除这条模型配置？")`。复制 `POST .../copy` 后刷新列表，不切视图。新建 → `showView("edit")`，`editingId=null`，`tested=false`，`btn-save.disabled=true`。修改 → 填表，`editingId=id`，改厂商**不**覆盖 model/base（只用 `fillProviderDefaults` 当 `!editingId`）。

测试连接：`POST /api/models/test`，body 含表单；若 `editingId` 则带 `id`。成功 `btn-save.disabled=false` 并记录 `testedOk=true`。改任何字段 → `testedOk=false`、保存再次 disabled。

保存：无 `testedOk` 则 return。新建 `POST /api/models`，修改 `PUT /api/models/:id`。成功回配置库。

`api()` 已支持 method；PUT/DELETE 直接用。

删掉 `#tab-quiz` / `#tab-cfg` / `#btn-import` 相关监听。

页面字符串必须包含字面量 `bagu-draft:`（给测试）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest test/test_bagu.py::test_http_more_routes -v`

Expected: PASS

- [ ] **Step 5: Commit** — 跳过（无 git）

---

### Task 6: README

**Files:**
- Modify: `README.md` 模型配置一节

- [ ] **Step 1: 改文档**（无单独失败测试）

把「模型配置（仅网页评卷）」整节换成：

```markdown
## 模型配置（仅网页评卷）

作答页顶部展示当前评卷模型（显示名、模型 ID、Base URL）。点进去进入配置库：点卡片切换当前模型；可新建、修改、复制、删除。同一厂商可存多条，每条自己的 Base URL 和 Key。

Key 写入项目 `.env` 的 `BAGU_KEY_<模型id>`，模型列表在 `settings.json`，都不会进 git。保存前必须测试通过，失败不写盘。作答失败时输入框草稿会保留。

下拉预设（新建时预填，可改）：
```

保留原来的供应商表格。删掉「从 Hermes 导入」整段。上文「本地网页：在作答框描述 → 用「模型配置」里的 API 评卷」改成「用当前选用的模型评卷」。

- [ ] **Step 2: 确认文件无 Hermes 导入句**

在 `README.md` 搜索 `Hermes 导入`，应无匹配。

- [ ] **Step 3: Commit** — 跳过（无 git）

---

### Task 7: 全量测试 + 浏览器验收

- [ ] **Step 1: 跑覆盖率**

Run: `python -m pytest test/test_bagu.py --cov=bagu --cov-report=term-missing -v`

Expected: 全部 PASS。若缺行，补最小测试或删死代码（`import_hermes_settings` 必须已删除）。

- [ ] **Step 2: 浏览器走主路径**

1. `python bagu.py serve`，打开 `http://127.0.0.1:8765`
2. 顶栏无「模型配置」Tab；可见「未配置评卷模型」或当前条
3. 点模型条 → 配置库 → 新建 → 测试失败时保存不可用且不写盘；测通后保存
4. 复制出「副本」，当前条不变；点另一张卡片切换；作答页条更新
5. 无模型或故意配错时提交作答失败，文字还在；进配置库再返回，文字还在
6. 修改时改厂商，模型 ID / URL 不被覆盖

- [ ] **Step 3: 把 spec 状态改为已实现**

`docs/superpowers/specs/2026-08-26-model-profiles-design.md` 顶部 `状态：待实现` 改为 `状态：已实现（2026-08-26）`。

---

## 自检

| Spec 条目 | 任务 |
| --- | --- |
| 新 json + `BAGU_KEY_<id>` | Task 1 |
| 旧格式迁移 | Task 1 |
| 测过才保存、test 不写盘 | Task 2–3 |
| copy / activate / delete | Task 2–3 |
| 去掉 POST settings / Hermes | Task 3 |
| 评卷用 active | Task 4 |
| 作答页条、去 Tab、三视图 | Task 5 |
| 草稿 sessionStorage | Task 5 |
| README | Task 6 |
| 浏览器验收 | Task 7 |
