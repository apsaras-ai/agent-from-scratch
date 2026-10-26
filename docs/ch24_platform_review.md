# 第 24 章：Agent Platform 生产部署验收清单

> **教学参考，不是生产部署模板。** 此项目提供离线 `preflight`、Kubernetes 参考 JSON、`ReleaseLedger` 和 `CanaryPolicy`；未连接 Kubernetes、Argo Rollouts、真实指标、IAM、数据库迁移执行器、容器镜像库或多租户隔离平台。以下每项都需要真实受信任操作员和目标环境的验收证据。

## 1. 架构与信任边界

- [ ] 有正式架构图：User API、Identity/Policy、Run Orchestrator、Scheduler、Model Gateway、Tool PEP、Sandbox Controller、Worker、Artifact Store、Trace/Cost。
- [ ] Control Plane 和执行不可信代码的 Workload 完全分离，权限/凭证不能从工作负载直接取得。
- [ ] 所有 Tool Calls 经由受信任的 Host Reference Monitor 检查，而不是相信 Model 的工具权限声明。
- [ ] Run/Step/Activity/Operation/Release 具有稳定身份和防重放机制。
- [ ] Worker Identity 来自 mTLS、Workload Identity 或其他真实身份服务。
- [ ] 已明确每个服务能够读取的 Secret、Tenant、Resource 和 External Endpoint。
- [ ] Credential Broker 使用短期、最小权限凭证，无全能长期 Token 注入到模型或 Sandbox。
- [ ] 服务升级/回滚无法撤销外部副作用；有 Operation Ledger、Reconciliation 和补偿预案。

## 2. Kubernetes Control Plane

- [ ] 部署对象使用经过审查的版本和不可变镜像 Digest。
- [ ] CI 验证镜像签名、SBOM、漏洞扫描、来源及构建供应链。
- [ ] Namespace、ServiceAccount、RBAC、ResourceQuota、LimitRange 已创建且实际可用。
- [ ] 所有非特权服务的 Pod 按 Restricted Profile 配置并由 Pod Security Admission 强制。
- [ ] `allowPrivilegeEscalation=false`、Non-root、Drop ALL Capabilities、Seccomp、只读 RootFS 的实际 Pod 状态符合预期。
- [ ] 禁止通用业务服务使用 HostNetwork、HostPID、HostPath、特权 Pod。
- [ ] Service/Ingress/NetworkPolicy 使用实际受支持的 CNI/Controller 语法。
- [ ] `default-deny` Ingress/Egress 实际生效，并对合法的 DNS/Proxy 通信设计最小白名单。
- [ ] 不能仅因声明网络策略而假设所有外部请求都被隔离；DNS/Redirect/IPv6/Metadata Service/Proxy 都经过实际测试。
- [ ] 所有 ServiceAccount 凭证均与角色绑定，Worker 无权更改 Kubernetes 管理资源。
- [ ] Deployment 的 Liveness、Readiness、Startup Probe 分别有不同的语义，避免负载高时触发级联重启。
- [ ] Worker RollingUpdate 和 TerminationGracePeriod 配合真正的停止领取、Lease/Heartbeat/Reaper。
- [ ] 使用真实的 HPA Metrics Adapter/KEDA 等扩缩容渠道，而不是假设 Queue Length 会自动驱动 HPA。

## 3. 多租户与资源隔离

- [ ] Tenant/Organization/Workspace/Run 的权限和归属由服务端 IAM 决定。
- [ ] Tenant 资源隔离策略（共享 Node、独立 Node、独立 Cluster）由真实风险模型选定。
- [ ] 不同租户的 Workspace、Memory、Object Store、Vector Search、Logs 均有有效 ACL。
- [ ] 跨 Tenant 的检索过滤在查询/排序之前发生。
- [ ] Tenant CPU/Memory/Disk、Max Queued、Max Running、Token/Tool Cost Budget 均有强制配额。
- [ ] Sandbox Worker 使用与真实 CNI、CRI 和 RuntimeClass 配套的 gVisor/MicroVM/其他经过验证的隔离方案。
- [ ] 单次任务结束，Host 独立确认所有 Sandbox Process、Mount、Network、Temp Files 已销毁。
- [ ] Lease Expired 不会提前释放仍在实际占用的 CPU/Memory/Disk。
- [ ] 异常/取消和已提交的外部副作用有可查证的实际状态，而非模型自报。

## 4. 配置与版本

为每次 Release 保存：

```text
release_id
stable_version + candidate_version
git_commit_sha
service_image_digests
prompt/model/tool_policy_versions
tenant/isolation_profile_versions
database_schema_compatibility
run_event_schema_version
sandbox_image_runtime_version
canary_policy_version + metrics_source
rollback_plan + host_approval
```

- [ ] Release ID 重放只能对应相同的 Image/Manifest/Policy Digest。
- [ ] 模型、Prompt、Tool Schema、Worker Image、Verifier Version 都已固定。
- [ ] 已验证 Stable 与 Candidate 对同一 DB/Run/Checkpoint Schema 的兼容。
- [ ] 数据库迁移遵循 Expand → Migrate → Switch → Contract 策略。
- [ ] 任何不可逆 Schema Migration 都应阻止不安全的旧镜像回滚。
- [ ] 进行实际迁移和数据恢复演练，而非只验证版本数字范围。
- [ ] Artifact/Trace/Memory 的旧格式在回滚后仍可读取，或有已验证的转换方案。

## 5. 灰度、观测和发布审批

- [ ] 发布策略有小流量起点、逐级放量、每阶段最少样本量与观测时间窗口。
- [ ] Canary/Stable 的任务类型、用户群、版本、成本口径可比较。
- [ ] 观测绑定 Release ID、Traffic Weight 和唯一窗口 ID；已批准的窗口不得跨阶段重放。
- [ ] 独立 Business Verifier 输出 Task Success，不能使用模型 `done` 作为成功。
- [ ] Provider Usage、Token/Tool/Sandbox Cost 包含失败、重试、验收消耗。
- [ ] P95/P99 Latency 衡量端到端用户 Task，而不是仅模型首 Token 时间。
- [ ] 已确认的 Security Incident 有独立 Abort Gate。
- [ ] 不可比/缺失/低样本量指标明确 Pause，不会默认 Promote。
- [ ] 已证实 Quality/Latency/Cost 回归明确 Abort 或触发人工止损。
- [ ] 100% 业务窗口通过后，才确认候选版本 Promoted。
- [ ] 每次发布决策有 Release ID、Manifest Hash、Policy Hash、Generation、可信指标来源。
- [ ] 两个部署控制器并发操作时使用 CAS/Lease，旧决定不会覆盖新回滚。
- [ ] 可信发布 Actuator 真正应用流量配置，读取实际 Cluster/Ingress 状态并确认。
- [ ] Desired Traffic != Observed Traffic 时不继续扩大灰度。
- [ ] 是否回滚数据库、撤销 Tool 权限、停止 Worker/External Effect 需要独立审查。

## 6. SLO、成本与容量

| 指标 | 需要的生产观测证据 |
| --- | --- |
| API Availability | Auth/API 端到端合法请求成功率 |
| Verified Task Success | 独立 Verifier 确认率 |
| Queue Wait P95/P99 | Ready→Claim、Submit→Verified 的分解 |
| Sandbox Acquire P95 | Cold/Warm 的真实分布 |
| Model Usage Cost | Provider Bill / Usage reconciliation |
| Cost per Verified Task | 包括失败和重试的端到端成本 |
| Security Incident Count | 已确认真实安全事件 |
| Reconciliation Pending | 外部副作用未知且待核对的 Run |
| Resource Saturation | CPU/Memory/Disk/Network/GPU/Provider Quotas |
| Release Rollback Duration | 从安全策略 Abort 到真实 Traffic Backout 的时间 |
| Recovery RTO/RPO | 真实故障注入后可验收结果 |

- [ ] 业务和基础设施 SLO 分开。
- [ ] Error Budget 不能抵销真实安全事故。
- [ ] 没有数据时不伪造 0ms、0 美元、100% Success。
- [ ] 有全局与租户级 Cost/Token/Provider RPM/TPM Budget。
- [ ] 至少测量短、中、长 Agent Task 的 Worker/Sandbox/模型关键路径与容量需求。

## 7. Disaster Recovery 与故障注入

- [ ] Run DB、Operation Ledger、Artifact Store、Trace/Config 的备份可恢复。
- [ ] 真实测试单机故障、Node Eviction、Worker OOM、Scheduler 重启、Queue 分区。
- [ ] DB 模型升级失败时有兼容旧 Worker 的恢复路径。
- [ ] Worker 异常后 Host Reaper 确认销毁，才重新预留资源。
- [ ] 外部 Tool Commit 但 ACK 丢失时不盲目重试 Non-idempotent Operation。
- [ ] 发生模型 Provider 大范围 429，业务和 API 不发生失控重试风暴。
- [ ] 发布回滚之后，对灰度期间真实外部 Side Effects 做独立核对。
- [ ] 清理 Worker 的孤儿进程、Mount、Snapshot、凭证和 Artifact。
- [ ] RTO/RPO 与演练真实结果比较，形成事故改进记录。

## 8. CI 与审查矩阵

| 故障注入 | 本章离线预期 |
| --- | --- |
| Image 指向 `latest` | 配置拒绝 |
| 服务缺少 Privilege Hardening | Preflight Finding |
| Untrusted Worker 降级普通 OCI | Preflight Finding |
| Tenant 共用 namespace | Preflight Finding |
| DB Candidate/Stable Schema 不兼容 | Preflight Finding |
| Missing Canary Metrics | Pause，当前权重不变 |
| Canary 样本量不足 | Pause |
| Canary Security Incident >0 | Abort，Desired Weight=0 |
| Verified Success Regression | Abort |
| P95 Latency/Cost Regression | Abort |
| 缺少 Stable 可靠基线 | Pause |
| 发布控制器使用旧 Generation | VersionConflict |
| 同一 Release ID 偷换 Image/Policy | Identity Conflict |
| 达到 100% 但未完成最终窗口 | 不标记 Promoted |
| Release 已经 Aborted/Promoted | 后续操作拒绝 |
| 发布决策记录写入成功 | 仅说明 Desired State 保存，不说明真实切流 |

**本章没有运行真实 Docker/Kubernetes/Argo、联网访问云资源或对真实多租户数据实施隔离测试。**

## 9. 不可省略的生产集成

```text
Authenticated Release API
    ↓
Trusted Manifest + Policy + Image Verification
    ↓
Reference Policy Gate / Human Approval
    ↓
Versioned Desired Release Ledger (CAS)
    ↓
Authenticated Kubernetes/Argo/ServiceMesh Actuator
    ↓
Observed Cluster Traffic / Rollback Receipt
    ↓
Trusted Telemetry (Run Verifier, P95, Cost, Security)
    ↓
Next Gate Decision or Abort
    ↓
Reconcile External Side Effects and User Task State
```

不要将本章 Python 对象的 `source="trusted-observer"` 或 `runtimeClassName="gvisor"` 当作实际认证或者安全隔离。实战必须接入目标环境的真实 IAM、Admission、Network Controller、Isolated Runtime、真实观测和外部业务回执。

## 10. 官方参考

- Kubernetes [Multi-tenancy](https://kubernetes.io/docs/concepts/security/multi-tenancy/)
- Kubernetes [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- Kubernetes [Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- Kubernetes [Resource Quotas](https://kubernetes.io/docs/concepts/policy/resource-quotas/)
- Kubernetes [Liveness, Readiness and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/)
- Kubernetes [Horizontal Pod Autoscaling](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
- Argo Rollouts [Analysis and Progressive Delivery](https://argoproj.github.io/argo-rollouts/features/analysis/)
- [第 24 章完整正文](../book/chapters/24.md)
