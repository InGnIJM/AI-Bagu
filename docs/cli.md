# CLI 参考

[文档导航](README.md) · [用户指南](user-guide.md) · [HTTP API](api.md) · [架构与数据](architecture.md)

本文依据已提交源码基线 `71fbbfd`，描述桌面命令及 Hermes 调用约定。命令在仓库根目录执行；默认数据库为 `bagu.py` 同目录下的 `bagu.db`，与桌面网页共用，不是 Android 私有题库。

## 命令一览

| 命令 | 参数与行为 |
| --- | --- |
| `python bagu.py init` | 创建或迁移数据库结构，不清空已有题目和进度 |
| `python bagu.py import` | 抓取代码中配置的公开题库；新增题目，更新匹配旧题的题干、答案及来源锚点，保留复习进度 |
| `python bagu.py import --code-only` | 先创建完整 SQLite 备份，只修复可匹配的旧答案代码格式 |
| `python bagu.py import --format-only --dry-run` | 联网核对旧答案，预览可恢复的表格、引用、列表等格式，不写入修复结果 |
| `python bagu.py import --format-only` | 完整 SQLite 备份后，只恢复正文与来源匹配的旧题库答案格式 |
| `python bagu.py stats` | 打印总题数、今日可复习题数、已掌握题数，以及各分类的总数、已刷数、到期数 |
| `python bagu.py list` | 打印全部题目的 ID、分类和题干；不提供分页或搜索参数 |
| `python bagu.py draw -n 5 --cat MySQL` | 开始一轮并打印题目；`-n` 默认 5，`--cat` 可省略，按完整分类名筛选 |
| `python bagu.py grade <session_id> <题id> <评级>` | 对本轮指定题记录一次评级；评级仅接受 `again`、`hard`、`good`、`easy` |
| `python bagu.py skip [session_id]` | 关闭指定会话；不传 ID 时关闭当前进行中会话 |
| `python bagu.py serve --port 8765` | 启动本机网页；端口默认 8765，仅监听 `127.0.0.1` |

查看参数帮助：`python bagu.py --help` 或 `python bagu.py draw --help`。不要把表中的尖括号占位符原样输入。CLI 没有模型配置、CSV 导入或备份恢复子命令；这些操作使用网页、Android 或 [HTTP API](api.md)。

## 从初始化到一次复习

```bash
python bagu.py init
python bagu.py import
python bagu.py stats
python bagu.py draw -n 5 --cat MySQL
```

`import` 需要网络；不想抓取公开题库时，可执行 `init` 和 `serve`，再从网页手动新增或导入 CSV。

`draw` 会输出 `session: ...`，随后打印题目 ID、分类、题干和来源链接，不打印答案。下一步使用这次实际返回的会话 ID 和题目 ID，例如：

```bash
python bagu.py grade s_20260826_a3f2c91b 12 good
```

此处 ID 仅演示格式，不能直接用于真实会话。`session_id` 由程序生成，格式为 `s_YYYYMMDD_` 加 8 位小写十六进制字符。逐题作答并各评分一次；最后一题评分成功后会话自动关闭。中途结束可执行：

```bash
python bagu.py skip
```

`skip` 不撤销本轮已完成的评分，也不修改未判题的等级、复习次数或到期日。

## 会话、评级与调度

- 每份数据库最多一条 `open` 会话。网页或 Hermes 已开一轮时，再执行 `draw` 会失败；应先完成该轮或 `skip`。
- `grade` 必须带本轮 `session_id`；旧写法 `grade <题id> <评级>` 已废除。错会话、题目不在本轮、已评分或会话已关闭，均拒绝评分且不改调度。
- CLI `grade` 没有 submission 重放参数。不要因没看到终端输出就盲目再次评分；必要时在网页查看当前会话状态。
- 抽题只考虑新题（`next_due` 为空）和到期题，先取到期复习题，再随机补新题。可选题不足时实际数量会少于请求数；没有可选题时不创建会话。
- `stats` 的“今日到期”包含新题；“已刷”指至少复习过一次；`level >= 3` 计入“已掌握”。

| 评级 | 调度行为 |
| --- | --- |
| `again` | 等级重置为 0，下次复习为 1 天后 |
| `hard` | 等级升一级，1 天 × 升级后等级倍率 |
| `good` | 等级升一级，3 天 × 升级后等级倍率 |
| `easy` | 等级升一级，7 天 × 升级后等级倍率 |

等级最高 3；等级 1、2、3 的倍率为 1、2、4。每次成功评分均增加 `times_seen`，只有 `good` / `easy` 增加 `times_right`。因此等级不是连续答对次数。网页自评按钮采用“不会 / 勉强 / 会了 / 秒答”标签；CLI 参数使用英文评级。AI 的内容评级标准见 [用户指南](user-guide.md)，不按速度评分。

## 退出码与错误处理

| 情况 | 可观察结果 |
| --- | --- |
| 普通命令成功 | 退出码 0，结果写入标准输出；`serve` 持续运行直到停止 |
| `draw` 遇到 open 会话 | 退出码 1，标准错误输出中文说明、会话 ID 与未判题 ID |
| `grade` 协议被拒绝 | 退出码 1，标准错误输出一行中文原因 |
| `skip` 没有可关闭的会话 | 退出码 1，标准错误输出“没有进行中的会话” |
| 参数缺失、非法评级等命令行语法错误 | `argparse` 输出帮助/错误，退出码 2 |
| `draw` 没有可选题 | 输出提示，不创建会话；不是失败，退出码 0 |

抓题时单个分类网络失败会输出 `[WARN]` 并继续其他分类，所以 `import` 退出码 0 不代表每个分类都成功。应同时检查分类日志和合计。上述表格是已处理的命令路径，不保证磁盘、数据库损坏等异常也只输出一行错误。

## 修复旧答案代码格式

```bash
python bagu.py import --code-only
```

适用于旧抓取结果丢失 SQL、Java 等代码围栏或缩进的情况：

1. 使用 SQLite 备份接口，在数据库旁创建 `*.before-code-format-*.sqlite3`，并打印备份路径。
2. 从公开来源重新抓取参考内容，仅对能逐行匹配的代码片段恢复围栏和缩进。
3. 已有围栏、内容有改动或匹配数量不一致的片段保持原样。

该命令不新增题目、不替换答案正文、不修改历史评分、调度或进度，也不调用模型，不需要模型 Key；仍需要连接公开题库来源。正常 `import` 会更新正文，不能当作 `--code-only` 的等价替代。

这里生成的是完整 SQLite 备份；应用导出的 `.bagu-backup` 仅含题目与进度，二者用途不同。数据库升级和恢复边界见 [架构与数据](architecture.md) 与 [Android Beta](android-beta.md)。

## 修复旧答案表格与其他格式

2026-08-28 新增；不代表旧 APK 或已启动的旧服务自动具备此功能。重启桌面服务后使用更新的渲染器。

```bash
python bagu.py import --format-only --dry-run
python bagu.py import --format-only
```

同一份来源网页分别按旧版纯文本规则、新版 Markdown 规则解析；只有整篇旧答案与来源一致（允许空行和行首缩进差异，兼容以前的代码块修复）才恢复格式。保留表格列、代码围栏及语言、列表、引用、标题、强调、删除线、链接与图片。不会猜测空格分列，不新增题目，不替换内容有变化或身份不唯一的答案；`unmatched_ids` 列出已找到来源但未匹配的题目。

默认只修改 `questions.answer`，不修改历史答案。若明确需要修复“已记录”的题库答案快照，先预览再执行：

```bash
python bagu.py import --format-only --include-history --dry-run
python bagu.py import --format-only --include-history
```

`--include-history` 仅检查已评分且来源为 `stored` 的 `result_full_answer`，必须独立匹配原文才恢复；模型答案、来源不明或内容变化的历史答案保持原样。点评、评级、submission、答案来源、会话和所有调度字段不变；查询仍使用已存快照，不从当前题库替换历史答案，也不重新评分。

格式命令只接受已经存在、且 schema 为当前版本的数据库，不执行初始化或升级；旧版或高版本数据库会被拒绝。`--dry-run` 使用 SQLite 只读连接；需要升级的数据库须另行完整备份并升级，不能用格式修复代替数据库迁移。

写入前在数据库旁创建 `*.before-answer-format-*.sqlite3` 完整备份，JSON 报告给出 `backup` 路径、题库和历史更新数量。所有格式生成与渲染先完成校验；来源请求失败时不写入任何修复，事务内遇到冲突或写入失败整批回滚。不调用模型、不读取模型 Key。`--dry-run` 不创建备份、不写入修复；这两个附加参数只能与 `--format-only` 同用，不能与 `--code-only` 混用。

## Hermes 聊天调用约定

Hermes 使用自己的模型评卷，本仓库 CLI 仅记录结果，不调用项目配置的模型：

1. 可先调用 `stats`，再 `draw`，保存返回的 `session_id`。
2. 一次只向用户展示一道题，不提前提供答案。
3. 用户作答后，Hermes 自行判断为 `again` / `hard` / `good` / `easy`。
4. 对该题调用一次 `grade <session_id> <题id> <评级>`。
5. 非 `easy` 时，对照题目 URL 讲解完整答案；用户中止时调用 `skip`。

禁止无会话 ID 评分、同题重复评分、用户未作答就自行评级、本轮未结束再次抽题，或把 Nous OAuth/token 拷入本项目。网页/Android 的模型配置不能改变 Hermes 这条调用路径。
