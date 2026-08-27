# 八股助手：Android 内测版设计

日期：2026-08-27  
状态：用户已确认，实施中

## 1. 目标

交付一份可直接安装到 Android 10 及以上手机的内测 APK。应用启动后直接进入“八股助手”应用内页面，不跳转到外部浏览器；网页、Python 核心和 SQLite 数据均随应用运行在本机。

内测版内置当前项目中的 408 道题，首次安装时将所有复习进度重置。以后安装更新只迁移数据库结构，不覆盖或重置用户已经产生的题库、刷题进度、会话结果或分析数据。

正式公开版不内置小林 Coding 题库，首次启动为空题库，并引导用户导入。正式版不在本设计中实现，但内测版的工程结构必须支持以后生成空题库正式变体。

## 2. 已确认范围

- 应用名：`八股助手`。
- 最低系统：Android 10（API 29）。
- 发布产物：可安装 APK；后续可上传 GitHub Releases。
- CPU：内测 APK 仅包含 `arm64-v8a`。
- 启动体验：显示原生启动页后进入应用内 WebView，不启动 Chrome 或其他浏览器。
- 前端：为 Android 做专门的移动布局，但继续使用同一个 `web/index.html` 和同一套业务逻辑，不复制第二套题库/会话规则。
- 宽屏与平板：概览位于主操作区上方，与其他尺寸的概览顺序一致，不能挤压答题主操作空间。
- 触控目标不小于 44 CSS px，并兼容窄屏、普通手机、横屏、平板和折叠屏窗口变化。
- 模型服务只允许 HTTPS；应用自己的随机端口 loopback 服务例外。
- 备份文件只包含题库和刷题进度，不包含模型配置、API Key、会话、模型点评、日志或草稿。
- 恢复由用户在应用内手动选择备份文件。
- 恢复冲突：相同“分类 + 题目”由备份覆盖内容及进度；备份中缺失的题新增；目标设备独有题保留。

## 3. 安装身份与待确认项

### 3.1 安装身份

已确认内测版和正式版共用同一安装身份：

- applicationId：`io.github.ingnijm.baguhelper`
- 应用名：`八股助手`
- 内测版本名：`0.1.0-beta.1`

正式版可以在包名、签名一致且 `versionCode` 递增时覆盖升级内测版。正式版 APK 自身使用空题库种子；已安装内测版的用户，其原有题库、进度和分析数据作为用户数据继续保留，应用更新不得覆盖。

### 3.2 APK 签名

已获用户授权：生成一把内测版与正式版长期共用的专用 release keystore。密钥和密码仅保存在本机 Git 忽略目录中，不写入源码、日志、聊天或 APK。生成后告知用户需要单独安全备份。

以后所有覆盖更新必须使用该签名并递增 `versionCode`。不得为正式版重新生成不同密钥，不使用临时 debug key 作为交付 APK 的签名。

### 3.3 工作区和下载授权

已获用户授权：在当前工作目录建立 `codex/android-beta` 分支，保留所有既有未提交改动，不自动提交或推送。该工作方式取代独立 worktree 流程，避免遗漏当前尚未提交的界面与故障恢复改动。

已获用户授权：将 Android SDK、Build Tools、Platform Tools、Gradle、模拟器及构建依赖下载到项目内的忽略目录，并接受 SDK 许可。不修改系统全局配置。

## 4. 技术架构

```text
Android Activity
  ├─ 原生启动页、窗口 Insets、返回键、文件选择器
  ├─ WebView（只加载 127.0.0.1 随机端口）
  └─ Chaquopy Python 3.11
       ├─ bagu.py 题库/会话/评卷核心
       ├─ ThreadingHTTPServer（loopback）
       └─ filesDir 中的 SQLite、设置和日志
```

构建基线：

- Chaquopy `17.0.0`
- Python `3.11`
- `minSdk 29`
- `compileSdk 36`
- `targetSdk 36`
- Android Gradle Plugin `9.0.1`
- Gradle `9.1.0`
- JDK `17`
- ABI `arm64-v8a`

Android 工程放在 `android/`。Python 核心由构建任务从仓库根目录同步到生成目录，避免维护两份 `bagu.py` 源码。

## 5. 本地服务与 WebView 安全

Python 服务只绑定 `127.0.0.1`，端口传 `0` 让系统分配。每次应用进程启动生成高熵随机令牌：

- 初始页面 URL 必须携带令牌。
- `/api/*` 请求必须携带令牌请求头。
- 无令牌或错误令牌返回 403。
- 非 Android 的现有本机网页入口保持兼容，不强制令牌。

Android Network Security Config 默认禁止明文，只为 `127.0.0.1` loopback 放行。WebView：

- 开启 JavaScript、DOM Storage；
- 禁止任意文件 URL 和跨文件访问；
- 禁止 mixed content；
- 不允许非 loopback 顶层导航；
- 只有用户明确点击题目参考链接时，才用系统浏览器打开 HTTP(S) URL；
- API Key 不通过 JavaScript bridge 传输。

## 6. 运行目录和更新规则

Android 不使用源码旁的 `bagu.db`、`.env` 或 `settings.json`。Python 接收显式 `AppPaths`：

- 数据库：`filesDir/data/bagu.db`
- 设置：`filesDir/config/settings.json`
- 密钥：`filesDir/config/.env`（应用私有目录，排除于手动备份）
- 日志：`filesDir/logs/`
- 静态页面：APK 只读资源或首次启动复制出的运行资源

首次启动：

1. 数据库不存在时，内测变体复制只读种子库。
2. 种子库只保留题目正文、答案、分类和 URL。
3. `level`、`times_seen`、`times_right` 重置为 `0`，`next_due`、`last_reviewed` 置空。
4. 不包含任何 session、submission、点评、模型或密钥数据。

更新启动：

1. 如果数据库已存在，绝不重新复制种子库。
2. 只按 `PRAGMA user_version` 在事务内执行结构迁移。
3. 迁移失败回滚并保留旧数据库。
4. 不自动重新抓题、导入题库或重算进度。

正式变体以后使用相同逻辑，但种子库为空，并在空状态展示导入引导。

## 7. 备份文件格式

扩展名：`.bagu-backup`  
容器：ZIP  
字符编码：UTF-8

```text
backup.bagu-backup
  ├─ manifest.json
  └─ questions.json
```

`manifest.json`：

```json
{
  "format": "bagu-backup",
  "schema_version": 1,
  "created_at": "2026-08-27T00:00:00Z",
  "app_version": "0.1.0-beta.1",
  "question_count": 408,
  "questions_sha256": "..."
}
```

`questions.json` 每项只允许：

```json
{
  "category": "MySQL",
  "question": "...",
  "answer": "...",
  "url": "https://...",
  "level": 0,
  "times_seen": 0,
  "times_right": 0,
  "next_due": null,
  "last_reviewed": null
}
```

导入限制：压缩文件不超过 20 MiB，解压总量不超过 50 MiB，题目不超过 10000 道；拒绝未知格式、路径穿越、重复 ZIP 成员、哈希不符、非法字段和非法日期。先完整校验，再在单一事务中写库，任何错误均不产生部分恢复。

导出使用 Android Storage Access Framework 的“创建文件”；恢复使用“打开文件”。应用不申请全盘存储权限。

## 8. Android 专用界面

仍使用 `web/index.html`，由 Android 启动参数启用移动应用壳：

- 顶部紧凑品牌栏和当前页面标题；
- 底部固定主导航：练习、题库、概览、设置；概览复用已有统计和分类掌握度，不新增独立分析系统；
- 统计概览始终位于内容顶部，可横向滚动或使用两列卡片；
- 答题主卡占据主要垂直空间；
- 主 CTA 靠近拇指操作区，但不遮挡输入法；
- 所有对话框、文件操作和危险操作提供明确状态与错误反馈；
- 使用 `viewport-fit=cover` 和 safe-area Insets；
- 输入框聚焦时适配软键盘 resize；
- 遵循 `prefers-reduced-motion`。

随机端口不能作为用户草稿的持久化边界：Android 端的 `bagu-` 本地状态由原生私有存储适配器保存，跨进程重启和更新保持；桌面端继续用 `localStorage`。兼容 Android 10 的较旧 WebView，提供安全 UUID 生成回退，不依赖 `crypto.randomUUID`。

验收视口至少覆盖：

- 320 × 640（极窄手机）
- 360 × 800（常见小屏）
- 412 × 915（常见大屏）
- 800 × 1280（平板）
- 915 × 412（横屏）
- 840 × 900（折叠屏/自由窗口）

不得出现页面级横向滚动、不可点击按钮、被系统栏遮挡、输入法遮住主要输入或宽屏侧栏挤压主操作区。

## 9. 测试与交付门槛

### Python

- 现有 `test/test_bagu.py` 全量通过。
- 新增路径注入、首次种子、升级不覆盖、备份 round-trip、合并冲突、原子回滚、ZIP 安全和 Android 令牌测试。

### Android

- Gradle 单元测试、Lint、Debug 和 Release 构建通过。
- 检查 APK 的 applicationId、version、min/target SDK、ABI 和签名。
- 在 API 29 与 API 36 模拟器完成启动、答题、设置、导出、恢复、旋转/窗口变化和覆盖安装测试。
- 如有可用 arm64 真机，再完成真机安装、启动、软键盘、文件选择和网络评卷冒烟测试。

### 交付物

- `dist/android/八股助手-0.1.0-beta.1-arm64-v8a.apk`
- APK 的 SHA-256
- 版本、包名、签名证书指纹、支持系统和 ABI
- 安装与更新说明
- 已验证设备/模拟器矩阵与剩余限制

APK 必须实际存在、签名可验证、能够被 Android 包工具解析，并完成至少一个 Android 运行环境中的安装和启动验证后，才可宣布交付完成。
