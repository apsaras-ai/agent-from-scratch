# 从零构建 AI Agent：原理、架构与生产实践

**Agent Development: From First Principles to Production-Grade Autonomous Systems**

一本面向软件工程师与研究生的中文 Agent 开发教程：不从框架 API 入手，而是从决策模型、Agent Loop、工具协议与状态机出发，逐步实现 Coding Agent、生产级 Agent Runtime、评测系统和自我改进机制。

> 📘 **状态：正式写作中。** 第 1–33 章已形成正文初稿与配套 Python 实验，仍待技术审稿；第 34 章目前为写作骨架。未完成的章节不代表已出版内容。

## 本书特色

- **原理优先**：MDP / POMDP、ReAct、Planning、Verifier、Agentic RL 与系统设计。
- **从零实现**：以 Python 3.11+ 编写最小 Agent Kernel，不依赖重型 Agent 框架。
- **工程闭环**：Sandbox、权限、安全、Durable Execution、并发调度、Tracing、Benchmark。
- **贯穿项目**：`mini-agent` 从最简单的工具调用演进为可部署、可评测的 Coding Agent。
- **实验驱动**：用任务成功率、成本、延迟、可靠性检验每个设计。

## 阅读路线

| 篇 | 内容 | 章节 |
| --- | --- | --- |
| 第一篇 | Agent 基础原理与系统模型 | 1–4 |
| 第二篇 | 真实原理与实现：Agent 核心架构 | 5–12 |
| 第三篇 | 真实场景：Research、Coding、Browser、Multi-Agent | 13–17 |
| 第四篇 | 生产级 Runtime 与 Infrastructure | 18–24 |
| 第五篇 | Evaluation、Benchmark 与优化 | 25–28 |
| 第六篇 | 高级推理、Agentic RL 与自我改进 | 29–32 |
| 第七篇 | 端到端的生产级 Coding Agent | 33–34 |

➡️ [第 1 章正文](book/chapters/01.md) · [第 2 章正文](book/chapters/02.md) · [第 3 章正文](book/chapters/03.md) · [第 4 章正文](book/chapters/04.md) · [第 5 章正文](book/chapters/05.md) · [第 6 章正文](book/chapters/06.md) · [第 7 章正文](book/chapters/07.md) · [第 8 章正文](book/chapters/08.md) · [第 9 章正文](book/chapters/09.md) · [第 10 章正文](book/chapters/10.md) · [第 11 章正文](book/chapters/11.md) · [第 12 章正文](book/chapters/12.md) · [第 13 章正文](book/chapters/13.md) · [第 14 章正文](book/chapters/14.md) · [第 15 章正文](book/chapters/15.md) · [第 16 章正文](book/chapters/16.md) · [第 17 章正文](book/chapters/17.md) · [第 18 章正文](book/chapters/18.md) · [第 19 章正文](book/chapters/19.md) · [第 20 章正文](book/chapters/20.md) · [第 21 章正文](book/chapters/21.md) · [第 22 章正文](book/chapters/22.md) · [第 23 章正文](book/chapters/23.md) · [第 24 章正文](book/chapters/24.md) · [第 25 章正文](book/chapters/25.md) · [第 26 章正文](book/chapters/26.md) · [完整 34 章目录](book/README.md) · [核心架构](docs/architecture.md) · [大学公开课与阅读资料](docs/references.md)

第 1 章可运行实验：`python examples/ch01_workflow_vs_agent.py`（完全离线，比较 Pipeline、条件 Workflow 和规则式 Policy Loop；不把手写策略误称为 LLM 推理）。

## 快速运行：一个不依赖 API Key 的 Agent Loop

仓库从一个 **离线脚本化 Model** 开始，用它验证 `model → tool call → tool result → model` 的控制流。它模拟模型输出，**不是**真正的 LLM，也不代表自主推理能力。

```bash
git clone https://github.com/apsaras-ai/agent-from-scratch.git
cd agent-from-scratch
python -m pip install -e .
python examples/offline_demo.py
python examples/ch01_workflow_vs_agent.py
python examples/ch04_agent_loop.py
python examples/ch05_run_lifecycle.py
python examples/ch06_gateway_demo.py
python examples/ch07_tool_runtime.py
python examples/ch08_context_lab.py
python examples/ch09_memory_lab.py
python examples/ch10_planning_lab.py
python examples/ch11_workflow_lab.py
python examples/ch15_coding_lab.py
python examples/ch17_multi_agent_lab.py
python examples/ch18_sandbox_lab.py
python examples/ch19_security_lab.py
python examples/ch20_scheduler_lab.py
python examples/ch21_reliability_lab.py
python examples/ch22_observability_lab.py
python -m unittest discover -s tests -v
```

原有最小示例应输出：

```text
Agent: 2 + 3 = 5
Steps: 2
```

可选：使用支持 Chat Completions + Tools 的兼容接口运行真实模型：

```bash
export AGENT_API_KEY="your-api-key"
export AGENT_MODEL="your-model"
# 可选：export AGENT_BASE_URL="https://your-compatible-endpoint/v1"
python examples/live_tool_call.py
```

不同服务商的 API、工具调用字段与模型支持情况可能不同；该适配器是教学用途的最小实现。


第 3 章新增离线数学实验：`python examples/ch03_decision_process.py`（有限 MDP Value Iteration、POMDP Belief Update、Value of Information）。

第 4 章实现新教学内核 `mini_agent.agent_v2`：消息协议、同轮多个工具调用、工具参数验证、错误观察、预算与最小可替换模型适配器。运行 `python examples/ch04_agent_loop.py` 无须 API Key。

第 4 章真实模型演示（**可选**，需自行提供支持 Chat Completions Tool Calling 的账号和模型）：

```bash
export AGENT_API_KEY="your-api-key"
export AGENT_MODEL="your-supported-model"
# optional: export AGENT_BASE_URL="https://your-compatible-endpoint/v1"
python examples/ch04_live_agent.py
```

第 5 章新增 Task / Run / Step / Event 内存领域模型、合法状态转换、纯事件重放和并发版本检查。运行 `python examples/ch05_run_lifecycle.py` 即可观察状态投影、审批状态和陈旧写入被拒绝。**这个 Event Store 仅在当前进程内有效，不支持重启恢复。**

第 6 章实现同步 Model Gateway（原生 OpenAI Chat Completions、Anthropic Messages）、SSE 消息组装、Usage 统一、Retry/Fallback 和静态模型路由。运行 `python examples/ch06_gateway_demo.py` 可离线验证；[可选真实模型演示](examples/ch06_live_gateway.py) 需自行提供模型凭证。

代码仅提供分章节的教学持久化、超时和结果验证示例，尚未实现生产级 Sandbox 与分布式 Durable Runtime，**不应作为生产环境的通用 Agent 执行不可信指令**。

第 7 章实现 [AsyncToolRuntime](src/mini_agent/tool_runtime.py)：JSON Schema Draft 2020-12、按任务权限过滤、调用级审批、异步并发、错误反馈和输出校验。MCP SDK v2 适配见 [mcp_tools.py](src/mini_agent/mcp_tools.py)，可选本地 Client 示例在 [ch07_mcp_client_v2.py](examples/ch07_mcp_client_v2.py)。运行 `python examples/ch07_tool_runtime.py` 无需凭证；默认工具不具有 Sandbox 隔离。

第 8 章新增 [Context Manager](src/mini_agent/context_engineering.py)：预算估算、保留完整 Tool Exchange、可信证据优先、工具结果裁剪和内存归档。配套 [离线实验](examples/ch08_context_lab.py) 和 [测试](tests/test_ch08.py) 不使用真实模型，也不把 Proxy Token 计数当作服务商实际 Token Usage。

## 第 15 章：真实文件修复闭环

[章节](book/chapters/15.md) · [Coding Agent](src/mini_agent/coding_agent.py) · [可信 Fixture](src/mini_agent/coding_fixture.py) · [实验](examples/ch15_coding_lab.py) · [24 项测试](tests/test_ch15.py)

```bash
python examples/ch15_coding_lab.py
# 可选：只创建新 Diff 文件，不覆盖已存在文件，不改写源目录。
python examples/ch15_coding_lab.py --patch-output proposed.patch
```

实验真实执行 Inspect → Edit → Test → Verify：基线 4 个测试中有 3 个失败；首个错误候选被丢弃；第二个候选通过回归及独立验收，导出 Unified Diff。源目录保持不变。

**Planner 默认为规则实现，不是真实 LLM 自主修复实验。** `ModelPatchPlanner` 兼容第 6 章 Model 接口，但当前测试 Runner 只能执行可信教学代码；未知仓库和模型生成代码必须交给真正隔离的 Worker。临时目录、`shell=False`、`-I` 和超时都不是安全沙箱。

## 第 16 章：真实浏览器的观察、审批与验收

[章节](book/chapters/16.md) · [控制器](src/mini_agent/browser_agent.py) · [Playwright 适配器](src/mini_agent/browser_playwright.py) · [本机站点](src/mini_agent/browser_fixture.py) · [实验](examples/ch16_browser_lab.py)

```bash
python -m pip install -e '.[browser]'
python -m playwright install chromium
python examples/ch16_browser_lab.py
RUN_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_ch16_browser.py -v
```

在新 Chromium 会话中真实操作本机网页：搜索、打开文档、填写草稿、等待宿主批准、保存并检查服务端记录。目标是 **5 个浏览器动作、恰好一条正确草稿、零发布**。页面显示成功但没有后端记录时应验收失败。

新增 [18 项离线契约测试](tests/test_ch16.py) 与 [8 项真实浏览器测试](tests/test_ch16_browser.py)。默认测试会跳过浏览器集成；独立 CI Job 安装 Chromium 并显式运行，缺失浏览器不是成功。

**规则策略不是通用视觉 Agent。** 不使用用户浏览器或登录态；不接入付费 LLM。DOM/ARIA 快照、HTTP 路由与审批哈希均不构成生产安全沙箱、认证服务或原子页面事务。

## 第 17 章：Multi-Agent Systems 实验

[章节正文](book/chapters/17.md) · [Supervisor / Blackboard](src/mini_agent/multi_agent.py) · [可信 Fixture](src/mini_agent/multi_agent_fixture.py) · [Model/Gateway Worker Adapter](src/mini_agent/multi_agent_model.py) · [单/多 Agent 对照实验](examples/ch17_multi_agent_lab.py) · [回归测试](tests/test_ch17.py)

```bash
python examples/ch17_multi_agent_lab.py
python -m unittest discover -s tests -p 'test_ch17.py' -v
```

演示在**同一合成问题与 Host Verifier** 下比较一个 Generalist Worker 与三个专业 Worker：前者串行完成三个委派，后者两个独立 I/O 调用并行执行，最终结果相同。另演示同一 Wave 对同一个 Shared Key 的竞争写入被整体拒绝，并由可信 Host 通过版本检查执行显式裁决。

**工程边界**：默认 Worker 是确定性 Python 替身，不是真实 LLM 推理评测。模型适配仅使用 Fake Model 验证输出 JSON 与 Tool 权限；Worker 同进程执行、Blackboard 仅在内存中，未经安全审查不能用来运行任意不可信代码，真实 Agent 必须使用第 18–19 章的隔离和授权机制。

## 第 18 章：Sandbox / Process / MicroVM 的隔离边界

[第 18 章教材正文](book/chapters/18.md) · [进程资源限制器](src/mini_agent/process_executor.py) · [容量与租约模型](src/mini_agent/sandbox_reservations.py) · [系统只读诊断](src/mini_agent/sandbox_diagnostics.py) · [生产隔离验收清单](docs/ch18_sandbox_review.md) · [测试](tests/test_ch18.py)

本章覆盖 Linux Namespace、cgroup v2、Rootless OCI、gVisor、Firecracker、Snapshot、Warm Pool、资源 Admission 和可靠销毁。附带可运行的 **Trusted Process Executor**，只对 Host 批准的 Python Fixture 进行 Resource Limit、Deadline、Output Cap 和 Scratch Cleanup 测试；它仍和宿主共享 Kernel、UID、Mount/Network，并不是防御恶意代码的 Sandbox。

```bash
python examples/ch18_sandbox_lab.py
python -m unittest discover -s tests -p 'test_ch18.py' -v
```

**重要限制**：未知/模型生成代码必须部署到额外配置和审计过的容器、gVisor 或 MicroVM 中执行。代码提供的资源预留模拟器不会真实创建、销毁或隔离 VM；TTL 过期时不会假装已经释放仍在使用的容量。

## 第 19 章：Agent Security

[完整章节](book/chapters/19.md) · [Security Reference Monitor](src/mini_agent/security_monitor.py) · [离线攻防实验](examples/ch19_security_lab.py) · [33 项安全测试](tests/test_ch19.py) · [上线安全审查清单](docs/ch19_security_review.md)

本章以 **Model Proposal ≠ Host Authorization** 为核心，演示 Principal/Run/Resource 白名单、JSON Schema、固定 HTTPS Endpoint、短期 HMAC 审批、参数摘要、单进程防重放、执行预算、合成敏感输出检查和只记录元数据的审计。

```bash
python examples/ch19_security_lab.py
python -m unittest discover -s tests -p 'test_ch19.py' -v
```

**重要限制**：没有访问真实网络或系统资源；攻击与密钥全是合成数据。Python Reference Monitor 不是 OS Sandbox、IAM、KMS、网络 Egress Proxy、完整 DLP 或 Prompt Injection 免疫证明。未知工具与不可信执行进程必须放到独立安全边界中处理。

## 第 20 章：Agent Runtime 调度与并发

[第 20 章完整正文](book/chapters/20.md) · [SQLite Scheduler](src/mini_agent/runtime_scheduler.py) · [离线演示](examples/ch20_scheduler_lab.py) · [33 项测试](tests/test_ch20.py) · [生产化验收清单](docs/ch20_scheduler_review.md)

本章实现**真正的 SQLite 事务与多个连接下的原子 Claim**、Tenant 轮转公平调度、Priority、全局/租户 Backpressure、CPU/Memory/Disk Admission、Worker Lease/Heartbeat、Generation Fencing、指数退避、运行中 Cancel 以及 Host 认可的清理回执逻辑。对于结果未知的非幂等操作，在可信 Host 核对外部结果之前**不自动重试**；任务是否 `verified` 由 Host-owned Verifier 判断，而不是 Worker 自报。

```bash
python examples/ch20_scheduler_lab.py
python -m unittest discover -s tests -p 'test_ch20.py' -v
```

**边界**：Worker 执行的是可信离线 Async Handler，没有跨机器 IAM、真实 Sandbox 进程销毁、外部邮件/付款服务或自动重试时钟服务。`cleanup_confirmed=True` 和 `reconcile(evidence=...)` 都是受信任 Host 教学 API，不能直接对不可信客户端开放。SQLite 演示并不等于生产级分布式 Exactly-Once Runtime。

## 第 21 章：Agent Reliability 与故障恢复

[第 21 章正文](book/chapters/21.md) · [Transactional Outbox 源码](src/mini_agent/reliability_outbox.py) · [Circuit Breaker / Saga](src/mini_agent/resilience_patterns.py) · [模拟外部 Provider](src/mini_agent/reliability_fixture.py) · [离线故障实验](examples/ch21_reliability_lab.py) · [36 项测试](tests/test_ch21.py) · [生产可靠性审查清单](docs/ch21_reliability_review.md)

本章通过**一个 SQLite 事务**同时保存 Business Intent 与 Outbox，演示安全地处理两类不同的崩溃窗口：本地事务中途故障时整体 Rollback；远端已提交但 ACK 丢失时，将非幂等操作置为未决，先要求可信 Host 回收执行资源，然后依据外部事实核对决定是否结束或重试。对于经过审查的幂等操作，模拟 Provider 使用稳定 Request Key 去重。另实现有界重试的 Full Jitter、Circuit Breaker、不可自动执行的 Saga Compensation Plan。

```bash
python examples/ch21_reliability_lab.py
python -m unittest discover -s tests -p 'test_ch21.py' -v
```

**能力边界**：Synthetic Provider 的外部业务状态只在内存中模拟；没有真实邮件/支付、网络、Worker IAM、OS 沙箱或分布式 Exactly-Once。Host Reaper 确认仍是可信 Fixture，不能直接把 `actually_destroyed=True` 交给未经授权的模型或客户端。Code + Unit Tests 验证控制不变量，不代替生产 Provider 契约和跨系统故障演练。

## 第 22 章：Agent Observability 与 Debugging

[章节正文](book/chapters/22.md) · [Trace Recorder](src/mini_agent/observability.py) · [JSONL Trace Store & Playback](src/mini_agent/trace_store.py) · [Model/Tool Instrumentation Bridge](src/mini_agent/observability_bridge.py) · [离线实验](examples/ch22_observability_lab.py) · [39 项单元测试](tests/test_ch22.py) · [生产观测上线清单](docs/ch22_observability_review.md)

本章将第 4 章 Agent Kernel / Tool Registry 与第 6 章 Model Gateway 接到隐私优先 Trace Recorder，支持 W3C Version 00 Traceparent、\`asyncio\` ContextVar 父子关系、Model/Tool Span、安全 Error Code、Provider Usage、显式版本 Price Card 的 Decimal 费用估算、有界 JSONL Export、哈希链和只读 Playback。

默认**不记录 Prompt、Tool Arguments/Results、异常内容、身份或凭证**；未知 Tool Name 也归一化，避免模型借元数据泄漏信息。Token Usage 或价格缺失时保持 Unknown，不把未知当作零。实验采用可信 Fake Gateway，**未实现生产 OpenTelemetry SDK/OTLP、跨租户日志 IAM 或完整 DLP**；Hash Chain 不是数字签名或不可篡改审计。

~~~bash
python examples/ch22_observability_lab.py
python -m unittest discover -s tests -p 'test_ch22.py' -v
~~~

## 第 24 章：Agent 平台化与生产部署

[完整教材](book/chapters/24.md) · [配置预检 + Canary Release Ledger](src/mini_agent/platform_deployment.py) · [合成平台 Fixture](src/mini_agent/platform_fixture.py) · [可运行实验](examples/ch24_platform_lab.py) · [41 项单元测试](tests/test_ch24.py) · [生产上线审查清单](docs/ch24_platform_review.md)

本章提供**多租户 Agent Platform 的可信部署决策原型**：Control/Execution Plane 分离、不可变 Image Digest、ServiceAccount/Namespace、CPU/Memory 预算、默认拒绝网络、独立执行 Runtime，以及 Canary **质量/延迟/成本/安全**多指标发布门。`ReleaseLedger` 使用 SQLite 保存 Manifest/Policy 指纹、每阶段决策和 Generation CAS，拒绝不安全配置和过时控制器的状态更新。

```bash
python examples/ch24_platform_lab.py
python -m unittest discover -s tests -p 'test_ch24.py' -v
```

**工程边界**：此实现只生成 K8s 参考 JSON 并保存*期望发布决策*，不会调用集群 API、Argo Rollouts、Ingress，也不部署 gVisor/MicroVM；受信任观测指标均为合成数据。Pod 安全声明与 `runtimeClassName` 字符串不能证明真实隔离。实际生产必须增加 IAM、Admission、网络策略执行、真正发布/回滚回执、持续监控及外部副作用核对。

## 第 25 章：Agent Evaluation 基础

[完整正文](book/chapters/25.md) · [Agent Evaluation Harness](src/mini_agent/agent_evaluation.py) · [实际 AgentKernel + Fake Model 适配](src/mini_agent/eval_fixture.py) · [六条合成任务数据集](evals/ch25_offline_suite.json) · [离线实验](examples/ch25_eval_lab.py) · [40 项单元测试](tests/test_ch25.py) · [生产评测验收清单](docs/ch25_eval_review.md)

本章建立**版本化 Dataset → 公开 Task → 受控工具环境 → 实际 Tool Effect → 独立 Outcome/Safety Grader → 统计报告**的完整教学流程。最终通过状态不依赖模型自报 `completed` 或 LLM-as-a-Judge 分数。框架可计算 Observed `pass@k`、`pass^k`、按不同 Case 首次 Trial 的 Wilson 区间和成对版本改进/退化，并在 Provider Usage/RateCard 缺失时将费用保持 Unknown。

```bash
python examples/ch25_eval_lab.py
python -m unittest discover -s tests -p 'test_ch25.py' -v
```

**能力边界**：真正调用本书 AgentKernel，但规则 Fake Model、Toy Tool Environment、价格与时间均为合成数据；Dataset 仅有六条公开 Case，Heldout 标签不能代表真实保密基准。接口级隔离隐藏目标状态**不是安全 Sandbox**。本章没有测试任何真实语言模型的能力或安全性，不应把模拟成功率当作现实表现。

## 第 26 章：Agent Benchmark 体系

[教材正文](book/chapters/26.md) · [Benchmark Framework](src/mini_agent/benchmark_framework.py) · [离线实验](examples/ch26_benchmark_lab.py) · [15 项测试](tests/test_ch26.py) · [生产审查清单](docs/ch26_benchmark_review.md)

本章提供 Versioned Benchmark Protocol、多领域切片、Micro/Macro Success、Failure Taxonomy、结果复现检查与安全/回归阻断。复用第 25 章的六条合成任务和 AgentKernel/Fake Model，**不运行真实 SWE-bench、WebArena、OSWorld、GAIA 或 BrowseComp**。

```bash
python examples/ch26_benchmark_lab.py
python -m unittest discover -s tests -p 'test_ch26.py' -v
```

## 第 27 章：从零实现 Agent Evaluation Framework

[完整正文](book/chapters/27.md) · [CampaignStore / Dashboard](src/mini_agent/eval_campaign.py) · [离线实验](examples/ch27_evaluation_framework_lab.py) · [14 项测试](tests/test_ch27.py)

本章在第 25–26 章 Harness/Benchmark 之上增加有版本的 CampaignSpec、可信评测结果聚合、SQLite 事务与 Revision CAS、配对改进/退化统计以及**不含隐藏答案、Prompt/Tool 参数的静态 HTML Dashboard**。评测仍使用真实 AgentKernel + Fake Model + Toy Environment 的合成任务，不能由结果推断真实模型表现。

```bash
python examples/ch27_evaluation_framework_lab.py
python -m unittest discover -s tests -p 'test_ch27.py' -v
```

**边界**：Campaign `approve` 是评测决策，不是生产 Release 执行回执；没有身份服务、真实联网工具、部署操作或 OS Sandbox。真实 Benchmark 要把可信 Grader 与不可信 Agent Worker 隔离，并通过认证的发布控制面消费结果。

## 第 28 章：Evaluation-Driven Development

[完整教材](book/chapters/28.md) · [EDD Decision Gate](src/mini_agent/evaluation_driven_development.py) · [离线实验](examples/ch28_edd_lab.py) · [12 项测试](tests/test_ch28.py)

以评测驱动持续优化：归因失败、登记可证伪的改进假设、进行固定 Benchmark 的 Baseline/Candidate/Ablation 对照，并在 Outcome、Tool Policy、成本和回归门槛满足时建议 `adopt`；条件不足返回 `inconclusive`，退化时 `reject`。安全结果来自可信环境而非模型自报，离线建议不直接部署生产环境。

```bash
python examples/ch28_edd_lab.py
python -m unittest discover -s tests -p 'test_ch28.py' -v
```

**边界**：全部基于六条合成公开任务和 Fake Model，不具备真实模型统计显著性、真正的在线 A/B 或外部 Sandbox 授权。

## 第 29 章：Inference-Time Scaling 与搜索算法

[完整教材](book/chapters/29.md) · [Best-of-N / Self-Consistency / Beam / MCTS Runtime](src/mini_agent/inference_search.py) · [离线实验](examples/ch29_search_lab.py) · [26 项单元测试](tests/test_ch29.py) · [搜索安全/上线检查表](docs/ch29_search_review.md)

本章从状态空间与动作预算出发，使用完全**离线且可验证的算术程序合成任务**比较四种推理期搜索策略，并将不可信 Proposal、受信任环境执行和独立 Host Verifier 分开。每个任务分别限制 Proposal、Tool/State Transition、Verifier 和 Candidate 次数；错误或非法动作无法绕过可信 Tool Allowlist，也会计入尝试成本。

```bash
python examples/ch29_search_lab.py
python -m unittest discover -s tests -p 'test_ch29.py' -v
```

**教学边界**：ToyProposer 不是训练过的 LLM，不生成或获取模型隐藏 Chain-of-Thought；搜出的程序只作用于受控整数状态。Synthetic Cost 只是人工设置的计算单价，不能用于真实模型价格/能力排名。真实 Coding/Browser/Search 需要隔离 Sandbox、独立工具权限、实际 Provider Usage 与外部业务副作用核对。

## 第 30 章：Verifier 与 Feedback Learning

[完整教材](book/chapters/30.md) · [Verifier / Feedback Learner 源码](src/mini_agent/feedback_learning.py) · [12 条合成数据](evals/ch30_feedback_suite.json) · [离线实验](examples/ch30_feedback_lab.py) · [38 项单元测试](tests/test_ch30.py) · [安全与研究审查清单](docs/ch30_feedback_review.md)

本章系统区分 **Process Verification 与 Outcome Verification**、确定性 Tool/Policy 与预测性 Reward Model。它通过故意偏置的“路径越长分越高”奖励揭示 Reward Hacking，并实现 TP/FP/TN/FN、Brier、ECE 校准；随后从 Train Split 生成经 Host 独立验证的反馈，用 Beta(1,1) 平滑训练一个**表格式算术动作策略**，在新 Initial State 的 Heldout Task 上评估效果。

```bash
python examples/ch30_feedback_lab.py
python -m unittest discover -s tests -p 'test_ch30.py' -v
```

**关键边界**：Toy Verifier 和 Tabular Feedback Policy 都只处理纯合成算术任务；训练集 6 条、Heldout 6 条，不能由其结果推断真实 LLM 推理/Agent 安全能力。没有 Transformer 权重、GPU、RL、生产 OS Sandbox、真实业务 Tool 和线上部署。`source` 文本标签不提供真实反馈来源认证，正式使用仍需 IAM、可信外部回执与独立 Eval。

## 第 31 章：Agentic Reinforcement Learning

[完整教材](book/chapters/31.md) · [策略梯度 / PPO / GRPO 教学源码](src/mini_agent/agentic_rl.py) · [独立 Host Verifier + 离线实验](examples/ch31_agentic_rl_lab.py) · [契约及数值回归测试](tests/test_ch31.py) · [可信训练/上线审查清单](docs/ch31_rl_review.md)

本章承接第 29 章 Inference-Time Search 和第 30 章 Feedback Learning：在**可信、可重放的多轮算术工具环境**中，真正采样轨迹、从独立 Host Verifier 收集终局回报，并更新随机表格式 Softmax 策略；从零对照 REINFORCE、PPO-style Clipped Policy Gradient 和 GRPO-style Group Advantages，单独处理零方差、正/负优势裁剪及可选的精确类别分布 KL。

~~~bash
python examples/ch31_agentic_rl_lab.py
python -m unittest discover -s tests -p 'test_ch31.py' -v
~~~

**关键边界**：这是离线**表格式 Agent Policy** 的真实参数训练，**不是** Transformer/LLM Fine-tuning、完整 PPO/GRPO、真实浏览器/代码工具的 RL、OS Sandbox 或模型能力排名。固定任务的精确成功概率、不同随机种子的成功次数不能推断未知 Goal 泛化；训练与发布必须继续遵守独立评测和 Host 授权。

## 第 32 章：Self-Improving Agent

[完整教材正文](book/chapters/32.md) · [可信反馈驱动的配置优化源码](src/mini_agent/self_improvement.py) · [离线改进闭环与回滚实验](examples/ch32_self_improvement_lab.py) · [测试](tests/test_ch32.py) · [上线与研究审查清单](docs/ch32_self_improvement_review.md)

本章在第 28 章 Evaluation-Driven Development 和第 29–31 章 Search/Verifier/RL 之上，建立一个**有界、可审计的 Agent 系统级自改进闭环**。它区分 Self-Refine、Reflexion、Prompt/Memory/Workflow Optimization、Policy RL 与 Open-ended Self-Modification，调研 MIPRO、GEPA、AFlow、SICA、Darwin Gödel Machine 和 AlphaEvolve 的原始资料。实现基于合成 Host 工具事实提取 Train Failure Codes、生成逐维度 Typed Proposals、在 Dev 上以成对质量/成本/安全门槛选择候选，搜索后只调用一次合成 Audit；实际修改内存激活配置需额外的可信调用方批准与 Revision CAS，提供回滚记录。

~~~bash
python examples/ch32_self_improvement_lab.py
python -m unittest discover -s tests -p 'test_ch32.py' -v
~~~

**边界**：默认 Agent 是可信的确定性 Python 策略；只优化固定允许的配置枚举，**不使用 LLM、不训练模型、不执行不可信代码或真实 Tool Side Effects，也不部署生产**。合成 Audit 数据在公开仓库中，并非真正的保密 Benchmark；in-memory Receipt、approved flag 和 CAS 不是生产 IAM/交易保障。单次小样本 Dev/Audit 成功仅说明该 Toy 任务内的行为变化，不能推断真实 Agent 泛化或自主递归进化。

## 第 33 章：构建完整的 Mini-Agent Runtime

[完整正文](book/chapters/33.md) · [集成 Runtime 源码](src/mini_agent/integrated_runtime.py) · [SQLite/Tracing/Memory/Verifier 端到端实验](examples/ch33_runtime_lab.py) · [测试](tests/test_ch33.py) · [安全、Durability、部署审查清单](docs/ch33_runtime_review.md)

本章真正复用并运行 **Ch04 AgentKernel → Ch06 ModelGateway → Ch08 ContextManagedModel → Ch09 Scoped Memory → Ch04 Tool Registry → Ch22 Privacy-first Tracing**，在外面补上 Host-owned 独立 Outcome 验收和 **SQLite 事务性 Run Status Journal**。它在模型与工具执行前持久化 Running，执行结束后通过 Revision CAS 写入 Verified/Rejected/Halted 等状态；新的 SQLite Connection 可以重新读取运行状态，重复 Run ID 被拒绝，模拟崩溃结果必须人工核对，**不自动重试**。所有 Tool 和 Memory 仅使用可信的合成只读 Fixture。

~~~bash
python examples/ch33_runtime_lab.py
python -m unittest discover -s tests -p 'test_ch33.py' -v
~~~

**边界**：默认 Model 是确定性离线 Fixture，不代表 LLM 推理能力；当前仍为可信单进程同步 Python，**没有**生产 IAM、HTTP API、真正的 OS/MicroVM Sandbox、分布式 Worker、Activity-level Durable Replay、外部业务副作用或 Exactly-Once。SQLite 只存安全状态元数据（不存 Prompt/Tool Args/Result），不是可以自动恢复任意 Tool Activity 的 Event History；Provider Usage/Cost 未观察到就保持 Unknown。

## 项目结构

```text
book/                 # 7 篇、34 章教材导航与章节写作骨架
docs/                 # 架构说明、资料索引与贡献指南
src/mini_agent/       # 最小可运行 Agent 核心、工具系统与模型适配
examples/             # 离线示例、真实模型示例
evals/                # 版本化合成 Agent Evaluation 数据集
tests/                # 标准库 unittest
.github/workflows/    # Python CI
```

## 分阶段实现

- **v0.1（已实现）**：最小 Agent Loop、工具注册、离线示例、基础测试。
- **v0.2（教学原型持续迭代）**：第 4 章完成结构化消息、工具调用和预算；第 6 章增加原生 Chat Completions / Anthropic Messages 适配、SSE 流式组装和静态路由。复杂工具协议留到第 7 章。
- **v0.3（规划）**：Context、Memory、Planning、Workflow、Checkpoint。
- **v0.4（规划）**：Research、Coding、Browser 和 Multi-Agent。
- **v0.5（规划）**：Sandbox、安全、并发、分布式执行、Tracing。
- **v0.6（规划）**：Benchmark、Evaluation 和回归测试。
- **v1.0（规划）**：高级推理、Verifier、Coding Agent 综合实践。

## 关于内容与许可

本仓库公开供阅读与交流。**公开仓库并不自动授予使用、修改或再分发许可**。教材版权与代码许可证尚未选定；发布正式许可文件前请勿假定项目已采用 MIT 或 Apache-2.0 许可。

课程资料与论文归原作者所有。这里提供原创讲解、实验和引用索引，不镜像转载课程视频或论文全文。

### 第 25–27 章校准

共享评测协议与候选专属 `CandidateConfig` 已拆分，支持对比不同模型/Prompt；`TrialContext` 提供可复现的任务/试验 Seed，兼容不支持 Seed 的旧 Runner；`CampaignStore` 现支持 Trial 级 SQLite Checkpoint、断点续跑及数据集指纹校验。参阅 [第 26 章](book/chapters/26.md)、[第 27 章](book/chapters/27.md) 和 [校准回归测试](tests/test_ch25_27_calibration.py)。
