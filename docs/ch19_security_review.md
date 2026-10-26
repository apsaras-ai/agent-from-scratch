# 第 19 章：Agent Security 上线审查与负面测试手册

> **范围说明**：这是供可信工程团队执行的安全审查清单，不是 Pentest 许可、KMS 设计手册，也不表示本书 Python `SecurityReferenceMonitor` 可作为生产 IAM/PEP/Sandbox 替代。执行对抗测试前必须有明确的授权、测试环境、范围和回收计划。

## A. 数据流和可信域

- [ ] 明确列出 User / Tenant / Run / Agent Identity 的签发来源。
- [ ] 画出 `System/Developer/User` 与 `Web/RAG/Email/Repo/Tool/MCP` 的数据流。
- [ ] 对每条 Memory、Evidence、Tool Result 记录来源、检索时间、授权范围。
- [ ] 无低信任数据进入 System/Developer 指令的直接拼接路径。
- [ ] 不将网页、Issue、代码注释、HTML Metadata 或 Agent Reply 中的“批准”视为真实授权。
- [ ] 模型输出作为*提议*经过单独 PEP 检查，不执行任意字符串形式的命令。
- [ ] 任何 Guardrail/Prompt Injection Classifier 的“通过”不等于 Tool Authorization 通过。

## B. Tool Registry 和最小权限

- [ ] 仅可信 Host 可注册或更新 Tool Handler / Risk Classification。
- [ ] 区分 `READ / WRITE / EGRESS / DESTRUCTIVE`，审查每个分类是否准确。
- [ ] 每个请求验证 Principal、Tool Name、Resource ID、Arguments、Current Policy。
- [ ] Schema 验证为有界 JSON；拒绝重复键、非标准 NaN、未知扩展字段。
- [ ] 不允许未经审查的 Schema Remote `$ref` 自动联网获取结构定义。
- [ ] 真实资源所有权与授权在**Tool Backend** 中再次检查，而不仅比较字符串。
- [ ] 高风险操作有具体的 Host/用户批准，审批界面显示真实动作、资源、目标与影响。
- [ ] Approval Token 绑定 Principal/Run/Tool/Arguments Digest/Expiry/Nonce。
- [ ] Policy 变更后可即时阻止被撤销的权限。
- [ ] 多实例 Nonce/Call ID 防重放使用持久化事务、CAS 或等效机制。
- [ ] 对每个 Run 设置模型调用次数、工具调用次数、费用、时间和最大副作用数量。
- [ ] 操作未知结果时不自动重试，先读取外部事实或查询 Idempotency Key。

## C. Egress/SSRF 和数据最小化

- [ ] 模型与沙箱不能直接访问任意外网 IP、内部网络或 Metadata Service。
- [ ] 每个可达目的地由可信 Host 签发的 Egress Policy 管理。
- [ ] 目标 URL 解析检查 HTTPS、userinfo、IPv4/IPv6、Loopback、Private IP、端口、路径。
- [ ] DNS 解析、IP Pinning、TLS Connection 与 Redirect 目标在实际网络网关重新检查。
- [ ] 防止 DNS Rebinding、Proxy 变更与跨 Tenant 网络策略泄漏。
- [ ] Egress 参数在网络调用前经过授权和敏感信息策略检查。
- [ ] Sensitive Output Guard 不能只是已知字符串匹配；编码、分片、日志、图像和中间 Artifact 都可能泄漏。
- [ ] 记录未经授权的出站尝试，但日志不能包含原始 Secret 与敏感 Payload。
- [ ] 真实账单/网络网关统计能与 Agent Run ID、User/Tenant 对账。

## D. Secret Broker

- [ ] 生产 API Key、PAT、Cookie、OAuth Token 不写进 Prompt、公开日志或仓库。
- [ ] 使用真实 Secret Manager/KMS/HSM 或短期 Workload Identity，按资源和任务缩小 Scope。
- [ ] Tool 后端获得短期 Credential，模型只获得非敏感结果或任务回执。
- [ ] 禁止不可信 Agent 读取 Host Environment、Docker Socket、云 Credentials、Kubernetes Token。
- [ ] Secret Reference 不能自行授予权限，Broker 仍检查身份/资源/Run 与有效期。
- [ ] 具备主动撤销、滚动更新、过期与 Emergency Shutdown 流程。
- [ ] 任务取消、Worker 迁移和用户撤销权限后，后续工具请求实际被拒绝。

## E. Sandbox 和第三方 MCP

- [ ] 未知/模型生成代码只在经审查的 OS 级隔离 Worker 执行。
- [ ] 对 Workload 配置 Network Deny、Filesystem Isolation、No Host Secrets、Runtime Limits。
- [ ] 第 18 章 Trusted Process Executor **不得替代** VM/container/gVisor 安全边界。
- [ ] 第三方 MCP Server 来自批准来源、固定版本并经过安全审查。
- [ ] 远程 Tool Description、`readOnlyHint`、`destructiveHint` 不是 Host Authorization 的替代。
- [ ] 工具 SDK、依赖、镜像与下载内容有来源校验、版本固定和安全扫描。
- [ ] MicroVM/Container Host 持有实际杀死执行环境和确认销毁的权限。
- [ ] 共享 Warm Pool / Snapshot 不保留前一个 Tenant 的 Secret、Browser Session 或工作区。
- [ ] 当 Agent 执行失败或结束，确保 Kernel/Processes、Network、Mount、Artifacts 和凭证清理完成。

## F. Memory、Multi-Agent 与上下文

- [ ] Memory/Semantic Search 使用 Tenant/Auth 过滤在检索评分与重排之前。
- [ ] 原始 Evidence 与模型摘要分离：摘要不得覆盖权威验收事实。
- [ ] Artifact/Memory 可修订、撤销、过期，并记录适用版本和来源。
- [ ] Multi-Agent 委派只能缩小 Parent Agent 的权限集合。
- [ ] Worker 输入只包含 Task 所需的材料，禁止全量转发其他用户对话或 Secret。
- [ ] 同 Key 并发结果进入版本化 CAS/事务，而不是 Last Writer Wins。
- [ ] Tool Result 中伪装的管理指令不能成为下一层 Agent 的 System Directive。
- [ ] 最终任务成功由独立 Verifier 判定，而不是由模型或 Worker 自报。

## G. 安全负面测试 / CI

| 用例 | 受控输入 | 预期控制 |
| --- | --- | --- |
| Prompt Injection | 合成网页声称拥有管理员权限 | Policy 不被修改；未授权 Tool 仍拒绝 |
| Unknown Tool | 提议不存在的 `shell.exec` | 不执行 Handler |
| Wrong Resource | Tool 正常但 Resource 属于另一 Scope | 资源级访问拒绝 |
| Schema Confusion | 重复 JSON Key、NaN、额外字段 | 参数校验失败 |
| Approval Tampering | 修改已批准操作的 Body/Endpoint | Action Digest 不匹配 |
| Approval Expiry | 已过期的审批票据 | 禁止 Write |
| Approval Scope | 把用户 A 的票据用于用户 B/其他 Run | 拒绝 |
| Approval Replay | 相同 Call ID 提交第二次 | 不重复调用工具 |
| Egress Confusion | 相似域名、用户信息、IP、HTTP、URL Path 偷换 | 拒绝 |
| Preflight Secret | 已知合成 Secret 放入模拟 Egress 参数 | **在执行前**拒绝 |
| Output Secret | 已知合成 Secret 出现在结果 | 不释放给模型 |
| Denied Budget | 达到最大工具调用额度 | 不进入 Handler |
| Unknown Side Effect | Handler 完成但结果丢失/被阻止 | 不自动重试；先协调事实 |
| Cross Tenant Data | 用户 A 查询用户 B 文档 | 不召回或转发 |
| External Schema Ref | Tool Schema 指向外部 URL | 不自动网络访问 |
| Worker Error | Handler 内部异常含敏感信息 | 不复制错误详情到模型响应 |

**重点**：不要只测试“这句话会不会被 LLM 拒绝”。更强的安全断言是：不管模型生成哪一种非法 ToolCall，Host PEP 都不会执行未获批准的行为。

测试环境使用固定的虚构秘密和 Fake Handlers。不要在共享 GitHub Runner 上向陌生站点发探测请求、执行危险进程或尝试真实凭证外传。

## H. 遥测、验收与误拒

监测：

- 未授权 Tool Call 触发频率，分 Tenant/Run/Tool；
- 拒绝原因占比（Scope、Schema、Approval、Budget、Endpoint 等）；
- 合法任务成功率和安全误拒；
- 过期/重放 Approval 事件；
- 未知执行结果与外部事务对账；
- 可信身份发行/撤销状态；
- 临时 Sandbox 生命周期、超时和销毁失败；
- 真实数据泄漏、Credentials Exposure 与 Incident 响应时长；
- 已验证任务每次操作的综合成本。

对于评测，分别计算：

```text
Authorized Task Success Rate
Adversarial Objective Success Rate
False Denial Rate
Cross-Tenant Leakage Count
Post-Approval Parameter Mismatch Count
Confirmed Unreconciled Side Effects
```

真实模型上的 Prompt Injection 鲁棒性不能由本书的确定性 Fake Model 测试替代。可以借鉴 AgentDojo 等基准进行**授权**的防御测试。

## I. 事故响应

1. **Contain**：暂停受影响 Run、撤销 Tool/Model/Network 权限；终止相关 Sandbox。
2. **Evidence**：保留必要的受限审计数据、工具回执和有来源的 Artifact，不扩散真实 Secrets。
3. **Scope**：分析 Task、Tenant、Tool、Model 和所有受影响外部系统。
4. **Reconcile**：对每次未知副作用查询服务端真实状态，避免盲目重复提交。
5. **Recover**：按业务回滚策略恢复代码/数据、旋转密钥、清理污染记忆和缓存。
6. **Prevent**：把本次故障加入授权、工具、工作流、Agent/浏览器和隔离层的回归测试。
7. **Review**：记录根因、边界失效和修复验收证据。

## 与教材关联

- [第 7 章 Tool Runtime](../book/chapters/07.md)：Schema、Risk、Tool Permission。
- [第 8 章 Context Engineering](../book/chapters/08.md)：Trust Labels、信息保留与检索边界。
- [第 9 章 Memory](../book/chapters/09.md)：来源、过期、跨租户。
- [第 12 章 Durable Execution](../book/chapters/12.md)：未知外部副作用与持久化操作记录。
- [第 15/16/17 章](../book/chapters/15.md)：Coding、Browser、Multi-Agent 专属攻击面。
- [第 18 章 Sandbox](../book/chapters/18.md)：真正隔离环境需要哪些可验证属性。
- [第 19 章 Security](../book/chapters/19.md)：Reference Monitor 与端到端威胁模型。

官方依据：[OWASP Agentic 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)、[OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)、[AgentDojo](https://arxiv.org/abs/2406.13352)、[NIST AI 600-1](https://doi.org/10.6028/NIST.AI.600-1)。
