# 八股助手 Android Beta Implementation Plan

> 历史实施与验收记录：保留当时步骤、勾选状态及产物结论，不是当前待执行清单或新的操作授权。移动隔离和发布安全约束仍有效；下文 `DATABASE_VERSION = 1` 已由 v2 替代，完成项不证明后续源码已经进入既有 APK。现行架构见[架构与数据约束](../../architecture.md)，构建说明见 [Android Beta](../../android-beta.md)，各基线/产物证据见[验证记录](../../validation.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. User authorization overrides automatic commits: do not commit or push.

**Goal:** Produce a signed, installable 八股助手 Android APK with a dedicated mobile shell, persistent local data, internal seed questions and portable question/progress backups.

**Architecture:** A Java Android Activity hosts a WebView, with Chaquopy Python running the existing shared core on a token-protected loopback server. A build-time sync packages only explicit source/assets, never the workstation's settings, secrets, progress, or analysis history. Native Storage Access Framework dialogs handle backup and CSV file access.

**Tech Stack:** Java 17, Android API 29–36, AGP 9.0.1, Gradle 9.1.0, Chaquopy 17.0.0/Python 3.11, SQLite, existing vanilla HTML/CSS/JS.

**Spec:** `docs/superpowers/specs/2026-08-27-android-beta-design.md`

## Global Constraints

- Application ID: `io.github.ingnijm.baguhelper`; label: `八股助手`; initial version: `0.1.0-beta.1`, versionCode `1`.
- Android 10 minimum (`minSdk 29`); compile/target SDK `36`; release ABI `arm64-v8a`. Emulator validation may use an additional x86_64 test variant.
- Do not commit, push, discard existing changes, read workstation API keys, or alter the real `bagu.db` for tests.
- No third-party Python dependencies. One `web/index.html`; shared quiz, grading and scheduling rules.
- HTTP binds only `127.0.0.1`. Android mode requires an unguessable access token for the HTML entry and all APIs. Query tokens must never appear in logs.
- Android model URLs must be HTTPS. API keys only in app-private `.env`, never backups or GET responses.
- Seed only on first install. Application updates preserve all existing questions, progress, sessions and saved analysis.
- Backup `.bagu-backup` contains only `manifest.json` and `questions.json`, with schema version `1`, SHA-256, strict validation and atomic merge.
- Backup conflicts match `category + question`; backup wins for content/progress, new rows are added, target-only rows and analysis history remain.
- Mobile touch targets ≥44px, top overview, no sidebar squeezing main actions, keyboard/insets/rotation support.
- Release signing key must be stable, local, git-ignored and absent from the APK.

## File responsibilities

- `bagu.py`: injectable runtime paths, migration version, safe static routing, token-aware handler, seed/backup primitives and APIs.
- `test/test_bagu.py`: all shared-core and transport tests using temporary directories.
- `android/`: Android project and native host; `app/src/main/python/android_runtime.py` owns process startup and native backup calls.
- `web/index.html`: shared business logic plus Android-specific shell and native storage adapter.
- `scripts/build_android_seed.py`: read-only export from local source DB into a clean generated seed.
- `scripts/android.ps1`: project-local environment, signing setup, repeatable build/verification entrypoints.
- `scripts/verify_android_apk.py`: pure-stdlib APK/recursive archive/ELF/seed verification used by the PowerShell entrypoint; not an app dependency.
- `test/test_android_project.py`: packaging/configuration contracts; no real DB mutations.
- `docs/android-beta.md`: install/update/backup and reproducible build instructions, limitations.

## Setup (controller-owned)

- [x] Create `codex/android-beta` in place with explicit user approval; preserve dirty baseline.
- [x] Add ignored toolchain/signing directories; baseline `python -m pytest test/test_bagu.py -q`: 140 passed.
- [x] Download official SDK command-line tools, Gradle 9.1.0, SDK platform 36/build-tools 36.0.0/platform-tools/emulator and API 29/36 x86_64 system images under ignored project directories. Use installed JDK 17.0.10. No global environment edits.
- [x] Record tool URLs/checksums and command results. Use a single task-scoped progress ledger. Review diffs against file snapshots because user prohibited commits.

### Task 1: Injectable Android runtime and protected HTTP

**Files:** Modify `bagu.py`, `test/test_bagu.py`.

**Interfaces:**
- Preserve existing positional callers of `handle_http` and `make_http_handler`.
- Produce `AppPaths(data_dir, config_dir, static_dir, log_dir)` with `db_path` property; paths are `pathlib.Path` values.
- Extend `handle_http(method, path, body, conn, root=None, *, static_root=None, android=False)`.
- Extend `make_http_handler(root=None, stream_fn=None, *, db_path=None, static_root=None, access_token=None, android=False)`.
- Android page URLs use `/?platform=android&token=<token>`; API requests use header `X-Bagu-Token`. Static brand/font assets are non-sensitive and may be served without auth.
- Produce `DATABASE_VERSION = 1`; `init_db` refuses a future schema version and advances legacy version 0 transactionally, preserving existing behavior.

- [x] Add failing tests for independent db/config/static roots, `/api/*` and HTML auth, invalid token, unauthorized SSE, query-token log redaction, maximum request size and static traversal.

```python
def test_android_paths_do_not_touch_desktop_data(tmp_path):
    paths = bagu.AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "static", tmp_path / "logs")
    assert paths.db_path == tmp_path / "data" / "bagu.db"

def test_future_schema_is_not_downgraded(conn):
    conn.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="版本"):
        bagu.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 999
```

- [x] Run focused tests and observe failures; implement dataclass, path injection in normal/SSE requests, allowlisted static files rooted at `static_root`, auth before parsing or database access, and request-size rejection (32 MiB maximum).

```python
if self._access_token and not secrets.compare_digest(provided_token, self._access_token):
    self._write(403, {"error": "未授权请求"}, "application/json")
    return
```

- [x] In Android mode reject model creation/test/update/activation or judging when the effective endpoint is not HTTPS; preserve desktop custom HTTP use. Keep original request IDs, logging redaction and session logic.
- [x] Make schema additions atomic and idempotent; do not rewrite user question/progress fields. Do not move existing public functions or change CLI protocol.
- [x] Run focused tests then full `test/test_bagu.py`; save RED/GREEN evidence and exact modified-file list to task report. Do not commit.

### Task 2: First-install seed and portable transactional backups

**Files:** Modify `bagu.py`, `test/test_bagu.py`; create `scripts/build_android_seed.py`.

**Interfaces:**
- Consume `init_db`, `_clean_question`, `get_open_session`, `AppPaths` from shared core.
- Produce `export_backup(conn, app_version="0.1.0-beta.1") -> bytes`.
- Produce `parse_backup(data: bytes) -> list[dict]` and `restore_backup(conn, data: bytes) -> dict` with `added`, `updated`, `total` counts.
- Produce `create_seed_database(source_path: Path, destination_path: Path) -> int` using read-only SQLite source connection.
- Produce `prepare_mobile_database(db_path: Path, seed_path: Path | None = None) -> None`; seed is copied atomically only when destination does not exist.
- HTTP GET `/api/backup/export` returns ZIP bytes; POST `/api/backup/restore` accepts `{"archive_base64":"..."}` and returns counts. Errors are 400; active session is 409.

- [x] Add failing tests with real temporary SQLite data and ZIP bytes.

```python
def test_backup_round_trip_excludes_analysis(conn, tmp_path):
    conn.execute("INSERT INTO questions(category,question,answer,url) VALUES(?,?,?,?)", ("A", "题", "答案", ""))
    conn.commit()
    sid, questions = bagu.draw(conn, 1)
    bagu.grade(conn, sid, questions[0]["id"], "good")
    conn.execute("UPDATE session_items SET result_comment=? WHERE session_id=?", ("PRIVATE_ANALYSIS", sid))
    conn.commit()
    payload = bagu.export_backup(conn)
    restored = bagu.parse_backup(payload)
    assert set(restored[0]) == {"category", "question", "answer", "url", "level", "times_seen", "times_right", "next_due", "last_reviewed"}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        exported_text = archive.read("questions.json").decode("utf-8")
    assert "PRIVATE_ANALYSIS" not in exported_text
    assert "result_comment" not in exported_text
```

- [x] Observe failure before implementation. Define ZIP members exactly as in the spec; serialize deterministic UTF-8 JSON, hash `questions.json`, include UTC timestamp and count in the manifest.
- [x] Enforce 20 MiB compressed, 50 MiB uncompressed, ≤10000 questions; refuse duplicate/extra ZIP entries, encryption, traversal, unknown schema, mismatched hashes/counts, duplicate category/question, bool-as-int, impossible progress (`times_right > times_seen`), invalid level/date/text sizes. Reuse `_clean_question` for content validation.
- [x] Restore parses fully before `BEGIN IMMEDIATE`, rejects an open session without closing it, merges by category/question, never deletes target-only rows or saved session analysis. Roll back on any failure.
- [x] Build seed through `sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)`, insert only question content into a new initialized DB, no source writes, no settings/keys/sessions, zero all progress. Test source byte-for-byte unchanged and existing mobile data unchanged after repeated startup.
- [x] CLI script supports `--source`, `--output`, `--empty`; refuses overwriting the source and writes only the generated output. Public build uses `--empty`.
- [x] Focused and full core tests pass; report TDD evidence and no commits.

### Task 3: Android host, build graph and native file bridge

**Files:** Create Android project under `android/`, `test/test_android_project.py`; controller may supply wrapper binaries/toolchain.

**Interfaces:**
- Consume Task 1 handler arguments and Task 2 seed/backup functions exactly.
- Python `android_runtime.start(files_dir: str, static_root: str, seed_path: str, variant: str) -> str` returns JSON `{url, port}`; owns a process singleton server and token, config/data/log directories.
- Python `android_runtime.export_archive() -> bytes`, `restore_archive(data) -> str` return backup data/JSON counts. Startup installs a redirect policy preventing HTTPS→HTTP downgrade.
- Native JS bridge name `BaguNative`; methods `getItem(String)`, `setItem(String,String)`, `removeItem(String)`, `keys() -> JSON array`, `exportBackup()`, `importBackup()`, `saveCsvTemplate(String)`; storage only accepts `bagu-` keys and bounded values.
- `BaguNative.getAppInfo()` returns only public build metadata (`name`, `packageName`, `versionName`, `versionCode`, `flavor`). The startup URL uses exact `platform=android`; settings must render real native metadata rather than a stale hardcoded future version.
- Native backup result callback: `window.dispatchEvent(new CustomEvent('bagu-native-result', {detail: {operation:'export'|'import'|'template', status:'ok'|'cancelled'|'error', message, added?, updated?}}))`; safe JSON encoding, no raw script interpolation.
- Native back dispatch calls `window.baguHandleBack()` when available. The function returns true when the shared UI handled a dialog/nested view, false at the practice root; native code then performs normal Activity back behavior.
- Preserve HTTPS answer images. The Android runtime adds a restrictive CSP response header blocking frames/objects/remote scripts/connections and a no-referrer header; existing inline app code/styles, local fonts/API and HTTPS images remain allowed. Do not replace answer images with a browser-only workflow or add an image proxy.

- [x] Add config/packaging contract tests first: exact min/target/package, no broad storage permission, `allowBackup=false`, external launch blocked, required bridge methods, generated Python/static sync declared. Run to see failures.
- [x] Add behavioral tests for Python runtime startup/token/path isolation and redirect policy using temporary directories/local HTTP only; add focused Java unit tests for pure navigation/storage validation helpers. Test tasks must execute assertions, not merely succeed with no tests.
- [x] Create Gradle wrapper/project (Groovy DSL, Java 17, AGP/Chaquopy versions in global constraints). `internal` flavor packages generated seed; `public` packages empty seed; same app ID and signing, versionCode overridable by project property.
- [x] Sync only root `bagu.py`, runtime Python, `web/index.html`, brand assets and bundled local UI assets into generated directories. Never add the repo root as an unrestricted Python/assets source directory. Task dependencies must guarantee sync precedes Python compilation/assets merge.

```groovy
android {
    namespace 'io.github.ingnijm.baguhelper'
    compileSdk 36
    defaultConfig {
        applicationId 'io.github.ingnijm.baguhelper'
        minSdk 29
        targetSdk 36
        versionCode providers.gradleProperty('baguVersionCode').getOrElse('1').toInteger()
        versionName providers.gradleProperty('baguVersionName').getOrElse('0.1.0-beta.1')
    }
}
```

- [x] Java Activity: initialize Python off the UI thread; show branded progress/error with retry; load local URL inside WebView only; consume window/IME Insets; allow resizing/rotation; support API 29 and API 33+ back; never suppress TLS errors or enable mixed content/file access. Add native adaptive icon and Android 12 splash theme.
- [x] App-private SharedPreferences backs `bagu-` storage so random server ports do not lose drafts/mode/submission state across app restarts. No key/config export.
- [x] SAF file chooser supports existing CSV input, backup open/create and CSV template save. Bound input sizes while streaming, close streams, handle cancellation/rotation and do backup work off main thread. No all-files/storage permissions.
- [x] HTTPS-only model transport and loopback cleartext exception; prevent unsolicited external navigation/new windows and only open explicit HTTP(S) reference clicks via ACTION_VIEW.
- [x] Run project contract tests, Gradle unit tests, lint and debug build with local toolchain; report exact output or concrete missing environment, not assumed success. Do not commit.

### Task 4: Dedicated Android mobile shell, offline UI assets and native persistence

**Files:** Modify `web/index.html`, `test/test_bagu.py`, `test/test_android_project.py`; optional create explicit local SVG/font assets under `assets/` with licenses.

**Interfaces:**
- Android detected from `platform=android` and trusted `BaguNative` bridge; desktop remains default.
- `BaguNative.getAppInfo()` supplies the real version/internal-public variant for the Android settings notice; no private settings or keys cross this bridge.
- `X-Bagu-Token` attached by both `api` and `streamAnswer`; token only read from startup URL, never persisted or displayed.
- Shared `appStorage` uses `BaguNative` storage when present, browser `localStorage` otherwise. Replace all draft/submission/study-mode accesses with this adapter.
- Use existing `showView`, quiz/question/model handlers; new views `overview` and `settings` route through the same view switcher. No duplicate question/grading state machine.
- Expose synchronous `window.baguHandleBack()` for the native host: close transient UI or return from nested views, return false only at the practice root.
- Package the cached official fonts as `assets/fonts/PlusJakartaSans.ttf`, `FiraCode.ttf`, `MaterialSymbolsRounded.ttf`, with notices `PlusJakartaSans-OFL.txt`, `FiraCode-OFL.txt`, `MaterialSymbolsRounded-APACHE-2.0.txt`; these exact names form the native asset allowlist.

- [x] Add JS/HTML contract tests and run RED; verify API headers, storage selection, navigation/backup event wiring and zero external font/icon dependency for Android.

```javascript
const isAndroidApp = new URLSearchParams(location.search).get('platform') === 'android';
const nativeStore = isAndroidApp && window.BaguNative;
const appStorage = nativeStore ? {
  getItem: key => nativeStore.getItem(key),
  setItem: (key, value) => nativeStore.setItem(key, String(value)),
  removeItem: key => nativeStore.removeItem(key),
  key: index => JSON.parse(nativeStore.keys())[index] || null,
  get length() { return JSON.parse(nativeStore.keys()).length; }
} : localStorage;
```

- [x] Add scoped `body.android-app` layout: compact top brand, bottom 4-item nav (练习/题库/概览/设置), top stats, main question full width, model card in settings, category mastery in overview. Reuse existing DOM where possible; desktop Bento unchanged.
- [x] Android settings include backup export/restore with clear scope, open-session warning, disabled busy state, cancellation/error/summary messages, app version/internal notice. Empty bank shows CSV import/add question guidance.
- [x] Use local SVG icons or packaged font resources so offline startup has no textual icon names; retain current cream/navy/yellow/lavender style. No new generated logo.
- [x] Mobile CSS: min-width:0, overflow wrapping, min touch44px, inputs≥16px, safe-area padding, no fixed dialog wider than viewport, code/table overflow only inside their containers; keyboard does not obscure primary input. Respect reduced motion.
- [x] Android 10 WebView compatibility: replace optional-chaining syntax with explicit null guards and use `crypto.getRandomValues` UUID-v4 fallback when `crypto.randomUUID` is unavailable; test the fallback produces the accepted `sub_<UUID>` format. Do not lower the UUID entropy or use Math.random.
- [x] Set a no-referrer policy before any external resources so older WebViews do not send the startup token in Referer headers when loading reference images or following links.
- [x] Replace or provide a FileReader fallback for File.text() in CSV import (absent in WebView 74). Support old Flexbox gap behavior using margins/Grid or real layout detection; CSS supports(gap) alone cannot distinguish Grid support from Flex support. Add basic focus fallback.
- [x] Test 320×640, 360×800, 412×915, 800×1280, 915×412, 840×900 through approved browser tool; check each nav/view and loaded image dimensions. Test bridge behavior with a controlled local test fixture, not production user data.
- [x] Full Python suite/project contracts and JS parse checks pass; report screenshots/measurements and no commits.

### Task 5: Reproducible signing, build and release packaging

**Files:** Create `scripts/android.ps1`, `scripts/verify_android_apk.py`, `docs/android-beta.md`; modify `README.md`, `test/test_android_project.py` as needed.

**Interfaces:**
- Script modes `SetupSigning`, `Build`, `Verify`; use only project-local toolchain/cache environment and installed JDK17.
- `.signing/release.jks` and `.signing/keystore.properties` are generated once, ignored; do not overwrite/recreate valid existing key. Passwords generated cryptographically, never printed or passed as literal process arguments.
- Gradle reads ignored signing properties or environment variables; release build fails when signing data is missing instead of silently signing debug.
- Output `dist/android/八股助手-0.1.0-beta.1-arm64-v8a.apk` plus SHA256SUMS, certificate fingerprint and install notes.

- [x] Tests check ignore rules, signing-failure behavior, flavor data separation and packaging allowlist. Generate signing key with `keytool -storepass:env ... -keypass:env ...`, capture only public certificate info.
- [x] Run `:app:assembleInternalRelease`, unit tests and lint. Use `apksigner verify --verbose --print-certs`, `aapt dump badging`, `zipalign -c -P 16 4` on the exact APK.
- [x] Inspect all APK ZIP entries for expected Python/native assets and absence of `.env`, settings.json, signing materials, workstation DB/session history. Inspect seed DB separately: all source questions present, progress zero, sessions/results empty.
- [x] Build public variant and inspect that no Xiaolin seed data is included; do not publish it or call it a final public release.
- [x] Write exact install/update/backup steps and signer backup warning; explain uninstall wipes app data and migration is through `.bagu-backup`. Do not publish to GitHub without separate authorization.

### Task 6: Android runtime, lifecycle and upgrade acceptance

**Files:** Add instrumentation tests under `android/app/src/androidTest/` and minimal test-only Gradle configuration, generated QA reports under ignored `dist/android/qa/`; modify scoped code only for reproduced defects; update the existing `docs/android-beta.md` with final verified usage/limitations.

- [x] Boot local API29/API36 x86_64 AVDs without visible helper windows, using project-local AVD/cache paths. Install test-compatible APK, launch explicit Activity, inspect logcat and UI hierarchy/screenshots.
- [x] Exercise first-run seed, offline memorize/review, CSV import, model configuration validation, backup export to SAF and import, damaged backup rejection, keyboard, rotation, bottom navigation, 320px/foldable/tablet dimensions.
- [x] Use synthetic HTTPS model configuration and instrumentation-only in-process transport/model stubs (restored after each test), or a trusted HTTPS fixture, for deterministic model error tests. The app's HTTP loopback server is not an exception to the HTTPS-only model policy. No workstation credentials, paid calls, TLS bypass, device CA installation or release test hooks.
- [x] Verify persistence after process kill/relaunch (including draft/submission native storage), and APK replacement install with higher versionCode: exact database question/progress/analysis rows and config remain unchanged. Cover internalRelease1 to publicRelease2 on API36 (empty public seed must not clear beta data) and a same-flavor update on API29; QA version2 must not replace the named delivery APK.
- [x] Validate the release ABI/signature against a connected arm64 device if available. If none exists, report the gap accurately; do not claim physical-device compatibility beyond evidence.
- [x] Final whole-change code review, full fresh tests/lint/release build and artifact checks. Record device/OS matrix and remaining limitations. Deliver APK only when actual build/install/start evidence exists; do not mark overall goal complete on source-only or configuration-only evidence. Closed by final-fix1-verdict.md plus root's293-case suite, exact2c3 APK verification/fresh API36 install/start, and preserved QA3 state; physical/network gaps remain explicitly documented.
- [x] Final user-facing Android guide must state Android10+/arm64 support, direct on-phone APK installation (ADB optional), app-local startup without a PC/browser service, and offline study versus network-dependent AI/images. Clearly state `.bagu-backup` contains questions/progress only, excluding analysis/model configuration/API keys/drafts; replacement updates preserve all private data, but uninstall deletes it. Replace the stale not-yet-device-tested paragraph with actual evidence and honest remaining gaps; no GitHub publishing.
