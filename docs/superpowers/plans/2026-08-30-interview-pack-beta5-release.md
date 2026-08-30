# 2026 秋招面经正式题包与 beta.5 发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task. Production changes follow TDD; private source and artifacts stay outside Git.

**Goal:** 从冻结的 27 个专题、748 个自测项生成正式题包，并与具备题包导入能力的 Android beta.5 作为同一 GitHub Release 的独立附件发布。

**Architecture:** 现有 `build_interview_pack.py` 继续负责严格、确定性的最终构包；新增纯标准库 preparer 将私有四文件结构转换为 catalog。发布描述只提交题包身份、数量和哈希，Release 工具验证外部题包后生成精确七附件，APK 和更新 feed 仍不内置或自动下载题包。

**Tech Stack:** Python 标准库、SQLite、单页 Web UI、Android Java/Python WebView 宿主、PowerShell/Gradle、GitHub CLI。

**Spec:** 用户在 2026-08-30 当前任务批准的《2026 秋招面经正式题包与 beta.5 发布计划》。

## Global Constraints

- 仓库外的真实私人源目录始终只读；用户于 2026-08-30 明确确认采用更新后的当前版本，执行基线为 109 个 Markdown，精确快照仅记录在仓库外私有回执中。
- 正式包固定 `pack_id=autumn-recruit-interviews-2026`、`revision=1`、`display_version=2026.08.30-r1`、27 专题、748 题、文件名 `ai-bagu-2026-autumn-interviews-r1.bagu-pack`。
- 一个 checklist/编号项就是一个题目；复合追问不拆分。Agent 01 和 Agent 09 为 `topic_set`，其余为 `interview`。
- 原始资料、私有 catalog/overrides/stable IDs、`.bagu-pack`、数据库、签名和 dist 不提交 Git；public APK 空种子且不含题包。
- 答案是维护者接受的 AI 参考答案，不冒充原帖或公司标准答案；明显纠错只写私有覆盖清单。题包内容保留所有权，仅供个人学习和本应用使用。
- 发布版本固定 `0.1.0-beta.5` / code 5；beta.4 不变；beta feed 仅描述 APK，stable feed 不变。
- 不新增第三方 Python 依赖，不改变 Hermes grade 协议，不放宽现有 ZIP、WebView、会话锁、评分或 Android 安全边界。

### Task 1: 审计并冻结当前开发树

- [ ] 在 `codex/release-beta5-interview-pack` 上逐项归类当前 diff；纳入题包/专题模拟、备份 v3、Android 安全导入和必要安全修复，排除 `.tmp-plan-baseline` 及所有私有/生成物。
- [ ] 运行现有全量基线并记录沙箱内外差异；确认没有真实 Key 或私有题库字节。
- [ ] 按核心/网页、Android、发布安全和文档形成可审查提交，不修改功能语义。

### Task 2: 四文件面经库离线 preparer

- [ ] 先写失败测试覆盖 checkbox/编号解析、H2 章节、单题/范围/分章节答案、引用、prepare、稳定 ID 复用、非法 URL、缺失答案、数量与快照漂移。
- [ ] 新增 `scripts/prepare_interview_catalog.py`，所有路径由参数传入；生成 private catalog、stable IDs、覆盖模板和零阻断报告。
- [ ] 题目 ID 使用 `q.<domain>.<topic>.<ordinal>`，专题/章节使用 `exp.*` / `sec.*`；既有映射优先，新题只追加 ID。
- [ ] 任何未映射技术题、未解析引用、模糊分类、空章节、孤题或私密本地路径均使 preparer 失败。

### Task 3: 生成并验证正式私有题包

- [ ] 复算原库快照，复制到仓库外的私有 r1 构建工作区；已存在的不匹配工作区拒绝覆盖。
- [ ] 在副本上生成 catalog；完成私有答案映射、prepare 分类和已可靠确认的技术纠错，报告必须为 27/748 且零阻断。
- [ ] 连续构建两次并比较字节；用运行时 validator、临时数据库安装/升级、专题启动和备份 v3 往返复验。
- [ ] 将精确题包和单文件 SHA-256 清单复制到仓库外的私有 release 目录，再次确认原库快照未变。

### Task 4: 正式题包 Release 契约与 beta.5

- [ ] 先写失败测试覆盖公开题包描述、prepare 的 `--question-pack`、身份/哈希/数量冲突、七附件 allowlist、双文件 SHA256SUMS、远端冲突和恢复。
- [ ] 新增只含公开身份/哈希/数量的 release descriptor；扩展 `release_github.py`、`release_metadata.py` 和 `android.ps1`，prepare 验证并复制外部题包，publish 只认 receipt 绑定字节。
- [ ] update feed 保持 APK-only；发布说明写明 AI 答案、个人学习授权和手动导入；APK verifier 继续拒绝 APK 内题包。
- [ ] 升级 `version.json` 到 beta.5/code5，同步文档与验收记录。

### Task 5: 全量验收、提交与公开发布

- [ ] 运行 converter/pack/release 聚焦测试、完整 Python/Node、Android Java、androidTest 编译、public lint、签名构建和 APK 精确校验。
- [ ] 请求完整分支审查，修复所有 Critical/Important 发现并复验。
- [ ] 安装 GitHub CLI，由用户完成交互登录；推送精确发布分支和提交。
- [ ] 依次执行 authenticated preflight、prepare、publish；如 feed 失败只执行恢复阶段，不删除/覆盖 Release。
- [ ] 匿名下载七附件并核对 tag、commit、哈希、pack manifest、APK 空种子、beta code5 与 stable 不变；用远端题包做临时桌面安装和专题启动。
