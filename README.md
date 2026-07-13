# AgentSkill-Eval

AgentSkill-Eval 是一个面向 Agent Skill 的可复现评测与回归分析项目。平台以受控配对实验为核心，比较同一 Agent 在 without-Skill、with-Skill 或不同 Skill 版本下的任务质量、成本、时延与稳定性。

当前仓库已完成 **P0 目标 1～3：Python 项目初始化、核心数据契约与本地可靠存储**。目前提供可安装的 monorepo、Typer CLI、冻结的 Pydantic 领域模型、稳定内容哈希、Run 状态机、JSON Schema 导出、原子 Manifest、内容寻址 Blob、崩溃恢复和可重建 SQLite 索引；Runner 防腐层、实验编排与报告会在后续目标中依次实现。完整设计见 [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md)。

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
agentskill-eval schema export /tmp/agentskill-eval-schema.json
agentskill-eval storage recover /path/to/workspace
agentskill-eval storage rebuild-index /path/to/workspace EXPERIMENT_UUID
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

## 已实现的核心契约

- `ExperimentVariant`：保存 Runner、Agent、Skill、工具、Memory/RAG、沙箱和价格快照，并生成与数据库 ID 无关的内容指纹。
- `PairBlock`：冻结 Case、independence group、repeat、seed 和 Variant 执行顺序。
- `Run`：区分执行生命周期与 `pass/fail/invalid` 评测结果，提供稳定幂等键和合法状态迁移表。
- `RunAttempt`：记录物理尝试、lease generation、fencing token、错误和 observed environment fingerprint。
- `ArtifactManifest`：保存内容哈希、大小、媒体类型和敏感级别，拒绝绝对路径、路径穿越及非规范路径。
- `ExperimentManifest`：冻结数据集、协议、统计计划、预算和 Variant 引用。

## P0 本地存储保证

- Manifest 使用 `ase/storage/v1` 完整性信封，同时验证 payload SHA-256 和领域语义指纹。
- 文件更新采用同目录临时文件、文件 `fsync`、原子 `replace` 和父目录 `fsync`。
- Attempt 与 Artifact 全部落盘后才更新 Run 的活动 Attempt 指针。
- 合法临时 Manifest 会在启动恢复时晋升；重复临时文件会删除；冲突或损坏文件进入 `quarantine/`。
- SQLite 开启 WAL，但只作为可删除查询缓存，能够完全从 Manifest 重建。
- 每个 Run 使用非阻塞 POSIX advisory lock，避免两个本地 Worker 同时领取同一逻辑任务。

详细协议见 [P0 本地存储与恢复](./docs/local-storage.md)。

## 开发原则

- P0 优先跑通无服务依赖的本地可信闭环。
- 不复制 `skill-up` 的内部实现，只依赖其公开 CLI、JSON 和产物契约。
- 确定性验证优先于 LLM Judge。
- 每个完成目标都必须通过自动化检查，并以独立 Git 提交推送。
