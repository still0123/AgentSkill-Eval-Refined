# AgentSkill-Eval

AgentSkill-Eval 是一个面向 Agent Skill 的可复现评测与回归分析项目。平台以受控配对实验为核心，比较同一 Agent 在 without-Skill、with-Skill 或不同 Skill 版本下的任务质量、成本、时延与稳定性。

当前仓库已完成 **P0 目标 1～9**：Python 项目初始化、核心数据契约、本地可靠存储、Runner 防腐层、本地配对实验编排、可信统计报告、12 Case Demo Dataset、一键 72 Run 演示，以及可信证据/审计包。平台在执行前冻结 Case 与 Skill 输入；逐 Attempt 保存 Skill 安装或 baseline 洁净证据；在任何 Runner 输出持久化前执行精确 Secret 扫描；并能生成确定性的离线审计与再分析包。完整设计见 [开发设计文档](./AgentSkillEval_%E5%BC%80%E5%8F%91%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3_v1.0.md)。

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
agentskill-eval demo run --workspace .agentskill-eval/demo
agentskill-eval storage recover /path/to/workspace
agentskill-eval storage rebuild-index /path/to/workspace EXPERIMENT_UUID
agentskill-eval report generate /path/to/workspace EXPERIMENT_UUID \
  --control CONTROL_VARIANT_UUID --treatment TREATMENT_VARIANT_UUID
agentskill-eval experiment bundle /path/to/workspace EXPERIMENT_UUID /tmp/evidence.tar
agentskill-eval experiment verify-bundle /tmp/evidence.tar
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
- `FrozenInputManifest`：冻结执行实际读取的 Case/Fixture/Skill 文件清单、逐文件哈希和树哈希。
- `SkillActivationEvidence`：区分预期安装、已观测安装、baseline 洁净与上游不支持观测的行为阶段。
- `SecurityScanEvidence`：只记录扫描器版本、计数、状态和命中的 Secret 变量名，不记录 Secret 值。
- `ReplayBundleManifest`：冻结离线审计与再分析包的成员集合、大小和 SHA-256。

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
- 每个 Case source 与 treatment Skill 在计划持久化时复制到不可变输入区；后续修改原目录不会改变执行输入。
- Runner stdout、stderr 和产物先在内存中完成精确 Secret 扫描；发现命中即阻断整个输出批次，避免部分泄漏。
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

## 一条命令配对实验

默认命令无凭据、无费用地运行 72 个模拟逻辑 Run，并完成持久化、统计与报告全链路：

```bash
agentskill-eval demo run --workspace .agentskill-eval/demo
```

模拟结果在 Manifest、JSON 和 HTML 中强制标记，不能作为性能证据。真实模式需要显式选择
`--mode skill-up`、指定 Engine/Secret，并传入 `--confirm-real-run`，防止误消耗额度。详见
[一条命令运行 P0 配对实验](./docs/one-command-demo.md)。

## 可信证据与审计包

- 真实 `skill-up` Adapter 对 baseline 验证 `skills: []` 且无 selected Skill 目录；对 treatment 验证配置、`SKILL.md` 与安装树哈希。
- `discovered/read/activated/followed` 只有在 Runner 提供直接事件时才记录；当前 `skill-up v0.5.0` 不暴露这些事件，报告明确标为 unsupported，绝不由“已安装”推断“已遵循”。
- 审计包包含 Manifest 真值、冻结输入、逐 Attempt 原始证据与静态报告；排除 SQLite 查询缓存、锁文件和临时文件。
- 同一实验生成的未压缩 tar 字节确定一致，校验器拒绝路径穿越、重复成员、非普通文件及大小/哈希不匹配。
- 审计包支持离线审查、恢复 Manifest 和重新运行统计；它不包含外部 Provider 的服务端状态，因此不承诺逐 Token 重放一次外部模型请求。

详细协议见 [执行证据、安全扫描与审计包](./docs/evidence-and-replay.md)。

## 开发原则

- P0 优先跑通无服务依赖的本地可信闭环。
- 不复制 `skill-up` 的内部实现，只依赖其公开 CLI、JSON 和产物契约。
- 确定性验证优先于 LLM Judge。
- 每个完成目标都必须通过自动化检查，并以独立 Git 提交推送。
