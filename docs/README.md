# Project documentation

本目录保存架构边界、实验协议、复现方法和各纵切的结论限制。

## 后续执行路线

- [Skill v1→v2 分阶段执行工作文档](./skill-evolution-execution-roadmap.md)

## 可信实验基础

- [P0 本地存储与恢复协议](./local-storage.md)
- [Runner 防腐层与 `skill-up v0.5.0` 兼容协议](./runner-adapters.md)
- [P0 本地配对实验引擎](./local-experiment-engine.md)
- [配对统计与静态报告](./statistics-and-reports.md)
- [执行证据、安全扫描与审计包](./evidence-and-replay.md)
- [Trace Intelligence 与规则诊断](./trace-intelligence.md)

## 数据、搜索与真实证据

- [P0 Python Review Demo Dataset](./demo-dataset.md)
- [一条命令运行 P0 配对实验](./one-command-demo.md)
- [Automatic Benchmark Generation MVP](./automatic-benchmark-generation.md)
- [Benchmark-guided Skill Search MVP](./benchmark-guided-skill-search.md)
- [Failure-Guided Skill Evolution MVP](./failure-guided-skill-evolution.md)
- [Audited Process Skill Proposal Generator MVP](./audited-process-skill-proposal-generator.md)
- [Observed Failure Evidence Bridge MVP](./observed-failure-evidence-bridge.md)
- [Independent Final Evaluation MVP](./independent-final-evaluation.md)
- [Real Agent Evaluation Evidence MVP](./real-agent-evidence.md)
- [阶段 3～5 数据准备与隔离](./dataset-preparation-stage3-5.md)
- [阶段 4A：SkillVersion Promotion Core](./skill-version-promotion.md)

## 专项 Lab 与界面

- [Unified Multi-Scenario Evaluation MVP](./unified-multi-scenario-evaluation.md)
- [Process Agent Scenario Evaluation MVP](./process-agent-scenario-evaluation.md)
- [Interactive Scenario Agent Loop MVP](./interactive-scenario-agent-loop.md)
- [MCP Tool Evaluation MVP](./mcp-tool-evaluation.md)
- [Memory/RAG Evaluation MVP](./memory-rag-evaluation.md)
- [Read-only Evaluation Dashboard MVP](./dashboard-mvp.md)

这些文档会明确区分真实证据与 simulated Lab。历史纵切文档中的“不支持”描述只约束该纵切，
不能据此推断整个集成仓库缺少其他已经合入的模块。

阶段 4A 只使用 Fake／simulated evidence 验证 Promotion 状态机、不可变 Manifest 和拒绝
流程；它不表示真实 winner 已确认，也不表示真实 Skill v2 已发布。
