# Chapter 32 · Self-Improving Agent 研究及生产化审查清单

> **定位**：2026-10-27 教学原型，主线是受约束的 Agent 配置改进，并非自主 Code/Model 修改。该清单是准备工作与可信控制面的设计建议，未勾选的生产能力**尚未实现**。

## 1. 当前确实实现了什么？

- [x] 完全离线、无网络/Shell、只读合成数据，固定两种工具 read / crosscheck。
- [x] 版本化公开合成 Suite：6 Train + 6 Dev + 6 Audit；问题 ID 跨 Split 唯一、内容不完全复制。
- [x] Base Agent Configuration 由 Prompt、Memory Rules 和 Workflow 组成，均使用固定类型/白名单。
- [x] Host-owned Outcome 与 Source Diagnostics 生成**可信 Train 失败类别**，不是 Agent 自报。
- [x] 仅根据 Train 类别产生 bounded、逐维度 Proposal；没有自由执行 Model/LLM 生成的代码。
- [x] Dev 比较**逐 Case** 成功/失败、Synthetic Average Cost 和 Denied Tool Attempts。
- [x] hard gate：质量净增 ≥1、逐 Case 回归 0、平均合成成本 ≤2.5、无未授权工具尝试。
- [x] 最大轮数、最大候选次数限制；拒绝提议不被隐藏。
- [x] 在开发集选择完成后最多调用**一次**合成 Audit 评测。
- [x] 审计通过也不自动激活；独立可信调用方显式传入合成批准才改变内存中的 Active Config。
- [x] 内存 Revision CAS、旧版本回滚与事件记录；错误 Revision、复制的 Audit Receipt 会被拒绝。
- [x] 恶意 Source 文本只是数据，不会被当作动态 Tool Instruction、Shell 命令或 Python 执行。
- [x] 所有特定的学习效果均由固定合成问题分布驱动；实验**不声称真实模型泛化**。

## 2. 实验实际教学结论

| 可控配置 | Dev verified success | 平均合成工具成本 |
| --- | ---: | ---: |
| Baseline: shortcut + direct | 2/6 | 0 |
| Prompt: inspect_source | 3/6 | 1 |
| Memory: verify_warned | 5/6 | 2 |
| Memory: retry_transient | 6/6 | 13/6 |
| Shortcut + verify_all | 6/6 | 3（**不符合成本门槛**） |

终局 Audit 使用新的 Fixture IDs/Truth 值，作为**公开合成的一次性负反馈**。在同一个进程里，Python 开发者仍可直接读取这些 Fixture；这样的“sealed”仅是 API 访问路径限制，不是机密性或真正第三方验收。

注：表格每一步代表当前配置加上之前已接受的配置变更，不能解读成各变量在全局上的独立因果贡献。需要另做消融才能证明因果归因。

## 3. 未实现的生产安全能力（NOT implemented）

### Evaluation 与 Data Governance

- [ ] 企业真实 Tool Effects / Verifier 的权威证据来源与身份验证。
- [ ] 真实训练、开发、审计数据的组织/权限隔离；不能由训练 Agent 访问隐藏答案。
- [ ] 独立评测服务，审计查询次数和反馈粒度受控，防止反复调参泄漏。
- [ ] 多 Seed、多任务切片的统计不确定性评估、变更审批前预注册方案。
- [ ] Prompt/Memory 真实 Token 成本、真实 Tool/模型用量、真实延迟与 API Rate Limits。
- [ ] 独立安全/滥用测试与客户业务后果评估。

### Memory Trust

- [ ] 对用户数据建立 Ownership、同意、Retention、删除、隔离、回滚与可追踪来源。
- [ ] 可信/不可信内容的解耦，来源指令永不自动获得系统或工具调用权限。
- [ ] 反思候选来源认证、支持反例/过期检查与 Memory Poisoning 抗性评测。
- [ ] 检索版本化、访问控制与任务上下文权限绑定。

### Workflow / Code Self-Modification

- [ ] 预审核 DAG Schema、类型检查、工具权限 Policy 静态分析。
- [ ] 真实不可信补丁必须在 OS/容器/MicroVM 中隔离执行、独立编译/测试。
- [ ] 模型无权修改 Security Monitor、生产 Release Policy、Hidden Tests 或 Audit Ledger。
- [ ] 网络 Egress、Secret 管理、非幂等操作与补偿机制。
- [ ] 候选修改后对所有已有 Safety Case 与长流程做全量回归。

### Release, Monitoring and Rollback

- [ ] 真实 IAM 与身份认证审批；不能依赖布尔 Approved Flag。
- [ ] 签名发布构件、真实 Git/Artifact Digest、外部事务和不可篡改审计渠道。
- [ ] CI Gate + Human Review + Canary Gate + 强制 SLO 监控；受信任控制器执行 Release。
- [ ] 线上错误归因、Rollback/Compensation，以及对已发生不可逆副作用的独立核对。
- [ ] Prompt/Memory/Workflow/Model Tool Schema 完整版本锁定。

## 4. 必须保留的单元测试不变量

1. Unknown Tool/超过预算的 Tool 一律拒绝。
2. 未经验证的网页内容不会成为 Tool 名称、系统指令或被执行的源代码。
3. 仅 Train Failure Code 进入 Proposal Generator。
4. Dev 比较必须使用相同 Dataset/Case IDs 和同一个版本化环境。
5. 只优化 Aggregate Score 但降低个别关键 Case 时，候选被 Regression Gate 拒绝。
6. 没有满足成本预算就算通过率更高也不得胜出。
7. Evaluation Output 里有 Denied Tool Attempts 时必须 Safety Gate 拒绝。
8. Audit 不接受由 Optimize Loop 反复查询，一次完成后关闭。
9. Copy/Forged Audit Receipt 不成为自动 Activation 依据（教学中的对象身份，而非生产认证）。
10. 所有激活/回滚都必须显式批准与 Revision CAS。
11. 通过 Eval 和实际生产发布是不同事实；当前实现根本没有部署行为。

## 5. 正确引用的研究方向

| 研究 | 主要改进对象 | 主要启示 |
| --- | --- | --- |
| Self-Refine (NeurIPS 2023) | 单次候选输出 | 迭代自反馈，不更新模型权重 |
| Reflexion (NeurIPS 2023) | 跨 Episode 反思记忆 | 语言反馈可影响后续行为，但不是梯度 RL |
| MIPRO (EMNLP 2024) | 多模块 Prompt 与演示 | 黑盒程序层优化 |
| AFlow (ICLR 2025) | Agent Workflow 结构 | 工作流搜索与独立结果评测 |
| SICA (2025) | Agent 代码 | 必须关注代码版本、Benchmark 与 Sandbox |
| DGM (2025) | Agent 代码演化 Archive | 多样候选路径与迭代式实证验收 |
| AlphaEvolve (2025) | 算法和程序候选 | 自动评估器是关键 |
| GEPA (ICLR 2026) | 反思式 Prompt 演化 | 从轨迹与反馈中提出优化候选 |

参考 URL 与正文见 [第 32 章](../book/chapters/32.md)。

## 6. 如何运行

~~~bash
python -m pip install -e .
python examples/ch32_self_improvement_lab.py
python -m unittest discover -s tests -p 'test_ch32.py' -v
~~~

**警示**：表格式/规则型教学 Agent 自改进不是“具有自我意识或无限能力增长的智能体”，也没有证据表明它能在真实 LLM 工作流中自动复制这些百分比。