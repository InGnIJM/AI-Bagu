# 文档导航

想开始复习，请先读[项目首页](../README.md)。这里按“使用、接入开发、历史依据”组织详细说明，不需要从设计计划读起。

## 使用八股助手

| 想做什么 | 从这里开始 |
| --- | --- |
| 在电脑启动，完成第一轮练习 | [快速开始](../README.md#快速开始) |
| 配模型、用语音、管理题库/题包、导入 CSV、处理报错 | [使用指南](user-guide.md) |
| 使用 beta.5 的面经模拟与 `.bagu-pack` | [面经模拟与题包](user-guide.md#面经模拟与题包) |
| 在 Android 安装、更新或保留数据 | [Android 指南](android-beta.md#安装更新与备份) |
| 下载公开 APK 和面经题包、查看更新清单 | [beta.5 Release](https://github.com/InGnIJM/AI-Bagu/releases/tag/v0.1.0-beta.5)、[更新源状态](data-transfer-and-updates.md#已上线的版本与更新清单) |
| 了解备份包含什么、恢复会覆盖什么 | [备份与恢复](user-guide.md#备份与恢复) |
| 查看界面及配图来源 | [README 界面示例](../README.md)、[图片说明](images/README.md) |

## 接入与开发

| 文档 | 内容 |
| --- | --- |
| [CLI 与 Hermes](cli.md) | 命令、会话约定、抓题与代码格式修复 |
| [HTTP API](api.md) | 路由、请求与响应、流式事件、错误及重试 |
| [架构与数据约定](architecture.md) | 会话、调度、SQLite、模型评卷与移动安全边界 |
| [开发与测试](development.md) | 环境、源码目录、测试矩阵及隔离检查 |
| [Android 构建与交付](android-beta.md) | 工具链、签名、构建校验与设备验证限制 |
| [数据迁移、更新与发布](data-transfer-and-updates.md) | 双模式备份、更新诊断、GitHub Release／Pages 发布与失败恢复 |
| [项目协作规则](../AGENTS.md) | 维护者与 Agent 的项目约束；AGENT.md 仅作兼容指向 |

## 版本与文档范围

截至 2026-08-31，公开预发布版为 [v0.1.0-beta.5](https://github.com/InGnIJM/AI-Bagu/releases/tag/v0.1.0-beta.5)，versionCode 为 `5`，来自精确提交 `2b847013f185781b817b6458743ae08e383e8849`。Release 分别提供空种子 APK 与 `ai-bagu-2026-autumn-interviews-r1.bagu-pack` 题包附件；APK 不内置或自动下载题包。Beta 更新清单已指向 beta.5，Stable 清单仍是成功的空通道。普通用户不需要配置 GitHub CLI 或 Pages。

该版本包含既有语音、评分答案来源、诊断与更新功能，并升级为 SQLite／备份 v3，加入面经模拟和桌面／Android 题包导入。以下范围须区分：

- **公开附件**：以 beta.5 Tag 的 APK、题包和校验附件为准；APK 与题包是独立字节，安装 APK 后仍需由用户选择题包并确认导入。
- **发布后文档**：发布状态同步可能晚于 APK 对应提交，不改变已签名附件字节或 Tag 指向。
- **版本配置**：公开 beta.5 的构建配置为 `0.1.0-beta.5 / 5 / beta`；复现发布必须使用对应 Tag、稳定签名和发布回执，不能把任意开发树当作公开版本。

题包与专题模拟最初在开发分支完成本地验收，现已随 beta.5 正式公开。27 专题／748 题 r1 题包仅作为 Release 附件提供，正式字节、私有审校清单和源面经不进入 Git 或 APK；公开仓库只保存题包身份、数量和 SHA-256 描述。题包答案由 AI 生成，维护者已复核并接受为正式参考答案，但并非原帖作者或面试公司的标准答案。

[历史验收记录](validation.md)集中保存每个阶段的测试、APK 哈希、模拟器与未覆盖项。beta.4 的六附件 Release 保留为历史版本；beta.5 已完成公开 Release 与附件校验，但尚未执行设备安装或仪器测试，不能把构建和静态校验等同于真机验收。较早章节中的“未发布／未验收”只描述当时的对象，不撤销，也不套用到新版本。

## 设计依据与历史计划

设计文件保留原路径，便于追溯。页首会说明仍有效的约束及已被替代的部分；它们不是首次安装教程。发生冲突时，先核对实际代码与所用版本，再更新对应现行说明。

| 文档 | 阅读定位 |
| --- | --- |
| [会话与网页设计](superpowers/specs/2026-08-26-session-web-design.md) | 会话协议起点；旧视觉、配置和部分答案行为已替代 |
| [多模型配置设计](superpowers/specs/2026-08-26-model-profiles-design.md) | 多模型约束；旧草稿存储及视觉说明不再代表现状 |
| [会话恢复与并发保护](superpowers/specs/2026-08-27-session-fault-recovery-design.md) | 数据迁移、原子性、幂等与恢复依据 |
| [Android Beta 设计](superpowers/specs/2026-08-27-android-beta-design.md) | 移动隔离、原生存储和打包边界 |
| [会话与网页实现计划](superpowers/plans/2026-08-26-session-web.md) | 历史实施步骤，不应逐段复制成当前实现 |
| [多模型实现计划](superpowers/plans/2026-08-26-model-profiles.md) | 历史实施步骤，旧接口及存储示例需核对 |
| [Android Beta 实现计划](superpowers/plans/2026-08-27-android-beta.md) | 当时的实施与验收过程，不证明新包已验收 |
| [面经题包与专题模拟计划](superpowers/plans/2026-08-29-interview-pack-simulation.md) | beta.5 功能的历史实现契约与任务记录；不包含真实面经、私有清单或正式题包字节 |

## 文档维护约定

- README 保留“是什么、怎样开始、如何完成常用操作”，完整协议和工程细节放本目录。
- 每项规则尽量只有一个详细说明来源，其余文档链接过去；用户风险提醒可在操作入口重复。
- 当前功能、开发中方案、历史验收分开写。测试记录带日期、对象与限制，不在首页长期固定一个“全部通过”数字。
- 仓库内文档和图片使用相对链接。截图说明来源，未知版本不猜测，不用概念图冒充实际界面。
- 截图只放 `docs/images/`，不加入应用静态资源或 APK 打包列表；更新时同步检查图注、按钮名称和链接。

本结构参考了 [PowerToys](https://github.com/microsoft/PowerToys#readme) 的使用/开发分层、[Flutter](https://github.com/flutter/flutter#readme) 的文档入口与效果展示，以及 [Docusaurus](https://github.com/facebook/docusaurus#readme) 的快速上手组织方式。项目仍使用普通 Markdown，不引入文档网站构建依赖。
