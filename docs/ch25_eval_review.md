# 第 25 章 Agent Evaluation：生产评测验收清单

> **范围**：用于研发和安全评审，不是生产测试授权或真实模型能力报告。当前 Python Harness 仅执行公开的合成 Fixture、可信 Fake Model、受控内存环境，不运行真实模型、网络、外部系统或未知代码。`expected_state` 仅通过接口与 Agent Runner 分离；**同进程运行不提供 OS 安全隔离**。

## 1. Task、Outcome 与 Verifier

- [ ] 每项任务有真实用户目标、环境初始状态、受控工具权限和独立成功条件。
- [ ] Model Text、Agent 自报、Tool Result 与最终外部业务状态分别存储。
- [ ] Task Success 依赖独立的后置状态、业务验收或测试，而不是模型生成的字符串。
- [ ] 只有可信 Verifier/Host 对最终业务状态进行标注，Worker 不能修改隐藏 Ground Truth。
- [ ] Tool Unauthorized Attempt 与真实 Unauthorized Side Effect 都要记录，且区分是否被 Host 拦截。
- [ ] 对 Coding Agent 验证 Patch、独立测试和禁止更改测试文件。
- [ ] 对 Browser Agent 验证服务端状态、Cookie 隔离、保存/发布次数。
- [ ] 对 Research Agent 验证 Claim-Evidence-Source 和引用准确性。
- [ ] 对 Multi-Agent 验证委派权限、共享状态 CAS、最终结果和外部环境。
- [ ] 对 Unknown External Outcome，不要擅自把 Timeout 视为没有产生副作用。

## 2. Dataset 版本与隐私

- [ ] Dataset/Case ID、Version、Split、环境/工具版本被固定或签名。
- [ ] 每次报告绑定 Dataset SHA256 和配置版本，不同 Ground Truth 不直接复用同一报告。
- [ ] Development/Regression/Heldout 的用途明确；Heldout 不参与迭代调参。
- [ ] 真实数据收集获得授权，去标识化后仍做存储/访问/保留策略审查。
- [ ] Agent Runner 只能读取公开任务输入和获得授权的工具响应。
- [ ] 隐藏标签与 Grader 不与不可信 Agent 共享进程、文件权限或 Namespace。
- [ ] 恶意结果不能通过 Prompt Injection 改写 Test Harness、报告或 Release Gate。
- [ ] 防止训练数据、公开测试仓库、缓存命中导致 Dataset Contamination。
- [ ] 定期添加真实新故障 Case，监控旧 Dataset 的 Saturation 和漂移。
- [ ] Task Case 包含可解释的分类/难度与失败类型，避免抽样偏差。

## 3. Trial 独立重置

- [ ] 每次 Trial 重新创建数据库/Repo Worktree/Browser Session/Sandbox。
- [ ] 真实工具副作用在可控测试资源发生，不能触达生产租户账户。
- [ ] 旧 Tool Requests、Session Cookie、缓存、模型上下文和 Temp Files 已清理。
- [ ] 并行评测不共享可变运行环境，或使用强事务隔离。
- [ ] 随机 Seed、Model/Prompt/Tool/Routing 版本、Provider Config 和运行时间记录。
- [ ] 多次 Trial 的费用、超时和失败均保留，不删掉非成功样本。

## 4. Graders 与 LLM-as-a-Judge

- [ ] 对可观察状态优先使用确定性环境 Grader。
- [ ] 主观评价有明确 Rubric、Human Calibration 与误判分析。
- [ ] Model Judge 不具有外部 Tool / IAM / 文件写入权限。
- [ ] 待评文本作为低信任数据，不是 Judge 的系统规则。
- [ ] Pairwise Judge 对 A/B 交换顺序，测量位置偏差与响应长度偏好。
- [ ] Judge Score 不自动覆盖环境后置条件和硬性安全约束。
- [ ] Judge 报错或缺少证据时输出 Unknown/Needs Review，而不是默认为通过。
- [ ] 任务有多种合理的合法路径时，不用某条唯一固定 Tool 顺序作绝对验收。
- [ ] 所有评分规则通过反例测试，验证不会轻易被模型自报完成欺骗。

## 5. 统计与 A/B

- [ ] 任务级成功率、调用级成功率和外部效果成功率分开统计。
- [ ] `pass@k` 表示至少一次成功，`pass^k` 表示 k 次全部成功；不从 n 小于 k 的数据外推。
- [ ] 指明采用观测前 k 条 Trial 还是组合估计，不混淆两种定义。
- [ ] Wilson Interval / 二项分布推断所依赖的独立性假设已说明。
- [ ] 同一 Task 多个 Trial 不能作为独立 Case 直接做 McNemar。
- [ ] Paired Comparison 固定 Dataset/Trial Index/Tool Policy/Resource Budget。
- [ ] 记录所有 Regressions，不只列出 Improvements。
- [ ] 报告任务类别与难度切片，防止总体成绩掩盖安全退化。
- [ ] 小样本不以 `100% pass` 声称一般场景可靠。
- [ ] 预定义发布阈值、置信度和安全一票否决规则，防止结果出来后选择性调参。

## 6. 成本与运行时指标

- [ ] 完整 Provider Usage（Input、Output、Cached）和固定版本 RateCard。
- [ ] Tool、Sandbox、Network、Verification、Retry 和失败成本全部记账。
- [ ] Usage/Rate 缺失时费用为 Unknown，不允许静默视为零。
- [ ] Cost/Verified Task 的分子包含所有尝试；分母为零时为不可计算。
- [ ] 分开观测 End-to-End Latency、Queue Wait、Sandbox Acquire、Model/Tool Span。
- [ ] P50/P95/P99 的样本量及观测窗口明确，不用合成响应时间作真实 SLA。
- [ ] 追踪匿名化或最小化，不泄漏 Prompt、用户身份、Secret 和 Tool Args。

## 7. CI / Benchmark 运行模式

### Fast offline CI

- [ ] 每次代码变更执行固定版本的小型 Fixture；
- [ ] 验证 Outcome Grader、Policy Deny、Dataset Loader、Trial Isolation；
- [ ] 验证异常失败不会从分母里丢失；
- [ ] 验证 Judge 虚假高分不会改变环境失败；
- [ ] 验证 Cost Unknown、Wilson Edge Cases、Paired Run 的正确性；
- [ ] 无联网/模型调用/外部副作用，确保低成本、确定性。

### Real authorized eval

- [ ] 使用独立隔离 Worker、可信 Tool Proxy、真实业务 Fixture Server；
- [ ] Model Provider、Sampling、RateCard、Dataset、Prompt、Tool Version 固定；
- [ ] 针对 Benchmark 许可和真实用户数据获取必要授权；
- [ ] 同步收集 Model Usage、Tool Calls、独立业务验收与安全事件；
- [ ] 反复运行、报告置信度、分组回归及 Cost/Verified Task；
- [ ] 故障时通过第 21 章 Durable Ledger 核对真实外部效果；
- [ ] 通过第 24 章可信 Release Gate 依据预先登记的阈值决定发布；
- [ ] 不把 Model-as-a-Judge 的摘要当作成功率唯一证明。

## 8. 本章能力边界

已实现：

- 可信本地 JSON Dataset Loader 与 Digest；
- PublicTask / Hidden Ground Truth 接口分离；
- 真实 AgentKernel + Fake Model/ToolRegistry 与内存环境；
- Independent Outcome/Policy Grading；
- Unknown Cost、Token/Cache RateCard、合成 Latency；
- Observed `pass@k`、`pass^k`、First-Trial Wilson 95%、Paired Comparison；
- 仅使用不同 Case 首次试验的教学版 Exact McNemar；
- 40 项单元测试和离线 CI。

**未实现**：

- 真实模型调用、Agent Benchmark Dataset 的统计代表性；
- 受权威信任的网络/文件/数据库副作用 Verifier；
- 容器/VM 级评测隔离、强制 IAM 与防逃逸；
- 真实 LLM-as-a-Judge 的人工校准；
- 大规模 Cluster Bootstrap、统计显著性计划、生产 A/B；
- 完整多模态评测、真实工具成本和线上 Trace Access Control；
- 外部 Benchmark 排名或任何真实产品质量保证。

## 核心参考

- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)
- [OpenAI: Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals)
- [SWE-bench](https://arxiv.org/abs/2310.06770)
- [τ-bench](https://arxiv.org/abs/2406.12045)
- [AgentDojo](https://arxiv.org/abs/2406.13352)
- [第 25 章正文](../book/chapters/25.md)
