# 第 21 章：Agent Reliability 生产化审查清单

> **定位**：生产设计与评测参考文档。当前 §OutboxStore§、§SyntheticProvider§、§CircuitBreaker§ 与 §CompensationPlan§ 是教学原型；没有真实 IAM、跨机事务、受限进程、付款/邮件服务或分布式一致性保证。

## 一、定义一致性边界

- [ ] 为 User Task、Run、Step、Activity、Operation、Outbox Event、External Receipt 指定统一 ID。
- [ ] 指明每种状态的唯一事实来源（System of Record）。
- [ ] 同一业务数据库内的 Intent + Outbox 插入采用**同一数据库事务**。
- [ ] 不假定两个服务的独立 Commit 可以靠本地 Python §try/except§ 统一回滚。
- [ ] Outbox Relay 可以 At-Least-Once 执行，Provider 必须能处理重复或返回权威状态。
- [ ] Client Submission、Outbox Delivery 和 External Effect 的幂等边界分别清楚定义。
- [ ] 实现 Schema Version、Migration、备份/恢复与 Replay Compatibility。
- [ ] 如使用 Consumer Inbox，Message Dedup 与业务更新应在同一个事务里完成。

## 二、可靠的 Operation Ledger

建议每项副作用操作保存：

§§§text
op_id, tenant_id, run_id, action, request_key,
effect_class, canonical_payload_digest,
state, generation, attempt,
lease_owner, lease_deadline,
provider_receipt_ref, last_verified_at,
reconcile_required_since,
security_approval_ref, sandbox_cleanup_ref
§§§

- [ ] Action 及 Effect Class 来自受信任的 Tool Registry，不接受 LLM 自报 §idempotent=true§。
- [ ] §op_id§、Scope 和 Request Key 对应稳定的业务对象。
- [ ] 重复 ID 与相同 Intent 幂等返回；重复 ID 但不同内容拒绝。
- [ ] 同一 Worker Lease/Generation 只能更新自己当前持有的 Operation。
- [ ] 过时 Worker ACK 必须 Fenced；不能覆盖新的 Receipt 或状态。
- [ ] 同一时间一个 Operation 只能有一个有效 Claim（正确的事务与唯一索引）。
- [ ] Operation 结果状态需区分 §confirmed§、§not_applied§、§unknown§、§cancelled§。
- [ ] 未知非幂等结果不能自动进入新 Attempt。
- [ ] Delivery Attempt、Provider Result 和用户 Task Verified 是三个独立状态。

## 三、故障窗口矩阵

| 故障位置 | 是否可直接重试？ | 必要证据 |
| --- | --- | --- |
| 创建 Intent 之前 | 可以重新提交相同逻辑请求 | 稳定 Client Request ID |
| Intent 与 Outbox 之间 | 依赖单 DB 事务一起 Rollback | 本地数据库记录一致 |
| 本地 Commit 后，未发 Provider | 根据幂等 Key 领取 Outbox | Outbox 处于 Pending |
| Claim 后，尚未产生外部效果 | 无法仅凭本地状态证明 | Provider 查询 / Host 进程状态 |
| Provider Commit 后，ACK 丢失 | **非幂等禁止盲目重试** | Provider Receipt / 业务账本读回 |
| Lease Timeout，Worker 未停 | **不能释放容量或自动重放** | Host 终止证明 |
| Worker 已停、Provider 状态未知 | 非幂等进入 Reconciliation | 正确的 Provider 查询协议 |
| Provider 声称 §not_found§ | 视 Provider 一致性契约决定 | 是否能权威断言“未发生” |
| ACK 已保存，用户没有收到 | Client Query / Idempotent Replay | Durable Operation Result |
| Compensation 已发，ACK 丢失 | 按新的 Operation 查询 | Compensation 的独立 Idempotency Key |

## 四、Host 与 Provider 的信任关系

- [ ] Worker 不能通过模型文本决定自己是 Tenant Admin。
- [ ] 真实 ToolCall 仍需第 19 章的 Host Policy/Approval。
- [ ] 真实执行发生在第 18 章的 OS 隔离 Sandbox 中。
- [ ] Executor 被强制停止由可信 Host 证明，不是 Worker 自己写 §destroyed:true§。
- [ ] Sandbox Lease 与 Operation Generation、Run ID 有稳定关联。
- [ ] Provider 结果查询由独立可信组件执行，不能只消费“模型说已完成”的回复。
- [ ] Provider Lookup 有明确的 Positive/Negative/Unknown 语义及一致性说明。
- [ ] 如果 Provider §not found§ 不能保证线性一致，不可映射到 §not_applied§。
- [ ] Provider Receipt 校验包含资源 ID、金额/对象、租户、动作、时间和业务验收条件。
- [ ] 外部 Idempotency Key 的生命周期、大小限制、重复结果语义和作用域已查阅官方契约。

## 五、Timeout、Retries、Circuit Breaker

- [ ] 每个调用都有可解释的 Deadline、Timeout、Attempt Budget 和 Cost Budget。
- [ ] 只对明确安全/幂等的情况重试，且限制层层叠加的放大倍数。
- [ ] Exponential Backoff 加 Jitter；遵循 §Retry-After§。
- [ ] 下游连续失败时 Circuit OPEN，冷却后只放行少量 Half-Open Probe。
- [ ] Circuit Breaker 仅代表依赖健康状态，不代替用户授权或业务幂等。
- [ ] Open Circuit 的请求不会占住不可回收的 Worker Slot。
- [ ] Retry Storm 测试包含大批同时失败的客户端、相同/不同 Request Key 和速率上限。
- [ ] 记录 Retry Attempt、Provider Outcome、Circuit State 与最终业务成功率。
- [ ] Timeout 或连接重置不能自动转为 §not_applied§。
- [ ] 缺少可靠外部结果查询的操作必须有人工核对流程。

## 六、Saga 与补偿

- [ ] 每个 Saga Step 的 Forward Action 有独立的幂等性和验证条件。
- [ ] 明确哪些效果真正可补偿，哪些不可逆或需要人工审批。
- [ ] 前向某一步效果为 Unknown 时先核对，不自动进入逆序补偿。
- [ ] 补偿按真实业务依赖逆序创建新的 Durable Operation。
- [ ] Compensation 请求具有独立审批、身份、Request Key、重试预算。
- [ ] Compensation 自身可能失败、超时或结果未知；不能当成数据库 Rollback。
- [ ] 补偿完成后仍需重新验收跨系统业务不变量。
- [ ] 用户看到的状态应清楚区分 §failed§、§compensating§、§compensated§、§manual_review§。

## 七、测试与可观测性

最小 CI：

- [ ] 本地两表事务插入中途故障：全部回滚，无“幽灵通知”。
- [ ] 本地成功、外部未执行：Outbox 仍可被领取。
- [ ] 两个连接同时领取：只有一个有效 Lease。
- [ ] 旧 Generation ACK：拒绝。
- [ ] Worker Lease Expired：§reap_required§ 继续占用 Host Capacity。
- [ ] Host Reaper 失败：不提前释放资源。
- [ ] 已经执行的非幂等操作丢 ACK：不重复外部效果。
- [ ] Provider Lookup Unknown：保持未决。
- [ ] Provider 权威 §not_applied§：才可以按预算重试。
- [ ] Provider 已提交 + 结果回执：状态从未决变为 Confirmed。
- [ ] 幂等 Provider 重复请求：Request Count 可增、Actual Effect Count 不增。
- [ ] Dead Letter、取消、人工审批和撤销路径都有明确事件。
- [ ] Circuit OPEN/HALF_OPEN/CLOSED 及并发 Half-Open Probe 有可复现测试。
- [ ] Saga 逆向补偿顺序正确，未知和不可逆操作被阻断。
- [ ] 日志与 Trace 不复制 Secret、审批密钥、未经允许的业务 Payload。

指标建议：

§§§text
outbox_pending
outbox_inflight
outbox_reap_required
outbox_needs_reconciliation
outbox_dead_letter
outbox_reconcile_age_seconds
provider_retry_total
provider_duplicate_delivery_total
provider_effect_dedup_total
provider_timeout_total
breaker_open_total
breaker_half_open_probe_total
compensation_manual_review_total
verified_operation_total
verified_task_total
cost_per_verified_task
§§§

指标标签不宜包含原始 Request Body、用户邮箱、Secret、Token 或无界 Task ID。

## 八、生产升级路径

§§§text
Authenticated Agent API
    → Host Tool Policy / Approval (Ch19)
    → Durable Business Intent + Outbox
    → Scheduler / Workload Identity (Ch20)
    → Isolated Sandbox / Host (Ch18)
    → External Provider with idempotency contract
    → Independent Reconciliation Service
    → Durable Operation + Task Verifier
    → Redacted Audit + Metrics (Ch22)
§§§

**不要用两个彼此无事务联系的 SQLite 文件依次提交来声称已解决跨服务双重写入。** 生产数据库、Outbox Relay、Inbox Consumer 和真实 Provider 之间的每条消息及写入都必须有明确的一致性边界。

本章还没实现的关键能力：多机 Worker IAM、真正执行环境销毁、真实外部 Provider Idempotency、CDC Relay、跨 Region Failover、持久化 Circuit State、Saga Compensation 实际执行与加密 Artifact/Receipt Store。

参考：

- [AWS Transactional Outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)
- [AWS Builders' Library: Jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)
- [Microsoft Distributed Data / Saga](https://learn.microsoft.com/en-us/dotnet/architecture/cloud-native/distributed-data)
- [Temporal Activity Failure Detection](https://docs.temporal.io/encyclopedia/detecting-activity-failures)
- [Stripe Idempotent Requests](https://docs.stripe.com/api/idempotent_requests)
- [Chapter 21](../book/chapters/21.md)
