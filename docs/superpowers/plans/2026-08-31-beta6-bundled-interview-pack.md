# beta.6 Bundled Interview Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship beta.6 with the formal r1 interview pack embedded as a native-confirmed Android Asset while retaining the identical standalone Release attachment and an empty public seed.

**Architecture:** Descriptor schema v2 is the single build-time authority for bundled delivery. Gradle copies one explicitly supplied pack to a fixed Asset, the APK verifier validates it against the descriptor and runtime validator, and a focused native controller reuses the existing inspect/confirm/install pipeline without exposing content to JavaScript. The web layer only exposes a capability-gated settings button.

**Tech Stack:** Python 3 standard library and pytest, PowerShell, Gradle/Android Java, single-file HTML/JavaScript, SQLite runtime validator, GitHub CLI release tooling.

**Spec:** `docs/superpowers/specs/2026-08-31-beta6-bundled-interview-pack-design.md`

## Global Constraints

- Version is exactly `0.1.0-beta.6`, code 6, channel beta; beta.5 and Stable feed remain byte-for-byte unchanged.
- Formal pack identity is `autumn-recruit-interviews-2026`, revision 1, display version `2026.08.30-r1`, 748 questions, 27 experiences, SHA-256 `47aa6b28768be85322924df4a7c17199bf248660997cd10247066821d6d23864`.
- Fixed APK member is `assets/question-pack/bundled.bagu-pack`; public seed remains empty and internal builds reject bundled delivery.
- No third-party Python dependency, HTTP/Python install protocol, permission, Manifest, Hermes grade, online store or independent pack-update change.
- Never commit or print the formal pack source path, pack bytes, source interviews, private catalog, stable-ID mapping, signing material, databases or generated release directory.
- Never issue ADB install/clear commands to a physical or vivo device; disposable API 29/API 36 emulator serials must be explicitly verified first.

---

### Task 1: Versioned question-pack descriptor protocol

**Files:**
- Modify: `scripts/release_metadata.py`
- Modify: `test/test_question_pack_release.py`
- Modify: `test/test_github_release.py`

**Interfaces:**
- Consumes: `version.json`, the existing canonical JSON reader and schema-v1 descriptor.
- Produces: a parsed descriptor object supporting exact schema-v1 fields or schema-v2 fields ending in `android_delivery="bundled_confirm"`; helper properties used by build and verification code.

- [ ] **Step 1: Write failing schema tests** for exact v2 acceptance plus rejection of duplicate keys, unknown/reordered fields, missing/wrong delivery, wrong version and schema-v1 embedded-delivery attempts.
- [ ] **Step 2: Run the focused tests and record RED.** Run `python -m pytest test/test_question_pack_release.py test/test_github_release.py -q`; expected failures must point to schema-v2 being unsupported.
- [ ] **Step 3: Implement the smallest versioned parser.** Preserve the exact nine-field v1 tuple; add a ten-field v2 tuple whose final field is `android_delivery`; compare `list(raw)` to the selected tuple; require integer schema 1 or 2 and the only v2 delivery value `bundled_confirm`.
- [ ] **Step 4: Make generated metadata delivery-aware.** Expose `external_only` for v1 and `bundled_confirm` for v2 without changing the seven external Release attachments or APK-only feed.
- [ ] **Step 5: Run focused tests and record GREEN.** Run the same command and require zero failures.
- [ ] **Step 6: Commit.** Stage only the three task files and commit `feat(release): add bundled pack descriptor v2`.

### Task 2: Fixed APK Asset and strict verifier

**Files:**
- Modify: `android/app/build.gradle`
- Modify: `scripts/android.ps1`
- Modify: `scripts/verify_android_apk.py`
- Modify: `scripts/release_metadata.py`
- Modify: `test/test_android_project.py`
- Modify: `test/test_question_pack_release.py`

**Interfaces:**
- Consumes: Task 1 descriptor delivery mode and `-QuestionPack` path after identity/hash validation.
- Produces: a generated public Asset directory containing only `question-pack/bundled.bagu-pack`; verifier arguments binding descriptor bytes to the APK; `verification.json` fields `android_delivery`, `bundled_pack_member`, and `bundled_pack_sha256`.

- [ ] **Step 1: Write failing packaging/verifier tests** for missing fixed member, extra/nested/wrong-path pack, wrong hash, malformed pack, schema-v1 pack, internal pack, pack-owned seed and a valid synthetic schema-v2 APK.
- [ ] **Step 2: Run focused tests and record RED.** Run `python -m pytest test/test_android_project.py test/test_question_pack_release.py -q`; expected failures must demonstrate the current all-path rejection and absent Asset generation.
- [ ] **Step 3: Add a generated Asset source set.** Read only a Gradle property produced by `android.ps1`, copy bytes once into the variant-specific generated directory as `question-pack/bundled.bagu-pack`, fail public release assembly when schema v2 lacks the property, and fail internal builds whenever the property is supplied.
- [ ] **Step 4: Harden input binding in PowerShell.** Before Gradle, validate descriptor identity/hash/counts, snapshot size/hash before and after copy, pass only the generated copy path, and leave `-Mode Check` pack-independent.
- [ ] **Step 5: Make APK verification descriptor-aware.** For v1/absent descriptor reject every `.bagu-pack`; for v2 public require exactly the fixed member, compare its SHA-256, validate its manifest through the runtime validator, and retain empty-seed/security/ABI/signature checks.
- [ ] **Step 6: Record only non-sensitive receipt fields.** Store the delivery value, fixed member and hash; assert the source absolute path never appears.
- [ ] **Step 7: Run focused tests and record GREEN.** Require zero failures from the Step 2 command and `python scripts/verify_android_apk.py --help`.
- [ ] **Step 8: Commit.** Stage only the six task files and commit `feat(android): bundle descriptor-bound interview pack`.

### Task 3: Native bundled-pack controller and lifecycle state machine

**Files:**
- Create: `android/app/src/main/java/io/github/ingnijm/baguhelper/BundledPackController.java`
- Create: `android/app/src/test/java/io/github/ingnijm/baguhelper/BundledPackControllerTest.java`
- Modify: `android/app/src/main/java/io/github/ingnijm/baguhelper/PendingImport.java`
- Modify: `android/app/src/test/java/io/github/ingnijm/baguhelper/PendingImportTest.java`
- Modify: `android/app/src/main/java/io/github/ingnijm/baguhelper/NativeBridge.java`
- Modify: `android/app/src/main/java/io/github/ingnijm/baguhelper/MainActivity.java`
- Modify: `android/app/src/test/java/io/github/ingnijm/baguhelper/NativeOperationArbiterTest.java`
- Modify: `android/app/src/androidTest/java/io/github/ingnijm/baguhelper/AndroidAcceptanceTest.java`

**Interfaces:**
- Consumes: fixed Asset path, `RuntimeHost.inspectInterviewPack(byte[])`, `RuntimeHost.installInterviewPack(byte[])`, existing native operation lease and `PendingImport` preview allowlist.
- Produces: `hasBundledInterviewPack(): boolean`, `importBundledInterviewPack(): void`, source enum values `EXTERNAL_FILE`, `BUNDLED_AUTO_PROMPT`, `BUNDLED_SETTINGS`, and a state-policy result distinguishing confirmable/read-only/busy/error previews.

- [ ] **Step 1: Write failing pure-Java policy tests** for one prompt per hash, new-hash prompting, manual reopen, new/upgrade/installed/downgrade/conflict, 20 MiB limit, SHA memory, open-session/busy gates, lease release and content never entering result payloads.
- [ ] **Step 2: Run Java tests and record RED.** Run the focused Gradle unit tests; failures must be missing controller/state APIs rather than fixture errors.
- [ ] **Step 3: Implement `BundledPackController`.** Inject byte source, inspector, preference store and lease gateway for unit testing; hash exactly the retained bytes; write the prompted hash immediately before an automatic preview becomes visible; return safe status codes only.
- [ ] **Step 4: Extend `PendingImport` and the confirmation dialog.** Retain source across `HostState`; make downgrade/conflict previews read-only; preserve existing clone/allowlist semantics and `operation="pack-import"`.
- [ ] **Step 5: Wire lifecycle and bridge.** Auto-check only after trusted page load and foreground/idle gates; config recreation re-shows the retained object; saved-instance/process restart never serializes or restores bytes; manual bridge bypasses prompted-hash suppression.
- [ ] **Step 6: Add instrumentation assertions** for capability, no implicit install on rotation/restart, no raw question/answer output, and source/status-only event payloads.
- [ ] **Step 7: Run focused Java tests and record GREEN.** Require zero failures from the focused Gradle unit-test command and successful androidTest compilation.
- [ ] **Step 8: Commit.** Stage only the native/controller/test files and commit `feat(android): confirm bundled pack natively`.

### Task 4: Capability-gated settings entry

**Files:**
- Modify: `web/index.html`
- Modify: `test/test_interview_pack_web.py`
- Modify: `test/test_android_project.py`

**Interfaces:**
- Consumes: optional native bridge methods from Task 3 and existing `pack-import` native result handler.
- Produces: hidden-by-default `安装内置题包` button that invokes the native method once per click and reuses existing busy/session/result refresh behavior.

- [ ] **Step 1: Write failing web tests** for desktop/old-host/internal hidden state, Android capability visible state, one call per click, busy and open-session blocking, and successful pack/experience refresh.
- [ ] **Step 2: Run focused tests and record RED.** Run `python -m pytest test/test_interview_pack_web.py test/test_android_project.py -q`; failures must show the missing settings entry/bridge use.
- [ ] **Step 3: Add minimal markup and capability probing.** Keep the button hidden in HTML; reveal only when platform is Android and the method exists and returns true; treat exceptions as unavailable.
- [ ] **Step 4: Reuse current operation guards/results.** Disable during an operation, refuse an open session before calling native, call `importBundledInterviewPack()` once, and let the existing `pack-import` completion refresh packs and experiences.
- [ ] **Step 5: Run focused pytest and Node tests and record GREEN.** Run the Step 2 command and `node --test test/speech_input.test.cjs`; require zero failures.
- [ ] **Step 6: Commit.** Stage only the three task files and commit `feat(web): expose bundled pack settings action`.

### Task 5: beta.6 public metadata, docs and release contract

**Files:**
- Modify: `version.json`
- Create: `docs/releases/0.1.0-beta.6-question-pack.json`
- Create: `docs/releases/0.1.0-beta.6.md`
- Modify: `README.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/android-beta.md`
- Modify: `docs/data-transfer-and-updates.md`
- Modify: `docs/validation.md`
- Modify: `test/test_release.py`
- Modify: `test/test_github_release.py`

**Interfaces:**
- Consumes: schema-v2 contract and generated verification receipt.
- Produces: exact beta.6 version/descriptor; installation/release text explaining explicit confirmation, AI-generated reference answers and desktop standalone import; exact seven-attachment expectation.

- [ ] **Step 1: Write failing release-contract tests** for version code/name/channel, canonical descriptor bytes, seven assets, APK-only feed, Stable non-mutation and historical beta.5 external-only wording.
- [ ] **Step 2: Run focused tests and record RED.** Run `python -m pytest test/test_release.py test/test_github_release.py test/test_question_pack_release.py -q`.
- [ ] **Step 3: Add exact beta.6 metadata.** Write schema-v2 fields in specified order; bump only `version.json`; retain beta.5 descriptor and history unchanged.
- [ ] **Step 4: Update current documentation.** State public Android includes an explicit-confirm Asset while the SQLite seed is empty; document cancel suppression and settings reopen; keep desktop standalone import and beta.5 history accurate.
- [ ] **Step 5: Run focused tests and record GREEN.** Require zero failures from the Step 2 command.
- [ ] **Step 6: Commit.** Stage only version, beta.6 public docs, current docs and release tests; commit `docs(release): prepare beta6 bundled pack`.

### Task 6: Disposable dual-emulator acceptance gate

**Files:**
- Create: `scripts/test_bundled_pack_avd.ps1`
- Create: `android/app/src/androidTest/java/io/github/ingnijm/baguhelper/BundledPackAcceptanceTest.java`
- Modify: `test/test_android_project.py`
- Modify: `docs/development.md`

**Interfaces:**
- Consumes: signed/public test APK, formal pack identity/counts, installed beta.5 APK path when exercising upgrade, local API 29/API 36 x86_64 images.
- Produces: a deterministic PowerShell gate that creates isolated AVD homes, launches one emulator at a time, verifies emulator-only identity/serial, runs scenarios without logging content, then stops the emulator; a machine-readable status file outside Git.

- [ ] **Step 1: Write failing static/policy tests** requiring unique AVD names, isolated `ANDROID_AVD_HOME`, exact serial binding, rejection of any `model`/manufacturer matching vivo/V2309A, and no unqualified `adb` mutation command.
- [ ] **Step 2: Run focused tests and record RED.** Run `python -m pytest test/test_android_project.py -q`.
- [ ] **Step 3: Implement the gated runner.** Accept explicit APK paths; validate system image availability; create temporary homes; capture the launched serial; assert `ro.kernel.qemu=1`, target API and non-vivo identity before install/clear; always stop only that serial in `finally`.
- [ ] **Step 4: Implement content-free instrumentation scenarios.** Assert pack ID/revision, question/experience counts and DB table counts only; cover cancel/no repeat, settings reopen/install, rotation, process kill, open session, operation contention and beta.5 upgrade with/without preinstalled pack.
- [ ] **Step 5: Run static tests and compile androidTest.** Require Step 2 GREEN and `:app:compilePublicDebugAndroidTestJavaWithJavac` success.
- [ ] **Step 6: Commit.** Stage only the runner, instrumentation test, static tests and development docs; commit `test(android): gate bundled pack on isolated avds`.

### Task 7: Full verification, signed build and public beta.6 release

**Files:**
- Generated outside Git only: signed public ARM64 APK, standalone `.bagu-pack`, release metadata, simulator evidence.
- No source edits are permitted after the final verification commit without restarting affected gates.

**Interfaces:**
- Consumes: clean pushed release branch, exact formal pack bytes, existing signing identity, GitHub CLI authentication and prepared update-feed branch.
- Produces: immutable `v0.1.0-beta.6` prerelease with exactly seven external assets, Beta feed code 6, unchanged Stable feed and anonymous redownload verification.

- [ ] **Step 1: Run source validation.** Require `python -m pytest test -q`, Node tests, Java unit tests, public release lint, androidTest compilation, `git diff --check`, private-path/secret/generated-artifact scans and zero failures.
- [ ] **Step 2: Build and verify signed ARM64 output.** Run `scripts/android.ps1 -Mode Build -QuestionPack <formal-pack>` then `-Mode Verify`; require empty public seed, one fixed Asset and descriptor/external/embedded hash equality.
- [ ] **Step 3: Run both disposable AVD gates.** Execute API 29 and API 36 scenarios; verify no currently connected vivo serial was targeted and require both reports successful.
- [ ] **Step 4: Re-run source status and push.** Require a clean branch, push the exact commit, and verify remote HEAD matches locally.
- [ ] **Step 5: Run remote preflight and prepare.** Use `release_github.py preflight --execute` then `prepare --execute --question-pack <formal-pack>`; inspect the receipt for exact commit, version, delivery, member, pack hash and seven files.
- [ ] **Step 6: Publish only after all gates.** Create the beta.6 prerelease without modifying beta.5; if the subsequent feed phase fails, use only the existing `feed` recovery command.
- [ ] **Step 7: Perform anonymous remote verification.** Redownload APK, pack and `SHA256SUMS`; extract the fixed APK member; compare all three pack hashes and descriptor values; verify Tag target, seven assets, Beta code 6 and Stable bytes.
- [ ] **Step 8: Record validation evidence.** Add only sanitized commands/counts/hashes and public URLs to `docs/validation.md`, rerun relevant doc/release tests, commit and push the evidence without changing the already published artifacts.
