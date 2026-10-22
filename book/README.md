# 教材目录

> **进度**：第 1–12 章已有正文初稿、参考文献、离线实验和单元测试；第 13–34 章仍为写作骨架。每章将逐步加入原理、实现、实验、练习和参考资料。


## 第一篇：Agent 基础原理与系统模型

- [第 1 章：从大语言模型到智能 Agent](chapters/01.md) · ✅ 正文初稿、代码实验已提交
- [第 2 章：LLM 如何驱动 Agent](chapters/02.md) · ✅ 正文初稿、代码实验已提交
- [第 3 章：Agent 的数学模型与决策原理](chapters/03.md) · ✅ 正文初稿、代码实验已提交
- [第 4 章：从零实现第一个 Agent](chapters/04.md) · ✅ 正文初稿、离线 Agent Kernel 与真实 LLM 接入示例

## 第二篇：从零实现 Agent 核心架构

- [第 5 章：Agent 的软件架构设计](chapters/05.md) · ✅ 正文初稿、Run 状态机及 Event Store 实验
- [第 6 章：Model Gateway 与模型适配](chapters/06.md) · ✅ 正文初稿、双 Provider Gateway 和 Streaming 实验
- [第 7 章：Tool Use 与工具系统](chapters/07.md) · ✅ 正文初稿、Async Tool Runtime 和 MCP 适配实验
- [第 8 章：Context Engineering](chapters/08.md) · ✅ 正文初稿、Context Manager、长度/证据保留实验
- [第 9 章：Memory 与知识检索](chapters/09.md) · ✅ 正文初稿、Memory Service 实验
- [第 10 章：Reasoning、Planning 与 Verification](chapters/10.md) · ✅ 正文初稿、DAG Planning / Replan / Verifier
- [第 11 章：Workflow 与 Agent Orchestration](chapters/11.md) · ✅ 正文初稿、DAG / Approval / Parallel 实验
- [第 12 章：State Persistence 与 Durable Execution](chapters/12.md) · ✅ 正文初稿、SQLite 恢复实验

## 第三篇：构建真实场景的智能 Agent

- [第 13 章：Research Agent](chapters/13.md)
- [第 14 章：Coding Agent 的代码仓库理解能力](chapters/14.md)
- [第 15 章：构建自主 Coding Agent](chapters/15.md)
- [第 16 章：Browser Agent 与 Computer Use](chapters/16.md)
- [第 17 章：Multi-Agent Systems](chapters/17.md)

## 第四篇：生产级 Agent Runtime 与 Infrastructure

- [第 18 章：Agent Sandbox 与执行环境](chapters/18.md)
- [第 19 章：Agent Security](chapters/19.md)
- [第 20 章：Agent Runtime 调度与并发](chapters/20.md)
- [第 21 章：Agent Reliability 与故障恢复](chapters/21.md)
- [第 22 章：Agent Observability 与 Debugging](chapters/22.md)
- [第 23 章：Agent Performance 与 Cost Optimization](chapters/23.md)
- [第 24 章：Agent 平台化与生产部署](chapters/24.md)

## 第五篇：Agent Evaluation、Benchmark 与系统优化

- [第 25 章：Agent Evaluation 基础](chapters/25.md)
- [第 26 章：Agent Benchmark 体系](chapters/26.md)
- [第 27 章：从零实现 Agent Evaluation Framework](chapters/27.md)
- [第 28 章：Evaluation-Driven Development](chapters/28.md)

## 第六篇：高级推理、Agentic RL 与自我改进

- [第 29 章：Inference-Time Scaling 与搜索算法](chapters/29.md)
- [第 30 章：Verifier 与 Feedback Learning](chapters/30.md)
- [第 31 章：Agentic Reinforcement Learning](chapters/31.md)
- [第 32 章：Self-Improving Agent](chapters/32.md)

## 第七篇：完整实战：生产级 Coding Agent

- [第 33 章：构建完整的 Mini-Agent Runtime](chapters/33.md)
- [第 34 章：生产级 Coding Agent 端到端实现](chapters/34.md)

## 可运行实验

- 第 1 章：[Workflow 与反馈驱动的 Policy Loop](../examples/ch01_workflow_vs_agent.py)，配套 [单元测试](../tests/test_ch01.py)
- 第 2 章：[模型协议与参数验证](../examples/ch02_model_protocol.py)，配套 [单元测试](../tests/test_ch02.py)
- 第 3 章：[Value Iteration 与 POMDP 信息价值](../examples/ch03_decision_process.py)，配套 [单元测试](../tests/test_ch03.py)

- 第 4 章：[Agent Loop 离线实验](../examples/ch04_agent_loop.py) · [可选真实模型示例](../examples/ch04_live_agent.py) · [单元测试](../tests/test_ch04.py)
- 第 5 章：[Task/Run/Step/Event 生命周期实验](../examples/ch05_run_lifecycle.py) · [纯领域模型](../src/mini_agent/run_domain.py) · [单元测试](../tests/test_ch05.py)

- 第 6 章：[双协议和流式网关实验](../examples/ch06_gateway_demo.py) · [可选真实模型示例](../examples/ch06_live_gateway.py) · [单元测试](../tests/test_ch06.py)

- 第 7 章：[异步工具与 Mock MCP 实验](../examples/ch07_tool_runtime.py) · [可选 MCP SDK v2 Client](../examples/ch07_mcp_client_v2.py) · [单元测试](../tests/test_ch07.py)

- 第 8 章：[上下文筛选与归档实验](../examples/ch08_context_lab.py) · [Context Manager 源码](../src/mini_agent/context_engineering.py) · [单元测试](../tests/test_ch08.py)

- 第 9 章：[Memory 检索实验](../examples/ch09_memory_lab.py) · [源码](../src/mini_agent/memory_service.py) · [测试](../tests/test_ch09.py)

- 第 10 章：[离线重规划实验](../examples/ch10_planning_lab.py) · [源码](../src/mini_agent/planning.py) · [单元测试](../tests/test_ch10.py)

- 第 11 章：[Workflow 实验](../examples/ch11_workflow_lab.py) · [源码](../src/mini_agent/workflow.py) · [测试](../tests/test_ch11.py)

- 第 12 章：[SQLite 恢复实验](../examples/ch12_durable_lab.py) · [源码](../src/mini_agent/durable_execution.py) · [测试](../tests/test_ch12.py)
