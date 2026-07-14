# AgentSkill-Eval

[![CI](https://github.com/ranmaoxia0123/AgentSkill-Eval/actions/workflows/ci.yml/badge.svg)](https://github.com/ranmaoxia0123/AgentSkill-Eval/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](./pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache--2.0-green)](./LICENSE)

AgentSkill-Eval 是一个面向 Agent Skill 的**评测、诊断与迭代优化系统**。它不只回答
“这个 Skill 得了多少分”，而是持续回答三个问题：

1. 加载 Skill 是否比不加载更有效？
2. Skill v2 是否比 v1 更好，改进和回归分别发生在哪里？
3. 如何利用失败 Case 与交互 Trace，生成并验证下一版 Skill？

系统在冻结 Agent、模型、Case、环境和预算的前提下运行配对实验，保存结果、执行轨迹、
失败诊断和审计证据，为后续 Skill 搜索、版本回归和自动优化提供可信依据。

**当前版本：[`v0.2.0-rc1`](https://github.com/ranmaoxia0123/AgentSkill-Eval/tree/v0.2.0-rc1)**

当前开发版在 RC1 基础上增加了跨仓库 Benchmark、真实 Agent 证据、多场景统一评测、
Process Agent 接入、交互式 Action/Observation 循环、Failure-guided Skill Evolution 和受审计的
Process Skill Proposal Generator、预算受控的 DeepSeek Skill Proposal Generator、
Fake-evidence SkillVersion Promotion Workflow，以及 Stage 5A.2 离线 Evolution Evidence Release CLI；
未完成能力会在下文明确标记。

## 项目的核心方向

这个项目的最终目标不是建立更多评分指标，而是让 Skill 能够被持续改进：

```text
Benchmark / Real Task
          │
          ▼
without-Skill ── with-Skill ── Skill v1 / v2
          │             │             │
          └────── 配对执行与 Trace ────┘
                        │
                        ▼
               失败诊断与回归定位
                        │
                        ▼
               Skill 候选生成与筛选
                        │
                        ▼
             Independent Locked Evaluation
                        │
                        ▼
                 发布不可变 SkillVersion
```

因此，评测在系统中承担的是**优化方向发现和版本选择机制**，而不是终点。

## 30 秒理解工作流

```text
                   同一个 Case
                        │
       ┌────────────────┼────────────────┐
       │                │                │
without-Skill       Skill v1         Skill v2
       │                │                │
       └────────────────┼────────────────┘
                        │
            结果 + Trace + Token/费用
                        │
             增益、回归与失败归因
```

配对设计的关键是：**每次比较只改变一个 Skill 变量。** 因此结果表达的是 Skill 的
边际价值或版本增量，而不是 Agent 的绝对能力。

## 能做什么

| 能力 | 用途 | 当前状态 |
|---|---|---|
| Skill 配对评测 | 比较 without/with Skill 的通过率、Token、时延和费用 | 已实现 |
| Trace Intelligence | 保存规范化轨迹，定位超时、工具、环境或验证失败 | 已实现 |
| Benchmark Generation | 从真实 Git 历史重建 before-fail / after-pass 评测 Case | 已实现 |
| Skill Search | 在固定预算下生成、筛选和冻结 Skill 候选 | 已支持 simulated 与预算受控的真实 Agent evaluator |
| Independent Final Evaluation | 用隔离的 locked test 检查搜索结果 | 已实现 |
| Real Agent Evidence | 通过显式授权和预算门运行真实 Agent 实验 | 已实现 |
| MCP Evaluation Lab | 评测工具选择、参数、顺序、恢复和副作用 | 离线 simulated Lab |
| Memory/RAG Lab | 评测检索、引用、污染、记忆更新和会话隔离 | 离线 simulated Lab |
| Unified Scenario | 用同一协议运行软件工程、MCP、Memory/RAG 并保留专项指标 | 已实现，simulated MVP |
| Process Scenario Agent | baseline/Skill 两臂由哈希固定进程生成 MCP/Memory-RAG 计划 | 已实现，process integration |
| Interactive Agent Loop | Agent 根据每一步环境 Observation 决定下一步 Action | 已实现，兼容现有 `plan_once` |
| Skill Version Regression | 比较 v1/v2 的改进、退化 Case 与成本变化 | 已有底层能力，真实发布工作流待阶段 3 证据 |
| SkillVersion Promotion | handoff→confirmation→locked test→人工审核→不可变版本 | Stage 4b Fake/fixture 集成已完成，真实 v2 待阶段 3 证据 |
| Evidence Release Prep | 脱敏报告、审计包校验、v1/v2 对比和不可变发布目录 | Stage 5 前置开发已完成，不运行 Agent |
| Evolution Evidence Release CLI | 将 Promotion、终评、SkillVersion 与 Evolution 谱系打包为离线可验证发布目录 | Stage 5A.2 Fake/fixture CLI 已完成，不调用模型 |
| Failure-guided Optimization | 从 train 失败诊断生成假设，经搜索与 regression_dev 门冻结候选 | 已实现 simulated MVP，独立 locked 终评不自动触发 |
| Process Skill Proposal | 哈希/版本固定的本地进程根据脱敏 train 失败生成候选变异 | 已实现 Fake Process MVP，不代表真实 LLM 优化 |
| DeepSeek Skill Proposal | 单次授权调用从 train 失败生成 3～5 个结构化候选，并冻结 prompt/schema/token/费用证据 | 代码与 Fake API 已实现；首次真实 train smoke 证据不足，proposal 未调用 |
| Real Optimizer Evaluator | 用真实 Agent 的 Case 结果、成本和 Trace 选择候选并执行 regression_dev | 已实现，真实 smoke 需单独授权 |
| Observed Failure Bridge | 从真实 Skill treatment Run 导出可追溯的 train failure bundle | 已实现，不调用模型 |
| Dashboard | 查看报告、Trace、候选、Promotion 谱系和 SkillVersion 状态 | 本地只读版 |

`simulated` 只证明评测管线可用，不能当作真实模型能力证据。

## 5 分钟跑通演示

### 1. 安装

需要 Python 3.9+ 和 Git。默认演示不调用真实模型，也不需要 API Key。

```bash
git clone https://github.com/ranmaoxia0123/AgentSkill-Eval.git
cd AgentSkill-Eval

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 2. 运行本地配对实验

```bash
agentskill-eval demo run --workspace .agentskill-eval/demo
```

该命令会运行 12 个演示 Case 的配对流程，产生：

- 冻结的 Experiment/Run Manifest；
- `without-Skill` 与 `with-Skill` 结果；
- Token、时延、成本和 W/T/L 统计；
- 可离线打开的 JSON/HTML 报告；
- Trace、失败诊断和审计产物。

> 演示使用确定性模拟执行，结果会强制标记 `simulated=true`。

也可以通过统一场景入口运行三类评测：

```bash
agentskill-eval scenario validate examples/unified/mcp-tool.yaml
agentskill-eval scenario run examples/unified/mcp-tool.yaml \
  --workspace .agentskill-eval/unified --allow-simulation
```

### 3. 运行质量检查

```bash
make check
```

等价的 Python 命令：

```bash
python -m ruff check .
python -m mypy apps packages
python -m pytest
```

Dashboard 位于 `apps/web/`，使用 pnpm 10：

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm test
pnpm run build
```

## 一个真实实验结果

仓库包含一组 Qwen Code 0.19.9 + DeepSeek V4 Pro 的脱敏实验记录：

| 实验 | Run 情况 | Baseline | Treatment | 结论边界 |
|---|---:|---:|---:|---|
| Smoke | 4 完成 / 0 invalid | 100% | 100% | 只证明真实执行与审计链路可用 |
| Evidence | 9 完成 / 3 invalid | 66.7% | 83.3% | 仅两个同源 Case，只是描述性观察 |

这些数字**不能证明 Skill 具有普遍增益**：样本很小，而且 Evidence 实验包含
3 个基础设施 invalid Run。仓库只保存脱敏配置、聚合指标和审计哈希：

- [DeepSeek smoke 脱敏记录](./experiments/real-deepseek-v4-pro-smoke-2026-07-13/README.md)
- [DeepSeek evidence 脱敏记录](./experiments/real-deepseek-v4-pro-evidence-2026-07-13/README.md)
- [Stage 3 train smoke 负结果](./experiments/stage3-train-deepseek-v4-pro-smoke-2026-07-14/README.md)
- [Stage 3 train smoke 工具预算修正后复跑](./experiments/stage3-train-deepseek-v4-pro-smoke-rerun-2026-07-14/README.md)

## 跨仓库 Benchmark 证据

Automatic Benchmark Generation v1alpha2 可在一个冻结 Job 中重建多个本地 Git source，
使用显式 provenance family 分组和去重，并阻止同一 fork lineage 跨 split 发布。仓库内的
`more-itertools` 与 `cachetools` 离线 bundle 的扩展配置共提供十二个独立缺陷家族；每个候选
都执行 before、after、mutation 和 alternative repair 各三次，共 144 次确定性验证。
仓库中的首份跨仓库验收记录仍保留当时四 Case 的结果，便于审计演进历史。

- [跨仓库离线验收说明](./experiments/cross-repository-benchmark-2026-07-14/README.md)
- [脱敏哈希与聚合结果](./experiments/cross-repository-benchmark-2026-07-14/result.sanitized.json)
- [Stage 3 四 Case train DatasetVersion 证据](./experiments/train-benchmark-expansion-2026-07-14/README.md)

## 真实 Agent 运行安全门

真实运行不会从 simulation 自动回退或升级，必须同时提供：

- `--confirm-real-run`：确认允许产生真实调用；
- `--max-cost-microusd`：最大费用上限；
- `--max-agent-runs`：最大 Agent Run 数。

先做无费用 preflight：

```bash
agentskill-eval real preflight /absolute/path/to/observed-agent.yaml
```

只有完成 Provider、model、Run 数和预算人工确认后，才应执行 smoke。完整命令与
Secret 配置见 [Real Agent Evaluation Evidence](./docs/real-agent-evidence.md)。

DeepSeek 候选生成使用独立的确认、调用数和费用预算门；配置、数据隔离和审计字段见
[DeepSeek Skill Proposal MVP](./docs/deepseek-skill-proposal.md)。

## 系统组成

```text
Case / Dataset / Skill
          │
          ▼
Experiment Planner ────冻结 Variant、SkillVersion、PairBlock 和预算
          │
          ▼
Runner Adapter ──────Mock / skill-up / Process Agent
          │
          ▼
Manifest + Trace + Artifact
          │
          ├── Evaluator / Statistics / Failure Diagnosis
          ├── Skill Candidate Search / Regression Analysis
          └── JSON / HTML Report / Replay Bundle
```

核心原则：

- 评测输入不可变，记录文件级 SHA-256；
- 确定性脚本验证优先于 LLM Judge；
- `pass/fail` 与基础设施 `invalid` 分开；
- 缺少 Trace 能力时标记 `capability unavailable`，不猜测“没有发生”；
- 不保存模型隐藏思维过程；
- 真实证据与 simulated 结果不混合统计。

## 三类核心实验

| 实验 | 控制组 | 实验组 | 回答的问题 |
|---|---|---|---|
| Skill 增益 | without-Skill | with-Skill | Skill 是否提供真实边际价值？ |
| 版本回归 | Skill v1 | Skill v2 | 哪些 Case 改进，哪些 Case 退化？ |
| 候选选择 | 当前稳定版 | 多个候选版本 | 哪个候选能通过独立 locked test？ |

只有前两类实验稳定可靠，自动优化才有可信的选择信号。候选不能根据最终 locked test
反复修改，否则会把终评集变成训练集。

## 仓库结构

```text
apps/cli/                统一 CLI
apps/web/                本地只读 Dashboard
packages/contracts/      Pydantic 领域契约
packages/experiment/     配对实验、存储、统计与报告
packages/runner_adapters/ Runner 防腐层
packages/trace_intelligence/
packages/benchmark_gen/
packages/skill_optimizer/
packages/real_evidence/
packages/mcp_lab/
packages/memory_rag_lab/
packages/scenarios/       跨场景计划、Adapter 和统一结果 envelope
examples/                演示 Skill、Dataset 和配置
experiments/             可公开的脱敏实验记录
tests/                   单元与集成测试
docs/                    模块级设计和操作文档
```

## 按任务找文档

| 我想了解…… | 文档 |
|---|---|
| 当前完成状态与证据边界 | [Project Completion Status](./docs/project-completion-status.md) |
| 五分钟答辩与面试问答 | [Portfolio Demo and Interview Guide](./docs/portfolio-demo-and-interview-guide.md) |
| 整体架构、阶段和数据设计 | [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md) |
| 一条命令运行演示 | [One-command Demo](./docs/one-command-demo.md) |
| 本地存储、恢复和幂等 | [Local Storage](./docs/local-storage.md) |
| Runner 适配和 `skill-up` 兼容性 | [Runner Adapters](./docs/runner-adapters.md) |
| 证据、Secret 扫描和审计包 | [Evidence and Replay](./docs/evidence-and-replay.md) |
| Trace 和失败诊断 | [Trace Intelligence](./docs/trace-intelligence.md) |
| 统计口径、W/T/L 和报告 | [Statistics and Reports](./docs/statistics-and-reports.md) |
| 自动生成 Benchmark | [Automatic Benchmark Generation](./docs/automatic-benchmark-generation.md) |
| Skill 搜索与独立终评 | [Skill Search](./docs/benchmark-guided-skill-search.md) / [Final Evaluation](./docs/independent-final-evaluation.md) |
| 失败驱动 Skill 演化 | [Failure-Guided Skill Evolution](./docs/failure-guided-skill-evolution.md) |
| 受审计的 Process 候选生成 | [Process Skill Proposal Generator](./docs/audited-process-skill-proposal-generator.md) |
| 真实失败证据桥接 | [Observed Failure Evidence Bridge](./docs/observed-failure-evidence-bridge.md) |
| 真实 Agent 评测 | [Real Agent Evidence](./docs/real-agent-evidence.md) |
| Skill 演化证据发布 CLI | [Evolution Evidence Release](./docs/evolution-evidence-release-cli.md) |
| MCP / Memory-RAG 专项 Lab | [MCP Lab](./docs/mcp-tool-evaluation.md) / [Memory-RAG Lab](./docs/memory-rag-evaluation.md) |
| 跨场景统一入口和结果协议 | [Unified Multi-Scenario Evaluation](./docs/unified-multi-scenario-evaluation.md) |
| 本地 Process Agent Skill 激活 | [Process Agent Scenario Evaluation](./docs/process-agent-scenario-evaluation.md) |
| 逐步 Action/Observation Agent 循环 | [Interactive Scenario Agent Loop](./docs/interactive-scenario-agent-loop.md) |
| Dashboard 启动和限制 | [Dashboard](./docs/dashboard-mvp.md) |

完整文档索引见 [`docs/README.md`](./docs/README.md)。

后续开发请按
[`Skill v1→v2 分阶段执行工作文档`](./docs/skill-evolution-execution-roadmap.md)
逐阶段进行；该路线优先完成真实优化证据，不提前扩建平台。

## 当前边界

`v0.1.0-rc1` 是可本地复现的研究型 RC，不是完整生产平台：

- 尚无 FastAPI、账号权限、远程任务队列和多租户控制面；
- Dashboard 只读取本地冻结报告；
- MCP 与 Memory/RAG 目前是离线、确定性 Lab；
- Process Agent 支持兼容的 `plan_once` 和有界 `step_loop`；后者当前只连接确定性本地环境；
- Failure-guided Evolution 已连通诊断、Process 候选生成、搜索和回归门，但当前 Generator 仍是
  确定性/Fake Process，尚未覆盖所有 Skill 类型或真实模型候选生成；
- 真实 Agent 数据量很小，不支持泛化性能声明。

## 参与与安全

- 开发规范：[CONTRIBUTING.md](./CONTRIBUTING.md)
- 安全和凭据事件：[SECURITY.md](./SECURITY.md)
- 版本变更：[CHANGELOG.md](./CHANGELOG.md)
- 第三方 Benchmark 输入：[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)

项目代码使用 [Apache License 2.0](./LICENSE)；生成数据集仍遵循各自 provenance 中记录的许可证。
