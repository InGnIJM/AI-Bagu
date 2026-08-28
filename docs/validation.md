# 历史验收记录

[文档导航](README.md) · [开发与测试](development.md) · [Android 构建与交付](android-beta.md)

本页按功能与产物记录验收证据，包括从原 README 和 Android Beta 文档迁移的历史记录。**数字、哈希和设备结果仅适用于各节明确注明的源码或产物，不能代表所有后续版本。** 最新的本地体验 APK 与此前更新诊断源码实施检查分别记录；通用日志导出的旧验收单独保留。

## 如何阅读

| 记录 | 对象 | 不能据此推断 |
| --- | --- | --- |
| 2026-08-28 更新诊断体验 APK | 明确哈希的 public ARM64 同版本体验包、实际构建/签名/内容校验 | 已安装手机、API29/API36 设备通过、正式版本递增或 GitHub/Pages 上线 |
| 2026-08-28 更新诊断／发布升级 | `11da156` 后的工作区、模拟网络测试、隔离源码 Android 单测/lint | 手机已升级、真实 Pages/Release 可用、已使用正式签名构建 |
| 2026-08-28 诊断日志 | 本次未提交工作区、自动化测试和桌面页面检查 | API29/较新系统已完成设备验收、已有 APK 包含日志导出 |
| 2026-08-28 AI 评卷 | 共享源码与模拟模型/浏览器检查 | 已生成包含这些功能的新 APK、真实模型效果通过 |
| 2026-08-28 语音专项 | 共享源码、独立测试 APK、隔离模拟器 | 所有手机的语音服务可用、已有安装包已更新 |
| 2026-08-28 事务修复 | 源码基线 `997fe91` | 原 `0.1.0-beta.1` 安装包包含修复 |
| 2026-08-27 Android Beta | 明确哈希的历史 APK 与当时测试 | 之后的源码或新包已经完成同等验收 |

文档整理时已提交的共享源码基线为 `71fbbfd`，包含语音提交 `5ab4140` 和后续评分更新。原文中“当前工作树未提交语音”和“main 基线 997fe91”的说法已不适合作为当前状态，相关测试证据保留如下。

后续迁移、自动更新和发布工作应在完成后另外记录源码、精确产物、测试环境及结果；不能用开发计划的勾选项或用户截图替代验收证据。

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
