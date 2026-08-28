# 数据迁移、Android 更新与 GitHub 本地发布

用户已确认 2026-08-28 完整方案。本文件记录实施契约和验证进度，不代表已经公开发布。

## Global constraints

- MIT 仅授权应用源码；public 空题库 APK；个人题库、进度、配置、Key、签名材料禁止提交或上传。
- 不自动提交、推送、公开 Release 或改仓库可见性。公开发布须另行确认仓库、版本和附件。
- 不新增 Python 依赖，不增加第二个 HTML，不放宽 loopback/token/CSP/WebView/文件边界。
- 保留既有 SQLite v2、评分和语音功能。备份格式 v2 与数据库版本无关。
- 版本文件 `version.json`: `versionName`, `versionCode`, `channel`；候选 `0.1.0-beta.2`, `2`, `beta`。
- 更新 envelope: `{schema_version:1, channel, release}`；release 可以 null，否则字段固定为 `versionName`, `versionCode`, `distribution` (public), `packageName`, `minSdk`, `abi` (arm64-v8a), `apkUrl`, `size`, `sha256`, `releaseUrl`, `publishedAt`, `notes`。
- APK 目标 `io.github.ingnijm.baguhelper`；信任证书 SHA256 `ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`。
- feed 固定 `https://ingnijm.github.io/AI-Bagu/updates/{beta|stable}.json`，APK 固定仓库 `InGnIJM/AI-Bagu` 的具体 tag Release 下载路径。

## Task 1: 双端数据迁移

Ownership: bagu.py, web/index.html (migration only), android_runtime.py, RuntimeHost.java, MainActivity.java (document handling only), NativeBridge.java (exportQuestionBank only), .gitignore, migration tests.

TDD implement schema v2 modes questions/progress; v1 reads as progress. Pure mode omits all schedule fields. Exact ZIP members manifest.json/questions.json. Retain 10000 questions,20MiB compressed,50MiB unpacked limits, checksums, duplicate/path/encryption/field/type checks. parse_backup retains list return. export_backup(conn, app_version=None, mode="progress") defaults progress, actual desktop version from version.json and Android injected via runtime. inspect_backup full validation read-only returns mode, question_count, created_at, app_version, schema_version. restore matches category+question; pure overwrites answer/url including empty, preserves existing progress; new pure defaults zero/null; progress overwrites schedule unchanged. Keep local-only rows, sessions/history. Open-session check inside BEGIN IMMEDIATE, exceptions rollback; result shape unchanged.

HTTP export query mode defaults progress (invalid/duplicate/empty rejected400); POST inspect/restore archive_base64 exact validated. Invalid400; active session restore409. UI desktop settings nav + empty-bank import guide; 3 actions export questions/export progress/import. Blob binary download; bounded FileReader byte->base64 without giant spreads; preview and confirm use same bytes. Native SAF worker full validation then native dialog confirmation same byte[]; preserve exportBackup; add exportQuestionBank. Do not transmit file to JS. Activity recreation never implicitly confirms; process death never replays. Unknown restore completion asks user verify. Busy/cancel/failure states; import blocked open session; export allowed. Synthetic tests only, real *.bagu-backup ignored. Run tests before and after; no commits, subagents, personal DB reads or signed builds.

## Task 2: 版本与发布基础

MIT, version.json consumed by Gradle/scripts/runtime; public-only default build, explicit internal build separated; per-version outputs. Preserve APK allowlist, empty seed, stable cert, native ELF/alignment checks. Never generate/change signing identity. Metadata checked from actual APK not only config. Fixed release allowlist APK, SHA256SUMS, certificate-sha256.txt, update.json, INSTALL.md, RELEASE_NOTES.md.

## Task 3: Android 更新

Own executor/native controller + policy + dedicated read-only APK provider, REQUEST_INSTALL_PACKAGES only new permission. Automatic check default on, foreground/page-ready, 24h since attempt; manual bypass. Stable reads stable; beta reads both selects greatest compatible integer code. Partial errors never latest. UI toggle/version/status/check and dismissible available notice. Native events bagu-update op IDs stale ignored. Candidate ID only, no arbitrary URL/path APIs. Plaintext notes. User download only, progress/cancel; stream .part <=128MiB, feed <=64KiB, finite HTTPS redirects allowlisted github.com/release-assets.githubusercontent.com. Full size/hash/package/code/name/minSdk/ABI/archive cert check. Cache one candidate, reverify restart and install; no downgrade/same version. Install source permission explicit; temporary URI read grant; no install during open session, grading, speech or file work. Never auto-end session; installed success only next boot actualversion. No silent/background install or resume promises.

## Task 4: GitHub 发布

Local CLI default dry-run; preflight clean committedsource, correctorigin/version/license/allowlist/validation, never auto commit or auth. Explicit prepare generates verified public artifact; publish draft -> upload allowlist -> verify remote bytes -> publish -> anonymous verify -> update channel feed branch codex/update-feed without changing currentcheckout -> verify livePages. beta prerelease. Never overwrite conflicting tag/asset, forcepush or delete Release. Samecommit+hash may resume, partial published/feedfailed reported and feed retry available. Separate stable/beta feeds preserve other. nullrelease for absent channel. gh installed+userloggedin prerequisite; no credentials bundled or written to project.

## Task 5: 验收与文档

Full pytest/Node/Java/lint/publicbuild, synthetic data and mocknetwork. Isolated API29/API36 transfer and two-version real-install tests; do not touch userdevice data. Separate QA output, never upload simulation. README/AGENTS/android-beta actualbehavior/commands/recovery. Report test/build/device/Release/Pages statuses separately; gated external work remains pending until authorized.
