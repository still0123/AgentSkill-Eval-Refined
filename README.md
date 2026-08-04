# AgentSkill-Eval

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](./pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache--2.0-green)](./LICENSE)

AgentSkill-Eval 是一个面向 Agent Skill 的**配对评测与发布门禁系统**。它解决的核心问题是：

> 如何知道一个 Skill 是否真的带来了增益？

系统在冻结 Agent、模型、Case、环境和预算的前提下，**同题双跑** Control（无 Skill / v1）和 Treatment（有 Skill / v2），通过确定性判分、证据归档和分阶段数据集判断新 Skill 是否值得发布。

> **AgentSkill-Eval-Refined** 是 AgentSkill-Eval 的聚焦公开版本，保留 Python Bug Fix Skill 评测主线，移除尚未形成真实证据的 MCP、Memory/RAG 和平台化扩展。
> 原始完整研究版见 [ranmaoxia0123/AgentSkill-Eval](https://github.com/ranmaoxia0123/AgentSkill-Eval)。

## 核心流程

![架构图](docs/assets/architecture-overview.svg)

```
冻结输入 → 配对执行 → 确定性验收 → 失败诊断 → 候选搜索 → 回归验证 → 独立终评 → 人工审核 → 发布
```

### 四层评测标准

| 层级 | 判定 | 含义 |
|---|---|---|
| 单次 Run | PASS / FAIL / INVALID | 确定性 Grader 判定，INVALID 表示基础设施异常 |
| 配对 Case | WIN / TIE / LOSS | 同题 Control vs Treatment 比较 |
| 实验统计 | AbsoluteGain + 95% CI | 两级 cluster bootstrap，样本不足时标记 `inference_ready=false` |
| 版本发布 | 五阶段门禁 | Search → Regression → Confirm → Locked → 人工审核 |

### 评测场景

当前仅支持 **Python Bug Fix** 主线：从真实 Git 历史重建缺陷 Case，通过 before-fail / after-pass / mutation-fail / alternative-pass 四态验证确保 Oracle 质量。

## 快速开始

```bash
git clone https://github.com/still0123/AgentSkill-Eval-Refined.git
cd AgentSkill-Eval-Refined

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"

# 运行 Demo（不调用真实模型）
agentskill-eval demo run --workspace .agentskill-eval/demo
```

## 演示结果

以下是一组真实 Demo 输出（零费用，完全离线模拟）：

```text
12 Cases × 2 Arms × 3 Repeats = 72 Runs
Invalid: 0
W/T/L: 5 / 6 / 1
Evidence Bundle: VERIFIED
Cost: $0
Evidence Class: SIMULATED DEMO
```

![Dashboard simulated demo](docs/assets/dashboard-simulated.png)

Demo 证据包包含：

| 文件 | 说明 |
|---|---|
| `experiment-report.json` | 完整实验报告 |
| `experiment-report.html` | 离线可打开的 HTML 报告 |
| `paired-results.json` | 配对 Case 对比结果 |
| `evidence-index.json` | 全部 Run 的索引与证据分类 |
| `audit-bundle.tar` | SHA-256 审计包 |
| `skill-diff.patch` | Skill 差异补丁 |
| `trace/` | Agent 执行 Trace 集合 |

> **注意**：Demo 标记为 `SIMULATED DEMO`，所有结果均为确定性 fixture，不代表真实 Agent 或 Skill 性能。

## 五分钟演示

参阅 [docs/five-minute-demo.md](docs/five-minute-demo.md) 获取完整的面试讲解流程。

## CLI 命令

```
agentskill-eval [OPTIONS] COMMAND

  demo run         运行演示实验
  dataset validate 验证数据集
  benchmark generate/publish  生成/发布 Benchmark
  optimize search/prepare-failures  候选搜索与失败导出
  final evaluate   独立终评
  skill promote begin/confirm/locked/approve/reject  Skill 版本发布
  real preflight/smoke/run  真实 Agent 实验
  scenario validate/run  统一场景验证与执行
```

## 项目结构

```
AgentSkill-Eval/
├── apps/cli/                 # CLI 入口
├── packages/
│   ├── contracts/            # Pydantic 领域模型
│   ├── experiment/           # 配对执行、统计、存储
│   ├── benchmark_gen/        # Git 历史 Benchmark 重建
│   ├── skill_optimizer/      # 候选搜索、终评、发布
│   ├── runner_adapters/      # Runner 防腐层
│   ├── real_evidence/        # 真实 Agent 证据
│   ├── scenarios/            # 统一场景协议
│   └── trace_intelligence/   # Trace 与失败诊断
├── tests/                    # 单元 + 集成测试
└── examples/                 # 配置与数据集示例
```

## 技术栈

| 领域 | 选择 |
|---|---|
| 核心语言 | Python 3.9+ |
| CLI | Typer / Click |
| 领域契约 | Pydantic v2 |
| 存储 | 本地内容寻址 Blob + SHA-256 |
| 评测 | 确定性 Grader + 两级 cluster bootstrap |
| 工程质量 | Ruff, mypy, pytest, GitHub Actions |

## 核心设计原则

- **控制变量**：PairBlock 固定执行顺序和环境，只改变 Skill
- **失败即证据**：任务失败、基础设施 invalid、预算终止使用不同语义
- **数据防泄漏**：同源仓库按 exposure zone 隔离，locked test 只消费一次
- **诚实的能力声明**：缺少事件标记 unavailable，样本不足限制 claim
- **负结果保留**：无增益时系统阻止发布，而非美化结果

## 当前边界

- 仅支持 Python Bug Fix 一个评测场景
- 真实实验样本较小，不支持通用性能排名
- Dashboard 只读本地证据
- 没有真实 Skill v2 发布（门禁阻止了无增益候选）

## License

Apache License 2.0
