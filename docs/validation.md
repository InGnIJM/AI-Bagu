# 历史验收记录

[文档导航](README.md) · [开发与测试](development.md) · [Android 构建与交付](android-beta.md)

本页按功能与产物记录验收证据，包括从原 README 和 Android Beta 文档迁移的历史记录。**数字、哈希和设备结果仅适用于各节明确注明的源码或产物，不能代表所有后续版本。** 最新公开版本为下方的 [beta.4](#beta4-公开发布)；较早体验包及源码实施阶段分别保留，不把后来的发布结果回填为当时已验收。

## 如何阅读

| 记录 | 对象 | 不能据此推断 |
| --- | --- | --- |
| 2026-08-30 面经题包与专题模拟 | 最初未提交工作树的历史验证证据，以及当前专用发布分支的提交检查点、合成题包/临时库和自动化 | beta.5 已公开或安装、远端七附件已验收、设备生命周期或 internal APK 已验收 |
| 2026-08-29 beta.4 公开发布 | `ac53f34`、精确 public ARM64 APK、Release／Pages、同签名 code 2 QA 包与自动化 | 报告问题的 API36 物理手机和厂商机型已完成安装验收 |
| 2026-08-28 beta.3 公开发布 | `8cc586f`、精确 public ARM64 APK、隔离设备及真实 Release／Pages／应用内升级 | 所有真机通过、后续未提交格式恢复已打包、原仪器测试夹具问题已修复 |
| 2026-08-28 答案格式恢复 | 后续本地工作区、合成自动化与单独授权的本地数据恢复 | beta.3 包含这些改动、手机题库自动同步 |
| 2026-08-28 更新诊断体验 APK | 明确哈希的 public ARM64 同版本体验包、实际构建/签名/内容校验 | 已安装手机、API29/API36 设备通过、正式版本递增或 GitHub/Pages 上线 |
| 2026-08-28 更新诊断／发布升级 | `11da156` 后的工作区、模拟网络测试、隔离源码 Android 单测/lint | 手机已升级、真实 Pages/Release 可用、已使用正式签名构建 |
| 2026-08-28 诊断日志 | 本次未提交工作区、自动化测试和桌面页面检查 | API29/较新系统已完成设备验收、已有 APK 包含日志导出 |
| 2026-08-28 AI 评卷 | 共享源码与模拟模型/浏览器检查 | 已生成包含这些功能的新 APK、真实模型效果通过 |
| 2026-08-28 语音专项 | 共享源码、独立测试 APK、隔离模拟器 | 所有手机的语音服务可用、已有安装包已更新 |
| 2026-08-28 事务修复 | 源码基线 `997fe91` | 原 `0.1.0-beta.1` 安装包包含修复 |
| 2026-08-27 Android Beta | 明确哈希的历史 APK 与当时测试 | 之后的源码或新包已经完成同等验收 |

早期基础文档核对的源码为 `71fbbfd`，公开 beta.3 对应 `8cc586f`，公开 beta.4 对应 `ac53f341342c2266079af72e23b953aa3ae43459`。较早章节中的“本轮未发布／未验收”仅描述各自当时的对象，相关失败与限制不删除。

后续迁移、自动更新和发布工作应在完成后另外记录源码、精确产物、测试环境及结果；不能用开发计划的勾选项或用户截图替代验收证据。

## 2026-08-30：面经题包与专题模拟（当前开发源码）

本节主体保留用户批准设计后、功能仍位于未提交工作树时取得的历史验证证据；随后这些实现已进入专用发布分支的提交检查点，并将本地版本配置升级为 beta.5/code 5。它仍不是已公开或已安装的新版本：尚未创建 Tag、上传 Release 或修改线上更新清单。已经公开的 beta.4 仍是上节记录的 SQLite/备份 v2，不包含这些能力。

### 实现与自动化证据

- beta.5 七附件发布契约采用 TDD：新增测试在生产改动前为 **49 failed**，实现后题包描述／绑定、六七附件、GitHub 状态机组合为 **226 passed**。正式外部题包只读绑定通过；测试夹具全部使用合成题包，不把真实正文或私有 catalog 带入仓库。
- 当前 Android 项目契约为 **82 passed**，核心＋Android 精确组合为 **566 passed**；Node 语音网页回归为 **27 passed**。Task 5 又以正式题包完成本地签名 public Build／Verify：Java **117 passed**，release lint **0 错误／5 警告**，`androidTest` 编译成功但未在设备运行；七附件、签名、证书、ABI、16 KiB 对齐、空种子和 APK 不含题包均通过精确校验。远端附件和设备验证仍未执行。
- 发布前第一次整仓运行为 **1014 passed / 2 failed**：一个 Windows localhost 请求偶发 `WinError 10053`，另一个 Java wrapper 被沙箱 Temp ACL 拒绝；两项精确复跑分别通过。随后 5000 次对照确认前者是 Windows 关闭仍有未读未授权正文的连接时偶发 TCP RST，不是鉴权或数据库行为失败；测试改用“非法 `Content-Length`、无实体字节”继续确定性证明鉴权先于正文解析，生产代码和正文上限不变。更晚的一次复跑又捕获到测试把随机 `operation_id` 中合法出现的 `73` 误判为安装 `session_id=73` 泄露；现改为校验精确字段白名单，并加入合法编号含 `73` 的确定性回归，生产更新逻辑仍未改变。两项测试修正均经独立审查；当前提交在沙箱外正常 Windows 临时目录运行完整 `python -m pytest test -q` 的结果为 **1016 passed**，退出码 0。
- 纯标准库构建器以临时 Markdown/catalog 覆盖确定性 ZIP、前后快照、未登记/漂移、稳定 ID、审校/来源、引用展开、孤儿/循环、大小和 runtime validator：`python -m pytest test/test_interview_pack_builder.py -q` 为 **66 passed**。该自动化阶段没有读取真实私人源目录或生成真实题包。
- SQLite v3/安装、专题会话与备份分别经 TDD 和独立审查修复。最终 `python -m pytest test/test_bagu.py -q` 为 **484 passed**，builder/web/transfer 组合为 **118 passed**，`test_android_project.py` 为 **76 passed**。整仓 `python -m pytest test -q` 得到 **900 passed / 3 failed**：其中两个 Windows localhost `WinError 10053` 参数项随后原测试隔离为 **8 passed**；Java `DiagnosticStore` 在受限沙箱内被 `toRealPath()` 拒绝，精确策略测试移到沙箱外为 **1 passed**。因此聚焦回归通过，但不能把整仓沙箱命令记录成全绿，也不能宣称环境级随机中止已根治。
- 共享网页聚焦 `test_interview_pack_web.py + test_transfer_web.py + test_update_web.py` 为 **64 passed**；题包网页自身 **16 passed**，Android-web 子集 **23 passed**。覆盖日常/面经切换、筛选/推荐章节、按序恢复、prepare、答案来源、只读/开关、桌面同字节确认、旧宿主和乱序响应。静态响应式/44 px/reduced-motion 复核完成，但 Edge 截图因宿主 GPU 进程失败未取得，因此没有真实渲染截图结论。
- Android/发布/迁移 Python 契约 `test_android_project.py + test_github_release.py + test_transfer.py` 最终为 **278 passed**；`test_android_project.py` 为 **76 passed**。直接 JUnit 的进程仲裁器 **2 passed**、租约 tracker **5 passed**，覆盖文件/更新争用、精确 token、同步终态、迟到回调和新租约保护。
- 当前树重新运行 Android Check 后，Gradle `testPublicDebugUnitTest` 报告为 **118 passed / 0 failed / 0 skipped**，`lintPublicRelease` 为 **0 错误／5 警告**；此前同一离线 JDK 17 工具链已完成 `compilePublicDebugAndroidTestJavaWithJavac` 与 `assemblePublicRelease`。仪器测试只编译，未在设备/模拟器运行。
- 当前 code 5 public ARM64 本地签名 APK 为 **30,031,937 字节**，SHA-256 `e64f60598fc2451f3f568befecd123845c5c43c00eb5fcc9a5e3529cb40ce1e9`。精确 verifier 确认 `questions=0`、`packs=0`、`experiences=0`、`sessions=0`、`session_items=0`，ABI/16 KiB/RELRO/允许列表通过；`apksigner` 确认 v2 签名和单一证书 SHA-256 `ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`。同一七附件目录中的正式题包为 **196,882 字节**，SHA-256 `47aa6b28768be85322924df4a7c17199bf248660997cd10247066821d6d23864`；双行 `SHA256SUMS` 按文件名排序且 `update.json` 仍只描述 APK。这些文件仍只是本地验证产物，未安装、上传或写入线上更新清单。

### 未完成与内容边界

- 上述历史自动化阶段没有访问真实面经库或生成首包。后续私有整理流程已在仓库外冻结 109 个 Markdown、完成 27 专题／748 题的正式 r1 构建和独立复核；原始 Markdown 保持只读，题包字节、源快照、catalog、稳定 ID 和审校材料均不提交。仓库只保存正式附件的公开身份、数量和 SHA-256 描述。
- 没有把题包加入 public/internal 种子；发布预检和 APK verifier 已增加 `.bagu-pack`/精确私有 catalog/pack-owned seed 拒绝契约。没有在线商店、自动题包更新、物理卸载或自动语义去重。
- 历史验证阶段没有递增版本或提交；当前发布检查点已使用 beta.5/code 5 并形成分支提交，也完成上述本地签名构建，但仍没有推送、创建新 Tag/Release、修改 Pages 或安装新 APK。公开 beta.4 的哈希、附件和功能范围完全不因本节改变。
- 未运行设备/模拟器 instrumentation；真实 SAF provider、取消/返回、Activity 重建、进程死亡、WebView 事件、系统安装器与文件/更新并发仍需隔离设备验收。未新构建 internal APK；当前源码对应的 public APK 只完成了宿主机验证，不能替代设备验收或公开发布。

## beta.4 公开发布

日期：2026-08-29。Android 安装确认修复、统一错误弹窗和 Compact Editorial 更新提示先在本地候选与 code 2 QA 包验证；随后经用户明确确认，在隔离干净工作区以精确提交 `ac53f341342c2266079af72e23b953aa3ae43459` 重新测试、签名构建并公开发布。Tag 为 `v0.1.0-beta.4`，发布分支为 `codex/release-beta4-public`。

### 实现与自动化

- API 35 及以上创建安装确认 Activity `PendingIntent` 时显式设置 creator BAL 允许模式；仍使用显式组件、可变回调、非导出 Activity 和用户确认，不恢复 APK `ACTION_VIEW` 或临时 Provider。
- `native.update` 新增固定 `confirm` 阶段，只记录确认回调到达、启动请求成功或固定失败码；不保存 Intent、APK 路径、异常正文或安装 Session ID。安装成功仍只能由下次启动核对实际 versionCode 确认。
- 主动触发的轮次、评卷、模型、题库、文件／备份、语音、诊断和手动更新错误统一进入可访问弹窗；必填／格式校验仍在字段旁，取消、成功、进度和后台自动检查失败不弹窗。更新可用提示为 B「Compact Editorial」，详情按钮只进入设置页，不直接下载。
- 先加入失败测试锁定缺失 BAL 与 `confirm` 生命周期，再实现最小修复。最终 `python -m pytest test -q` 为 **668 passed**，零失败；独立 `node --test test/speech_input.test.cjs` 为 **27 passed**。
- Windows PowerShell 执行 `scripts/android.ps1 -Mode Check` 为 **BUILD SUCCESSFUL**；10 份 JUnit XML 共 **108 项，零失败／错误／跳过**。release lint 为 **0 错误、5 条警告**（`MonochromeLauncherIcon`、`ObsoleteSdkInt`、`StaticFieldLeak`、`UnusedAttribute`、`UseRequiresApi`），未通过压低规则掩盖问题。`:app:compilePublicDebugAndroidTestJavaWithJavac` 另行成功，证明包含 BAL 分支断言的仪器测试可编译；没有把“可编译”写成已在设备运行。
- `scripts/android.ps1 -Mode Verify` 对下述 beta.4 精确 APK 通过：包名／版本、稳定签名、public 空种子、仅 ARM64、ZIP 以及 **68 个原生 ELF 的 16 KiB LOAD 对齐与 GNU_RELRO** 均符合约束。
- 桌面与窄屏合成页面检查了错误弹窗的焦点、纯文本、长消息滚动和动作布局；视觉检查发现弹窗背景引用了未定义 token，随后固定为白色并限制长消息高度。自动化另覆盖 Escape、Tab 循环、焦点恢复、更新去重、错误优先和后台失败静默。

### 发布产物与本地 QA

- [公开 beta.4 APK](https://github.com/InGnIJM/AI-Bagu/releases/download/v0.1.0-beta.4/bagu-0.1.0-beta.4-public-arm64-v8a.apk)：**29,956,897 字节**，SHA-256 `72688b17e48243e121b9f3cabe47a6fafa9a8561aceceb7590bf1eeeedcd225a`，versionName `0.1.0-beta.4`、versionCode `4`、beta 通道。它由发布准备从精确提交重新构建，不复用此前未绑定提交的候选字节。
- 发布前本地候选为 **29,957,285 字节**，SHA-256 `e67342481f3dbe50eb780d8a04d2c1bf1483e58a7d91f14937cf8330d8841308`；该哈希不是公开附件，保留只用于说明不同工作区构建不能互相冒充。
- 同签名 code 2 QA 包：`build/qa/bagu-install-fix-code2-public-arm64-v8a.apk`，**29,957,285 字节**，SHA-256 `4aa2592bbf75639f5b13ba9cdfe94a8056eecccb34957b651d211e445bb21364`。它用于先覆盖当前体验包再检查应用内升级链路，不是公开版本。
- 公开 APK、本地候选和 code 2 QA 包的证书 SHA-256 均为 `ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`；复用既有稳定身份，没有重新生成或输出签名凭据。Release 恰有 APK、`SHA256SUMS`、证书指纹、`update.json`、安装说明和发布说明六项附件。
- 一次 UI 最终修正前的候选输出已保留为 `dist/android/0.1.0-beta.4/public.interrupted-ui-before-white-card`，明确标记为中断产物，不应安装或发布。

### 发布状态与尚未完成

当前 ADB 只发现 API32 x86_64 模拟环境，与 ARM64 交付包及报告问题的 API36 手机不匹配，因此没有为追求“已安装”而强制安装、清除数据或改 ABI。仍需在报告问题的 API36 物理手机验证首次来源授权、系统确认页出现、取消后重试、同签名覆盖升级、启动后的 code 核对和数据保留；并继续覆盖小米 HyperOS 及至少一台 vivo／ColorOS 设备。

[Release / Tag：v0.1.0-beta.4](https://github.com/InGnIJM/AI-Bagu/releases/tag/v0.1.0-beta.4) 为公开预发布版（`draft=false`、`prerelease=true`），目标提交为 `ac53f34`。发布脚本返回 `Release=verified; anonymous assets=verified; Pages=verified`；独立读回确认六附件、Beta 清单的 code 4／大小／哈希以及 Stable `release:null`。`codex/update-feed` 只更新 Beta，未覆盖 Stable。发布后只修正了 Release 正文中已过时的候选措辞，不替换附件、Tag 或 feed 字节。

## beta.3 公开发布

日期：2026-08-28。以下为实际发布阶段的记录，不是本轮文档修改重新执行全套测试的结果。

### 源码、版本与附件

- [Release / Tag：v0.1.0-beta.3](https://github.com/InGnIJM/AI-Bagu/releases/tag/v0.1.0-beta.3)，公开预发布版（`draft=false`、`prerelease=true`），versionCode `3`，beta 通道。
- 精确提交：`8cc586febdb94a24185e93a5c33d979f2f0ee645`，发布分支 `codex/release-beta3-public`。使用独立干净工作区，未合并 main；原工作区未提交的答案格式恢复及无关 Windows 设计草稿未纳入。
- [APK](https://github.com/InGnIJM/AI-Bagu/releases/download/v0.1.0-beta.3/bagu-0.1.0-beta.3-public-arm64-v8a.apk)：`bagu-0.1.0-beta.3-public-arm64-v8a.apk`，**29,936,357 字节**。包名 `io.github.ingnijm.baguhelper`，minSdk 29、targetSdk 36，仅 arm64-v8a，public 空题库。
- APK SHA-256：`8a177050afc4eaf5132bd0186292787ab3c5aacc30b7ba40836dd575af9734b3`。
- 稳定签名证书 SHA-256：`ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`；复用已有身份，未复制、重新生成或发布私钥。
- Release 恰好六项附件：上述 APK、`SHA256SUMS`、`certificate-sha256.txt`、`update.json`、`INSTALL.md`、`RELEASE_NOTES.md`。本地 `verification.json` 将精确提交绑定到全部附件哈希；回执、QA 包、数据库、备份、日志及配置不属于公开附件。

### 正式构建与自动化

| 检查 | 发布时结果 |
| --- | --- |
| `python -m pytest -q` | **630 passed**，126.84 秒，退出码 0 |
| `node --test test/speech_input.test.cjs` | **27 passed**，零失败 |
| Gradle `assemblePublicRelease` / `testPublicDebugUnitTest` / `lintPublicRelease` | BUILD SUCCESSFUL，2 分 21 秒；9 份 JUnit XML 共 **90 项，零失败／错误／跳过** |
| release lint | **0 错误、4 警告**：UnusedAttribute、ObsoleteSdkInt、StaticFieldLeak、MonochromeLauncherIcon |
| 精确 APK | 版本／包名／ABI、稳定签名、ZIP／ELF 16 KiB 对齐、原生库与资源允许列表、SHA-256 校验通过 |
| 内容隔离 | 种子 questions／sessions／session_items 均为 0；8 项静态资源匹配发布源码，DEX 包含更新诊断类 |
| 独立附件复核 | 六项附件与回执全部匹配，QA 构建后正式 APK 哈希未变化 |

先前源码准备回归曾发生一次既有超长诊断请求用例的 Windows `10053` 连接中止；同用例复跑及随后的完整回归通过，未修改 HTTP 安全边界或测试来掩盖。原因仍未确定，不能将重跑通过描述为已修复产品问题。保留 SDK XML 与弃用 API 提示，不宣称零告警。

### 隔离设备与真实升级

仅使用本次新建的 API29／API36 模拟器和合成题目、假模型配置，未安装到或清除个人设备。API29 运行 x86_64 QA 包；API36 通过 native translation 运行**与 Release 哈希完全一致的最终 ARM64 APK**，不等同于物理 ARM 手机。

- 两平台完成 code2 → code3 同签名覆盖与保留断言，核对合成题库、调度、会话／评分历史、配置及 5 项原生状态。API36 的最终 APK 验证通过，不仅检查版本名。
- 文件选择验证覆盖导出写入失败／重试、有效备份保存和有 open 会话时拒绝导入；APK Provider 只读边界通过。API36 启动时的 System UI ANR 曾遮挡文件选择器，处理测试系统弹窗后，原文件测试不改断言即通过；保留首轮失败事实。
- 原 `DiagnosticAcceptanceTest` 的监视器缺少 MIME／OPENABLE 匹配，API29 文件测试的宽泛 Downloads 文本选择器可能点到工具栏标题。先复现失败，再仅在忽略目录的 QA 镜像夹具修正；两平台诊断镜像各 **8 项通过**，API29 文件导出／导入拒绝／保留复核各 1 项通过，原行为断言保留。**已跟踪的两处测试辅助逻辑未修复，不能写成原仪器测试全部通过。**
- 诊断验证覆盖 Python 工作队列阻塞、页面失败／空 WebView、保存失败／关闭输出、重复／取消、Activity 与保存状态恢复、过滤及符号链接边界。模拟回调和状态恢复不代表所有系统级启动故障或杀进程路径均已实测。
- Release 上线后，API36 恢复到原 beta.2 测试状态，由**实际应用界面**完成：手动检查 beta.3 → 从 GitHub 下载 → 完整性检查 → 有练习时安装被阻止 → 主动结束合成练习 → 原生说明与系统来源授权 → 返回后再次点击安装 → 系统确认 Update → 新版启动确认成功。该链路没有使用 `adb install` 代替安装器。
- 系统最终安装的 code 为 3、版本名为 beta.3，实际安装文件 SHA-256 与公开 APK 一致。升级前后经真实文件选择器分别导出 2 题含进度备份，`questions.json` 哈希均为 `f899b096d7ec7b99d85cdd9e7092007b4d7b93f644c405f6cb7bdca86ab296e3`，内容、次数和日期完全一致。

测试结束已关闭本次两台隔离模拟器；QA 证据保留于发布工作区的忽略目录，未作为附件上传。

### Release、匿名下载与 Pages

正式发布命令退出码为 **0**，分别返回 `Release=verified; anonymous assets=verified; Pages=verified`。独立读回确认 Tag 指向精确 `8cc586f`、六项附件齐全，匿名下载字节与本地回执一致。

`codex/update-feed` 发布后提交为 `0f3aab41b91325ccbc8c9f424dfe000ab40457aa`，Pages 从该分支根目录部署。[Beta 清单](https://ingnijm.github.io/AI-Bagu/updates/beta.json)指向 beta.3/code3，[Stable 清单](https://ingnijm.github.io/AI-Bagu/updates/stable.json)保持 `release:null`，未覆盖另一通道。

本轮文档同步又匿名复核 Release 网页及两份清单，均 HTTP 200；Beta 的版本、大小、哈希与公开产物相符。匿名 GitHub REST 查询另返回 403，未将它误判为 Release／Pages 不存在，也未为文档检查使用登录凭据或改动远端。

文档同步验证：`python -m pytest test/test_transfer.py test/test_transfer_web.py test/test_release.py test/test_github_release.py -q -p no:cacheprovider` 为 **216 passed**（8.97 秒，退出码 0）；9 份现行文档的 141 个本地链接、39 个锚点检查通过，`git diff --check` 通过。本轮未编辑业务代码或版本配置；收尾保护快照另检出 `test/test_bagu.py`、`web/index.html` 的并行变动，未覆盖或重置，因此 216 项结果仅对应当次运行，不代表并行改动后整树已重新验收。本轮未再次构建、签名、安装、提交、推送或发布。

### 本版仍未覆盖

- 物理 ARM64 手机、真实 16 KiB 页设备、不同厂商系统；转译模拟器和静态对齐检查不能替代真机。
- 真实模型评分质量、厂商语音识别质量、远程答案图片在目标手机的连通性。
- 最终 ARM64 APK 上所有安装取消／安装中进程死亡／缓存与网络故障组合，以及两种模式全套跨端迁移的重新验收；较早 beta.2 的跨端结果保留在[原记录](releases/0.1.0-beta.2-validation.md)，不能扩写为本版全部路径已通过。
- 上述两处已跟踪仪器测试夹具的源码修复；本地 QA 修正不属于本次发布或本轮文档更新。

## 2026-08-28：答案特殊格式与旧数据恢复（本地工作区）

- 修复 HTML 抓取与 Markdown 渲染中的多段/嵌套引用、列表段落及续行、代码语言和长围栏、行内反引号、表格反斜杠/管道符、转义强调符与实体二次解码；表格保留对齐，表头不拆字，窄屏在表格容器内横向滚动。
- 新增 `import --format-only`、只读 `--dry-run` 和显式 `--include-history`。旧答案必须与同一来源的旧解析结果整篇匹配；拒绝缺失/非当前版本数据库，不触发自动迁移。实际写入前完整备份，事务内复核题目身份和原答案，冲突整批回滚。
- 自动化：`python -m pytest test/test_bagu.py test/test_android_project.py -q -p no:cacheprovider` 曾完整通过 **410 项**（61.22 秒，退出码 0）。最终 CSS 调整后的整跑为 **409 passed / 1 failed**：原有鉴权用例收到 Windows `ConnectionAbortedError / 10053`，无格式断言失败；该鉴权组随后单独复跑 **8 passed**。先前另一次整跑在该组另一用例也出现同样连接中止。未修改鉴权或测试来掩盖偶发错误；不能称最后一次整跑全部通过。测试进程使用临时 `PYTHONUTF8=1` 保持子进程输出编码一致。
- 本地执行前生成 `*.before-answer-format-*.sqlite3` 完整备份，实际恢复 **316 道题、20 条 stored 历史答案**。对备份逐字段比对：只有 `questions.answer`、`session_items.result_full_answer` 变化，全部点评、评级、submission、来源、会话及调度不变；schema 不变，SQLite `integrity_check=ok`。
- 浏览器实测原问题表格恢复为 **5 列、8 个数据行**。390px 窄屏下页面内容宽度与视口同为 375px（扣除滚动条），表格容器 243px、内容 571px，横向滚动可到末端 328px，页面没有横向溢出。桌面/窄屏截图在本地忽略目录 `dist/format-repair/`，不加入应用资源。
- 未中断用户正在进行的练习，既有桌面服务进程未重启；真实页面的表格数据和新 CSS 已验证，新增 Python 渲染行为由自动化测试验证，服务重启后加载。未构建/安装/发布 APK，手机私有库不会随桌面恢复自动同步。

## 2026-08-28：更新诊断本地体验 APK（同版本、未公开发布）

源码实施完成后，用户单独授权“先打包一份我看看效果”。本次使用已有稳定签名，从包含未提交更新诊断修改的当前工作区离线构建 public ARM64 体验包；保留 `0.1.0-beta.2 / 2 / beta`，不提交、推送或修改正式版本，不配置 Pages、不发布 GitHub、不安装设备。下方源码实施阶段的“未生成 APK”描述是此前阶段的边界，不表示本节体验包未构建。

- 独立产物：`dist/android/qa/update-diagnostics-preview-20260828/bagu-0.1.0-beta.2-public-arm64-v8a-preview-20260828.apk`，**29,936,589 字节**；同目录有安装说明、SHA256SUMS 及公开证书指纹，没有可发布的 update.json 或发布回执。
- APK SHA-256：`f78eb868d2056ecba6e6773f84438e6f703769a85756008146e2e8b8ba47b88d`。
- 签名证书 SHA-256：`ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`；复用原有身份，未重新生成或更换密钥。
- 离线 Gradle 执行 `:app:assemblePublicRelease :app:testPublicDebugUnitTest :app:lintPublicRelease`：**BUILD SUCCESSFUL，64 秒，退出码 0**；9 份实际 JUnit XML 共 **90 项、零失败/错误/跳过**。lint **零错误、4 条既有警告**（UnusedAttribute、ObsoleteSdkInt、StaticFieldLeak、MonochromeLauncherIcon）。
- 精确 APK 通过 apksigner、aapt badging（包名 `io.github.ingnijm.baguhelper`、versionCode 2 / versionName 0.1.0-beta.2、minSdk 29、targetSdk 36、仅 arm64-v8a）、`zipalign -c -P 16 4`、既有内容允许列表及 SHA-256 校验。public 种子为 **0 题、0 会话、0 session_items**；**68 个原生 ELF** 均通过 16 KiB LOAD 对齐及 GNU_RELRO 检查。
- 构建前后 40 个源码文件哈希一致；精确 APK 内 **8 个静态文件逐字节匹配**当前源码，两个应用 Python 模块的编译代码匹配源码（仅归一化编译路径）；DEX 中定义了包括 UpdateFailure、UpdateDiagnostic、UpdateCheckSummary 在内的 7 个必要更新/诊断类。
- 旧正式目录的 beta.2 APK 未覆盖，构建前后 SHA-256 仍为 `befd5b1f3f43029e4d87a55fc3d5077be182d9560fb01aed7f7d1972227e55e2`。未读取或打包工作站题库、配置或 Key；签名材料仅用于已授权的本地签名。

首次构建在沙箱内遇到 Gradle 缓存 `annotations-13.0.jar` 的 `AccessDeniedException`，同时出现符号解析错误；获准后以相同源码、配置和构建命令在沙箱外复验成功，未改应用逻辑或依赖规避问题。保留既有 SDK XML 与 Gradle 弃用提示。

这是**同版本体验包**，应用内更新不会把 code 2 判断为更高版本，需从文件管理器手动安装。安装前先导出“题库＋进度”，不要卸载；若系统提示签名冲突或降级，停止并核对，不强制处理。新装为空题库，已有本地数据不由种子覆盖。真实设备是否接受同版本安装、启动效果、日志导出、API29/API36 生命周期、同签名覆盖升级及线上更新链路**本次均未验收**；用户将在手机上体验。

## 2026-08-28：更新诊断与发布流程升级（11da156 后工作区）

本轮只实现本地源码、测试与文档，保持 `0.1.0-beta.2 / 2`，不新增数据库迁移、不修改评分/会话/备份、固定更新地址或签名信任。不提交、推送或发布；与用户未提交的 Windows 打包设计无关，未修改该文件。

| 检查 | 本轮证据 |
| --- | --- |
| 完整 pytest | 最终 `python -m pytest -q`：**629 passed**，零失败/跳过，95.56 秒，退出码 0；保留之前一次 Windows 连接中止记录，见下方 |
| 独立 Node | `node --test test/speech_input.test.cjs`：**27 passed**，零失败/取消/跳过 |
| 原生策略/宿主源码编译 | `python -m pytest test/test_update_policy.py -q -s`：**84 项 JUnit + 2 项 pytest 通过**；包含实际 Android36 SDK 下宿主源码编译，不使用正式签名 |
| 网页更新与安装互斥 | `python -m pytest test/test_update_web.py -q`：**22 passed**；包含纯文本逐通道提示、旧宿主兼容、原生错误不重复上报、导出取消/失败/成功后互斥释放、未结束练习仍能导出 |
| 发布工具 | `test/test_github_release.py` 与 `test/test_release.py` 专项 **164 passed**，也包含在最终全量中；实际 Pages API/匿名读取状态传播经外部边界桩验证 |
| Android Gradle 单测 | 隔离源码执行 `:app:testPublicDebugUnitTest`：**90 项，零失败/错误/跳过**，读取 9 份实际 JUnit XML 报告 |
| Android release lint | 同次执行 `:app:lintPublicRelease`：**零错误、4 条既有警告**（UnusedAttribute、ObsoleteSdkInt、StaticFieldLeak、MonochromeLauncherIcon） |
| 离线 init-feed | 当前脏工作区执行 `python scripts/release_github.py init-feed`：退出码 **0**，仅打印固定分支和三文件计划，不调用 gh、不使用凭据或写远端 |
| 静态检查 | 修改的 Python 文件 `py_compile` 与 `git diff --check` 通过；仅有既有 LF/CRLF 转换提示 |

Android 检查使用当前源码的 66 个允许文件副本（包含新增 Java 类/用例，逐文件 SHA-256 与工作区相同）、假的未使用签名属性和既有 SDK/Gradle 缓存，副本无 keystore、无 APK。未复制真实 `.signing/`、题库或配置；未执行 assemble/package/install。环境为 Windows、Python 3.11.7、Node 22.16.0、JDK 17.0.10、Gradle 9.1.0、compileSdk 36，`baguAbi=arm64-v8a` 只是编译配置，不是已生成 ARM64 安装包的证明。

回归覆盖 HTTP/DNS/TLS/超时与格式/大小/哈希/身份失败、空通道及部分失败、操作编号/取消/过期回调、检查摘要中断与损坏恢复、日志或摘要写入失败隔离，以及 `native.update` 经真实日志文件与 ZIP 再过滤后保留白名单字段、去除假 Key/签名 URL/私有路径/异常消息。发布测试使用合成附件和模拟 Git 数据，覆盖初始化确认/幂等/并发、Pages 前置顺序、六附件/哈希与已公开 Release 恢复。

保留失败及审查记录：

- 失败测试先验证缺失分类/摘要/日志字段与生命周期问题，再实现；独立审查修复了 Python 接受非 ASCII 版本数字而 Android 拒绝的差异，以及启动缓存故障误用旧检查反馈编号的问题。后者的 hash/APK/missing × 有无租约六种组合已回归。
- 首次隔离 Gradle 在沙箱中因读取生成的 R.jar 被拒绝而失败，获准在沙箱外运行；随后发现新增测试使用 Android SDK 编译面没有的 `Files.readString/writeString`，改为等价 UTF-8 字节读写后，同一单测/lint 命令成功，退出码 0。保留 SDK XML 与弃用 API 提示，不修改工具链或正式签名配置规避问题。
- 一轮全量测试中，既有本机 HTTP 认证用例 `test_runtime_auth_precedes_body_parsing_and_database_access[GET-/-None]` 出现 Windows `ConnectionAbortedError / 10053`（595 passed、1 failed）；同组 8 项单独复跑、最终 629 项全量均通过，未修改认证逻辑或放宽断言。这次中止原因未确定，不把重跑通过当作已修复产品缺陷。
- 主审发现 Pages API 404 被当作缺配置，以及匿名下载失败在重试/PARTIAL 中丢失状态，已按失败测试修正。后续成功或内容不一致不会沿用上一次网络失败的 HTTP 状态；错误正文、URL 和工具 stderr 不进入输出。
- 最终独立审查发现 Pages 明确使用 workflow 但残留旧 source 时会误过发布门禁；新增 5 项失败测试后限制显式部署模式为 legacy，同时保留缺字段兼容，聚焦复审确认解决。没有修改 Pages 设置或执行真实发布。
- 发布阶段首次 RED 用例漏 mock 匿名下载，触发一次意外匿名读取尝试，没有保留成功响应证据；没有调用 gh、凭据或远端写入。随后修正 fixture，并加 socket/DNS 禁止外网守卫，后续发布回归在守卫下运行。真实 GitHub 可达性不计为通过。

**本轮未执行：** API29/API36 设备安装与仪器验收、Activity/进程被系统中断的真实恢复、启动失败日志导出、最终 ARM64 APK 签名/校验、同签名覆盖升级、更新源远端初始化、Pages 配置、Release 发布、匿名真实附件与旧包到新包链路。真实设备、安装器和线上可达性需要后续单独授权；本轮代码和自动化通过不能替代这些结果。

## 2026-08-28：诊断日志与导出（当时工作区）

在 `a995e84` 及原有未提交工作区改动之上实现桌面和 Android 日志导出，未自动提交、修改版本或生成正式交付 APK。以下是本次实际执行的源码验证，**不沿用旧 APK 的设备验收结论**。

| 检查 | 本次结果 |
| --- | --- |
| `python -m pytest test -q` | **492 passed**，零失败；最终一轮用时 82.05 秒 |
| `node --test test/speech_input.test.cjs` | **27 passed**；包含语音失败编号关联及不记录语音/作答/服务商消息 |
| Windows PowerShell 执行 `scripts/android.ps1 -Mode Check` | **BUILD SUCCESSFUL**；`:app:testPublicDebugUnitTest` 共 **65 项，零失败/错误/跳过** |
| `:app:lintPublicRelease` | **零错误、4 项警告**：UnusedAttribute、ObsoleteSdkInt、StaticFieldLeak（原有更新组件）、MonochromeLauncherIcon |
| 新增诊断仪器测试及测试 provider | 使用 JDK17、实际 Android36 SDK、缓存 AndroidX/JUnit 和本轮编译的应用类执行 `javac`，退出码 **0**；**仅编译，未在设备运行** |
| Python 编译及差异空白检查 | 修改的 Python 文件 `py_compile` 与 `git diff --check` 通过；Git 的 LF/CRLF 提示不代表内容错误 |
| 桌面实际页面 | 临时隔离题库下检查设置卡片、导出按钮、完成后恢复控件；浏览器控制台无错误/警告。下载提示符合“已开始下载”，未取得文件保存成功的浏览器事件，不能据此声称文件已落盘；HTTP ZIP 字节由自动化测试验证 |

本次回归覆盖新写入/旧日志重过滤、假 Key/令牌/作答/语音污染、大整数/损坏行/尾部半行、轮转和每来源限额、目录不可写、部分来源缺失、HTTP Host/Origin/专用头/限流、数据库连接禁止调用、Android HTTP 不暴露诊断接口、普通/SSE 请求编号、网页错误队列及并发导出、旧宿主兼容和重复点击/取消；原有评分事务、submission 重放、导入和安装互斥测试继续通过。审查发现的“导出等待旧批次却漏掉刚入队错误”和“启动中重建后反馈编号变化”均已修复并补回归。

新增仪器用例描述独立导出执行器在 Python 队列阻塞时的行为、主页面失败回退、WebView 为空时销毁、Activity/文件选择器重建、保存取消/重复操作、失去进程内状态后的恢复及写入失败/成功关闭。测试 provider 只存在于测试 APK，并只接受固定合成输出文件；不得安装到真实用户数据环境。Bundle 重建不是实际系统杀进程，模拟页面错误回调不是 WebView provider 缺失验证。

验证环境限制：PowerShell 7 的 JSON Int64 会触发现有版本脚本校验失败，最终使用项目原有 Windows PowerShell。沙箱内 Java 解析临时目录/SDK 真实路径曾被拒绝；获准在沙箱外复验后通过。Gradle 仍有 SDK XML 版本与弃用 API 提示，未为本功能修改工具链或版本文件。

**尚未执行：** API29 及较新 Android 隔离设备上的安装/仪器测试、真实系统文件选择器交互、进程终止恢复、WebView 构造失败、Python 启动失败时实际导出，以及远端文件提供方关闭失败等设备验收。现有 ADB 连接未确认隔离数据与安装授权，因此没有安装、清除或覆盖设备应用。未构建/发布包含此功能的新 APK，手机上的旧应用不会因源码修改自动更新。

## 2026-08-28：AI 评卷与答案来源

该次共享源码更新采用明确四档标准、两题八条校准示例和具体学习反馈，结果依次展示评级、学习反馈及标准答案。所有 AI 评级均保存答案：题库优先，没有题库答案才由模型补充；easy 默认折叠，其他评级展开。来源随首次评分持久化，刷新恢复不重新生成；自评、复习间隔和原生语音协议不变。

SQLite 版本由 1 升为 2，仅增加可空的评分答案来源，旧记录来源保持为空。正式升级真实库前应备份完整 SQLite；升级后的库不能直接交给旧版程序。应用导出的 `.bagu-backup` 格式不变，且不包含会话与评分分析，不能作为完整回滚备份。

该次仅修改共享源码、测试及说明，**没有构建或发布 APK、升级真实题库、使用真实模型服务**。下文旧命名 APK 和语音专项测试包均不会随源码自动更新；下文历史 APK、lint、设备与语音验收记录不代表新评卷功能已完成设备验收。实际模型评分质量还需另行授权，用未出现在提示词中的题目与作答对比验证。

该次源码验收：`python -m pytest test/test_bagu.py test/test_android_project.py -q` 共 374 项通过；`node --test --test-reporter=spec test/speech_input.test.cjs` 共 26 项通过。使用临时数据库和模拟模型检查了浏览器中的 easy 折叠、hard 展开、题库/模型来源、反馈转义与换行、代码列表和刷新恢复；390px 窄屏没有横向溢出，答案展开控件高度 44px。这些不代替 APK、设备或真实模型效果验收。

## 2026-08-28：语音专项与独立测试包

- `python -m pytest test/test_bagu.py test/test_android_project.py -q`：329 项通过，退出码 0；使用临时数据库与模拟网络。
- `node --test --test-reporter=spec test/speech_input.test.cjs`：26 项通过，退出码 0；执行实际页面脚本，模拟浏览器和原生识别边界，覆盖成功回填、不可用、权限拒绝、超时、取消、串题防护与不自动评分。
- Gradle publicDebug/x86_64 与 publicRelease/arm64-v8a 均构建成功，各自 Java 报告 22 项通过（语音 16 项、宿主策略 6 项），lint 0 错误、3 条既有警告。ARM64 首次构建被沙箱拒绝访问现有依赖缓存，获准在沙箱外重跑同一构建后通过，未修改业务代码规避错误。
- 浏览器界面曾通过临时题库和模拟识别服务检查桌面/窄屏布局、草稿回填与刷新保留、不可用提示；未采集实际音频，不代表真实服务连通性。
- 用户授权后，新建 `bagu_speech_20260828`（API29 / Android10 / WebView74）和 `bagu_speech_api36_20260828`（API36 / Android16 / WebView133）隔离模拟器；未安装到已有设备、未清除已有应用数据、宿主机音频输入关闭。两台分别运行 `AndroidSpeechAcceptanceTest`，最终均 **6/6 通过**，无跳过。API29 使用 x86_64 debug，API36 使用与下方交付副本完全相同的 ARM64 release APK，经模拟器原生桥转译运行，不等同于物理 ARM 手机。
- API36 首次冷启动有系统界面无响应弹窗，第一轮 6 项中 2 项因页面加载/生命周期等待超时而失败。保留此失败事实；等待系统恢复并关闭弹窗后，未改源码、未放宽测试期限，重跑完整 6 项用例，22.05 秒全部通过。API29 首轮 19.36 秒全部通过。
- API29 另做实际页面操作：在新建模拟器中临时禁用识别服务，点击语音输入显示“系统语音识别服务不可用”；恢复服务后在真实权限弹窗选择拒绝，显示麦克风权限错误。两种情况下原答案 `Existing_answer` 均保留、提交按钮恢复可用。截图为 `dist/android/qa/speech-20260828/api29-unavailable.png` 和 `api29-permission-denied.png`。识别服务已恢复。

测试包为 `dist/android/qa/speech-20260828/bagu-speech-public-arm64-test.apk`，29,811,157 字节，SHA-256：`6a1acf35d1dcfcba1f8a9440bcefcd09d68671ce73d201e8e339c5130aad4cc1`。证书 SHA-256：`ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`，与旧安装包身份一致。已核对安装后的 APK 哈希、该次网页源码字节、清单（仅 INTERNET / RECORD_AUDIO）、空种子及原生库允许列表；`verify_android_apk.py --flavor public --expected-questions 0 --readelf ...`、`apksigner verify` 和 `zipalign -c -P 16 4` 均通过。

**此为独立测试构建，不是新版正式发布。** 仍使用 versionCode `1` / versionName `0.1.0-beta.1`，首次安装为空题库，不含工作站数据、配置、Key 或签名材料；不应为安装测试包而卸载已有应用或强制降级。旧命名 APK 的哈希仍为历史记录中的值，未覆盖。真实语音识别质量、联网服务可达性与各厂商中文支持仍需在目标手机验证，不作普遍可用承诺。

## 2026-08-28：评分事务修复

源码基线 `997fe91` 的完整回归记录为 328 项通过；另行编译执行的 `HostPolicyTest` 为 6 项通过。该修复保证评分结果和答案 HTML 构造失败时不留下已计分记录，无法解析的答案链接降级为安全文本。

这些是源码检查。2026-08-27 的旧命名 `0.1.0-beta.1` APK 不包含这次后续修复。`Verify` 只检查已有产物，不会重建，也不能单独证明它与之后的源码相同。

## 2026-08-27：最初 Android Beta 安装包

本节保留当时的构建与设备证据，不表示当前源码已完成同等验收；后续源码验收见本页前两节。

2026-08-27 已在 API29 / Android10 / WebView74 的 x86_64 模拟器 QA 构建，以及 API36 / WebView133 的 arm64 转译模拟器上实际安装、启动和测试。API36 运行的是与交付文件 SHA-256 相同的签名 arm64 release APK；API29 的 x86_64 测试包不是手机交付包。覆盖真实系统文件选择器、取消/坏档、CSV、离线复习、键盘单次返回后底部导航恢复、旋转和窄屏/平板/折叠屏尺寸模拟。确定性模型错误使用测试 APK 内的合成 HTTPS 配置及临时模型桩，不代表真实模型服务联通。

已实际验证进程结束重启和同签名更高版本覆盖：API36 从 internalRelease v1 更新到空种子的 publicRelease QA2，API29 从 publicDebug v1 更新到 QA2。完整题目/调度/评分分析、模型配置及全部原生草稿/提交状态逐项保持一致；public 空种子未清除内部 Beta 数据。QA2 仅用于本地升级验收，不是公开发行，交付包仍为版本1。

最终备份修复又在两台原模拟器上以独立 public QA3 覆盖验证：原有完整数据/配置/草稿保持一致；真实系统文件选择器下，临时超限导出明确失败且只留零字节占位，恢复限制后重试成功，导出档案通过本应用解析。`viewport-fit=cover` 的实际窗口检查确认原生安全区只应用一次，没有叠加网页底部留白。QA3 同样仅为测试包，不是命名交付 APK。

当时的命名 APK 为 `0.1.0-beta.1`，大小 30,092,181 字节，SHA-256 为 `2c3ad3a05ac912639ea99d12ffb7f8ef11f0444cf7707aa256cad3b1c5181c5a`。它又在独立的全新 API36 / WebView133 模拟器中完成首次安装与冷启动：应用内首页显示 408 道题、408 道待复习、已掌握 0、没有进行中的会话；设备上安装文件的哈希与交付 APK 完全相同，未清除或降级原有 QA 数据。

2026-08-27 对应源码当时通过 293 项 Python/项目回归测试（零失败、零跳过、零告警）；两种 debug 配置各有 6 项 Java 策略测试通过，最后构建复用了这些未变化测试的结果。两种 release 构建和交付物校验通过，release lint 各为零错误、4 项告警；当时的审查及一轮修复复审未留下已知的严重或重要问题。这不涵盖 2026-08-28 后续发现并修复的评分事务问题。非阻断后续项包括 CSV 临时缓存回收、lint 告警，以及联网测试跳过条件的收紧；不把这些描述为已修复。

### 当时的构建约定

当时脚本将版本固定为 `0.1.0-beta.1` / versionCode `1`，一次构建 internal 与 public release，internal 清洁种子校验为 408 题、public 为空。交付文件为 `dist/android/八股助手-0.1.0-beta.1-arm64-v8a.apk`，伴随 `SHA256SUMS`、`certificate-sha256.txt` 和 `install-notes.txt`。当时再次执行 Build 会替换同名交付文件，不自动增加版本。

这些路径和行为属于旧脚本，不应当作当前版本化发布流程的操作说明。公开 flavor 当时仅用于空种子验证，不代表发生过公开发布。

## 上述设备验收未覆盖的范围

两台模拟器均为 4 KiB 内存页。虽然交付物已做 16 KiB ELF/ZIP 对齐检查，尚无真实 16 KiB 设备运行证据；也没有物理 arm64 手机、真实折叠铰链或各厂商系统的覆盖。模拟尺寸和转译运行不能替代这些真机验证。搜索键在 API29 保持键盘打开是实际输入法行为；测试确认系统返回关闭键盘后导航恢复，不把它等同于所有输入法的 Done 键行为。

上述验收中**真实远程 HTTPS 图片加载未通过、仍未验证**：公开图片在宿主机返回 200，但两台模拟器解析该域名均报 `unknown host`，API36 WebView 回报 `ERROR_HOST_LOOKUP`（-2），API29 在有界等待内未完成。实测未触发 `img-src` CSP 拒绝；该网络探测在测试报告中明确标为跳过，不计作成功加载。没有修改 DNS、证书、TLS 或放宽网络策略来绕过问题；请在有正常网络的目标手机上另行确认远程图片与模型服务。

## 文档配图的证据范围

[图片说明](images/README.md)列出了用户于 2026-08-28 提供的三张桌面截图和一张 Android 截图。它们用于说明操作入口，没有核验对应 APK 的版本、哈希、设备型号或模型质量，也不改变上面的验收结论。
