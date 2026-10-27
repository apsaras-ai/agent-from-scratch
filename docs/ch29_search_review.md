# 第 29 章：Inference-Time Scaling / Tree Search 生产化审查清单

> **范围**：这一仓库里的 Chapter 29 只使用安全算术 DSL、Toy Proposal Policy、Host-owned Independent Verifier 和 Python 标准库进行完全离线的合成实验。它既不调用真实语言模型，也不执行 Shell/HTTP/Browser、模型内部 Chain-of-Thought、真实 LATS、Training/RL 或支付邮件服务。  
> **身份假设**：Host、Verifier 和 BudgetMeter 均为本地可信教学代码。如果在同一 Python 进程中执行了恶意 Arbitrary Code，这些 API 并不能提供 OS 级防篡改保障。

## 一、定义搜索算法真正要优化的对象

- [ ] 目标是任务级 **Verified Success**，不是 Token Length、Model Confidence 或 Tool Exit 0。
- [ ] 把 Task Objective、Initial State、Authorized Actions、Verifier、预算、Deadline 明确建模。
- [ ] Proposal Policy 只提出候选动作，不拥有直接修改业务系统和评测结果的权利。
- [ ] Environment State 由可信 Tool/Sandbox 实际执行后确定，不能信任模型输出的 `state` 字段。
- [ ] Verifier 根据环境回执/完整操作路径独立复核，不依赖 Agent 自报 `verified=true`。
- [ ] Search Failure 必须区分 Budget Exhaustion、Unsafe Proposal、Verifier Failure 和没有发现解；不随意宣称“不可解”。
- [ ] 需要真实模型时明确其独立 Candidate/Prompt/Tool 版本；不能以合成搜索分数代表模型能力。

## 二、四类策略的正确评价

| 策略 | 主要机制 | 需要验证的属性 |
| --- | --- | --- |
| Best-of-N | N 条独立候选，使用可靠 Verifier 选择 | Proposal Coverage、相关性、Verifier Cost |
| Self-Consistency | 多路径归一化后的最终答案投票 | 错误共识、输出等价性、采样独立性 |
| Beam Search | 逐层扩展、有限 Width、Heuristic | 剪枝是否错误排除解、State Dedup、Branching Cost |
| MCTS/UCT | Selection、Expansion、Rollout、Backup | UCT Visit/Reward、Rollout 质量、搜索结束条件 |

- [ ] Self-Consistency 只针对最终答案聚合，而非把任意 Tool Path 文本相似当作成功。
- [ ] Best-of-N 需要区分理想 Verifier、近似 Reward Model 和真实 Outcome，避免奖励模型被 Hack。
- [ ] Beam 的启发式是否可能出现 Local Optimum Trap、Task-specific Bias。
- [ ] MCTS 的 Node Parent/Child、Visit Count、UCT Exploration Term 和 Backup 符合数学定义。
- [ ] State 是否 Markov、能否正确 Snap/Rewind、Transposition Table 是否需要包含权限和 Context Version。
- [ ] Tree Node 可能共用中间前缀，但不能跨隔离工作区不安全共享可变状态。

## 三、Budget 与账单

- [ ] **分别**限制 Proposal/Token、Tool/Environment Transitions、Verifier Calls、Candidate/Simulation Count。
- [ ] 按实际消耗记录 Input/Output/Cached Tokens；不能以一次 Toy Proposal 代表一个真实 Token 单位。
- [ ] Verifier 运行也可能很贵，所有失败候选要计入费用。
- [ ] 每个 Run/Tenant/Branch 有明确 Max Spend、Timeout、Provider RPM/TPM、Worker Resource Capacity。
- [ ] Budget 被耗尽时停止新候选，不能继续执行未经批准的 Tool Call。
- [ ] Candidate Fast Pass 可提前停止，但同一 Budget Ceiling 不应被误称为相同的 Actual Compute。
- [ ] 比较 Success–Cost、Time-to-Verified、P95/P99 Latency、Unsafe Attempts，而不只比较单个成功率。
- [ ] 多策略比较要固定任务、Seed、Prompt、Tool/Verifier Version、风险与费用假设。
- [ ] 同一 Case 的多次 Trial 有相关性，不能直接当作独立用户样本做置信度估计。

## 四、Safety 和 Tool Effect

- [ ] Host Reference Monitor 在**每次实际 Tool Execution 前**检查动作、Args、Resource Scope、Tenant/Run。
- [ ] 所有候选在可重置的受控 Workspace/Sandbox 中探索，禁止模型访问宿主 Token、Docker Socket、敏感文件。
- [ ] HTTP/DNS/Egress 和 Secret Broker 由可信主机层强制，而不是 Prompt 指令。
- [ ] 不可逆动作（付款、发邮件、发布、生产部署、删数据）不能作为无约束 MCTS Rollout。
- [ ] 对真实 Tool Side Effect 使用第 21 章稳定 Idempotency Key、外部账本和 Unknown Outcome Reconciliation。
- [ ] 搜索算法停机、候选失败、Worker Lease 过期不等于进程/Sandbox 真的安全停止。
- [ ] Tool Call 恶意字符串如 `shell.exec` 或 `send_email` 必须安全拒绝、记预算，并不能自动变成可执行工具。
- [ ] Candidate Path 不能直接写 `success=true`，Verifier 必须可信地重新计算或读取真实业务状态。
- [ ] 搜索成功只批准一个具体候选，实际落地还要再次检查身份、审批版本和资源范围。

## 五、测试与反例矩阵

| 注入场景 | 必须观察的结果 |
| --- | --- |
| 始终生成非法工具动作 | 所有候选被拒绝，零外部实际 Tool Effect |
| Candidate 伪造 final state | Host 从初始状态重放后拒绝 |
| Goal 在 Tool Range 外 | Task Validation Fail Closed |
| NaN、负预算、Bool-as-int | 输入被拒绝 |
| Proposal Policy 抛秘密异常 | Report 不复制敏感异常全文 |
| Verifier Calls Budget 用完 | 不会伪造有效程序 |
| Self-Consistency 一致但错误 | 多数票依然不能通过 Outcome Check |
| Beam 剪枝错过可行解 | 明确失败而非宣称数学不可解 |
| 不同 Seed 相同任务 | 记录 Trial/Case 版本和区别，统计多次波动 |
| 多次固定 Seed 重放 | 结果/预算/已验证程序精确一致 |
| MCTS 奖励错误 | 独立测试必须能揭示 Reward Hacking |
| Worker Timeout / Lost ACK | External Effect Unknown 必须核对 |
| 两个 Candidate 共用可变文件夹 | 必须使用独立 Sandbox/Workspace 和 Artifact Version |
| 真实 Provider Token Rate 不完整 | 成本必须标为 Unknown，不是假零 |

## 六、真实 Agent Integration 分层

```text
Authenticated User Objective
    ↓
Trusted Agent Orchestrator + Global/Tenant Budget
    ↓
Search Controller / Candidate Policy
    ↓
Node + Branch-Specific Tool Scope
    ↓
Host Tool Policy / Security PEP (Ch19)
    ↓
Isolated Sandbox / Snapshot (Ch18)
    ↓
Trusted Independent Outcome Verifier (Ch25)
    ↓
Durable Artifact + Trace + Cost Ledger (Ch12/21/22/23)
    ↓
Select a specific approved candidate
    ↓
Authorized final effect / Reconciliation
    ↓
EDD Regression Gate (Ch28) + Canary Release Gate (Ch24)
```

**现有离线示例不提供以上跨服务生产部署。** 真实 Coding/Browser/Research Agent 必须逐步增加独立 Workspace、实际 Model Gateway、Provider Usage、Host IAM、授权 Tool Proxy 和多轮故障注入。

## 七、建议记录的生产指标

```text
search_proposal_calls_total{method,task_class}
search_env_transitions_total{method,task_class}
search_verifier_calls_total{method,task_class}
search_candidate_count{method,task_class}
search_unsafe_proposal_total{method,reason}
search_budget_exhaustions_total{method,budget_type}
search_verified_success_total{method,task_class}
search_cost_per_verified_task{method,task_class}
search_time_to_verified_seconds{method,task_class}
search_branch_depth_histogram{method,task_class}
search_verifier_false_positive_total{verifier_version}
search_external_effect_unknown{tool_type}
```

不要使用任务原文、用户标识、未经授权的源码或 Secret 当作 Metrics Labels。

## 八、与课程后续的关系

- **第 30 章 Verifier / Feedback Learning**：评估验证器能力、过程/结果奖励质量、反馈数据的来源与误差。
- **第 31 章 Agentic RL**：在受控环境里训练/更新 Policy，明确 Search Policy 改变之前与之后的独立测试。
- **第 32 章 Self-Improving Agent**：在长期迭代中使用可信 Evaluation、版本比较和外部审批，防止自我评分导致虚假能力提升。

## 参考资料

1. [Scaling LLM Test-Time Compute Optimally](https://arxiv.org/abs/2408.03314)
2. [Is Best-of-N the Best of Them?](https://proceedings.mlr.press/v267/huang25c.html)
3. [Self-Consistency Improves Chain of Thought Reasoning](https://arxiv.org/abs/2203.11171)
4. [Tree of Thoughts](https://proceedings.neurips.cc/paper_files/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract.html)
5. [Bandit Based Monte-Carlo Planning / UCT](https://doi.org/10.1007/11871842_29)
6. [Language Agent Tree Search (LATS)](https://proceedings.mlr.press/v235/zhou24r.html)
7. [第 29 章完整正文](../book/chapters/29.md)
