# 教材目录

> **进度**：第 1–32 章已有正文初稿、参考文献、实验和单元测试；第 33–34 章仍为写作骨架。每章将逐步加入原理、实现、实验、练习和参考资料。


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

- [第 13 章：Research Agent](chapters/13.md) · ✅ 正文初稿、证据来源与引用实验
- [第 14 章：Coding Agent 的代码仓库理解能力](chapters/14.md) · ✅ 正文初稿、Python AST Repository Map 实验
- [第 15 章：构建自主 Coding Agent](chapters/15.md) · ✅ 正文初稿、真实文件修复闭环、回滚与独立验收
- [第 16 章：Browser Agent 与 Computer Use](chapters/16.md) · ✅ 正文初稿、真实 Chromium、操作审批与后端验收
- [第 17 章：Multi-Agent Systems](chapters/17.md) · ✅ Supervisor、最小权限、共享状态 CAS、冲突裁决及单/多执行者实验

## 第四篇：生产级 Agent Runtime 与 Infrastructure

- [第 18 章：Agent Sandbox 与执行环境](chapters/18.md) · ✅ 正文初稿、可信进程限制器、资源租约模型及系统隔离架构
- [第 19 章：Agent Security](chapters/19.md) · ✅ 正文初稿、Reference Monitor、短期签名审批和安全负面测试
- [第 20 章：Agent Runtime 调度与并发](chapters/20.md) · ✅ SQLite Queue、Lease/Fencing、Tenant Fairness、Retry 和 Reaper 状态机
- [第 21 章：Agent Reliability 与故障恢复](chapters/21.md) · ✅ Transactional Outbox、未知外部效果核对、Circuit Breaker、Saga 与故障注入
- [第 22 章：Agent Observability 与 Debugging](chapters/22.md) · ✅ Trace、Model/Tool Span、Token/费用、隐私过滤与只读回放
- [第 23 章：Agent Performance 与 Cost Optimization](chapters/23.md) · ✅ Token/RateCard、关键路径、缓存 Scope 和成本质量门
- [第 24 章：Agent 平台化与生产部署](chapters/24.md) · ✅ 多租户配置、K8s 参考清单、Canary Gate、CAS 发布账本及 41 项测试

## 第五篇：Agent Evaluation、Benchmark 与系统优化

- [第 25 章：Agent Evaluation 基础](chapters/25.md) · ✅ Outcome/Policy Graders、版本化 Dataset、AgentKernel 集成、pass@k/pass^k 和统计实验
- [第 26 章：Agent Benchmark 体系](chapters/26.md) · ✅ 版本化多领域 Benchmark、结果复现、回归与安全 Gate
- [第 27 章：从零实现 Agent Evaluation Framework](chapters/27.md) · ✅ SQLite Campaign、逐 Trial Checkpoint/恢复、配对回归、静态 Dashboard 和测试
- [第 28 章：Evaluation-Driven Development](chapters/28.md) · ✅ 失败归因、预注册优化假设、A/B + 消融、质量/安全/成本回归 Gate

## 第六篇：高级推理、Agentic RL 与自我改进

- [第 29 章：Inference-Time Scaling 与搜索算法](chapters/29.md) · ✅ Best-of-N、Self-Consistency、Beam Search、MCTS/UCT、硬预算和独立验证器
- [第 30 章：Verifier 与 Feedback Learning](chapters/30.md) · ✅ Outcome/Process Verifier、Reward Hacking、Brier/ECE、可信反馈与 Train/Heldout 学习
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

- 第 13 章：[离线研究实验](../examples/ch13_research_lab.py) · [源码](../src/mini_agent/research_agent.py) · [测试](../tests/test_ch13.py)

- 第 14 章：[仓库索引实验](../examples/ch14_repository_map.py) · [源码](../src/mini_agent/repository_index.py) · [测试](../tests/test_ch14.py)

- 第 15 章：[代码修复闭环](../examples/ch15_coding_lab.py) · [控制器](../src/mini_agent/coding_agent.py) · [可信 Fixture](../src/mini_agent/coding_fixture.py) · [24 项测试](../tests/test_ch15.py)

- 第 16 章：[浏览器闭环实验](../examples/ch16_browser_lab.py) · [控制器](../src/mini_agent/browser_agent.py) · [Playwright](../src/mini_agent/browser_playwright.py) · [18 项离线测试](../tests/test_ch16.py) · [8 项浏览器集成测试](../tests/test_ch16_browser.py)

- 第 17 章：[单/多执行者对照与冲突实验](../examples/ch17_multi_agent_lab.py) · [Supervisor/Blackboard](../src/mini_agent/multi_agent.py) · [Model Worker](../src/mini_agent/multi_agent_model.py) · [单元测试](../tests/test_ch17.py)

- 第 18 章：[受限进程与生命周期实验](../examples/ch18_sandbox_lab.py) · [Process Executor](../src/mini_agent/process_executor.py) · [Admission/Lease](../src/mini_agent/sandbox_reservations.py) · [生产安全验收清单](../docs/ch18_sandbox_review.md) · [单元测试](../tests/test_ch18.py)

- 第 19 章：[Agent Security 离线攻防](../examples/ch19_security_lab.py) · [Reference Monitor](../src/mini_agent/security_monitor.py) · [安全测试](../tests/test_ch19.py) · [生产安全审查清单](../docs/ch19_security_review.md)

- 第 20 章：[调度离线实验](../examples/ch20_scheduler_lab.py) · [SQLite Scheduler](../src/mini_agent/runtime_scheduler.py) · [33 项单元测试](../tests/test_ch20.py) · [生产化验收清单](../docs/ch20_scheduler_review.md)

- 第 21 章：[SQLite Outbox 与故障实验](../examples/ch21_reliability_lab.py) · [Operation Ledger](../src/mini_agent/reliability_outbox.py) · [Resilience Patterns](../src/mini_agent/resilience_patterns.py) · [36 项单元测试](../tests/test_ch21.py) · [生产审查清单](../docs/ch21_reliability_review.md)

- 第 22 章：[Trace/费用实验](../examples/ch22_observability_lab.py) · [Recorder](../src/mini_agent/observability.py) · [Playback](../src/mini_agent/trace_store.py) · [Bridge](../src/mini_agent/observability_bridge.py) · [39 项测试](../tests/test_ch22.py) · [生产观测审查](../docs/ch22_observability_review.md)

- 第 24 章：[部署模拟实验](../examples/ch24_platform_lab.py) · [Preflight/Canary Controller](../src/mini_agent/platform_deployment.py) · [合成 Fixture](../src/mini_agent/platform_fixture.py) · [41 项单测](../tests/test_ch24.py) · [生产上线清单](../docs/ch24_platform_review.md)

- 第 25 章：[Eval Harness 示例](../examples/ch25_eval_lab.py) · [源码](../src/mini_agent/agent_evaluation.py) · [Fake Kernel Runner](../src/mini_agent/eval_fixture.py) · [合成 Dataset](../evals/ch25_offline_suite.json) · [40 项测试](../tests/test_ch25.py) · [生产评测审查](../docs/ch25_eval_review.md)

- 第 26 章：[离线 Benchmark 实验](../examples/ch26_benchmark_lab.py) · [Benchmark Framework](../src/mini_agent/benchmark_framework.py) · [15 项单元测试](../tests/test_ch26.py) · [生产审查](../docs/ch26_benchmark_review.md)

- 第 27 章：[Evaluation Campaign 实验](../examples/ch27_evaluation_framework_lab.py) · [CampaignStore/Dashboard](../src/mini_agent/eval_campaign.py) · [14 项测试](../tests/test_ch27.py)

- 第 28 章：[EDD Hypothesis/Ablation 实验](../examples/ch28_edd_lab.py) · [EDD Decision Gate](../src/mini_agent/evaluation_driven_development.py) · [12 项回归测试](../tests/test_ch28.py)

- 第 29 章：[四种搜索算法离线实验](../examples/ch29_search_lab.py) · [Search Runtime 源码](../src/mini_agent/inference_search.py) · [26 项单元测试](../tests/test_ch29.py) · [生产化审查清单](../docs/ch29_search_review.md)

- 第 30 章：[反馈学习实验](../examples/ch30_feedback_lab.py) · [Feedback Verifier/Policy](../src/mini_agent/feedback_learning.py) · [12 条合成数据](../evals/ch30_feedback_suite.json) · [38 项单元测试](../tests/test_ch30.py) · [反馈安全审查](../docs/ch30_feedback_review.md)
- 第 31 章：[Agentic RL 离线实验](../examples/ch31_agentic_rl_lab.py) · [策略梯度 / PPO / GRPO 源码](../src/mini_agent/agentic_rl.py) · [强化学习回归测试](../tests/test_ch31.py) · [研究与上线审查清单](../docs/ch31_rl_review.md)
- 第 32 章：[Self-Improvement 离线实验](../examples/ch32_self_improvement_lab.py) · [Prompt/Memory/Workflow 优化源码](../src/mini_agent/self_improvement.py) · [质量/成本/安全/回滚测试](../tests/test_ch32.py) · [上线与数据隔离审查清单](../docs/ch32_self_improvement_review.md)
