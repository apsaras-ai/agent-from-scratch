# 从零构建 AI Agent：原理、架构与生产实践

**Agent Development: From First Principles to Production-Grade Autonomous Systems**

一本面向软件工程师与研究生的中文 Agent 开发教程：不从框架 API 入手，而是从决策模型、Agent Loop、工具协议与状态机出发，逐步实现 Coding Agent、生产级 Agent Runtime、评测系统和自我改进机制。

> 📘 **状态：正式写作中。** 第 1–8 章已形成正文初稿与配套 Python 实验，仍待技术审稿；第 9–34 章目前为写作骨架。未完成的章节不代表已出版内容。

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
| 第二篇 | 从零实现 Agent 核心架构 | 5–12 |
| 第三篇 | 真实场景：Research、Coding、Browser、Multi-Agent | 13–17 |
| 第四篇 | 生产级 Runtime 与 Infrastructure | 18–24 |
| 第五篇 | Evaluation、Benchmark 与优化 | 25–28 |
| 第六篇 | 高级推理、Agentic RL 与自我改进 | 29–32 |
| 第七篇 | 端到端的生产级 Coding Agent | 33–34 |

➡️ [第 1 章正文](book/chapters/01.md) · [第 2 章正文](book/chapters/02.md) · [第 3 章正文](book/chapters/03.md) · [第 4 章正文](book/chapters/04.md) · [第 5 章正文](book/chapters/05.md) · [第 6 章正文](book/chapters/06.md) · [第 7 章正文](book/chapters/07.md) · [第 8 章正文](book/chapters/08.md) · [完整 34 章目录](book/README.md) · [核心架构](docs/architecture.md) · [大学公开课与阅读资料](docs/references.md)

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

代码尚未实现 Sandbox、Durable Execution、真实墙钟超时或独立结果验证，**不应作为生产环境的通用 Agent 执行不可信指令**。

第 7 章实现 [AsyncToolRuntime](src/mini_agent/tool_runtime.py)：JSON Schema Draft 2020-12、按任务权限过滤、调用级审批、异步并发、错误反馈和输出校验。MCP SDK v2 适配见 [mcp_tools.py](src/mini_agent/mcp_tools.py)，可选本地 Client 示例在 [ch07_mcp_client_v2.py](examples/ch07_mcp_client_v2.py)。运行 `python examples/ch07_tool_runtime.py` 无需凭证；默认工具不具有 Sandbox 隔离。

第 8 章新增 [Context Manager](src/mini_agent/context_engineering.py)：预算估算、保留完整 Tool Exchange、可信证据优先、工具结果裁剪和内存归档。配套 [离线实验](examples/ch08_context_lab.py) 和 [测试](tests/test_ch08.py) 不使用真实模型，也不把 Proxy Token 计数当作服务商实际 Token Usage。

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
