# 第 33 章：Mini-Agent Runtime 集成与上线审查清单

> 2026-10-27 教学版端到端 Runtime。当前实现连接第 4/6/8/9/22 章的真实 Python 模块；SQLite 仅持久化安全运行状态。**不是生产服务、OS 沙箱、恢复引擎或经过认证的多租户平台。**

## 1. 现有可运行的模块

| 能力 | 本章实际实现 | 边界 |
| --- | --- | --- |
| Model Loop | Ch04 AgentKernel | 同步且受模型/工具调用数量限制 |
| Model Gateway | Ch06 ModelGateway | 默认确定性 FixtureBackend，无真实 API |
| Context | Ch08 ContextManagedModel | 固定 System/Task，工具回合整体处理，粗略 Token Proxy |
| Memory | Ch09 MemoryService + Access | 先按 Tenant、Owner 和过期时间过滤 |
| Tool Policy | Ch04 ToolRegistry | 仅一个只读 lookup_fact；具体 Key 的 Host Allowlist |
| Tracing | Ch22 TraceRecorder + Traced Adapters | 根 Span、Chat、Tool、Verify；最小结构化元数据 |
| 持久化 | 新 SQLiteRunJournal | 执行前事务登记、执行后 CAS 完结，只存安全元数据 |
| Evaluation | 独立 Host Verdict | 实际 Tool Evidence + Scope + Final Truth 一致才通过 |
| Python SDK | MiniAgentRuntime.run / journal.read | 本机 Python 方法，无 HTTP/API 认证 |
| Crash Recovery | reconcile_interrupted | 显式标记结果未知，不自动重做动作 |

真实执行链：

~~~text
Trusted LookupRequest
 -> Host scoped FactCatalog preflight
 -> SQLite BEGIN / INSERT running
 -> TraceRecorder root span
 -> MemoryService.retrieve (scoped)
 -> ContextManagedModel
 -> ModelGateway(FixtureBackend) + TracedGatewayModel
 -> AgentKernel
 -> TracedToolRegistry(ToolRegistry(read_only_fact))
 -> Host Independent Verifier
 -> SQLite compare-and-swap terminal update
 -> Return verified answer or None
~~~

## 2. CI 验证的核心不变量

- [x] 真的调用 Ch04 Kernel、Ch06 Gateway、Ch08 Context、Ch09 Memory 和 Ch22 Trace。
- [x] 规则 FixtureBackend 使用真正 Tool Observation 生成最终结果，不直接读取真值表。
- [x] 仅模型声称正确但没有真实 Tool Read 时，判为 Unverified。
- [x] Unknown Tool、Schema 错误、越权 Key、工具调用预算预检失败都不能被当作成功。
- [x] 不同 Tenant/Owner 的同名 Key 不会交叉读取；未知/越权资源在建 Run 前拒绝。
- [x] Memory 按 Scope 和 Expiry 预过滤；工具或 Memory 文本不会升级为系统权限。
- [x] Tracing 只记录已允许的操作名和结构化元数据；不记录 Prompt、Tool Args 或结果。
- [x] 无 Provider Usage、Rate Card 时费用保持 Unknown，不能推断零成本。
- [x] SQLite 可跨进程内的新连接读取 Run 状态，但不落盘 Tool Output、Memory 或原始 Prompt。
- [x] 重复 Run ID 不会再次执行；过时 Revision 不能结束同一 Run。
- [x] Crash 模拟保留 Running，可信 Host 可标记 Needs Reconciliation，**不会自动重试**。

**局限**：所有操作仍运行于同一个可信 Python 进程。不能抵御能执行恶意 Python 的租户、真实供应商提供的不可信代码、数据平面权限绕过或复杂 Prompt Injection。

## 3. 生产前还缺什么？

### 模型与上下文

- [ ] 真实 Provider Credential/IAM、Network Egress 限制、Endpoint/Redirect/SSRF 测试。
- [ ] 模型能力描述、真正的 Tokenizer/Usage 和版本化定价；Streaming / Cancellation / Rate Limit。
- [ ] Prompt/Memory 数据分级、脱敏、生命周期和隐私控制。
- [ ] 异步 Batch/工具并发；Ch07 的 AsyncToolRuntime 尚未与本章同步 Kernel 结合。

### Tool、Sandbox、安全

- [ ] 真实工具的 Host-enforced Schema、Principal-Run-Resource 权限、审批和审计。
- [ ] 真正执行不可信代码/浏览器任务时，隔离容器/gVisor/MicroVM/远程 Worker 必须提供可验证资源边界。
- [ ] Ch18 TrustedProcessExecutor 只管理资源、不是 OS 安全隔离；不能用来运行不可信代码。
- [ ] Network/Filesystem/Secret 隔离、工具执行幂等键、外部非幂等效果对账。
- [ ] 不得因模型的环境观测文本改变权限策略，不能因超时假定没有执行外部效果。

### Durable Runtime

- [ ] Worker Lease、Heartbeat、Generation Fencing、租户级公平调度和限流。
- [ ] Activity-level 可信 Checkpoint，记录结果事实与副作用状态。
- [ ] Transactional Outbox、Provider Idempotency、External Effect Reconciliation。
- [ ] 完整 Workflow Event History + Deterministic Replay；不得在重放中再次执行外部 I/O。
- [ ] 数据库访问控制、备份恢复、加密、密钥轮换、WAL/并发故障演练。
- [ ] 目前的 reconcile_interrupted 只是可信调用方标记，缺失 Lease 检测；**不能对可能仍在运行的 Worker 直接执行**。

### API、部署、评测

- [ ] 认证 API Gateway：OAuth/OIDC、Tenant/Owner 身份由受信任身份系统判定，而非客户端自报。
- [ ] 真实 Create/Get/Approve/Cancel/Stream API，明确 Idempotency Key、错误语义与身份隔离。
- [ ] Release Gate、Canary、Rollback、隔离的生产环境与运营监控。
- [ ] 版本化、隔离且有统计区间的独立 Eval：Task Success、工具失败、未知结果、安全、费用、P95 延迟。
- [ ] 新模型/新提示/工具变更必须配对 Baseline & Candidate、分场景回归、安全/成本门槛。

## 4. 推荐的生产服务拆分（设计建议，尚未实现）

~~~text
API / Control Plane
 ├─ Authentication and Authorization
 ├─ Versioned Run/Task Store, Scheduler, Backpressure
 ├─ User Approvals and Release Gates
 └─ Safe Run Status / Trace Metadata API

Execution Plane
 ├─ Isolated Agent Worker and Model Gateway
 ├─ Scoped Memory / Context Proxy
 ├─ Tool Authorization + Egress Proxy
 ├─ Sandbox / VM lifecycle
 └─ External Effect and Outbox Reconciliation

Evidence Plane
 ├─ Independent Outcome Verifier
 ├─ Privacy-first observability / traces
 └─ Versioned benchmark and evaluation campaign
~~~

## 5. 本地运行

~~~bash
python -m pip install -e .
python examples/ch33_runtime_lab.py
python -m unittest discover -s tests -p 'test_ch33.py' -v
~~~

实现：[Chapter 33 Core](../src/mini_agent/integrated_runtime.py) · [Demo](../examples/ch33_runtime_lab.py) · [Tests](../tests/test_ch33.py) · [Textbook](../book/chapters/33.md)。

## 6. 官方标准与参考

- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- W3C, Trace Context Recommendation: https://www.w3.org/TR/trace-context/
- OpenTelemetry, GenAI Semantic Conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Temporal, Workflow Replay: https://docs.temporal.io/workflows
- MCP, Authorization Specification: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- SQLite, SQL Transactions: https://www.sqlite.org/lang_transaction.html

> 最重要的提醒：有数据库记录和 Trace 不代表“工具副作用能被确定重放”，更不代表“Worker 崩溃时未知结果可以自动重试”。授权与外部事实核对始终是 Host 的职责。