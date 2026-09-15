# AI 批改执行机制

## 当前实现（14a / 14b / 14c）

提交请求只做权限、截止、题目匹配及内容完整性校验，然后同次插入答案与 `ai_status=pending`。数据库保存完成才返回 201；不调用 provider，也不创建内存后台任务。重复提交仍返回 409，不覆盖答案。

人工 `status` 仍为 `pending_teacher_review` / `confirmed`，与 AI 状态独立。教师或管理员响应新增 `ai_status`；学生响应保持原白名单，不返回 AI 状态、分数或评语草稿。

| AI 状态 | 含义 |
| --- | --- |
| pending | 新提交等待独立 worker 领取 |
| processing | 正在执行，持有执行令牌和有限租约 |
| succeeded | 完整草稿已原子保存，仍等待教师确认 |
| failed | 不可自动重试的错误或本轮预算耗尽，可手动重试，不阻碍人工评分 |
| cancelled | 人工提前确认，取消待执行/执行中/失败的评分 |
| legacy_unknown | 旧记录缺失状态，不推断成功、不自动重新排队 |

旧记录只在读取模型时获得兼容状态，不批量改写数据库，不清除既有 AI 草稿或最终成绩。即使历史记录有分数，也不凭分数猜测执行结果。

教师可在没有任何 AI 分数的情况下提交完整人工评分。待处理、处理中或失败的记录经人工确认后变为 cancelled；历史未知或已成功的状态保留。首次确认仍原子锁定最终成绩，重复或并发确认返回 409。确认基于数据库当前答案合并人工字段，不覆盖同期 AI 草稿，并清除执行令牌；已发出的外部请求不保证立即取消，但返回结果无法再写入。

## 14b 启动与恢复

在仓库根目录另开终端，使用与 Web 相同的 `.env` 和数据库：

```powershell
uv run --frozen --no-sync python -m app.worker
```

Web 仍通过 uvicorn 独立运行，不会自动启动 worker。未启动 worker 时答案照常保存，保持 pending。当前 worker 仅支持 placeholder，其他 AI_PROVIDER 启动时明确报错，不会假装接入真实 AI。

- Submission 本身是持久队列，无额外队列服务或跨集合写入；新增非唯一 ai_work_queue 索引，原提交唯一索引不变。
- 每个 worker 同时执行一份提交，多个 worker 原子竞争 pending 或租约已到期的 processing；领取时更新随机 ai_token、ai_lease_until 和 ai_attempts。租约使用 MongoDB 的 $$NOW，避免依赖不同进程的本机时间。
- AI_JOB_TIMEOUT_SECONDS 默认 60 秒，覆盖整份提交的内容读取和逐题评分；AI_LEASE_SECONDS 默认 90 秒，必须大于超时；AI_POLL_SECONDS 默认 2 秒。无心跳续租，超时上限是有意边界。
- 成功只更新 AI 字段，不改变答案或最终成绩；每题校验有限且不越界的分数及非空、最多 2000 字符评语，再一次性保存完整草稿。任何一题失败不写入部分结果。
- 写回同时检查人工未确认、processing、执行令牌一致、租约尚未过期。旧 worker 的迟到结果不能覆盖新结果或人工确认。崩溃/取消后的 processing 等租约到期，由其他或重启 worker 接管，不需要内存任务列表。
- 执行失败只保存 timeout / grading_failed 等固定错误码，不存储或记录可能含答案/密钥的异常文本。教师响应新增 ai_attempts、ai_error_code；学生仍不获得任何 AI 字段。数据库暂时断连后继续轮询；领取成功但结果未保存时依靠租约恢复。
- Ctrl+C 关闭 worker 并释放数据库客户端；中断中的任务保留租约，稍后可恢复。外部 provider 必须遵守异步取消并自行配置网络超时，不能用阻塞或吞掉取消的实现；14b 不是操作系统级强制终止外部请求机制。
- 恢复可能再次调用 provider，不保证外部调用恰好一次。14c 已将崩溃接管纳入本轮预算及退避。历史缺失状态记录不会自动领取；损坏的 processing 缺失租约需显式维护，不静默推断。

## 14c 自动及手动重试

- `AI_MAX_ATTEMPTS=3`：每轮包含首次执行及崩溃接管，最多 3 次，允许配置 1 至 10。14b 的旧记录没有 ai_cycle_attempts 时使用已有 ai_attempts，不重置历史已用预算。
- `AI_RETRY_BASE_SECONDS=5`：指数退避 5、10 秒（默认三次预算），配置范围 1 至 300。暂时失败写回 pending 和 ai_next_attempt_at，MongoDB 时钟判断到期后才领取。崩溃任务从租约到期再等待同样退避；预算耗尽的过期任务转 failed/retry_exhausted，不再接管。
- 只有 TimeoutError 和适配器显式抛出的 TransientGradingError 自动重试。真实 provider 应将临时网络错误、限流、服务不可用映射为后者，将认证、输入、格式错误保留为不可重试错误；当前未接入真实 provider。不分析异常字符串、不存储凭据或原始异常。
- 末次正常失败保留 timeout/provider_unavailable；非法评分、内容/其他错误记 grading_failed，直接 failed。整份提交重新执行，不保存半份草稿。错误期间教师可随时确认人工成绩。

手动接口：`POST /api/v1/submissions/{id}/retry-grading`，仅管理员或该班教师可用（归档后仍按批改权限处理），请求：

```json
{"expected_retry_count": 0}
```

先读取教师详情的 ai_retry_count（旧记录为 0）。接口只对未确认且 failed 的记录原子操作，返回 202 和教师提交详情；pending、processing、succeeded、cancelled、legacy_unknown 或版本过期均返回 409。未登录 401，无权限 403，不存在 404，请求不合法 422。

接受后 ai_retry_count 加一，本轮 ai_cycle_attempts 归零，累计 ai_attempts 保留；至少等待一个基础退避周期再执行。并发相同请求仅一次成功，即使该轮再次失败，旧 expected_retry_count 仍不能重放。教师可明确发起后续新一轮，不限制人工总轮数；每轮自动预算有界。客户端不要自动刷新版本持续重试，否则会绕开人工费用确认意图。

教师响应提供 ai_retry_count、ai_next_attempt_at、ai_attempts、ai_error_code；学生不暴露这些内部状态。人工确认原子清除等待时间和令牌，重试与确认竞争不会重新打开已确认成绩。手动重试不执行评分，也不修改答案或最终成绩。

## 后续范围

- 14b 已完成独立 worker、原子领取、租约恢复及迟到结果保护；未引入 Redis/Celery。
- 14c 已完成有限自动重试、退避、恢复预算及带版本的人工重试。
- 14d 已完成真实 HTTP 重试/确认/查分串联和 worker 进程中断重启验收，见 [验收记录](ai-execution-acceptance.md)。
- 15：再讨论真实 provider、模型、密钥、效果与费用。14a 未改变依赖或 AI 配置。

节点 14 已完成后台占位评分、有限重试及限定范围串联验收，不是生产就绪的真实 AI 系统。使用占位 provider 的原单元测试继续保留。
