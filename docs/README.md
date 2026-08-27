# 文档导航

想开始复习，请先读[项目首页](../README.md)。这里按“使用、接入开发、历史依据”组织详细说明，不需要从设计计划读起。

## 使用八股助手

| 想做什么 | 从这里开始 |
| --- | --- |
| 在电脑启动，完成第一轮练习 | [快速开始](../README.md#快速开始) |
| 配模型、用语音、管理题库、导入 CSV、处理报错 | [使用指南](user-guide.md) |
| 在 Android 安装、更新或保留数据 | [Android 指南](android-beta.md#安装更新与备份) |
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
| [项目协作规则](../AGENTS.md) | 维护者与 Agent 的项目约束；AGENT.md 仅作兼容指向 |

## 版本与文档范围

基础使用、CLI、API 和架构说明以已核对的提交 `71fbbfd`（2026-08-28）为基线，包含语音输入和评分反馈改进。源码支持的功能不等于任一旧 APK 都包含这些功能。

文档整理期间，工作区另有进行中的迁移、Android 更新与发布流程改动；相关实施计划和 `0.1.0-beta.2` 发布说明仍是未随本次文档提交收录的草稿，不构成已发布或已验收证明。基础指南中的旧备份行为与桌面入口范围应结合所用版本阅读；新功能验收完成后，需同步更新指南和 API，而不是只修改版本号。

[历史验收记录](validation.md)集中保存已有的测试次数、APK 哈希、模拟器结果和未覆盖项。Android 指南单独说明正在版本化调整的构建脚本，不能拿旧脚本参数替代新产物校验。

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

## 文档维护约定

- README 保留“是什么、怎样开始、如何完成常用操作”，完整协议和工程细节放本目录。
- 每项规则尽量只有一个详细说明来源，其余文档链接过去；用户风险提醒可在操作入口重复。
- 当前功能、开发中方案、历史验收分开写。测试记录带日期、对象与限制，不在首页长期固定一个“全部通过”数字。
- 仓库内文档和图片使用相对链接。截图说明来源，未知版本不猜测，不用概念图冒充实际界面。
- 截图只放 `docs/images/`，不加入应用静态资源或 APK 打包列表；更新时同步检查图注、按钮名称和链接。

本结构参考了 [PowerToys](https://github.com/microsoft/PowerToys#readme) 的使用/开发分层、[Flutter](https://github.com/flutter/flutter#readme) 的文档入口与效果展示，以及 [Docusaurus](https://github.com/facebook/docusaurus#readme) 的快速上手组织方式。项目仍使用普通 Markdown，不引入文档网站构建依赖。
