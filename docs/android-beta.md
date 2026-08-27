# 八股助手 Android Beta

此文档说明本地、已签名的 Android Beta 构建与交付。它不会发布到 GitHub、应用商店或任何外部服务。

## 当前状态（2026-08-28）

- `main` 的源码基线为 `997fe91`：已包含评分事务修复，评分结果和答案 HTML 构造失败时不会留下已计分记录；无法解析的答案链接降级为安全文本。
- 该源码基线已通过 328 项 Python/项目回归测试，以及另行编译执行的 6 项 `HostPolicyTest`。这些是源码检查，不代表 APK 已重建。
- 现有命名 APK 仍是 2026-08-27 的 `0.1.0-beta.1` 构建，**不包含上述后续修复**。更新源码或文档不会自动替换安装包；`Verify` 也只校验已有产物，不会构建或证明它与当前源码一致。
- 下方模拟器、签名、lint 与 APK 哈希记录是历史验收记录。需要包含最新修复的手机安装包时，应重新构建、校验，并记录新的产物哈希和设备验收结果。
- 当前工作树已加入双端语音输入，并完成下文的语音专项验收；新 ARM64 测试包独立存放于 `dist/android/qa/speech-20260828/`，不覆盖上述旧命名安装包，也不代表已提交源码或发布新版本。

## 在手机上使用

安装包面向 **Android 10 及以上、arm64-v8a（64 位 ARM）设备**。将 `八股助手-0.1.0-beta.1-arm64-v8a.apk` 传到手机，在系统文件管理器中点开，按系统提示仅为该文件来源允许安装未知应用，完成后可关闭该许可。无需电脑常驻、ADB、外部浏览器或另启网页服务；从桌面打开“八股助手”即可使用。下方 ADB 命令只是开发调试的可选安装方式。

内部 Beta 首次安装自带 408 道题；题库和练习进度保存在应用本机私有目录。背题、查看已保存答案和自评复习可以离线进行；AI 评卷需要网络和自行配置的 HTTPS 模型服务，答案中的远程 HTTPS 图片也需要网络。模型请求会把题目和你的回答发送给所选服务；请自行确认服务商的数据政策，勿输入敏感内容。

题库页支持新增、修改和 CSV 导入，UTF-8 表头为 `category,question,answer,url`；可先保存模板。单文件上限 2 MiB、5000 题，错误整批拒绝，重复分类+题干跳过。Android 文件操作使用系统文件选择器，可取消后重试。

## AI 评卷更新（2026-08-28，共享源码）

当前共享源码采用明确四档标准、两题八条校准示例和具体学习反馈，结果依次展示评级、学习反馈及标准答案。所有 AI 评级均保存答案：题库优先，没有题库答案才由模型补充；easy 默认折叠，其他评级展开。来源随首次评分持久化，刷新恢复不重新生成；自评、复习间隔和原生语音协议不变。

SQLite 版本由 1 升为 2，仅增加可空的评分答案来源，旧记录来源保持为空。正式升级真实库前应备份完整 SQLite；升级后的库不能直接交给旧版程序。应用导出的 `.bagu-backup` 格式不变，且不包含会话与评分分析，不能作为完整回滚备份。

本次仅修改共享源码、测试及说明，**没有构建或发布 APK、升级真实题库、使用真实模型服务**。上方旧命名 APK 和语音专项测试包均不会随源码自动更新；下方历史 APK、lint、设备与语音验收记录不代表新评卷功能已完成设备验收。实际模型评分质量还需另行授权，用未出现在提示词中的题目与作答对比验证。

本次源码验收：`python -m pytest test/test_bagu.py test/test_android_project.py -q` 共 374 项通过；`node --test --test-reporter=spec test/speech_input.test.cjs` 共 26 项通过。使用临时数据库和模拟模型检查了浏览器中的 easy 折叠、hard 展开、题库/模型来源、反馈转义与换行、代码列表和刷新恢复；390px 窄屏没有横向溢出，答案展开控件高度 44px。这些不代替 APK、设备或真实模型效果验收。

## 语音输入接入说明

语音输入位于答题模式的答案框下，采用 Android 系统 `SpeechRecognizer`，不使用 WebView 的浏览器识别，也不配置独立转写 API Key。开始前检查系统服务并按需申请 `RECORD_AUDIO`；Manifest 仅增加麦克风权限与 `android.speech.RecognitionService` 查询声明，不改变本机 HTTP、令牌、文件选择或 WebView 导航边界。

原生桥接提供 `startSpeech(requestId)`、`stopSpeech(requestId)`、`cancelSpeech(requestId)`，通过 `bagu-speech` 事件传递 `ready / partial / result / error / cancelled`。请求 ID 将回调绑定到单次录音；Activity 暂停、销毁或页面切换会取消录音，迟到权限和识别回调不得重新启动或写到另一题。结束后等待最终结果有超时，服务缺失、权限拒绝、网络/麦克风故障和无结果均显示错误，不自动切换外部云端服务。

转写只追加到答案草稿，不提交模型、不评分；已确认文字保留，临时片段不落草稿。系统服务可能联网处理音频，不能承诺所有手机品牌都有可用服务或离线能力。权限弹窗引起暂停时保留待决请求：拒绝仍显示权限错误，授权成功则取消本次请求，需要用户再次点击，不能自动开始录音。已有命名 `0.1.0-beta.1` APK 不会因源码更新而获得此功能，需另行构建和验收。

### 本次语音功能验收记录（2026-08-28）

- `python -m pytest test/test_bagu.py test/test_android_project.py -q`：329 项通过，退出码 0；使用临时数据库与模拟网络。
- `node --test --test-reporter=spec test/speech_input.test.cjs`：26 项通过，退出码 0；执行实际页面脚本，模拟浏览器和原生识别边界，覆盖成功回填、不可用、权限拒绝、超时、取消、串题防护与不自动评分。
- Gradle publicDebug/x86_64 与 publicRelease/arm64-v8a 均构建成功，各自 Java 报告 22 项通过（语音 16 项、宿主策略 6 项），lint 0 错误、3 条既有警告。ARM64 首次构建被沙箱拒绝访问现有依赖缓存，获准在沙箱外重跑同一构建后通过，未修改业务代码规避错误。
- 浏览器界面曾通过临时题库和模拟识别服务检查桌面/窄屏布局、草稿回填与刷新保留、不可用提示；未采集实际音频，不代表真实服务连通性。
- 用户授权后，新建 `bagu_speech_20260828`（API29 / Android10 / WebView74）和 `bagu_speech_api36_20260828`（API36 / Android16 / WebView133）隔离模拟器；未安装到已有设备、未清除已有应用数据、宿主机音频输入关闭。两台分别运行 `AndroidSpeechAcceptanceTest`，最终均 **6/6 通过**，无跳过。API29 使用 x86_64 debug，API36 使用与下方交付副本完全相同的 ARM64 release APK，经模拟器原生桥转译运行，不等同于物理 ARM 手机。
- API36 首次冷启动有系统界面无响应弹窗，第一轮 6 项中 2 项因页面加载/生命周期等待超时而失败。保留此失败事实；等待系统恢复并关闭弹窗后，未改源码、未放宽测试期限，重跑完整 6 项用例，22.05 秒全部通过。API29 首轮 19.36 秒全部通过。
- API29 另做实际页面操作：在新建模拟器中临时禁用识别服务，点击语音输入显示“系统语音识别服务不可用”；恢复服务后在真实权限弹窗选择拒绝，显示麦克风权限错误。两种情况下原答案 `Existing_answer` 均保留、提交按钮恢复可用。截图为 `dist/android/qa/speech-20260828/api29-unavailable.png` 和 `api29-permission-denied.png`。识别服务已恢复。

测试包为 `dist/android/qa/speech-20260828/bagu-speech-public-arm64-test.apk`，29,811,157 字节，SHA-256：`6a1acf35d1dcfcba1f8a9440bcefcd09d68671ce73d201e8e339c5130aad4cc1`。证书 SHA-256：`ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3`，与旧安装包身份一致。已核对安装后的 APK 哈希、当前网页源码字节、清单（仅 INTERNET / RECORD_AUDIO）、空种子及原生库允许列表；`verify_android_apk.py --flavor public --expected-questions 0 --readelf ...`、`apksigner verify` 和 `zipalign -c -P 16 4` 均通过。

**此为独立测试构建，不是新版正式发布。** 仍使用 versionCode `1` / versionName `0.1.0-beta.1`，首次安装为空题库，不含工作站数据、配置、Key 或签名材料；不应为安装测试包而卸载已有应用或强制降级。旧命名 APK 的哈希仍为历史记录中的值，未覆盖。真实语音识别质量、联网服务可达性与各厂商中文支持仍需在目标手机验证，不作普遍可用承诺。

## 前置条件

- 使用 Windows PowerShell，准备好 JDK 17、Python 3.11 和 GNU `readelf`。脚本的 `-JavaHome`、`-BuildPython`、`-ReadElf` 可覆盖本机路径；默认分别为 `C:\Program Files\Java\jdk-17.0.10`、`E:\Anaconda\python.exe`、`C:\Program Files\mingw64\bin\readelf.exe`。
- 当前仓库固定 Gradle 9.1.0、Android Gradle Plugin 9.0.1、Chaquopy 17.0.0（Python 3.11）；`minSdk=29`，`compileSdk/targetSdk=36`，交付 ABI 为 `arm64-v8a`。
- 项目本地 `.android-sdk/` 中须有 SDK Platform 36 和 Build Tools 36.0.0，`.toolchains/gradle-9.1.0/` 中须有 Gradle，`.gradle-user-home/` 中须备齐相关构建依赖。脚本不负责安装工具链或准备依赖缓存；这些目录不随 Git 分发。脚本只设置当前 PowerShell 进程的环境变量，不会改系统环境变量。
- `internal` 构建从仓库根目录经授权的本地 `bagu.db` **只读生成清洁种子**，清除进度、会话和评分结果；当前交付校验要求 408 题。`public` 使用空种子。源题库不在 Git 中，因此仅克隆仓库不足以复现内部题库构建；不得用原始工作站数据库直接替代打包种子。
- 首次在空的 `.signing/` 下运行 `SetupSigning` 会用 JDK `keytool` 生成本地身份；它把密码仅放入当前进程环境变量，并写入被 Git 忽略的 `release.jks` 与 `keystore.properties`，以及公开的 `certificate-sha256.txt` 指纹 pin。已有身份必须从受控离线备份成对恢复。

不要在终端、日志、文档或版本库中打印、复制或提交签名密码、keystore 或属性文件。

## 构建与校验

以下命令会创建/验证本地签名身份并生成构建产物，只在需要构建时执行。从仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\android.ps1 -Mode SetupSigning
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\android.ps1 -Mode Build
```

`SetupSigning` 只会在完全没有签名标记时创建身份。后续执行会用 `keytool` 验证 keystore、私钥密码和公钥指纹，完整有效的身份保持字节不变；缺少 key、属性或已有 pin 中任一项的部分/无效身份会失败，绝不覆盖或替换。既有稳定身份没有 pin 时只接受既定的发布证书指纹。`Build` 强制 `arm64-v8a`、版本 `0.1.0-beta.1`/`1`，构建 internal 与 public release，运行 debug 单元测试与 release lint，并校验：

- internal 的 408 道清洁种子和 public 的空种子；两者均无进度、会话或结果；
- 显式网页、品牌、离线字体/许可证和两份应用 Python 模块；没有 `.env`、`settings.json`、签名材料、工作站数据库或会话历史；
- 所有外层、Chaquopy bootstrap-native 与嵌套 `.imy` 原生库均精确匹配已审阅 manifest，并检查 GNU_RELRO 和 16 KiB ELF LOAD 对齐；
- 签名证书 DER SHA-256、`aapt dump badging`、`zipalign -c -P 16 4` 和 APK SHA-256；交付目录的 `certificate-sha256.txt`、`SHA256SUMS` 和 `install-notes.txt` 也会与精确 APK 交叉校验。

`Build` 会替换 `dist/android/` 中的同名交付文件；需要保留旧版本时先另行归档。脚本不会自动递增版本：正式准备后续更新时，必须同步调整版本参数、交付文件名及相关校验，不能把再次运行当前脚本当作发布更高版本。

输出位于 `dist/android/`：

```text
八股助手-0.1.0-beta.1-arm64-v8a.apk
SHA256SUMS
certificate-sha256.txt
install-notes.txt
```

可只重跑交付物校验：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\android.ps1 -Mode Verify
```

交付文件名含中文。若 Windows SDK 工具不能解析该 Unicode 路径，脚本会创建仅在校验期间存在的 ASCII 副本，先后 SHA-256 必须与精确交付文件相同；Python 内容检查仍直接读取精确中文文件。脚本不会更改系统代码页。

## 源码回归测试

在仓库根目录运行：

```powershell
python -m pytest test/test_bagu.py -q
python -m pytest test/test_bagu.py test/test_android_project.py -q
```

第一条需要 pytest 和 Node.js；第二条还需要上述 Windows/Android 工具链与离线 Gradle 缓存，部分项目测试使用固定的本机工具路径，迁移环境前应检查 `test/test_android_project.py`。测试使用临时数据库、临时签名材料和模型桩，不应读取真实 Key 或修改真实题库。

Java 策略测试位于 `android/app/src/test/`，设备仪器测试位于 `android/app/src/androidTest/`。pytest 通过不能替代 Java 测试、release lint、精确 APK 校验或目标设备运行验证；本次文档更新也不重新签名或生成 APK。

## 安装、更新与备份

首次安装：

```powershell
adb install .\dist\android\八股助手-0.1.0-beta.1-arm64-v8a.apk
```

更新前，在应用“设置”中导出 `.bagu-backup`，选择手机中的保存位置。备份仅含题目、答案、来源 URL 及复习进度；**不含评分分析、模型配置、API Key、草稿或会话历史**。恢复按分类+题干合并，已有题的答案、URL 和进度会被备份覆盖，新题新增，备份外的题目不会删除，已有会话和评分分析历史不变。恢复前先备份当前数据，有未结束的练习时应先结束练习。损坏或超限备份会整体拒绝，不应部分写入。

Android 与电脑的题库、配置及草稿相互独立，不会自动同步。桌面服务已有备份导出/恢复 API，但当前桌面界面没有相应按钮，接口见 [README](../README.md#http-api仅本机)。

导出和恢复使用相同上限：最多 **10000 题、ZIP 文件 20 MiB、两个 JSON 成员解压后合计 50 MiB**，且题目字段必须有效。题库累计超过上限或含无效数据时，导出会明确报错，不截断题库、不更改原数据，也不会把不可恢复的档案报告为成功。请检查题目字段和题库大小、确认保存位置可写；系统选择器可能留下零字节占位文件，这不是有效备份。进行中的练习不妨碍导出，但备份不包含该会话。

保持相同包名和签名的覆盖更新会保留所有应用私有数据，包括题库、进度、分析、模型配置、Key 与原生草稿状态。实际后续版本应使用更高 versionCode；下面是对当前同版本 APK 执行替换安装的可选调试命令，不会生成更高版本：

```powershell
adb install -r .\dist\android\八股助手-0.1.0-beta.1-arm64-v8a.apk
```

**卸载会删除全部应用私有数据**。跨卸载迁移只能恢复 `.bagu-backup` 包含的题目/进度，不能恢复被排除的分析、配置、Key 或草稿；不要把应用私有目录、数据库、设置或密钥当作迁移方式。

离线、受控地备份 **成套** 的 `release.jks`、`keystore.properties` 与公开 `certificate-sha256.txt`。丢失稳定签名身份后无法向已安装用户交付可信更新，通常只能让用户卸载并重新安装。公开 flavor 仅用于验证空种子，不是可发布的公开版本。

## 历史 APK 验收记录（2026-08-27）

本节保留当时的构建与设备证据，不表示当前源码已完成同等验收；后续源码状态见文档开头。

2026-08-27 已在 API29 / Android10 / WebView74 的 x86_64 模拟器 QA 构建，以及 API36 / WebView133 的 arm64 转译模拟器上实际安装、启动和测试。API36 运行的是与交付文件 SHA-256 相同的签名 arm64 release APK；API29 的 x86_64 测试包不是手机交付包。覆盖真实系统文件选择器、取消/坏档、CSV、离线复习、键盘单次返回后底部导航恢复、旋转和窄屏/平板/折叠屏尺寸模拟。确定性模型错误使用测试 APK 内的合成 HTTPS 配置及临时模型桩，不代表真实模型服务联通。

已实际验证进程结束重启和同签名更高版本覆盖：API36 从 internalRelease v1 更新到空种子的 publicRelease QA2，API29 从 publicDebug v1 更新到 QA2。完整题目/调度/评分分析、模型配置及全部原生草稿/提交状态逐项保持一致；public 空种子未清除内部 Beta 数据。QA2 仅用于本地升级验收，不是公开发行，交付包仍为版本1。

最终备份修复又在两台原模拟器上以独立 public QA3 覆盖验证：原有完整数据/配置/草稿保持一致；真实系统文件选择器下，临时超限导出明确失败且只留零字节占位，恢复限制后重试成功，导出档案通过本应用解析。`viewport-fit=cover` 的实际窗口检查确认原生安全区只应用一次，没有叠加网页底部留白。QA3 同样仅为测试包，不是命名交付 APK。

当时的命名 APK 为 `0.1.0-beta.1`，大小 30,092,181 字节，SHA-256 为 `2c3ad3a05ac912639ea99d12ffb7f8ef11f0444cf7707aa256cad3b1c5181c5a`。它又在独立的全新 API36 / WebView133 模拟器中完成首次安装与冷启动：应用内首页显示 408 道题、408 道待复习、已掌握 0、没有进行中的会话；设备上安装文件的哈希与交付 APK 完全相同，未清除或降级原有 QA 数据。

2026-08-27 对应源码当时通过 293 项 Python/项目回归测试（零失败、零跳过、零告警）；两种 debug 配置各有 6 项 Java 策略测试通过，最后构建复用了这些未变化测试的结果。两种 release 构建和交付物校验通过，release lint 各为零错误、4 项告警；当时的审查及一轮修复复审未留下已知的严重或重要问题。这不涵盖 2026-08-28 后续发现并修复的评分事务问题。非阻断后续项包括 CSV 临时缓存回收、lint 告警，以及联网测试跳过条件的收紧；不把这些描述为已修复。

## 尚未验证与已知限制

两台模拟器均为 4 KiB 内存页。虽然交付物已做 16 KiB ELF/ZIP 对齐检查，尚无真实 16 KiB 设备运行证据；也没有物理 arm64 手机、真实折叠铰链或各厂商系统的覆盖。模拟尺寸和转译运行不能替代这些真机验证。搜索键在 API29 保持键盘打开是实际输入法行为；测试确认系统返回关闭键盘后导航恢复，不把它等同于所有输入法的 Done 键行为。

本轮**真实远程 HTTPS 图片加载未通过、仍未验证**：公开图片在宿主机返回 200，但两台模拟器解析该域名均报 `unknown host`，API36 WebView 回报 `ERROR_HOST_LOOKUP`（-2），API29 在有界等待内未完成。实测未触发 `img-src` CSP 拒绝；该网络探测在测试报告中明确标为跳过，不计作成功加载。没有修改 DNS、证书、TLS 或放宽网络策略来绕过问题；请在有正常网络的目标手机上另行确认远程图片与模型服务。
