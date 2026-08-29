# 数据迁移、Android 更新与本地发布

[项目首页](../README.md) · [Android 指南](android-beta.md) · [历史验收记录](validation.md)

本文说明已发布 beta.3 的迁移／更新实现与维护者发布流程。公开版本来自 Tag `v0.1.0-beta.3`、提交 `8cc586f`，不包含开发工作区后续未提交的格式恢复改动。发布、构建、设备检查的证据及限制见[beta.3 验收记录](validation.md#beta3-公开发布)；下面的命令说明本身不构成再次执行的授权。

## 已上线的版本与更新清单

截至 2026-08-28，[beta.3 Release](https://github.com/InGnIJM/AI-Bagu/releases/tag/v0.1.0-beta.3) 已公开为预发布版，code 为 `3`，提供 [public ARM64 空题库 APK](https://github.com/InGnIJM/AI-Bagu/releases/download/v0.1.0-beta.3/bagu-0.1.0-beta.3-public-arm64-v8a.apk)，下载不需要登录。

更新清单是应用读取的“小型版本目录”，不是 APK 或题库。它告诉应用版本编号、兼容要求、下载地址、大小及 SHA-256；APK 本体仍从 GitHub Releases 下载。

| 入口 | 当前内容 |
| --- | --- |
| [源码 Tag](https://github.com/InGnIJM/AI-Bagu/tree/v0.1.0-beta.3) | 精确发布源码 `8cc586f` |
| [Beta 清单](https://ingnijm.github.io/AI-Bagu/updates/beta.json) | `0.1.0-beta.3` / versionCode `3`，指向具体 Tag 的 APK |
| [Stable 清单](https://ingnijm.github.io/AI-Bagu/updates/stable.json) | `release:null`，暂无稳定版；是检查成功，不是 HTTP 404 |

Release 网页和两份清单已匿名核验为 HTTP 200。beta 安装检查两个通道，所以 Stable 文件仍须存在。已有更新入口的 beta.2 可手动检查并升级；安装 beta.3 后，两通道都成功且无更高兼容版本时显示“当前没有兼容的新版本”。

## 电脑与手机之间迁移

两端数据独立保存，不会自动同步。桌面进入「设置与数据迁移」，Android 进入「设置」；空题库页面也有导入入口。

| 操作 | 文件内容 | 导入同名题时 |
| --- | --- | --- |
| 导出纯题库 | 分类、题干、答案、来源链接 | 覆盖答案和链接，保留本机复习进度 |
| 导出含进度备份 | 上述内容，加等级、复习次数、答对次数、下次到期与最近复习日期 | 覆盖答案、链接和复习进度 |
| 导入题库／备份 | 读取 `.bagu-backup`，根据文件中的类型处理 | 先完整校验、预览，再确认导入 |

“同名题”指分类与题干一致。两种模式都会用文件中的答案和链接覆盖已有内容，**空答案／空链接也会覆盖**。纯题库中的新增题从零次复习、无调度日期开始；本机其他题目不会被删除。已有会话、评分分析和历史答案不被改写。

两个导出模式都不包含模型配置、API Key、草稿、会话或评分分析。含进度备份也不是完整 SQLite 备份；数据库升级保护与跨设备迁移是两件事。

### 推荐操作顺序

1. 在来源设备选择导出模式，把文件保存到应用外；桌面检查浏览器下载记录，Android 在系统文件选择器中选择保存位置。
2. 将文件传到目标设备。文件可能包含个人题库与学习进度，只交给你信任的人或存储位置。
3. 目标设备先结束当前练习，并另存一份含进度备份；导入不会替你自动结束练习。有进行中的会话时仍可导出。
4. 选择「导入题库／备份」，核对类型、题数、创建时间、来源版本和覆盖规则，再确认。Android 使用原生确认框，不把文件正文传给网页脚本。
5. 完成后核对新增／更新题数、几道同名题的答案和复习进度。

预览与实际导入使用同一份已读取字节；确认前更换原文件，不会悄悄改变已预览的内容。取消选择或确认不会执行备份合并。Android Activity 重建后仍须明确确认；进程死亡不会自动重放导入。若提示“是否完成未知”或网页未收到结果，先重新打开并核对数据，**不要直接重复导入**；含进度备份可能覆盖后来产生的进度。

### 格式、兼容与失败处理

- 当前导出备份格式 v2，类型为 `questions` 或 `progress`；仍可读取 v1，统一按含进度备份处理。旧应用不能因此自动获得读取 v2 的能力，应先更新应用。
- 备份格式 v2 与 SQLite `user_version=2` 相互独立，不应据此修改数据库版本或用旧程序打开已升级数据库。
- ZIP 只能含 `manifest.json` 和 `questions.json`；最多 10000 题、压缩文件 20 MiB、解压 JSON 合计 50 MiB。
- 校验成员名、路径、重复成员／JSON 字段、加密标记、数据类型、题目字段、重复题、题数与 SHA-256；不通过则整批拒绝。恢复在事务内检查会话锁，失败回滚，不只导入前半部分。
- CSV 是另一种导入：UTF-8、最多 2 MiB／5000 题，重复题跳过、不覆盖，不携带进度。不要把 CSV 和 `.bagu-backup` 的合并规则混用。

程序接入：`GET /api/backup/export?mode=questions` 导出纯题库，`?mode=progress` 导出含进度备份，省略 mode 默认 `progress`；空值、非法值或重复 `mode` 返回 400。`POST /api/backup/inspect` 和 `/api/backup/restore` 都只接受 `{ "archive_base64": "…" }`；inspect 完整校验但不执行备份合并，restore 遇到 open 会话返回 409。HTTP 预览仍会经过公共数据库初始化，可能创建或迁移数据库，不能用于数据库的只读迁移预演；核心／Android 原生预览函数不访问数据库。Android API 仍需原有进程令牌，不应绕过原生文件边界。

## Android 应用更新

更新仅面向已包含更新功能的 Android 安装包；桌面网页没有 APK 安装入口。旧 internal Beta 若没有更新卡片，需要先手动取得可信、同包名、同签名且 versionCode 更高的 APK，进行一次覆盖更新。不要先卸载；先导出含进度备份。public 空种子只用于首次初始化，不用于覆盖已有题库；真实覆盖升级仍须按交付版本验收。

### 检查与下载

- 「设置」显示当前版本、自动检查开关、上次检查状态和手动检查按钮。自动检查默认开启，仅在应用前台且页面准备好时触发，通常与上次检查**尝试**间隔 24 小时；失败尝试也计入间隔。手动检查不受该间隔限制，但不会并发启动已有操作。
- 通道由安装包版本配置决定，不是用户任意填写的下载地址。stable 只检查 stable；beta 检查 beta 和 stable，选兼容设备且高于本机的最大整数 versionCode。部分请求失败会明确提示，不能把部分成功说成“已是最新”。
- 自动检查失败只更新设置页，不弹窗、不切页或打断练习；手动检查可看到逐通道原因和 `n_` 开头的反馈编号。`release: null` 表示成功读取的空通道；只有所有应检查通道都成功且没有兼容新版，才显示“当前没有兼容的新版本”。部分失败时仍可使用已验证的新版本，但无法确认是否还有更高版本。
- 可用版本通知中的「稍后」只关闭当前提示，不关闭自动检查，也不永久忽略此版本；之后仍可从设置查看。具体设备行为需纳入新包验收。
- 自动检查**不会自动下载或安装**。点击「下载更新」才开始，显示大小、文本说明和进度。可取消下载；应用进入后台也会取消下载，没有后台下载或断点续传承诺，返回后可重新下载。
- 更新清单最多 64 KiB，APK 最多 128 MiB。只接受固定仓库的版本 Release 下载地址；HTTPS 重定向逐跳校验允许的域名。下载到私有缓存，完整检查长度、SHA-256、包名、版本、最低系统版本、ABI 和证书信息后才出现安装入口。

### 检查失败时如何反馈

在更新卡片查看 Beta／Stable 各自的结果，例如 HTTP 404、域名解析失败、超时、安全连接失败或清单校验失败。HTTP 状态和应用错误码分别记录；404 不能单凭这个状态就认定是“尚未配置 Pages”，也可能是更新清单尚不可访问。不要通过关闭证书校验、改成 HTTP 或随意更换下载地址解决。

记录失败时间、操作步骤及反馈编号，然后使用「设置 → 问题诊断 → 导出诊断日志」。一次检查的两个通道共用编号；日志包中的 `native.update` 记录阶段、结果、通道、错误码、HTTP 状态和耗时，不记录完整 URL、远端正文或异常消息。网页不再重复上报同一原生错误。

最近一次检查仅保存不超过 4 KiB 的安全摘要。若应用进程在检查期间终止，下次启动显示“上次检查中断，请重试”，不重放旧操作。旧安装没有摘要时按未知状态显示；摘要写入失败时本次结果仍可在内存中显示，不能保证跨进程保留。日志写入失败不改变更新结果，安装授权和文件交接的持久化检查仍然严格执行。节流跳过或忙时重复点击不会抹掉已有失败编号。

### 安装、权限与恢复

点击「安装更新」会再次核对缓存与本机版本。正在练习、评卷、语音输入、文件选择／导入导出或待确认导入时，安装会被阻止；先自行完成或取消相关操作，不会自动 skip。

若系统未允许本应用安装未知应用，先显示说明，由你点击打开设置。授权返回后仍要再次点击安装，不会自动接着安装；设备策略禁止该来源时显示固定原因，不循环打开授权页。

当前开发源码把再次校验通过的 APK 写入系统 `PackageInstaller.Session`：使用完整安装模式，写入后执行 `fsync`，再用显式、可变且只指向应用内非导出 Activity 的回调提交。Android 15/API 35+ 创建回调 `PendingIntent` 时显式授予 creator BAL，只用于启动系统返回的安装确认界面；确认回调到达、启动成功或失败分别写入固定 `confirm` 阶段，不记录 Intent、路径、Session ID 或系统错误正文。不再把 `content://` APK URI 交给通用文件打开流程，也不指定厂商安装器包名。最终完整签名验证仍由 Android 安装器执行，应用解析到的证书信息本身不等于完整 APK 密码学验签。该源码修复尚未自动变成已发布 beta.3 APK；须另行构建、签名和设备验收。

系统回调为成功、打开确认界面或下载完成，都不直接代表升级已经由本应用核实。只有后续进程启动核对实际已安装版本后才报告成功；用户取消后废弃该系统会话并保留完整缓存，允许再次点击安装。不支持同版本覆盖或降级。

重启会丢弃未完成下载，并重新校验完整缓存；缓存缺失或损坏时应回到重新检查／下载流程，不可跳过校验或使用损坏文件。系统 session ID、目标版本和安装租约仅在原生私有状态中保存，不进入网页 JSON、日志或诊断导出。发现尚未结束的 session 时不自动重试：先核对实际安装版本，再由用户处理系统确认界面，或明确点击恢复以废弃该 session；恢复后重新校验保留的 APK。Android 16.1+ 若返回开发者验证失败 extra，显示固定错误码 `1303`，不记录系统原始错误正文。此恢复路径与系统权限、生命周期、安装取消和两版本升级一起列入设备验收，不能用单元测试替代。

## 维护者：版本与本地构建

普通手机用户不需要执行以下命令。所有命令从仓库根目录运行；本文仅提供说明，不授权自动提交、推送、使用签名材料或公开发布。

版本的唯一入口为 [version.json](../version.json)：`versionName`、递增整数 `versionCode`、`channel`。Gradle、发布脚本和 Android 运行时共用该版本；桌面导出也读取它。修改版本文件不会更新已安装 APK。

先看本地构建计划：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\android.ps1 -Mode Plan
```

准备好 [Android 工具链与既有签名](android-beta.md) 后，`-Mode Check` 运行 Java 单元测试与 release lint；现有 Gradle 配置即使只运行 Check 也会读取签名属性，未经凭据授权应使用隔离源码和假签名配置做测试，不能直接在正式签名目录运行。`-Mode Build` 默认只构建空题库 public 包，`-Mode BuildInternal` 才是明确授权的内部清洁种子构建。`-Mode Verify` 校验已有 public 交付物，不重新编译。均不能代替设备验收。

发布用稳定签名不得重新生成；`.signing/` 中 keystore、密码和属性文件保持私有，只能发布公开证书指纹。`SetupSigning` 不是修复丢失签名的步骤。internal 包和源题库不得出现在公开附件中。

public 交付目录是 `dist/android/<versionName>/public/`，允许且必须仅有六个附件：版本化 public arm64 APK、`SHA256SUMS`、`certificate-sha256.txt`、`update.json`、`INSTALL.md`、`RELEASE_NOTES.md`。APK 的实际清单、签名、空种子、资源允许列表、原生库及对齐和哈希都需要核对，不能只检查 `version.json`。

## 维护者：独立初始化更新源

本仓库已完成清单初始化和 Pages 配置，普通使用或检查更新无需重新初始化。下方保留首次部署／维护说明：先准备清单分支，再由维护者配置 Pages。此流程与源码发布解耦，无需工作区干净、版本递增、源码已推送或存在 APK／Release。默认 dry-run 完全离线，不调用 GitHub CLI、不使用凭据或写远端：

```powershell
python .\scripts\release_github.py init-feed
```

确认允许使用已登录的 GitHub CLI 和创建公开清单分支后，才显式执行：

```powershell
python .\scripts\release_github.py init-feed --execute --confirm-repository InGnIJM/AI-Bagu
```

脚本检查固定 origin、仓库公开且未归档、写权限和 Git 数据访问，之后才将目标分支的 404 当作缺失。只操作 `codex/update-feed`，不切换本地工作区；该分支只允许 `.nojekyll`、`updates/beta.json`、`updates/stable.json`。缺失 beta 通道写入 `{"schema_version":1,"channel":"beta","release":null}`，stable 文件对应使用 `"channel":"stable"`；已有合法清单保留原字节。非法清单、额外文件、符号链接或并发冲突都会停止，不覆盖、不强推。

`init-feed` 成功本身仅表示“清单分支就绪”。维护者还须在 GitHub Pages 将来源设为 **Deploy from a branch → `codex/update-feed` → `/ (root)`**，脚本不会修改 Pages 设置。配置方式见 [GitHub 官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。本次 beta.3 已另行完成配置及匿名验证，状态见[更新清单](#已上线的版本与更新清单)；后续发布仍要重新检查，不能复用当时的上线结论。

## 维护者：发布预检、准备与执行

[scripts/release_github.py](../scripts/release_github.py) 默认为 `preflight`，所有阶段**不加 `--execute` 都是 dry-run**：只做本地检查，不登录、不签名／构建、不查询或写远端。除上面的独立 `init-feed` 外，发布流程检查干净且已提交的源码、固定 origin、版本与 MIT 文件，以及不跟踪私人数据或生成物；失败时不会帮你提交或清理工作区。

```powershell
python .\scripts\release_github.py preflight
python .\scripts\release_github.py prepare
python .\scripts\release_github.py publish
python .\scripts\release_github.py feed
```

真正执行前，由维护者自行安装 GitHub CLI 并完成 `gh auth login`，单独确认凭据使用。以下发布阶段的 `--execute` 会做已登录的远端预检：仓库须已公开、未归档且可写，精确源码提交须已存在于远端，并核对 tag／版本冲突。脚本不会推送源码、改变仓库可见性或替你登录。因此 **`prepare --execute` 虽只生成本地交付物，也不是离线模式**。

```powershell
python .\scripts\release_github.py preflight --execute
python .\scripts\release_github.py prepare --execute
```

`preflight --execute` 仅做本地与远端只读预检。`prepare --execute` 运行项目 pytest、Node 测试和 public 构建／Java 测试／lint，校验附件后，将精确源码 commit 与每个附件哈希写入版本目录的 `verification.json`。该回执不是设备验收证明；后续源码或附件有任何变化，都不能沿用旧回执。直接 `android.ps1 -Mode Build` 的输出也不能自动替代发布准备回执。

Pages 必须在这些时点就绪，避免先签名或公开 Release 才发现客户端没有可用清单：

| 阶段 | Pages 检查时点 |
| --- | --- |
| `preflight --execute` | 返回预检成功之前 |
| `prepare --execute` | 构建、签名之前 |
| `publish --execute` | 创建 Draft、上传附件或公开 Release 之前 |
| `feed --execute` | 允许先修复当前通道清单，之后验证，不被旧清单损坏提前阻断 |

就绪要求来源为指定分支根目录、两份清单都可匿名读取、完整校验通过且与所检查的分支内容一致。若 Pages API 明确返回部署模式，必须是按分支发布的 `legacy`；即使残留正确的来源分支字段，也拒绝 `workflow` 等其他显式值。旧响应没有该字段时继续检查分支及清单，保持兼容。部署模式定义见 [GitHub Pages REST 文档](https://docs.github.com/en/rest/pages/pages)。

GitHub JSON 请求用 [`gh api --include`](https://cli.github.com/manual/gh_api) 读取并保留真实 HTTP 状态，但不输出响应头／正文或工具 stderr；401/403、404、429、5xx 和无响应分别报告，404 不统一解释为未配置。附件校验仍使用纯二进制字节，不混入 HTTP 头。Pages 部署延迟时可稍后重试，不绕过就绪检查。

`0.1.0-beta.3 / 3` 已公开分发，不再是可重复使用的候选编号。当前开发工作区已明确采用未发布的 `0.1.0-beta.4 / 4`；本地构建不代表远端不存在冲突，也不代表已经发布。公开发布前仍须使用精确、干净且已推送的源码执行远端预检；若有冲突则停止，不能重建替换或覆盖旧版本。

### 中断的本地准备

`preparation.json` 只记录正在准备的 commit／version 与目录归属，不证明里面的 APK 已验证。相同 commit／version 的准备被中断后，再执行 `prepare --execute` 时，脚本将有归属的未完成 `public/` 保留为同级 `public.interrupted-<UUID>/`，再从已提交源码重建；不会给旧的中断字节补发验证回执。保留目录可用于人工排查，不是发布附件。

已有完整、匹配的 `verification.json` 才能复用附件，而且仍重新执行检查与 Verify。无归属的现有输出、其他 commit／version 的中断记录、链接／异常目录或哈希冲突都会停止，先人工检查，不应删除目录、伪造回执或覆盖成品来绕过保护。

### 公开发布与 feed 恢复

只有维护者另外明确确认仓库、版本、六个精确附件及验收范围后才执行发布。以下记录 beta.3 发布时使用的命令，**本版已经发布，无需再次执行**；以后应替换为另行确认的新版本，并与干净发布工作区的 `version.json` 完全一致。确认参数不带 Tag 的 `v` 前缀：

```powershell
python .\scripts\release_github.py publish --execute --confirm-repository InGnIJM/AI-Bagu --confirm-version 0.1.0-beta.3
```

流程为：验证回执与实际 APK、确认 Pages 就绪 → 创建或续用匹配草稿 → 上传允许附件并核验远端字节 → 公开 Release → 匿名验证附件 → 更新 `codex/update-feed` 分支 → 验证实际 Pages 内容。beta 创建 prerelease；不会覆盖冲突 tag／附件、强推或删除 Release，也不会改变当前本地 checkout。只有同 commit、同内容的草稿可续传；已公开但缺少附件的 Release 不会被偷偷补写。

Pages 来源为 `codex/update-feed`、根目录 `/`，由维护者另行配置，脚本不会自动修改。当前 Beta 指向 beta.3、Stable 保持空通道；后续发布仍须逐次验证固定清单的匿名可达性与精确内容。

若输出 `PARTIAL`／退出码 2，表示 Release 已公开，但匿名附件、feed 或 Pages 核验尚未完成。不要重建另一个同版本包，也不要删除已公开 Release。保留精确源码与附件，排查后仅重试：

```powershell
python .\scripts\release_github.py feed --execute --confirm-repository InGnIJM/AI-Bagu --confirm-version 0.1.0-beta.3
```

`feed` 恢复命令也须在对应发布源码、原始精确附件与 `verification.json` 所在的工作区执行；当前 beta.3 已验证成功，不需要重试。它只接受精确匹配的已公开 Release，仍检查本地回执与远端附件，不能把草稿当成已发布版本。更新当前通道时保留另一通道，未发布过的通道用 `release: null`，拒绝通道降级和同版本内容冲突。即使另一通道已有更高版本，也不应阻止修复本通道已发布版本的 feed。最终分别核对 Release、匿名附件和 Pages 的结果，不把其中一项成功当成全部完成。

## 许可、隐私与验收记录

应用自有源码按 [MIT License](../LICENSE) 提供；这不把抓取或导入的题目、答案以及第三方素材／依赖变成本项目可再授权的内容。公开包必须为空题库，不因源码采用 MIT 就重新分发题库或个人学习进度。

第三方字体、图标和 Android／Python／Chaquopy 等运行时或依赖保留各自许可证。已有字体声明位于 [assets/fonts](../assets/fonts/)；发布时应核对随包依赖与所需声明，不用项目 MIT 替代它们。不得提交或上传 `.env`、模型配置、真实数据库、备份、草稿、签名私钥或密码。

最终验收须分别记录源码版本、测试／lint、精确 public APK 与签名／哈希、API 29／36 隔离迁移与两版本安装、Release、Pages，以及失败或未覆盖项。当前公开版本见 [beta.3 发布验收](validation.md#beta3-公开发布)；[beta.2 本地验收](releases/0.1.0-beta.2-validation.md)保留为历史对象，不能当作本版或所有真机的通过证明。
