# 更新诊断与发布流程升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 用户已授权当前任务实施；不提交、推送、改版本、签名、安装设备或发布。

**Goal:** 在现有日志导出基础上，让更新失败可解释、可关联、可恢复，并独立初始化及验证 GitHub 更新源。

**Architecture:** UpdateIO 在故障边界产生 UpdateFailure；UpdateEngine 拥有不可变操作上下文、逐通道结果和安全摘要。AndroidDiagnostics 复用同一落盘/导出白名单；网页只呈现状态。发布工具独立处理 init-feed，其余发布阶段在产生远端副作用前检查 Pages。

**Tech Stack:** Java 标准库、Android 原生宿主、现有单页 HTML/JavaScript、Python 标准库、pytest/Node/JUnit。

**Spec:** 用户在当前任务确认的《八股助手：更新诊断与发布流程升级》，本文件固定其实现契约与验证顺序。

## Global Constraints

- 基线 `11da156`；保留工作区既有文件，尤其无关 Windows 打包设计。当前版本 `0.1.0-beta.2 / 2` 不变。
- 不新增依赖、数据库迁移，不改评分、会话、备份、更新地址、签名信任、安装交接或授权持久化边界。
- 沿用真实接口 `getUpdateState()` / `bagu-update`（用户粘贴文本的乱码不构成接口更名）。
- 自动失败只在设置显示；手动失败显示短原因及反馈编号；新版本可关闭提醒保留。
- 错误码：HTTP/DNS/timeout/TLS/connect=1001..1005；JSON/manifest/limit/redirect=1101..1104；storage/length/hash/APK=1201..1204；permission/installer=1301..1302；unknown=1999。HTTP 状态单独保留，取消不是错误。
- 日志与 lastCheck 不包含 URL、正文、异常消息、私有路径或密钥；原异常只在内存中用于既有安全栈提取。
- 默认 dry-run 不用凭据、不请求网络、不写远端。正式发布和设备验收需单独授权。

### Task 1: 原生错误、操作上下文与安全摘要

**Files:** 新增 `UpdateFailure.java`、`UpdateDiagnostic.java`、`UpdateCheckSummary.java`；修改 `UpdateIO.java`、`UpdateEngine.java` 及对应 `android/app/src/test/java/.../Update*Test.java`、`test/test_update_policy.py`。

**Interfaces:** `UpdateFailure` 暴露整数 code、可空 HTTP status；`Consumer<UpdateDiagnostic>` 替代阶段/异常双参数回调。`Preferences` 新增安全摘要读写；旧安装缺少摘要为 unknown。`state().lastCheck` 为对象：`diagnosticId/startedAt/completedAt/status/errorCode/channels`，通道也是对象，成员为 `status/errorCode/httpStatus/durationMs`；只使用固定枚举和数字。

- [x] 写失败测试并运行：HTTP 状态、DNS/timeout/TLS、解析/校验、超限/重定向、下载大小/hash/storage、空通道/部分失败、取消。
- [x] 在原始边界生成类型化错误，不按异常消息分类；保留真实 HTTP status。
- [x] 被接受的操作才分配 `n_` + 32 hex；同次双通道/取消共用编号，闭包捕获上下文；拒绝/节流不清空失败编号。
- [x] 单条最多 4096 UTF-8 字节的 lastCheck；checking 先写，启动恢复为 interrupted，不重放。摘要写失败仅降级内存；安装关键 prefs 继续严格失败。
- [x] 回归运行 `python -m pytest test/test_update_policy.py -q -s`，确认现有缓存、取消和安装门禁仍有效。

### Task 2: 原生诊断接入与页面

**Files:** 修改 `UpdateController.java`、`AndroidDiagnostics.java`、`DiagnosticPolicy.java`、`web/index.html`；扩展 `DiagnosticPolicyTest.java`、`DiagnosticStoreTest.java`、`test/test_update_web.py`。

**Interfaces:** `AndroidDiagnostics.update(UpdateDiagnostic)` 只写 `native.update`；限定 `channel/outcome/error_code/status/duration_ms/operation_id`。同一 DiagnosticPolicy 同时过滤落盘和导出。

- [x] 失败测试锁定 update 字段只对 native.update 开放；污染字段经落盘/ZIP 不泄漏；日志失败不影响结果。
- [x] 移除控制器全局可变诊断编号，操作接受/回调/安装错误交回 engine 所属上下文；不增桥接方法。
- [x] 页面以 textContent 展示逐通道原因、HTTP 状态和反馈编号；旧宿主降级原消息，自动失败不弹窗。
- [x] 回归过期回调、重建、诊断导出/安装互斥；不重复网页上报原生错误。
- [x] 运行 `python -m pytest test/test_update_web.py test/test_update_policy.py -q`。

### Task 3: GitHub 初始化与发布前置检查

**Files:** 修改 `scripts/release_github.py`、必要时 `scripts/release_metadata.py`，扩展 `test/test_github_release.py` / `test/test_release.py`。

- [x] 先补模拟响应的失败测试：init-feed 脏工作区离线 dry-run；执行确认、幂等、保留原字节；权限/网络/并发拒绝；阶段顺序与 feed 修复。
- [x] init-feed execute 只验证固定 origin、公开非归档仓库及权限、Git 数据访问，不要求源码干净/推送/版本/APK/Release。固定三文件，缺失才补；额外文件/符号链接/非法清单拒绝；缺分支只在仓库及 Git 访问成功后由 ref404 判定；不强推不切换工作区。
- [x] JSON GitHub 请求用安全类型保存 HTTP 状态，可解析 `gh api --include`；不输出响应正文/头/工具 stderr。二进制附件仍返回原始字节。
- [x] preflight 返回前、prepare 构建签名前、publish Draft/附件/发布前检查 Pages 来源分支/root、双通道匿名完整校验和分支内容一致；显式部署模式只接受 legacy，缺字段兼容。feed 可先修复后检查。
- [x] init-feed 仅报告分支就绪，Pages 由维护者配置。正式发布既有精确提交/回执/六附件/版本约束保留；Release 已公开而清单失败保留部分完成和 feed 重试。
- [x] 运行 `python -m pytest test/test_github_release.py test/test_release.py -q`。

### Task 4: 集成、审查与交付证据

- [x] 独立审查 native/UI 与 release 的契约、安全和异常恢复；修复必须补针对性失败测试。最终 Pages 部署模式问题经 RED/GREEN 和聚焦复审确认解决。
- [x] 完整 `python -m pytest test -q`、`node --test test/speech_input.test.cjs`、Android `:app:testPublicDebugUnitTest` / `:app:lintPublicRelease`；读取实际退出码和输出。
- [x] 更新现有用户指南、接口说明、Android/发布文档、项目规则及验收记录，区分源码、自动测试、设备、Release、Pages。
- [x] 无授权不签名构建、不安装设备、不调用真实 GitHub。API29/API36、ARM64 真包、真实故障 ZIP/覆盖升级及线上链路列为未执行，不冒用旧验收记录。
