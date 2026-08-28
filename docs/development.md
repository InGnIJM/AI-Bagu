# 开发与验证

[文档导航](README.md) · [架构与数据](architecture.md) · [HTTP API](api.md) · [Android Beta](android-beta.md) · [验收记录](validation.md)

本文按已提交源码基线 `71fbbfd` 描述开发入口与验证方法。具体源码、构建产物和设备的验收结论分开记录；测试通过不意味着 APK 已更新或发布。

## 环境分层

| 工作内容 | 环境要求 |
| --- | --- |
| 桌面 CLI / 网页运行 | Python；现有验证使用 Python 3.11 系列，核心只用标准库 |
| 核心、HTTP、网页回归 | Python、pytest、可从命令行调用的 Node.js |
| Android 项目回归及本地构建 | Windows PowerShell、JDK 17、Python 3.11、项目 Android SDK/Gradle 与 Chaquopy 缓存；APK 检查还用 GNU `readelf` |
| Android 应用使用 | Android 10+，交付 ABI 为 `arm64-v8a`；用户设备无需自行安装 Python |

无需为核心增加第三方 Python 依赖；pytest 是测试工具，不是应用运行时依赖。离线字体与图标已在仓库内。抓题、模型评卷、远程答案图片需要相应网络，语音识别可用性由系统/浏览器服务决定。

Android 工具链的固定版本、目录、覆盖参数和缓存准备见 [Android Beta](android-beta.md)。不要把维护者本机 JDK/Python 的绝对路径当作通用安装要求。当前 `test/test_android_project.py` 的部分测试仍有本机工具路径假设；迁移环境前检查该文件，不能声称只安装 pytest 就可在任意系统运行完整项目测试。

## 源码目录与职责

```text
bagu.py                            核心、CLI、HTTP、抓题、评卷、备份与配置
web/index.html                     桌面和 Android 共用的唯一页面
android/app/src/main/java/         原生活动、WebView 策略、存储/文件/语音桥接
android/app/src/main/python/       Android 私有路径与 Python 服务启动层
android/app/src/test/              Java 宿主策略与语音状态机单元测试
android/app/src/androidTest/       设备仪器测试及隔离验收 fixture
assets/fonts/                      离线字体及许可证
assets/branding/                   品牌图片
scripts/android.ps1                本地签名准备、构建及交付校验
scripts/build_android_seed.py       从授权只读题库生成清洁种子，或生成空种子
scripts/verify_android_apk.py       APK 允许列表、原生库及私有数据检查
test/test_bagu.py                   核心、HTTP、网页行为回归
test/test_android_project.py        Android 项目、桥接、运行时和打包契约回归
test/speech_input.test.cjs          网页/原生语音交互的 Node.js 回归
test/manual_speech_server.py        使用临时题库和模拟语音服务的浏览器检查入口
docs/                              用户、接口、架构、开发及验收文档
docs/superpowers/specs/             已定设计及后续协议补充
docs/superpowers/plans/             历史实现计划，不替代当前运行说明
AGENTS.md                          当前项目协作规则
```

Java 源码包为 `io/github/ingnijm/baguhelper/`：`MainActivity` 管理页面与生命周期，`HostPolicy` 限制导航与 URL，`RuntimeHost` / `android_runtime.py` 启动隔离运行时，`NativeBridge` 暴露受限存储、文件和语音能力，`SpeechInput` / `AndroidSpeechBackend` 管理识别状态与系统服务。不要把 Android 的随机端口 origin 当作可靠跨启动存储。

本地生成且禁止提交：`.env`、`settings.json`、`bagu.db`、`.signing/`、`.toolchains/`、`.android-sdk/`、Gradle/Android 缓存、`dist/`。桌面服务日志默认位于 `.superpowers/bagu-server.log`，Android 日志位于私有 `logs/`；日志不是源码，不应携带 Key、令牌或作答正文。

## 诊断日志（当前工作区新增）

桌面由 `bagu.py` 的白名单过滤、轮转日志和独立于数据库的诊断接口负责；网页在主脚本之前注册错误监听，通过专用请求头批量写入。Android 由 `BaguApplication` 提前初始化 `AndroidDiagnostics`，`DiagnosticPolicy` / `DiagnosticStore` 校验并重过滤历史记录；导出使用独立执行器和系统文件选择器，不调用 Python 启动或工作队列。

排查隐私问题时只使用临时目录与假 Key/令牌/作答。固定文件名为 `bagu-server.log`、`bagu-web.log`、`bagu-native.log` 及各自 `.1`—`.3`；禁止将旧日志直接压缩发送。服务日志为 5 MiB、网页/原生日志为 1 MiB，每个来源保留 3 份历史；导出各来源最近最多 2 MiB，ZIP 最多 8 MiB。缺失、不完整及不可读来源必须在清单中说明。

回归入口为 `python -m pytest test -q` 和 `node --test test/speech_input.test.cjs`。项目工具链就绪并获准使用已有签名配置后，以 **Windows PowerShell** 执行 `scripts/android.ps1 -Mode Check`，运行 `:app:testPublicDebugUnitTest` 与 `:app:lintPublicRelease`，不会生成正式签名 APK。当前脚本的整数校验不兼容 PowerShell 7 `ConvertFrom-Json` 返回的 Int64；不要因此修改 `version.json`。

`DiagnosticAcceptanceTest` 用于隔离设备上的启动、导出及隐私验证；仅编译它不能证明设备行为。API 29 与较新 Android 上的真实文件选择器、写入失败、旋转、进程终止及 WebView/Python 启动失败需单独验收。设备安装与签名交付遵循原有授权流程，结果记录在 [验收记录](validation.md)，不能用历史 APK 的通过结果代替。

更新诊断复用该存储：`UpdateFailure` 负责固定错误码，`UpdateDiagnostic` 是操作上下文绑定的不可变事件，`UpdateCheckSummary` 负责最多 4 KiB 的检查历史与中断恢复。新增字段仅开放给 `native.update`，同时验证落盘与 ZIP 重过滤；`getUpdateState()`／`bagu-update` 不变，扩展契约见 [API 文档](api.md#android-更新状态与诊断当前源码扩展)。不要将最近检查编号、当前下载／安装编号与桥接 operationId 混为一谈。

更新／发布重点回归可运行 `python -m pytest test/test_update_policy.py test/test_update_web.py test/test_github_release.py test/test_release.py -q`。Java 测试同时需通过 Android Gradle 编译面，单独 JDK 编译可用的库方法不一定在 Android SDK 编译面存在。未获准读取正式签名配置时，使用[隔离源码与假签名配置](android-beta.md#源码与设备检查)运行单测与 lint；禁止复制真实题库或密钥。

发布工具测试必须 mock GitHub CLI、匿名下载、构建和签名边界；`test_github_release.py` 的 socket/DNS 禁止外网夹具不能移除。`init-feed` 离线 dry-run 可在当前脏工作区运行；真实执行、Pages 配置和发布仍需另外授权，详见[发布指南](data-transfer-and-updates.md#维护者独立初始化更新源)。

## 修改前的边界检查

1. 读取根目录 [AGENTS.md](../AGENTS.md) 及目标子目录规则，检查工作树，保留用户和其他任务的未提交改动。
2. 修改会话、抽题或评分前，先看 [架构与数据](architecture.md) 及相关 spec；规则尽量落在 `bagu.py` 核心函数，让 CLI/HTTP 保持薄封装。
3. 网页仍放在 `web/index.html`，不另开配置页面，不引入外部运行时字体；沿用现有颜色、圆角和触控目标。
4. 保持会话唯一、一次评分、skip 不调度、提交前完成结果渲染、submission 幂等及 Android 隔离边界。
5. 用失败测试锁定正常、边界与异常行为，再做最小变更。数据库迁移先在临时库验证，正式升级真实库须另行完整备份。

项目规则不允许把真实工作站数据库、进度、模型配置或签名材料直接打包。`internal` 种子来自明确授权的只读源，`public` 为空种子；克隆仓库不会自动获得内部题库或本地工具链。

## 测试矩阵

从仓库根目录运行，首次使用时可在选定的 Python 环境安装 pytest：

```bash
python -m pip install pytest
python -m pytest test/test_bagu.py -q
```

Android 工具链和本地缓存就绪后：

```bash
python -m pytest test/test_bagu.py test/test_android_project.py -q
```

| 检查层 | 入口 | 能证明什么 / 不能证明什么 |
| --- | --- | --- |
| 核心与网页 | `python -m pytest test/test_bagu.py -q` | SQLite、会话、评卷、HTTP、网页脚本回归；不证明真实模型或浏览器服务连通 |
| Android 项目契约 | 加上 `test/test_android_project.py` | 项目配置、隔离运行时、桥接和打包策略；包含 PowerShell/JDK/离线 Gradle 检查，不是全部 APK/设备验收 |
| 语音脚本 | `node --test test/speech_input.test.cjs` | 实际页面脚本配合模拟浏览器/原生识别边界；也由核心 pytest 调用，不采集真实音频 |
| Java 单元测试 | `android/app/src/test/` 的 Gradle 单元测试任务 | 宿主、语音、更新状态机／网络分类、诊断过滤与 ZIP；不替代 WebView 或系统安装器真正运行 |
| Android lint | 对目标 variant 运行 release lint | Android 静态问题；零错误不等于设备兼容性或无警告 |
| APK 校验 | `scripts/android.ps1 -Mode Verify` 及 APK 校验脚本 | 指定产物的签名、内容、原生库及哈希；不会重建，也不能单独证明产物对应最新源码 |
| 设备仪器与手动验收 | `android/app/src/androidTest/` 和隔离设备 | 该 APK 在该系统、WebView、ABI、页面大小和服务环境中的表现；不能泛化为所有手机 |

`Build` 不只是单测命令，它还会生成签名 APK。旧基线脚本同时处理 internal/public，当前版本化脚本已将二者分开；请按 [Android 指南](android-beta.md#构建与校验)核对所用版本、flavor 与计划，勿照搬历史任务列表。构建前须准备缓存和稳定签名身份，internal 还需授权题库；不在普通文档/单元测试任务中顺带构建或安装。

## 测试隔离要求

- pytest fixture 使用 `tmp_path`；传入显式临时数据库，或将 `DB_PATH` monkeypatch 到临时路径。不要运行会触碰真实 `bagu.db` 的测试。
- 模型配置、日志目录、原生数据目录和签名测试材料也放临时目录；不能读取真实 `.env` 或复用真实签名身份做破坏性用例。
- 抓题和模型请求使用 mock/桩，不打真实来源或模型 API。密钥用 `sk-test` 等假值，检查响应和日志不会泄露 Key。
- 保留并发会话、同题重复评分、跨题 submission 冲突、评分结果重放、断流与渲染失败回滚等测试；仅检查 HTTP 200 不足以判断流式评卷成功。
- Android 设备测试使用可丢弃的隔离设备/应用数据；安装、清除数据、真实麦克风或外部服务调用应作为单独授权的验收步骤。

## 语音界面模拟服务

无需录音即可检查真实页面中的输入、错误提示及草稿交互：

```bash
python test/manual_speech_server.py --scenario success
```

打开终端打印的本机地址；默认端口 18766，可加 `--port 18767`。支持 `success`、`unavailable`、`denied`、`network`、`timeout` 五种场景，例如：

```bash
python test/manual_speech_server.py --scenario denied --port 18767
```

该工具在临时目录创建独立题库和一轮示例会话，通过实际页面/HTTP 加载模拟 `SpeechRecognition`，不打开麦克风、不读取工作站数据库或模型配置，也不发送远程音频。`success` 场景的最终结果在点击“结束转写”后回填。按 Ctrl+C 关闭服务并清理临时目录。

此检查不代表真实浏览器识别服务、Android 系统识别器或厂商输入法可用；网络、权限、语言及设备兼容性仍需目标环境验证。用户操作和隐私边界见 [用户指南](user-guide.md)。

## 交付前验证与记录

运行与改动最近的测试，再按影响范围扩大到完整回归、Java、lint、构建或设备检查。读取完整输出，记录命令、退出码、失败/跳过情况和精确源码提交；工具链不足时明确未运行项，不把静态检查写成运行通过。

文档修改至少检查相对链接、命令/字段与实际源码一致、历史状态与当前操作分离，以及是否误写了未实现功能。不要把某次历史测试总数写成日常验收门槛；历史证据集中在 [验收记录](validation.md)。

交付 APK 时还需记录精确文件、哈希、签名、manifest、原生库和设备验收范围。源码合入、测试通过、APK 构建、安装及公开发布是不同事件；更新文档或运行 `Verify` 不会自动更新 APK。未经明确要求，不提交、推送、创建 PR、上传或发布产物。
