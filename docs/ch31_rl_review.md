# Chapter 31 Agentic RL：研究、验证与生产化检查表

> 状态：教学基线 / 技术审稿清单（2026-10-27）。本章**没有**实现线上 Tool Agent Reinforcement Learning。任何未列出的生产能力均不得据此推断存在。

## 一、当前实际实现（What is implemented）

- [x] 完全离线、固定工具 Allowlist 的算术 MDP：每次 Host Action 真实改变整数环境状态。
- [x] 二步 Trajectory 的显式结构、真实状态转移与 Terminal Binary Reward。
- [x] 独立 Host Replay：不相信 Agent 自报的末态、工具输出、成功或 Reward。
- [x] 表格式 Softmax 随机策略；每个 Observation 记录受约束的合法动作概率。
- [x] 同一旧策略产生 On-policy Batch，并冻结 Old Probabilities 供多轮更新。
- [x] REINFORCE 式 Advantage × Score Function 参数更新（仅一轮更新）。
- [x] PPO-style clipped surrogate，包括正、负 Advantage 的裁剪梯度正确方向。
- [x] GRPO-style 同一 Prompt 的 Group-relative Advantage，显式零方差处理。
- [x] 可选的精确小动作空间 Categorical KL 正则。
- [x] 对有限环境枚举所有动作，计算训练前后**精确** Outcome Success Probability。
- [x] 独立随机数种子的 Evaluation Rollout；验证任务无泛化的负对照。
- [x] Python 3.11+ 及标准库实验/单元测试；不依赖模型 API、GPU 或容器。

注意：程序中训练的是真实可变参数的离散策略，不是标签查表；但它**不是**语言模型训练。Batch-mean Baseline 在有限批次下有偏。表格式策略从未见过的 Goal 上自动回到均匀分布，因此不是跨任务泛化。

## 二、生产落地前必做（Not implemented）

### 数据与 Reward 的信任边界

- [ ] 版本化任务、独立的 Heldout / Production Regression Dataset，记录数据使用范围。
- [ ] 由可信 Host/外部权威系统签署或认证 Outcome 与 Tool Effects；不能让 Model 创建“真实奖励”。
- [ ] Reward 与 Tools 调用费用、时间、失败原因对应；未经观测的 Usage 必须保持 Unknown。
- [ ] 增加隐藏测试、反 Reward-Hacking 对照集和回报模型校准。
- [ ] 对所有 Policy Variants 复用**同一真实业务目标**，避免 Proxy 选择偏差。
- [ ] 高风险动作只能进入经审批的隔离/模拟环境，不能为了梯度探索重复真实付款、邮件、发布。

### 真正的语言模型训练

- [ ] 使用 Token-level Log Probability，明确定义 Tool Arguments 和 JSON Token 的 Policy Mask。
- [ ] 不要对系统、工具返回及用户消息求 Action Token 的策略梯度。
- [ ] Implement Critic/GAE 或其他可验证的 Long-horizon Credit Assignment；需要研究独立的 Baseline 估计。
- [ ] 多长度 Trajectory 的 Token/Turn Normalization、EOS/Truncation、Stop Reward 需要系统对照。
- [ ] 采用受验证的训练栈管理 PyTorch Autograd、Mixed Precision、Optimizer、Numerical Overflow、Gradient Norm。
- [ ] 保证 Actor sampling 版本、Reference Checkpoint 与 Learner Update 的时序关系；检测 Stale Rollout。
- [ ] 引入大模型训练前先通过短任务与长任务、多 Seeds、真实未见任务和多个 Reward 版本的对比。
- [ ] 明确 DP/隐私、数据归属、训练 Provider 的使用授权及存储期限。

### Agent Runtime / Security / Reliability

- [ ] 用真实隔离 Worker/Sandbox/VM 执行第三方代码、网页、插件或工具（参见第 18–21 章）。
- [ ] 每个 Episode 独立 Context/工作区/身份权限/可恢复状态，验证 Reset 没有跨 Rollout 污染。
- [ ] 高风险 Tool 需 Host Authorization、Least Privilege、审批 Token 与防重放控制。
- [ ] 真实 Provider 的幂等性及 Non-idempotent Operation Ambiguity 不得由训练框架自行“猜测完成”。
- [ ] 评估每次 Rollout 的 Wall Time、Token、Tool Calls、Sandbox 资源和未决清理。
- [ ] Metrics 必须覆盖 Safety Violations、Unauthorized Effects、Verified Success、Cost/Success、P95 Latency、Retry/Timeout、Truncation、Policy Divergence。
- [ ] 独立 Canary/Release Gate 批准后才能更新生产 Policy，支持回滚。

## 三、回归测试应验证的关键不变量

1. **Host reward is authoritative**：伪造的 Final State/Reward 必须拒绝。
2. **Action allowlist enforced**：非法动作不能触发真实状态转移。
3. **Frozen old distribution**：同一 PPO/GRPO Batch 中，重要性分母不应随每个 Epoch 更新。
4. **Negative-advantage clipping correct**：负优势比率过低应进入梯度平台，过高仍可受罚。
5. **No artificial gradient for zero-variance groups**：所有 Group Rewards 一致时 Reward Gradient 应为零。
6. **Reference-KL is independent**：如果打开 KL 正则，即使 Reward Gradient 为零，Reference Penalty 仍可能改变参数；必须分项计量。
7. **No evaluation leakage**：训练样本/训练种子/评测任务必须明确版本与切分；即使新 Random Seed 更好也不能称为新任务泛化。
8. **Metrics don't lie**：未观测成本、未知风险和没有检查的真实任务不能当作 0 或成功。

## 四、工程中建议的训练产物契约

| 产物 | 必需版本及证据 |
| --- | --- |
| Task Spec | task_id、origin/goal、tool schema version、sandbox snapshot、constraints、split |
| Rollout | sampling policy digest、old logprobs、tool observation digest、terminal/truncated reason、resource usage |
| Verified Reward | verifier digest、event proof、reward schema、hard constraint result、signed/trusted origin |
| Training Run | dataset digest、actor checkpoint、reference checkpoint、optimizer config、sampling Seed、source revisions |
| Evaluation | independent data digest、quality/safety/cost/latency result、uncertainty & regressions |
| Release | explicit approver、candidate digest、canary evidence、rollback target |

这些字段是一份**计划性的契约**，本章 Toy Demo 并没有实现认证、完整存储或生产追溯系统。

## 五、最小可复现实验

~~~bash
python -m pip install -e .
python examples/ch31_agentic_rl_lab.py
python -m unittest discover -s tests -p 'test_ch31.py' -v
~~~

实验入口的结果只用于验证算法行为和边界，不用作真实 LLM / Browser / Coding Agent 的能力声明。

## 六、关键资料

- [PPO (Schulman et al., 2017)](https://arxiv.org/abs/1707.06347)
- [DPO (Rafailov et al., 2023)](https://arxiv.org/abs/2305.18290)
- [GRPO / DeepSeekMath (Shao et al., 2024)](https://arxiv.org/abs/2402.03300)
- [DeepSeek-R1 (Nature, 2025)](https://doi.org/10.1038/s41586-025-09422-z)
- [RAGEN (Wang et al., 2025)](https://arxiv.org/abs/2504.20073)
- [ToolRL (Qian et al., NeurIPS 2025)](https://proceedings.neurips.cc/paper_files/paper/2025/hash/97c5b2707228e7e3fb67e4ecc2e0e607-Abstract-Conference.html)
- [Agent Lightning (Luo et al., 2025)](https://arxiv.org/abs/2508.03680)
- [verl Agentic RL](https://verl.readthedocs.io/en/latest/start/agentic_rl.html)
