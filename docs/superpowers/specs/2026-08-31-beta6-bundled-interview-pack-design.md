# beta.6 内置面经题包显式确认设计

## 发布目标

- 发布 `v0.1.0-beta.6`（`versionCode=6`、Beta 预发布），不修改 beta.5 的 Tag、附件、APK 或更新源历史。
- 继续使用正式题包 `autumn-recruit-interviews-2026` revision 1，display version `2026.08.30-r1`，共 748 题、27 个专题，SHA-256 为 `47aa6b28768be85322924df4a7c17199bf248660997cd10247066821d6d23864`。
- 完全相同的题包字节既作为 Release 独立附件提供给桌面端，也作为 Android public APK 的固定 Asset `assets/question-pack/bundled.bagu-pack` 提供。
- public SQLite seed 必须继续为空；只有用户在原生预览中点击“确认安装”后才允许写入数据库。
- beta.5 历史发布不可变；Stable feed 原字节保持不变，Beta feed 仅把 APK 更新到 code 6。

## Descriptor 协议

schema v1 保持现有九字段 canonical JSON 契约，语义为题包只作为 Release 附件；缺少 descriptor、schema v1 或 internal flavor 时，APK 中任何 `.bagu-pack` 都必须被拒绝。

schema v2 使用相同字段顺序，并在最后增加 `android_delivery`：

```json
{
  "schema_version": 2,
  "versionName": "0.1.0-beta.6",
  "file_name": "ai-bagu-2026-autumn-interviews-r1.bagu-pack",
  "sha256": "47aa6b28768be85322924df4a7c17199bf248660997cd10247066821d6d23864",
  "pack_id": "autumn-recruit-interviews-2026",
  "revision": 1,
  "display_version": "2026.08.30-r1",
  "question_count": 748,
  "experience_count": 27,
  "android_delivery": "bundled_confirm"
}
```

解析器必须拒绝重复键、未知字段、字段乱序、未知 schema、错误 delivery 和与 `version.json` 不匹配的版本。schema v2 的 `android_delivery` 只接受 `bundled_confirm`。

## Android 运行时

`NativeBridge` 新增：

```java
@JavascriptInterface public boolean hasBundledInterviewPack();
@JavascriptInterface public void importBundledInterviewPack();
```

第一项只返回能力布尔值；第二项从固定 Asset 触发原生检查和预览，不经过文件选择器。题包正文、路径、哈希和字节不得进入 JS；完成事件继续使用 `operation="pack-import"`。

新增 `BundledPackController`：

- 只读取固定 Asset，压缩字节上限 20 MiB，并在一次读取的同一份字节上计算 SHA-256、调用 `RuntimeHost.inspectInterviewPack()` 和创建 `PendingImport`。
- 只有 descriptor v2 public 构建暴露能力；internal 和旧宿主不暴露。
- 复用现有文件操作租约、原生预览确认框和 `RuntimeHost.installInterviewPack()`，不新增 HTTP API、权限或 Manifest 项。
- `PendingImport` 的来源为 `EXTERNAL_FILE`、`BUNDLED_AUTO_PROMPT` 或 `BUNDLED_SETTINGS`；安装核心和操作名不变。

自动提示只可在可信本机页面加载完成、Activity 前台、没有 open 会话、文件操作、更新安装或其他待确认导入时尝试。`new` 和 `upgrade` 自动提示；`installed`、`downgrade`、`conflict` 不自动提示。设置入口可手动打开所有可检查状态，其中 downgrade/conflict 只读且无确认按钮。

在展示自动预览前，把当前题包 SHA-256 写入原生私有偏好 `bundled_pack_auto_prompted_sha256`。取消、已知失败或进程死亡后，同一哈希不再自动提示；不同哈希可再次提示。配置重建保留内存中的 `PendingImport` 并重新展示，不自动确认；进程死亡不恢复字节、不重放安装。该偏好不进入 JS、备份、诊断或更新摘要。

## 共享网页

设置页“面经题包”卡片增加默认隐藏的“安装内置题包”按钮。只有 Android 桥存在且 `hasBundledInterviewPack()` 为 true 时显示；点击一次只调用一次 `importBundledInterviewPack()`。桌面、旧宿主和 internal APK 不显示。外部 `.bagu-pack` 导入入口、题包只读管理、日常复习开关和专题模拟不变。

## 构建与 APK 校验

- `android.ps1 -Mode Build -QuestionPack <正式题包>` 在 schema v2 public 构建中把同一字节嵌入固定 Asset，并复制为独立 Release 附件。
- 正式 public assemble 缺题包、descriptor 不匹配或输入文件读取期间变化时失败；普通 `Check` 不依赖私人题包；internal 构建拒绝题包参数及内置题包。
- verifier 对 schema v1/无 descriptor 要求 APK 不含任何 `.bagu-pack`；对 schema v2 要求恰好一个固定 Asset，核对 SHA-256，并用公共 runtime validator 核对 pack ID、revision、display version、748 题和 27 专题。
- verifier 拒绝额外、嵌套、错路径、坏 ZIP、私有 catalog 和任何 pack-owned seed；public seed 的 questions/packs/experiences/sessions/session_items 均为零。
- `verification.json` 记录 delivery mode、APK 成员名和哈希，不记录正式题包的绝对源路径。

## Release 与门禁

Release 精确保留七个外部附件：public ARM64 APK、独立题包、`SHA256SUMS`、证书摘要、`INSTALL.md`、`RELEASE_NOTES.md` 和 `update.json`。`SHA256SUMS` 只列 APK 与独立题包；`update.json` 和 Pages feed 只描述 APK。

自动化必须覆盖完整 pytest、Node、Java 单测、public release lint、androidTest 编译、签名 ARM64 Build/Verify、七附件契约、APK 全成员、空 seed 和三方哈希一致性。

公开发布前，必须在隔离 `ANDROID_AVD_HOME` 下创建唯一命名的 API 29 与 API 36 x86_64 一次性模拟器；所有 ADB 命令绑定脚本启动并验证的 emulator serial，禁止触碰任何 vivo V2309A。两端均验证首次预览、取消抑制、设置重开、安装、27/748、旋转、进程死亡、open 会话与互斥、beta.5 已安装/未安装两种覆盖升级。任一失败阻止发布。

## 固定边界

- 不新增第三方 Python 依赖，不修改 Hermes grade 协议，不增加在线题包商店或独立题包自动更新。
- 不提交正式题包字节、源面经、私有 catalog、稳定 ID 映射、签名材料、数据库或发布目录。
- 不修改原始 109 个 Markdown，不修改 beta.5 发布物，不接触当前 vivo 设备。
