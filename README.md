# 从零构建 AI Agent：原理、架构与生产实践

**Agent Development: From First Principles to Production-Grade Autonomous Systems**

一本面向软件工程师与研究生的中文 Agent 开发教程：不从框架 API 入手，而是从决策模型、Agent Loop、工具协议与状态机出发，逐步实现 Coding Agent、生产级 Agent Runtime、评测系统和自我改进机制。

> 📘 **状态：正式写作中。** 第 1–22 章已形成正文初稿与配套 Python 实验，仍待技术审稿；第 23–34 章目前为写作骨架。未完成的章节不代表已出版内容。

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

➡️ [第 1 章正文](book/chapters/01.md) · [第 2 章正文](book/chapters/02.md) · [第 3 章正文](book/chapters/03.md) · [第 4 章正文](book/chapters/04.md) · [第 5 章正文](book/chapters/05.md) · [第 6 章正文](book/chapters/06.md) · [第 7 章正文](book/chapters/07.md) · [第 8 章正文](book/chapters/08.md) · [第 9 章正文](book/chapters/09.md) · [第 10 章正文](book/chapters/10.md) · [第 11 章正文](book/chapters/11.md) · [第 12 章正文](book/chapters/12.md) · [第 13 章正文](book/chapters/13.md) · [第 14 章正文](book/chapters/14.md) · [第 15 章正文](book/chapters/15.md) · [第 16 章正文](book/chapters/16.md) · [第 17 章正文](book/chapters/17.md) · [第 18 章正文](book/chapters/18.md) · [第 19 章正文](book/chapters/19.md) · [第 20 章正文](book/chapters/20.md) · [第 21 章正文](book/chapters/21.md) · [第 22 章正文](book/chapters/22.md) · [完整 34 章目录](book/README.md) · [核心架构](docs/architecture.md) · [大学公开课与阅读资料](docs/references.md)

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

## 项目结构

```text
book/                 # 7 篇、34 章教材导航与章节写作骨架
docs/                 # 架构说明、资料索引与贡献指南
src/mini_agent/       # 最小可运行 Agent 核心、工具系统与模型适配
examples/             # 离线示例、真实模型示例
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
