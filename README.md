# AgentSkill-Eval

[![CI](https://github.com/ranmaoxia0123/AgentSkill-Eval/actions/workflows/ci.yml/badge.svg)](https://github.com/ranmaoxia0123/AgentSkill-Eval/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](./pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache--2.0-green)](./LICENSE)

AgentSkill-Eval 用可复现的 A/B 实验回答一个问题：

> 给 Agent 加载某个 `SKILL.md` 后，任务成功率是否真正提升，又额外消耗了多少 Token、时间和费用？

它会在相同 Agent、模型、Case 和环境下运行 `without-Skill` 与 `with-Skill`
两组实验，保存结果、执行轨迹、失败诊断和审计证据，并支持后续回归分析。

**当前版本：[`v0.1.0-rc1`](https://github.com/ranmaoxia0123/AgentSkill-Eval/tree/v0.1.0-rc1)**

当前 RC2 候选在 RC1 基础上增加跨仓库 Benchmark 重建：使用两个真实 MIT 开源仓库、
四个独立缺陷家族完成全离线质量门验证。该证据不调用模型，也不代表 Agent 性能结论。

## 30 秒理解工作流

```text
           同一个 Case
                │
       ┌────────┴────────┐
       │                 │
without-Skill        with-Skill
       │                 │
       └────────┬────────┘
                │
       结果 + Trace + 费用
                │
       增益分析 + 失败诊断
```

配对设计的关键是：**除了是否加载 Skill，其他条件尽量保持一致。**
因此结果表达的是 Skill 的边际价值，而不是 Agent 的绝对能力。

## 能做什么

| 能力 | 用途 | 当前状态 |
|---|---|---|
| Skill 配对评测 | 比较 without/with Skill 的通过率、Token、时延和费用 | 已实现 |
| Trace Intelligence | 保存规范化轨迹，定位超时、工具、环境或验证失败 | 已实现 |
| Benchmark Generation | 从真实 Git 历史重建 before-fail / after-pass 评测 Case | 已实现 |
| Skill Search | 在固定预算下生成、筛选和冻结 Skill 候选 | 已实现，离线演示为 simulated |
| Independent Final Evaluation | 用隔离的 locked test 检查搜索结果 | 已实现 |
| Real Agent Evidence | 通过显式授权和预算门运行真实 Agent 实验 | 已实现 |
| MCP Evaluation Lab | 评测工具选择、参数、顺序、恢复和副作用 | 离线 simulated Lab |
| Memory/RAG Lab | 评测检索、引用、污染、记忆更新和会话隔离 | 离线 simulated Lab |
| Unified Scenario | 用同一协议运行软件工程、MCP、Memory/RAG 并保留专项指标 | 已实现，simulated MVP |
| Dashboard | 查看已冻结的报告、Trace、W/T/L 和候选状态 | 本地只读版 |

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

## 跨仓库 Benchmark 证据

Automatic Benchmark Generation v1alpha2 可在一个冻结 Job 中重建多个本地 Git source，
使用显式 provenance family 分组和去重，并阻止同一 fork lineage 跨 split 发布。仓库内的
`more-itertools` 与 `cachetools` 离线 bundle 共提供四个真实历史缺陷；每个候选都执行
before、after、mutation 和 alternative repair 各三次，共 48 次确定性验证。

- [跨仓库离线验收说明](./experiments/cross-repository-benchmark-2026-07-14/README.md)
- [脱敏哈希与聚合结果](./experiments/cross-repository-benchmark-2026-07-14/result.sanitized.json)

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

## 系统组成

```text
Case / Dataset / Skill
          │
          ▼
Experiment Planner ────冻结 Variant、PairBlock 和预算
          │
          ▼
Runner Adapter ──────Mock / skill-up / Process Agent
          │
          ▼
Manifest + Trace + Artifact
          │
          ├── Evaluator / Statistics / Failure Diagnosis
          └── JSON / HTML Report / Replay Bundle
```

核心原则：

- 评测输入不可变，记录文件级 SHA-256；
- 确定性脚本验证优先于 LLM Judge；
- `pass/fail` 与基础设施 `invalid` 分开；
- 缺少 Trace 能力时标记 `capability unavailable`，不猜测“没有发生”；
- 不保存模型隐藏思维过程；
- 真实证据与 simulated 结果不混合统计。

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
| 整体架构、阶段和数据设计 | [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md) |
| 一条命令运行演示 | [One-command Demo](./docs/one-command-demo.md) |
| 本地存储、恢复和幂等 | [Local Storage](./docs/local-storage.md) |
| Runner 适配和 `skill-up` 兼容性 | [Runner Adapters](./docs/runner-adapters.md) |
| 证据、Secret 扫描和审计包 | [Evidence and Replay](./docs/evidence-and-replay.md) |
| Trace 和失败诊断 | [Trace Intelligence](./docs/trace-intelligence.md) |
| 统计口径、W/T/L 和报告 | [Statistics and Reports](./docs/statistics-and-reports.md) |
| 自动生成 Benchmark | [Automatic Benchmark Generation](./docs/automatic-benchmark-generation.md) |
| Skill 搜索与独立终评 | [Skill Search](./docs/benchmark-guided-skill-search.md) / [Final Evaluation](./docs/independent-final-evaluation.md) |
| 真实 Agent 评测 | [Real Agent Evidence](./docs/real-agent-evidence.md) |
| MCP / Memory-RAG 专项 Lab | [MCP Lab](./docs/mcp-tool-evaluation.md) / [Memory-RAG Lab](./docs/memory-rag-evaluation.md) |
| 跨场景统一入口和结果协议 | [Unified Multi-Scenario Evaluation](./docs/unified-multi-scenario-evaluation.md) |
| Dashboard 启动和限制 | [Dashboard](./docs/dashboard-mvp.md) |

完整文档索引见 [`docs/README.md`](./docs/README.md)。

## 当前边界

`v0.1.0-rc1` 是可本地复现的研究型 RC，不是完整生产平台：

- 尚无 FastAPI、账号权限、远程任务队列和多租户控制面；
- Dashboard 只读取本地冻结报告；
- MCP 与 Memory/RAG 目前是离线、确定性 Lab；
- 真实 Agent 数据量很小，不支持泛化性能声明。

## 参与与安全

- 开发规范：[CONTRIBUTING.md](./CONTRIBUTING.md)
- 安全和凭据事件：[SECURITY.md](./SECURITY.md)
- 版本变更：[CHANGELOG.md](./CHANGELOG.md)
- 第三方 Benchmark 输入：[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)

项目代码使用 [Apache License 2.0](./LICENSE)；生成数据集仍遵循各自 provenance 中记录的许可证。
