# 八股抽问：会话故障恢复与并发保护

日期：2026-08-27  
状态：已实现

本文补充 `2026-08-26-session-web-design.md`。原有 CLI/Hermes 调用协议、调度间隔和“模型失败不评分”规则保持不变。

## 目标

1. SQLite 层保证全局最多一条 open 会话。
2. 并发评分同一会话同一题时只允许第一次写入调度。
3. 网页提交已经评分但响应丢失时，可用同一提交 ID 恢复原判定、点评和完整答案。
4. 未提交回答在关闭标签页或重启浏览器后仍可恢复，但不写入 SQLite。

## 数据与约束

`session_items` 幂等增加：

```sql
submission_id TEXT
result_comment TEXT
result_full_answer TEXT
```

数据库建立两个部分唯一索引：

```sql
CREATE UNIQUE INDEX uq_sessions_one_open
ON sessions(status) WHERE status='open';

CREATE UNIQUE INDEX uq_session_items_submission
ON session_items(submission_id) WHERE submission_id IS NOT NULL;
```

旧库若已有多条 open，会保留 `created_at DESC, rowid DESC` 的第一条，其他只关闭会话，不修改未判题调度；日志只记录保留的 session ID 和关闭数量。

## 原子性

- `draw`、评分和 `skip` 使用短暂的 `BEGIN IMMEDIATE` 写事务。
- 评分以 `grade IS NULL` 条件更新 `session_items`，成功占有该题后才更新 `questions`；任一步失败都回滚。
- 答案 HTML 和返回结果必须在提交事务前构造完成；渲染异常不保留评分、调度或 submission，原提交可重试。无法解析的 HTTP(S) 链接按转义后的普通文本显示。
- 模型网络调用不持有写锁。调用前只读校验用于减少无效请求，提交时在写事务内再次权威校验。
- CLI `grade` 不使用 submission，重复调用仍失败。

## 网页提交幂等

网页为 `/api/answer`、`/api/answer/stream` 和 `/api/review` 发送 `submission_id`，格式为 `sub_<UUID>`。字段对旧客户端可选。

- 相同 ID、相同会话和题目已完成：直接返回第一次持久化结果，不再次评分或调用模型。
- 相同 ID 用于另一题：400。
- 不同 ID 提交已评分题：失败且不改库。
- `GET /api/submissions/<submission_id>` 可在会话关闭后读取题目公开字段和持久化结果；未知结果返回 404。
- SSE 命中已完成结果时只发 `start`、`done`。

只在评分成功的同一事务内保存 submission、评级、点评和完整答案。用户原始回答不写入 SQLite；模型 HTTP、断流、空响应或解析失败也不写 submission 结果。

本设计采用“提交完成后幂等”，不建立 processing/租约状态。极少数同时在途重试可能调用模型两次，但数据库只接受并返回第一次成功提交。

## 浏览器恢复

- 草稿使用 `localStorage["bagu-draft:<session_id>:<question_id>"]`。
- 当前提交使用 `localStorage["bagu-active-submission"]`，仅包含 submission、session、question 和 flow，不重复保存回答正文。
- 刷新时先恢复 open 会话，再查询 active submission：已完成则重显结果；404 且题仍待评则保留草稿重试；404 且会话或题已变化则清理失效状态。
- 查询网络失败不清理本地状态。
- “下一题”、成功 `skip` 或确认提交失效后清理相应 submission；`skip` 清理该会话的草稿。

## 验收

- 两个独立连接并发 draw，最终恰好一条 open。
- 两个独立连接并发 grade，`times_seen` 只加一。
- 同 submission 重放返回相同结果且不重复调用模型；跨题复用失败。
- 最后一题使会话关闭后仍能查询结果。
- 模型断流不留下 grade 或 submission。
- 浏览器关闭后草稿可恢复，结果确认后草稿清除。
