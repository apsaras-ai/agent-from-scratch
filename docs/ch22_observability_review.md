# 第 22 章：Agent Observability / Debugging 生产化审查清单

> **范围**：这是一份供工程团队审查的架构参考，不是已经部署的 OpenTelemetry 服务。`TraceRecorder` 只是本书标准库教学原型；JSONL SHA-256 哈希链不提供数字签名或可信的审计存储，不具备多租户 IAM、OTLP Exporter 或生产容量保证。

## 1. 明确业务事实来源与 Trace 角色

- [ ] Task、Run、Step、Activity、Attempt、Operation 和 Trace 之间有明确关系。
- [ ] Durable Run/Outbox/Provider Receipt 存放在可信业务数据库，不能靠 Trace Sampling 重建。
- [ ] 每个 Trace 有随机非零 Trace ID，每个 Span 有独立 Span ID 和 Parent ID。
- [ ] 远程传播使用官方 W3C Propagator；外来 `traceparent` 仅表示关联，不授予读取/执行权限。
- [ ] Trace 查询 API 需要 Tenant/Run/Role 鉴权；知道 Trace ID 不是读取权限。
- [ ] 独立 Verifier 的结果来源明确；Model/Tool/Worker 不能在 Trace 里自报最终成功。
- [ ] 业务数据与遥测数据有不同的 Retention、删除和备份规则。
- [ ] Tool / Provider 版本和模型路由版本可在可信系统侧追踪。

## 2. OpenTelemetry SDK / Semantic Conventions

- [ ] 锁定 OTel Python API/SDK/Instrumentation 和 GenAI SemConv 的实际版本。
- [ ] 区分 `invoke_agent`、`invoke_workflow`、`plan`、`chat`、`execute_tool`、`verify`。
- [ ] 仅在真正能识别 Planning 时创建 `plan` Span；不要把模型内部 Chain-of-Thought 当可见状态。
- [ ] 对同一个 Tool Operation 不重复建立两份相同 Span。
- [ ] `gen_ai.provider.name`、`gen_ai.request.model` 来源是可信 Host/Provider 信息。
- [ ] `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` 按 Provider 真实计量处理。
- [ ] 不在通用 Usage 总数外再次加上已经包含于 Input Usage 中的 Cache Token。
- [ ] 模型 Usage 缺失显示 Unknown，不按 0 处理。
- [ ] 监测 SDK/Exporter 版本升级造成的字段语义变化。
- [ ] OTel Exporter 与网络凭证均由 Host 控制，不能暴露给模型工具调用。

## 3. 隐私和数据最小化

- [ ] 生产默认**不**录制 Raw Prompt、Completion、Tool Arguments、Tool Results。
- [ ] 用户代码、邮件正文、Browser Cookies、系统提示、OAuth/API Key 不得意外进入 Span Attributes。
- [ ] 使用已批准的 Attribute Schema/Label Allowlist，限制 Value Type 与长度。
- [ ] **未知 Tool Name** 应安全归一化，避免模型把私密文本写进自由形式的 Tool Label。
- [ ] Error Code 来自固定枚举，不允许记录任意 Exception Message/Stack。
- [ ] Span Events 有限枚举和数量上限，不接受任意用户文本。
- [ ] Provider 请求 URL、文件路径、用户 Identity 不自动加到通用 Metric Label。
- [ ] 生产 Sensitive Debug 有短时授权、审计、加密、最短保留与删除策略。
- [ ] 不把 Trace 与 Metrics 后端作为“顺手备份”用户原文的旁路存储。
- [ ] 监测内容截断/采样导致的错误诊断结论，并在 UI 标明证据覆盖范围。

## 4. Trace Context 与时序

- [ ] W3C Trace Context Format、非零 ID、Flags、Inbound 解析均已测试。
- [ ] 外来 Trace ID 不可用来决定 Tool Authorization 或用户权限。
- [ ] `contextvars` 下的 Async Child Span 继承正确的 Parent Span。
- [ ] 跨进程/跨 MicroVM 的 Context Propagation 由可信进程服务完成。
- [ ] 时间戳使用 Wall Clock 关联、Monotonic Clock 计算本地 Duration。
- [ ] 同机、跨机 Clock Skew 有明确误差与校正方法。
- [ ] 并发子 Span 的 Inclusive Duration 不被直接相加当作端到端总耗时。
- [ ] Critical Path 需要 DAG/时间重叠关系，不由 `slowest_child` 直接替代。
- [ ] Span Cancellation、Timeout、Error 与 Tool Denial 属于不同受控类型。

## 5. Token 计量与费用

- [ ] 从第 6 章 GatewayResponse 等可信来源读取 Provider Usage。
- [ ] 输入/输出总 Token 与缓存 Token 口径有明确契约。
- [ ] 每次 Model Call 有 Provider/Model 和可审查的 Rate Card Version。
- [ ] 费用计算可复现，采用 Decimal 或受控整数金额单位。
- [ ] 未获取 Usage、Price 时返回 Unknown；不能显示错误的 $0 账单。
- [ ] 费用估算不冒充 Provider Invoice。
- [ ] 重试、Streaming 中断、失败调用是否计费有明确账单策略。
- [ ] Cost per Verified Task 的分子包含失败与重试成本。
- [ ] Verified Task 来自独立验收；分母为零显示 N/A。
- [ ] 同时展示 Usage/Price Coverage，避免遗漏模型调用造成偏差。

## 6. Log/Trace Export 和容量保护

- [ ] Span/Event 每次运行有上限；单条 Attributes 和总导出字节有界。
- [ ] 遥测传输采用受控 TLS、认证、网络权限和配额。
- [ ] Collector 失败时非关键遥测缓冲有界，不阻塞所有 Agent Worker。
- [ ] 丢失/超时/采样的 Trace 有可观察的 Dropped Telemetry Counter。
- [ ] 部署安全审计与一般 Trace 使用不同的可信度与 Retention。
- [ ] 使用 Tail Sampling 时不会删除真正的业务 Run/Outbox 状态。
- [ ] 按 Provider/Model/Tool/Outcome 聚合，避免高基数指标爆炸。
- [ ] 长 Agent Run 和多 Agent 跨 Worker Trace 的归档/查询成本可量化。

## 7. Trace Playback 与数据完整性

- [ ] Trace Playback **只读**，不重新执行模型、工具或外部写入。
- [ ] Replay Reader 拒绝非法 Schema Version、超大文件、重复 Span/ID 和不合法 Parent。
- [ ] Replay Reader 只接受批准的 Metadata 字段/错误类别。
- [ ] JSONL Hash Chain 仅作为有限 Integrity Check，绝不宣称不可篡改审计。
- [ ] 安全审计如需要防恶意改写，使用受保护的 Key/签名、只追加存储及访问日志。
- [ ] 不把 Trace `status=ok` 当成用户 Task 真实完成。
- [ ] 未知外部操作结果必须通过第 21 章业务核对，不得仅根据 Trace 猜测。
- [ ] 事故调查中区分 First Observed Error 与 Root Cause，并报告不确定性。

## 8. 本章离线测试覆盖

| 领域 | 预期 |
| --- | --- |
| W3C Trace ID/Span ID | 格式正确、非零、正确 Parent 关联 |
| Async ContextVar | 并发子任务拥有相同 Root Parent，不互相串线 |
| Privacy Allowlist | Raw Prompt/Token/用户内容不可作为随意 Attributes |
| Unknown Tool Name | 统一归类，不能作为模型生成敏感 Label 原样记录 |
| Error Guard | 任意 Exception/私密错误串不写入 Trace |
| Usage | 已知 Token 正确计算；缺失仍为 Unknown |
| Rate Card | Provider/Model/版本必须匹配；费用 Decimal 可复现 |
| Model/Tool Bridge | 真实 Agent Kernel 与 ToolRegistry 权限检查仍生效 |
| JSONL Replay | 不执行 Model/Tool、不产生外部副作用 |
| Hash Chain | 可发现未重算的修改、顺序和结构错误 |
| Adversarial Rehash | 重新生成整条 Hash Chain 不构成来源真实性保证 |
| Tool/Task Result | Tool 中间错误可以与最终 Verifier 通过并存 |

## 9. 生产 SLO 与 Dashboard 建议

~~~text
agent_run_duration_seconds
agent_model_duration_seconds
agent_model_input_tokens_total
agent_model_output_tokens_total
agent_token_usage_missing_total
agent_tool_denied_total
agent_trace_spans_dropped_total
agent_verifier_outcome_total
agent_queue_wait_seconds
agent_reconciliation_pending
agent_estimated_cost_coverage
agent_cost_per_verified_task
~~~

**重点不是指标数量，而是通过数据证据逐步提升任务成功率、可恢复性、成本覆盖率和受信任的故障诊断能力。**

## 10. 尚未交付的生产能力

- 正式 OpenTelemetry SDK/OTLP Exporter/Collector 和 Semantic Convention 版本迁移；
- 真实 Model Provider Usage/Billing 与缓存计价；
- 多机时钟同步和跨边界的 W3C Trace Propagation；
- 日志加密、IAM、跨租户隔离、Audit Signing、保留/删除服务；
- Tail Sampling、Span Links、Streaming Chunks、Metric Export；
- 高并发流式 Trace Backend 容量与性能测试；
- 真实 Coding/Browser/Multi-Agent 的 Span SDK 集成；
- 通过故障 Trace 驱动自动化事故响应的安全审批机制。

进一步参考：
[OTel GenAI](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) ·
[OTel Attribute Registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) ·
[W3C Trace Context](https://www.w3.org/TR/trace-context/) ·
[OTLP](https://opentelemetry.io/docs/specs/otlp/) ·
[Chapter 22](../book/chapters/22.md).
