# AgentSkill-Eval

AgentSkill-Eval 是一个面向 Agent Skill 的可复现评测与回归分析项目。平台以受控配对实验为核心，比较同一 Agent 在 without-Skill、with-Skill 或不同 Skill 版本下的任务质量、成本、时延与稳定性。

当前仓库处于 **P0 目标 1：Python 项目初始化**。本阶段只提供可安装的 monorepo 骨架、Typer CLI 和基础测试工具；Runner 防腐层、领域契约、实验编排与报告会在后续目标中依次实现。完整设计见 [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md)。

## 环境要求

- Python 3.9 或更高版本
- Git
- 后续真实 Runner 目标需要固定版本的 `skill-up v0.5.0`，当前初始化阶段不要求安装

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

agentskill-eval --help
agentskill-eval version
```

## 本地验证

```bash
make check
```

也可以分别运行：

```bash
python -m ruff check .
python -m mypy apps packages
python -m pytest
```

## 仓库结构

```text
apps/                     可执行入口；P0 CLI/Worker，P1 API/Web
packages/contracts/       Pydantic 领域契约
packages/runner_adapters/ Runner 防腐层
packages/experiment/      配对实验、统计与报告
packages/trace_intelligence/
packages/benchmark_gen/
packages/skill_optimizer/
packages/mcp_lab/
packages/memory_rag_lab/  P1/P2 研究模块占位
runner_compatibility/     固定 Runner 版本与 Golden Contract
examples/                 演示 Skill 与 Dataset
tests/                    unit / integration / e2e
```

## 开发原则

- P0 优先跑通无服务依赖的本地可信闭环。
- 不复制 `skill-up` 的内部实现，只依赖其公开 CLI、JSON 和产物契约。
- 确定性验证优先于 LLM Judge。
- 每个完成目标都必须通过自动化检查，并以独立 Git 提交推送。
