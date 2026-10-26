# 第 26 章 Benchmark 生产验收清单

本项目提供离线 Benchmark Protocol、领域切片、回归门和可复现实验。**没有运行外部 Benchmarks，没有使用真实模型、云环境或用户数据**。

## Dataset 与污染
- [ ] 固定 Dataset/Benchmark/Verifier 的名称、版本、SHA 和许可。
- [ ] 开发集、回归集、保密 Heldout 分开管理。
- [ ] 检查训练/检索中是否泄漏测试问题、答案、Patch。
- [ ] 记录任务来源、日期、难度、领域和 Ground Truth 变更。
- [ ] 定期替换已公开/饱和且高污染风险的任务。

## 运行与安全
- [ ] Agent Worker 和隐藏评分器处于真实进程/容器/VM 隔离。
- [ ] 任务环境每轮 Reset：代码仓库、浏览器 Profile、数据库、缓存。
- [ ] 工具请求有可信 Host Policy，不能由模型自报权限。
- [ ] 记录 Timeout、Cancel、未知副作用、真实 Sandbox 销毁回执。
- [ ] 外部操作明确授权；不在共享 CI 直接执行攻击或真实发布。

## Protocol 可复现
- [ ] 固定 Model/Prompt/Tool/Environment/Verifier 版本。
- [ ] 固定 Seed、Sampling 参数、Retry/Model/Tool Budget。
- [ ] 采集实际 Provider Usage、工具成本、Latency 和任务质量。
- [ ] 报告日期、镜像摘要、环境版本与官方 Benchmark Commit。
- [ ] 不能将不同版本或不同预算的排行榜直接比较。

## Grading 与统计
- [ ] 可信 Environment/Tool Backend 的最终状态优先于 Agent 自报。
- [ ] 记录总体 Micro 与领域 Macro 成绩、类别切片和失败类型。
- [ ] 越权尝试与实际外部违规分开统计。
- [ ] 成对 Trial 报告改善与回归，不只看总体提升。
- [ ] 重复 Trial 的 pass@k / pass^k 明确指标定义。
- [ ] Wilson/McNemar/Bootstrap 符合独立性或聚类假设。
- [ ] 未知费用不是零，失败尝试不能从分母中剔除。
- [ ] LLM-as-a-Judge 需要人类校准，不能覆盖硬性环境验收。

## 发布门
- [ ] 有任何未授权操作尝试时 Fail Closed。
- [ ] 关键领域回归超阈值时 Block，不允许均值改善掩盖。
- [ ] 缺少强制成本或成功证据时 Insufficient Evidence。
- [ ] Gate 批准仅是评测条件满足，不代表已部署。
- [ ] 真正部署还需第 24 章可信 Release Actuator 与真实状态回读。

核心文件：[Benchmark Framework](../src/mini_agent/benchmark_framework.py) · [Chapter 26](../book/chapters/26.md) · [实验](../examples/ch26_benchmark_lab.py) · [单元测试](../tests/test_ch26.py)。
