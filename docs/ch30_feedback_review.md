# 第 30 章：Verifier / Feedback Learning 生产化与研究审查清单

> **定位**：教学评测与原理验证，不是生产 Reward Service、真实 LLM 训练、独立 IAM、强化学习框架或恶意代码安全沙箱。
>
> 本仓库当前只提供 Toy 算术环境、确定性 Host Verifier、偏置 Proxy Reward 的对抗例子、Reward Calibration 和 Beta(1,1) 平滑的 Tabular Policy。数据集为 12 条公开合成 Task（6 Train + 6 Heldout），训练标签可通过固定四个 Operator 的可信重放完全生成；实验不需要外部网络/API/GPU 或用户数据。

## 1. Verifier 类型与真实业务事实

| 组件 | 输入 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| Syntax Validator | JSON Schema、字段类型 | 格式合法 | 任务已完成 |
| Tool Policy / PEP | Principal、Run、Resource、Action | 允许尝试的操作 | 实际 Tool Effect 成功 |
| Process Verifier | Task + Step Trace | 局部步骤符合检查条件 | 最终 Outcome、全局最优 |
| Outcome Verifier | 状态/外部权威回执 | 某个业务目标的真实后置条件 | 所有相关影响都已撤销 |
| Reward Model | Prompt、Trajectory、Prediction | 模型对质量的估计 | 可直接授权生产操作 |
| Human Feedback | 经过审查的任务与证据 | 某种 Rubric 下的人工判断 | 自动避免评价分歧和标注偏差 |

- [ ] Verifier 的权限由可信 Host/IAM 控制，不与 Candidate Agent 的身份混用。
- [ ] Verifier 不能只阅读 Agent Final Text 或 `reward=1` 自报。
- [ ] 能读取/校验最终业务系统状态，而不是只看 ToolCall 已被接受。
- [ ] 对高风险操作，安全审批通过和真实业务完成分别记录。
- [ ] Process Label 必须定义清晰，是规则合法性、逻辑推理正确性，还是业务先决条件正确性。
- [ ] Process Verifier 确认最早出错步骤后停止不安全的后续工具执行。
- [ ] Unknown External Effect 与 Verified Failure 不混淆；丢失 ACK 不能重放不可逆操作。
- [ ] Verifier Version、Environment Version、Tool Schema 和审批范围与反馈记录关联。
- [ ] 模型不能通过修改测试、删除验收条件或伪造外部回执使 Verifier 返回成功。

## 2. 反馈来源与数据许可

每条真实 Feedback Record 应考虑至少保存：

```text
feedback_id, task_id, run_id, tenant_id,
task_version, tool_scope, policy_version,
candidate_model_version, dataset_version,
environment_version, verifier_version,
trajectory_or_artifact_digest,
outcome_label, earliest_process_error,
trusted_provenance, oracle_receipt_reference,
source_permission_and_retention,
training_or_heldout_split, timestamp
```

- [ ] 反馈来源有 IAM/工作负载身份、签名或其他不可伪造的权威证明。
- [ ] `source="independent_host_verifier"` 这类字符串不能取代真实来源认证。
- [ ] 敏感日志、真实邮件/付款/客户文件最小化，受授权/加密和保留期约束。
- [ ] 所有反馈经过真实业务回执核对，区分 `confirmed`、`not_applied`、`unknown`。
- [ ] 训练是否许可使用对应用户/项目数据得到明确授权。
- [ ] Hidden Grader 与 Heldout Dataset 的访问权限与 Runner/Trainer 分离。
- [ ] Train 与 Heldout Case ID 不重叠，防止直接样本重复或 Ground Truth 泄漏。
- [ ] 数据集、Label 和 Reward Model 发生版本变动后不直接重用旧评测分数。
- [ ] 剔除重复/不完整/选择性采样造成的训练分母错误。
- [ ] 发布相关的反馈使用可信独立校准集，不允许 Model 自行筛选它最擅长的 Case。

## 3. Reward Model 安全审查

- [ ] 事先定义 Reward 是概率、相对偏好，还是启发式分数。
- [ ] 只有真有概率语义的 Score 才解释为校准概率。
- [ ] 在独立真值上测量 TP/FP/TN/FN、Precision、Recall、FPR。
- [ ] 计算 Brier/ECE 时记录 Bucket 规则、阈值、样本量与置信不确定性。
- [ ] 在独立数学/代码/工具安全数据上测试 OOD 泛化。
- [ ] 有针对安全、支付、删除、发布等高风险 Tool 的单独 False Positive 门。
- [ ] 不能以 Reward Model 的训练 Loss 或 Token Length 冒充 Outcome Success。
- [ ] 用 Best-of-N/MCTS 对 Reward Model 做选择压力测试，检查是否放大奖励漏洞。
- [ ] 用真实 Outcome Verifier 检查那些 Proxy 得分最高的候选。
- [ ] Reward Hacking 和 Goodhart 反例加入 Regression 数据集。
- [ ] Human/LLM Judge 的偏好、位置偏差和 Prompt Injection 风险经过校准。
- [ ] Verifier、Reward 和 Tool Permission 分别有固定版本、Owner 和变更审查。

## 4. 教学实验所证明的事实

### Process/Outcome 的分离

```text
Task initial=1, target=7
A: add3 -> add3        => process_ok=True, outcome_ok=True
B: add1 -> add1        => process_ok=True, outcome_ok=False
C: add3 -> publish     => first_error_step=2, process_ok=False
```

### Reward Hacking

```text
Task initial=1, target=4, max_steps=4
Short correct program: add3                  proxy score 0.25, true 1
Long wrong program:   add1 add1 add1 add1   proxy score 1.00, true 0
```

Brier=0.78125、ECE=0.875 的示例只用于暴露固定的错误 Proxy，不能推导现实任何 Reward Model 的校准性能。

### Feedback Learning

```text
6 training tasks × 4 permitted operators
    = 24 host-verified binary labels
    = 6 positive, 18 negative

6 heldout tasks:
    Baseline add1-only choice: 3/6
    Tabular feedback policy:   6/6
```

这个改善来自人工设计的简单 `target-initial` 特征重用，不是 LLM RL/训练或独立大型 Benchmark 的性能证据。

## 5. 需要的负面测试

- [ ] 伪造 `claimed_final` 不会改变真实状态和标签。
- [ ] 非法 Tool (publish/shell/send_email) 不被执行。
- [ ] 第一个非法步骤定位正确，之后的动作不会执行。
- [ ] 长而错误的候选可取得高 Proxy Reward，但仍无法通过 Host Outcome。
- [ ] Reward 输入 NaN、Infinity、Bool-as-number、负概率被拒绝。
- [ ] TP/FP/FN/TN、Brier/ECE 按固定 Toy 数据可复现。
- [ ] Train/Heldout 明确分离，训练过程不读取 Heldout Labels。
- [ ] 同一问题跨 Split 复制时 Dataset Loader 拒绝。
- [ ] 标签翻转、路径 Digest 篡改、Suite Version 不匹配时拒绝训练。
- [ ] Train Action Coverage 不完整时报告错误，不伪装成完整反馈。
- [ ] 旧 Policy 不能冒充新 Suite Digest 的 Heldout 评测。
- [ ] 反馈数据没有来源授权或真实外部回执时不能成为可信标签。
- [ ] Data Leakage、Label Selection Bias 和 Reward Gaming 被单独记录。
- [ ] 真实不可信 Runner 不能直接访问 Verifier Secret/Hidden Dataset。

## 6. 完整的生产架构目标

```text
Authenticated Agent Run
      ↓
Trusted Tool Policy and Sandbox
      ↓
Versioned Tool/External Event Ledger
      ↓
Independent Outcome/Process Verifier
      ↓
Signed feedback with privacy & provenance
      ↓
Authorized training data curation
      ↓
SFT / BC / Reward Modeling / RL (distinct training jobs)
      ↓
Versioned candidate policy
      ↓
Independent heldout + adversarial + cost evaluation
      ↓
EDD Gate / Human Approval
      ↓
Authenticated progressive release
      ↓
Real-world outcome observation and new failures
```

与前书：

- 第 18–19 章：OS 隔离、Tool Scope 和可信 Host；
- 第 20–21 章：Lease、Retry、真实副作用、Unknown Outcome；
- 第 22–24 章：脱敏 Trace、Cost 和 Release；
- 第 25–28 章：Benchmark、Outcome Eval、EDD；
- 第 29 章：固定 Policy 下的 Test-Time Search；
- 第 31 章：真正 Policy Gradient / Agentic RL；
- 第 32 章：长期自我改进与防止自评闭环失真。

## 7. 官方和学术参考

- [Training Verifiers to Solve Math Word Problems](https://arxiv.org/abs/2110.14168)
- [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050)
- [PRM800K](https://github.com/openai/prm800k)
- [ProcessBench, ACL 2025](https://aclanthology.org/2025.acl-long.50/)
- [RewardBench](https://arxiv.org/abs/2403.13787)
- [DeepSeek-R1, Nature 2025](https://www.nature.com/articles/s41586-025-09422-z)
- [Specification Gaming, Google DeepMind](https://deepmind.google/blog/specification-gaming-the-flip-side-of-ai-ingenuity/)
- [第 30 章正文](../book/chapters/30.md)

**验收边界**：本教学 Repo 的 CI 通过，只证明这份固定任务/代码的内部断言。任何生产训练、模型发布、用户数据处理或外部工具使用都需要单独的实质性授权与真实环境验收。
