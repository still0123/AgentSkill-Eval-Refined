# AgentSkill-Eval

AgentSkill-Eval 是一个面向 Agent Skill 的可复现评测与回归分析项目。平台以受控配对实验为核心，比较同一 Agent 在 without-Skill、with-Skill 或不同 Skill 版本下的任务质量、成本、时延与稳定性。

当前仓库已完成 **P0 目标 1～7：Python 项目初始化、核心数据契约、本地可靠存储、Runner 防腐层、本地配对实验编排、可信统计报告及 12 Case Demo Dataset**。目前提供冻结领域模型、原子 Manifest、内容寻址 Blob、Mock/`skill-up v0.5.0` Adapter、确定性 PairBlock 执行、RunMeasurement、group/case 层级统计、W/T/L、invalid 双口径、效率指标、安全离线 HTML，以及严格可寻址的 Python 代码审查演示集。完整设计见 [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md)。

## 环境要求

- Python 3.9 或更高版本
- Git
- 真实 Runner 集成需要固定版本和二进制哈希的 `skill-up v0.5.0`；其他开发与 Mock 测试不要求安装

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

agentskill-eval --help
agentskill-eval version
agentskill-eval schema export /tmp/agentskill-eval-schema.json
agentskill-eval dataset validate examples/datasets/python-review-demo
agentskill-eval storage recover /path/to/workspace
agentskill-eval storage rebuild-index /path/to/workspace EXPERIMENT_UUID
agentskill-eval report generate /path/to/workspace EXPERIMENT_UUID \
  --control CONTROL_VARIANT_UUID --treatment TREATMENT_VARIANT_UUID
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

## Runner 防腐层

- `MockRunnerAdapter` 提供确定性结果、事件与取消，用于编排层测试。
- `SkillUpRunnerAdapter` 只依赖上游公开 CLI/JSON，运行前校验固定版本和二进制 SHA-256。
- baseline/treatment 被编译为隔离的单 Case 目录；关闭上游 benchmark、并发和 retry，避免双重实验语义。
- Runner 退出码仅用于诊断，Case 的通过/失败以 `result.json` 为准。
- 超时和取消会终止进程组；Runner HOME、缓存和临时目录按运行隔离。
- Golden parser 测试无需外部依赖；发现固定二进制时自动执行真实 Custom Engine 集成测试。

详细协议见 [Runner 防腐层与兼容协议](./docs/runner-adapters.md)。

## 本地配对实验

- 计划器以 UUIDv5 和冻结 seed 确定性生成 PairBlock、Run 与 Variant 执行顺序。
- Experiment 引用、Variant 指纹、运行时配置和预算在执行前做一致性检查。
- Run 与 Attempt 分别持久化生命周期，任务失败和基础设施 invalid 使用不同终态。
- Runner 原始产物复制前复验路径、大小和 SHA-256，并同步写入内容寻址对象存储。
- 已完成 Run 可幂等重放，不会再次调用 Agent；崩溃后的未终态 Run 只报告、不静默重复计费。
- 真实集成测试使用 `skill-up` Custom Engine 跑完整 baseline/treatment 两臂，无需模型凭据。

详细协议见 [P0 本地配对实验引擎](./docs/local-experiment-engine.md)。

## 统计与报告

- 主口径将终态 invalid 保守计为失败；capability 敏感性口径只使用双臂均有效的 PairBlock。
- repeats 先在 Case 内聚合，再按 independence group 等权，避免大仓库支配总体结论。
- 成功率、增益、Token、时延和成本均使用固定 seed 的 group→case 层级 bootstrap。
- 报告 W/T/L、完整/有效 block 比例、invalid 数、配对效率差和 cost per success。
- `report.json` 保留机器结果；`report.html` 可离线打开、严格转义且不执行脚本。

详细方法见 [配对统计与静态报告](./docs/statistics-and-reports.md)。

## 演示数据集

- `python-review-demo v1.0.0` 包含 12 个合成 Case：4 个正例、2 个反例、2 个干扰、
  2 个复杂和 2 个鲁棒性样本；
- Case 采用上游原生 YAML，平台 sidecar 冻结 split、provenance、group、oracle 和适用性；
- Loader 将 Case、fixture、prompt 和 grader 纳入 Dataset 身份，拒绝逃逸路径、符号链接、
  重复 ID 与类别配额不足；
- 配套 `python-review-v1` Skill 冻结版本和 `SKILL.md` 哈希；
- 固定 Runner 可用时，集成测试会真实编译并校验全部 12 个 Case。

该数据集及 grader 完全公开，只用于工程 Demo 和开发回归，不支持稳定泛化结论。详细说明见
[P0 Python Review Demo Dataset](./docs/demo-dataset.md)。

## 开发原则

- P0 优先跑通无服务依赖的本地可信闭环。
- 不复制 `skill-up` 的内部实现，只依赖其公开 CLI、JSON 和产物契约。
- 确定性验证优先于 LLM Judge。
- 每个完成目标都必须通过自动化检查，并以独立 Git 提交推送。
