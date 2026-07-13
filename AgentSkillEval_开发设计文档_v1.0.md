# AgentSkill-Eval：面向大模型智能体的评测、诊断与受控优化平台

> 开发设计文档 v1.1
>
> 定位：个人秋招主项目 / 小型 Agent Research System
>
> 技术路线：Python、FastAPI、PostgreSQL、Redis Streams、Docker/OpenSandbox、OpenTelemetry、Vue 3
>
> 执行内核基线：`skill-up v0.5.0`，通过 CLI/JSON 契约集成
>
> 本版修订日期：2026-07-13

---

## 0. 文档说明

本文档是 AgentSkill-Eval 的开发基线，用于指导需求冻结、架构设计、编码、测试、实验和演示。平台不重写通用 Skill 执行器，而是在可复用执行内核之上围绕 Agent 系统形成完整闭环：

```text
Benchmark 生成 → 配对执行 → 多级评分 → 轨迹诊断
       ↑                                  ↓
       └──────── Skill 优化与回归验证 ────┘
```

本文档中的 P0、P1、P2 分别表示首个可演示版本、增强版本和研究版本。开发时必须先完成 P0 的端到端闭环，再扩展高级能力。

### 0.1 术语

| 术语 | 定义 |
|---|---|
| Agent | 能规划、调用工具、观察环境并多轮执行任务的智能体 |
| Skill | 以 `SKILL.md` 为核心的操作说明、流程、脚本和参考资料包 |
| Case | 一个可独立执行和验证的评测任务 |
| Run | Agent 在固定配置下对一个 Case 的一次执行 |
| Baseline | 不加载目标 Skill 的执行组 |
| Treatment | 加载目标 Skill 的执行组 |
| Trajectory | Agent 的消息、工具调用、观察、文件变更和评分事件序列 |
| Judge | 对 Run 结果进行评分的规则、脚本或模型 |
| Regression | 新版本相较旧版本从通过变为失败或关键指标显著下降 |
| MCP | Model Context Protocol，用于标准化模型与工具/资源的连接 |
| Runner | 负责准备 workspace、安装 Skill、启动 Agent 并生成原始结果的执行内核 |
| Primary sampling unit | 最高层独立抽样组，如 repository/fork lineage/patch family；只有来源互不相关时才退化为 Case |
| Repeat | 同一 Case 内用于估计随机性的重复执行，不是独立样本 |

### 0.2 上游复用与系统边界

P0 固定使用 `skill-up v0.5.0` 作为默认 Runner，复用其已有的 Skill 安装、单条件执行、CLI Agent、local/HTTP Custom Engine、Docker/OpenSandbox、Expect/Rule/Script/Agent Judge、多轮会话、基础 MCP、产物采集、报告和 OTLP 能力。本项目不依赖其 `internal/*` Go 包，而是通过固定版本的 CLI、`result.json`、`grading.json`、`eval_metadata.json`、可选 `benchmark.json` 和产物目录建立防腐层。Variant、pairing、repeat 和随机顺序始终由本平台拥有；调用上游时关闭其 benchmark 配对模式，保证一次调用只对应一个逻辑 Run。

本项目自研范围：

- Skill/Dataset/Experiment/Variant 的不可变版本和历史查询；
- Skill v1/v2、多 Variant 和受控消融实验；
- Case 内聚合、independence-group 聚类统计、回归门禁和成本决策；
- 轨迹持久化、回放、成对差异和失败归因；
- 自动 Benchmark 候选生成与 Benchmark-guided Skill Search；
- 有状态 MCP 故障注入和 Memory/RAG 分层评测。

兼容策略：在 `runner_compatibility` 中记录已验证的 `skill-up` tag/commit、Schema、Agent CLI 版本和报告格式；升级前必须运行 Golden Contract Test。

---

## 1. 项目背景与目标

### 1.1 背景

现代 Agent 的效果由模型、系统提示、Skill、工具、Memory、RAG、规划策略和运行环境共同决定。仅查看最终文本无法回答以下工程问题：

- Skill 是否带来了真实增量，还是只增加上下文与 Token？
- Agent 失败是规划错误、工具错误、检索错误，还是环境故障？
- Skill 从 v1 升级到 v2 后是否引入回归？
- 相同 Skill 在不同模型、Agent Runtime 和 MCP Server 上是否稳定？
- 能否从失败样本生成候选 Skill，并在不污染 locked test 的前提下验证？

AgentSkill-Eval 通过受控实验、轨迹级可观测性、确定性执行验证和 benchmark-guided Skill search 回答这些问题。

### 1.2 项目目标

1. 对同一 Case 自动运行 without-Skill / with-Skill 配对实验。
2. 记录任务成功率、质量分、Token、成本、时延、工具调用和稳定性。
3. 支持规则、脚本、LLM-as-a-Judge 及多 Judge 共识评分。
4. 自动分析轨迹并对失败进行可解释归因。
5. 从仓库、Issue、文档和人工种子自动生成 Benchmark 候选。
6. 评测 MCP 工具选择、参数、调用顺序、恢复和效率。
7. 评测 Memory/RAG 的检索、引用、抗污染和上下文效率。
8. 基于失败样本生成 Skill 候选，并通过 train、validation_search、validation_confirm、regression_dev 与一次性 locked-test batch 筛选和报告。
9. 支持 Skill v1/v2、模型 A/B 和配置变更的持续回归。

### 1.3 非目标

P0 阶段不实现：

- 自研基础模型训练；
- 大规模 Kubernetes 多租户平台；
- 企业级计费与复杂 RBAC；
- 保存或展示模型隐藏思维链；
- 无人工监督地把自动搜索结果直接发布为正式 Skill；
- 覆盖所有 Agent 框架；
- 重写 `skill-up` 已有的通用 Agent 安装、基础 Judge、基础 MCP 和报告引擎；
- 同时把 Benchmark Generator、Optimizer、MCP Lab 和 Memory/RAG Lab 全部做到生产级。

### 1.4 成功标准

P0 验收必须同时满足：

- 可导入一个 Skill 和不少于 10 个 Case；
- `SkillUpRunnerAdapter` 能调用固定版本 Runner，解析结果并通过契约测试；
- 可对每个 Case 运行两个不可变 Variant，包括 without/with 或 Skill v1/v2；
- 每次 Run 使用全新 workspace、HOME、Agent 配置和 Memory namespace；
- 至少支持 Mock Runner 和一个真实 `skill-up` Agent Engine；
- 复用并持久化 Expect、Script Judge 证据；
- 可查看 Runner 原始结果、可用的轨迹/文件差异/日志与评分明细；不可观察能力显式标记 unavailable；
- 可生成 HTML/JSON 实验报告；
- 先按 Case 聚合 repeats，再按 independence group 推断成功率、W/T/L、Token 和时延；
- 本地进程崩溃后可由 manifest 恢复，且原子提交不会产生两个活动逻辑结果；
- 核心流程具备自动化测试与一键本地演示。

### 1.5 分阶段范围

| 阶段 | 必须交付 | 明确不阻塞该阶段的内容 |
|---|---|---|
| P0 本地可信闭环 | Runner 防腐层、干净环境、不可变 Variant、12 Case Demo、Case 聚合/分组统计、确定性评分、静态报告 | FastAPI、Redis、Vue、LLM Judge、四个高级实验室 |
| P1 可查询平台 | SecureRuntime/SecureGrader、PostgreSQL、FastAPI、Redis Streams、fencing/Outbox、轨迹持久化、最小 Dashboard、Skill v1/v2 回归 | Kubernetes、多租户、完整 AgentOps 产品 |
| P2 研究纵深 | 自动 Benchmark + Skill Search 主线；MCP 与 Memory/RAG 二选一做可运行纵切 | 另一个模块只保留契约/fixture/roadmap；无人审核自动发布 |

若个人开发时间只有 8～10 周，简历主成果以 P0 + 可信实验 + 一个 P2 主线为准；P1 页面和分布式调度是可选加分，不得反过来挤占实验方法与真实结果。

---

## 2. 实验方法与指标体系

### 2.1 受控配对实验

同一个 `case_id` 在相同模型、温度、最大轮数、代码快照、镜像、网络策略和评分器下运行两次，唯一实验变量为 Skill：

```text
case_i ─┬─ baseline:  system + task
        └─ treatment: system + skill + task
```

随机种子可控时使用成对种子；不可控时每个条件重复 `n≥3` 次，并在同一 Case 内随机化 Variant 先后。模型无法提供共同随机数时，相同 `repeat_index` 只是实验 block，不意味着两次生成具有完全对应的随机轨迹。

每次 Run 必须使用全新 session、HOME、Agent 配置、Skill registry、workspace、MCP 状态和 Memory namespace，禁止读取宿主目录的全局 Skill、`AGENTS.md` 或历史会话。若冻结 fixture 本身声明项目级指令，该文件属于两组共享 Case 内容，必须纳入 Case hash。缓存、镜像预热和 provider 服务时段必须记录。

#### Skill 触发的两种协议

- `forced_use`：显式要求 Runtime 加载 Skill，评估 Skill 内容本身是否有效；
- `natural_trigger`：只安装 Skill，不在任务中提及 Skill 名称，评估发现、触发和误触发。

激活证据分级记录：`installed` 是 P0 必须提供的确定性证据；`discovered/read/activated` 仅在 Engine 暴露相应事件时记录；`followed` 只能由可观察行为推断。不可观察值保存 `unsupported/null`，不能记为 0 或作为跨 Engine 硬门槛。计算 natural-trigger Precision/Recall 时，DatasetVersion 必须预先标注 Skill 是否适用于该 Case。不同注入方式 `native_install/system_prompt/workspace_mount` 分层报告，不直接混合聚合。可选增加 length-matched placebo Skill，区分专业指导价值与“只是增加上下文”。

### 2.2 数据集拆分

- `train`：供 Skill Optimizer 查看任务、轨迹、评分和失败归因；
- `validation_search`：候选排序、successive halving 与自适应早停；
- `validation_confirm`：只对冻结 shortlist 运行一次，用于稳定选择但不提供确认性 CI；
- `regression_dev`：可重复运行的开发回归集，属于已见数据；
- `locked_test`：最终报告，只允许发起一次冻结评测批次；批次包含预注册 control 和唯一 winner，并各执行预注册 repeats；
- `challenge`：边界、扰动、工具故障、Memory 污染等压力样本。

禁止 Optimizer 访问 `locked_test` 的 prompt、标准答案、fixture、grader 源码、逐 Case 分数、轨迹、失败类别和切片结果。locked prompt/fixture/oracle 不进入公开仓库或 Optimizer workspace，最终 Worker 只按 opaque ID 从独立权限域拉取。若人员查看结果后继续修改 Skill，必须废弃该 test 版本并创建新的 locked test。

数据按 repository、fork lineage、时间、Issue/补丁家族分组后再切分，禁止同源代码族跨 split。

### 2.3 核心指标

先在 Case 内聚合 Run，再按最高独立抽样组进行推断。设 Case `c` 在 Variant `v` 下第 `r` 次运行的通过变量为 `y_cvr∈{0,1}`：

```text
p_cv = mean_r(y_cvr)
d_c = p_c,treatment - p_c,control
p_gv = mean_{c in group g}(p_cv)
d_g = mean_{c in group g}(d_c)
PassRate_v = mean_g(p_gv)          # 默认 equal-group-weighted
AbsoluteGain = mean_g(d_g)
RelativeGain = AbsoluteGain / PassRate_control  # 仅 control > 0
```

当 `PassRate_control=0` 时，RelativeGain 报告 `N/A`，只保留绝对百分点增益；禁止用任意 ε 生成夸大的相对提升。

点估计的目标总体与权重必须预注册。跨仓库泛化默认 `equal-group-weighted`，避免大仓库因 Case 多而支配结论；若产品真实流量要求 case-weighted/traffic-weighted，可显式选择，但点估计、bootstrap 和功效模拟必须使用同一权重。

配对结果按 Case 分类。实验启动前预先声明 repeats 如何形成 Case 级判定，默认用多数票仅作 W/T/L 展示，主统计保留 `p_cv` 与 `d_c`：

- Win：control 失败，candidate/treatment 成功；
- Tie+：两组均成功；
- Tie-：两组均失败；
- Loss：control 成功，candidate/treatment 失败。

必须报告 W/T/L、Case 数、Run 数与完整 block 比例，而不能只报告平均分。预注册主指标为 locked test 上的任务成功率绝对增益；Loss 数、Token、时延与成本为次指标。

### 2.4 效率与成本

记录：输入/输出 Token、缓存 Token、模型费用、总时延、首 Token 时延、工具调用数、无效调用数、容器 CPU 峰值和内存峰值。

```text
TokenOverhead = (Tokens_with - Tokens_without) / Tokens_without
LatencyOverhead = (Latency_with - Latency_without) / Latency_without
CostPerSuccess = TotalCost / SuccessfulRuns
```

任一分母为 0 时指标为 `N/A`。时延拆分为 queue、artifact/image preparation、Agent/provider、tool、grader 和 report，避免把平台排队误判为 Skill 推理开销。

默认使用 Pareto 前沿和硬约束进行决策，不强制生成单一综合分。如业务需要排序，可在实验配置中显式定义归一化方法和权重：

```text
Utility = 0.60 × Success + 0.20 × Quality
        + 0.10 × Robustness + 0.10 × Efficiency
```

权重属于实验配置，报告中必须展示，禁止以总分掩盖各维度。不同 provider/Agent 的 Token 口径、缓存语义和工具调用可见性不同，只在同一 comparability group 内直接比较；缺失值保存 `null`，不伪造为 0。

### 2.5 稳定性与统计

- repeats 只估计 Case 内随机性，不增加独立样本数；
- 若多个 Case 来自同一 repository/lineage/patch family，先抽最高层 independence group，再在组内抽 Case；仅当 Case 来源独立时才按 Case 整簇重采样；
- 使用 10,000 次 group/case hierarchical cluster bootstrap 计算 95% CI，并报告独立组数；
- McNemar 只用于“每个独立 Case 一个预定义二元结果”且不存在更高层聚类的设计；其他情况主结论使用 hierarchical cluster bootstrap，扩展研究可使用 mixed-effects logistic model/GEE；
- Token/时延/成本报告配对中位数差、比值、95% CI 和异常值，不只报均值；
- 多切片分析默认为探索性；正式多重检验时才用 Benjamini-Hochberg 控制 FDR；
- 除 p 值外必须报告效应量、Case 数、Run 数、CI 和预注册阈值。

确认性实验必须由功效模拟给出 `min_independent_groups`，并在启动前写入协议；实际有效组数不足时只报告描述性点估计和探索性区间，不作确认性提升声明。

默认主 estimand 是 assignment-based end-to-end effectiveness：预注册重试耗尽后的 invalid 按失败计；capability estimate（只用完整有效 PairBlock）作为敏感性分析，并单独报告 invalid 数及 Variant 间差异。若研究目标确实是纯模型能力，可以在启动前反向指定，但必须同时冻结最大 invalid 比例，不能看结果后切换口径。

样本分级：

- 10～12 个独立 Case × 3 repeats/arm：只用于工程 Demo；
- 约 30 个独立 Case × 3 repeats/arm：探索性实验，必须展示宽 CI；
- 首份可信报告的目标规模：不少于 50 个 locked Case、覆盖多个独立 repository/lineage group，×3 repeats/arm，共约 300 Runs；功效与 CI 仍按 group 结构估计。

50 不是通用充分样本量；正式研究应根据目标最小效应做功效评估。

---

## 3. 总体架构

### 3.1 逻辑架构

```text
┌────────────────────── Interaction ──────────────────────┐
│ P0 Local CLI / Static HTML          P1 Vue Dashboard    │
└───────────────────────────┬──────────────────────────────┘
                            │ CLI / REST / SSE
┌───────────────────────────▼──────────────────────────────┐
│ Experiment Control Plane                                │
│ Registry | Variant Compiler | Scheduler | Statistics    │
└──────────────┬──────────────────────────────┬────────────┘
               │                              │
     Local manifest/SQLite (P0)      PostgreSQL + Redis Streams (P1)
               │                              │
┌──────────────▼──────────────────────────────▼────────────┐
│ Python asyncio Worker / Distributed Worker              │
│ Pair Block | Lease + Fencing | Retry | Budget           │
└───────────────────────────┬──────────────────────────────┘
                            │ RunnerRequest / RunnerResult
┌───────────────────────────▼──────────────────────────────┐
│ SkillUpRunnerAdapter                                    │
│ pinned skill-up CLI → Agent/Runtime/Judges/MCP/Reports  │
└───────────────────────────┬──────────────────────────────┘
                            │ JSON / artifacts / OTLP
┌───────────────────────────▼──────────────────────────────┐
│ Differentiated Intelligence                             │
│ Regression | Trace Diagnosis | Benchmark-guided Search  │
│ Stateful MCP Lab | Memory/RAG Lab                       │
└──────────────────────────────────────────────────────────┘
```

P0 不要求启动 Web、Redis 和 PostgreSQL：本地 CLI 可直接调度 `asyncio` Worker，并将冻结清单、原始 Runner 结果和报告写入内容寻址目录。P1 再引入持久化控制面、Redis Streams、自定义 Worker 租约和实时页面。这样先验证实验闭环，再承担平台复杂度。

### 3.2 技术选型

| 层 | P0 | P1/P2 演进 | 选择理由 |
|---|---|---|---|
| Runner | `skill-up v0.5.0` CLI/JSON | 兼容矩阵与迁移器 | 复用成熟执行、评分、MCP 和报告能力 |
| 入口 | Typer CLI + Pydantic | FastAPI + Vue 3 | P0 先跑通，P1 再平台化 |
| 持久化 | 冻结 manifest + SQLite 索引 | PostgreSQL 16 + Alembic | 本地可重放，服务端可查询 |
| 调度 | Python `asyncio` + 本地并发限流 | 自研 Redis Streams Worker | 统一消费、ACK、租约和恢复语义，不混用 Celery |
| 沙箱 | 上游 `runner_default`（仅审核 fixture） | P1 SecureRuntime；P2 gVisor/Firecracker | 不把上游 Docker 误称为完整安全沙箱 |
| 轨迹 | Runner JSON/artifact + 可选 OTLP | PostgreSQL 索引 + Tempo | 保留上游原始证据并按能力矩阵关联 |
| 产物 | 内容寻址本地目录 | MinIO/S3 | P0 不增加对象存储服务 |
| 向量检索 | P0 不引入 | pgvector | 仅 Memory/RAG Lab 需要时启用 |
| 报告 | Runner HTML + 平台静态报告 | Vue 3 + TypeScript + ECharts | 避免首版重复造报告系统 |
| 模型出口 | Mock + 审核 fixture 的短期凭证模式 | Model Gateway + DLP/预算审计 | P0 真实演示受限，P1 沙箱不持有长期 Provider Secret |

### 3.3 仓库结构

```text
agentskill-eval/
├── apps/
│   ├── cli/                 # P0 本地实验入口
│   ├── api/                 # P1 FastAPI 控制面
│   ├── worker/              # P0 asyncio / P1 Streams Worker
│   └── web/                 # P1 Vue Dashboard
├── packages/
│   ├── contracts/           # Pydantic/JSON Schema
│   ├── runner_adapters/     # SkillUp/Mock Runner 防腐层
│   ├── experiment/          # Variant、PairBlock、统计和报告
│   ├── trace_intelligence/  # 轨迹归因
│   ├── benchmark_gen/       # Benchmark 自动生成
│   ├── skill_optimizer/     # Benchmark-guided Skill Search
│   ├── mcp_lab/             # 有状态场景与故障注入
│   └── memory_rag_lab/      # 检索、Memory 和消融评测
├── runner_compatibility/    # 固定版本、Schema 与 Golden Contract
├── migrations/
├── examples/
│   ├── skills/
│   └── datasets/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── deploy/
│   ├── docker-compose.yml
│   └── images/
├── docs/
├── pyproject.toml
└── README.md
```

禁止从 `skill-up` 复制 `internal/*` 实现。若未来需要替换 Runner，只需实现相同 `RunnerAdapter` 契约，而不改变平台领域模型。

---

## 4. 领域模型与数据设计

### 4.1 核心实体

1. `Skill` / `SkillVersion`：逻辑 Skill 与不可变内容版本；
2. `Dataset` / `DatasetVersion` / `EvalCase`：冻结数据集、上游 Case 和平台 sidecar 元数据；
3. `Experiment`：预注册协议、数据版本、预算和统计计划；
4. `ExperimentVariant`：不可变实验臂，保存 Skill、Agent、模型、工具、MCP、Memory/RAG、Runner 与沙箱的完整快照；
5. `PairBlock`：同一 Case、repeat 和 seed/order 下的一组 Variant，是调度与配对完整性单元；
6. `Run`：一个 Variant 在一个 PairBlock 中的一次逻辑执行；
7. `RunAttempt`：至少一次投递、重试或租约抢占产生的物理尝试；
8. `GradingRun` / `EvaluationResult`：可独立重跑且有谱系的评分批次与单 Judge 结果；
9. `TraceEvent` / `Artifact`：原始可观察轨迹与内容寻址产物；
10. `Diagnosis`：多标签失败假设、置信度、证据和人工裁决；
11. `EnvironmentFingerprint`：代码、Runner、CLI、模型、镜像、价格和依赖快照；
12. `BenchmarkJob` / `BenchmarkCandidate` / `Provenance`：Benchmark 候选及来源链；
13. `OptimizationJob` / `SkillCandidate` / `CandidateLineage`：搜索任务、候选和父子谱系；
14. `McpScenario` / `McpServerSnapshot` / `ToolCall`：有状态 MCP 场景、协议快照和调用证据；
15. `CorpusSnapshot` / `IndexSnapshot` / `RetrievalEvent` / `RelevanceJudgment`：Memory/RAG 冻结配置和分层真值。

`ExperimentVariant` 是实验可比性的核心，不能只在 `Run` 上保存 `baseline/treatment` 字符串。without-Skill、with-Skill、Skill v1/v2、placebo、模型 A/B、MCP schema 版本和 RAG 消融都统一表达为 Variant。

### 4.2 关键表

以下 PostgreSQL DDL 属于 P1。P0 使用同一 Pydantic 领域对象，但保存为冻结的 `experiment.json`、`variants/*.json`、`pair-blocks/*.json` 与 `runs/{pair_block}/{variant}/{attempt}/` 内容寻址目录；不得为了数据库尚未完成而改变 hash、状态或结果契约。

#### P0 文件契约

```text
workspace/experiments/{experiment_id}/
├── experiment.json                 # 协议、预算、统计计划、Dataset hash
├── variants/{variant_id}.json      # 不可变 Variant 配置指纹
├── pair-blocks/{block_id}.json     # Case、repeat、seed、执行顺序
├── runs/{run_id}/
│   ├── run.json                    # 逻辑状态、选中 Attempt/GradingRun
│   ├── attempts/{attempt_no}/
│   │   ├── attempt.json            # observed fingerprint、成本、错误
│   │   ├── raw-runner/             # 原始上游输出，禁止原地改写
│   │   └── artifacts/manifest.json # path、sha256、size、media_type、sensitivity
│   └── grading/{grading_run_id}.json
├── reports/
└── index.sqlite                    # 可删除重建的查询索引，不是真值源
```

`ExperimentManifest` 至少包含 schema_version、ID、创建时间、代码 revision、DatasetVersion、预注册协议/统计/预算和 Variant 引用；`RunManifest` 包含 PairBlock/Variant、run plan fingerprint、execution status、evaluation outcome、选中 Attempt、选中 GradingRun、逻辑结果 hash；`ArtifactManifest` 禁止绝对路径和 `..`，并记录敏感级别。

提交协议：先写同目录 `.tmp-{uuid}`，完成文件与父目录 `fsync` 后原子 `rename`；Run 指针只在 Attempt 全部落盘后更新。进程启动时扫描临时目录和未终态 Run，校验 hash 后恢复或隔离。SQLite 使用 WAL，但始终可从 manifest 重建。每次 Schema 迁移保留纯函数迁移器和 Golden fixture；P1 Importer 按稳定 UUID/hash 幂等导入，原始文件继续保留为审计源。

#### skill / skill_version

```sql
CREATE TABLE skill (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE skill_version (
  id UUID PRIMARY KEY,
  skill_id UUID NOT NULL REFERENCES skill(id),
  version TEXT NOT NULL,
  content_sha256 CHAR(64) NOT NULL,
  artifact_uri TEXT NOT NULL,
  manifest JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(skill_id, version),
  UNIQUE(skill_id, content_sha256)
);
```

SkillVersion 发布后不可原地修改；任何内容变化均生成新版本。

#### experiment / run / run_attempt

```sql
CREATE TABLE experiment (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  dataset_version_id UUID NOT NULL,
  protocol_snapshot JSONB NOT NULL,
  statistics_plan JSONB NOT NULL,
  budget_snapshot JSONB NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE experiment_variant (
  id UUID PRIMARY KEY,
  experiment_id UUID NOT NULL REFERENCES experiment(id),
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  runner_snapshot JSONB NOT NULL,
  agent_snapshot JSONB NOT NULL,
  skill_snapshot JSONB,
  tool_snapshot JSONB NOT NULL,
  memory_rag_snapshot JSONB,
  sandbox_snapshot JSONB NOT NULL,
  variant_fingerprint JSONB NOT NULL,
  variant_sha256 CHAR(64) NOT NULL,
  UNIQUE(experiment_id, name),
  UNIQUE(experiment_id, variant_sha256)
);

CREATE TABLE pair_block (
  id UUID PRIMARY KEY,
  experiment_id UUID NOT NULL REFERENCES experiment(id),
  case_id UUID NOT NULL,
  repeat_index INT NOT NULL,
  seed BIGINT,
  execution_order JSONB NOT NULL,
  UNIQUE(experiment_id, case_id, repeat_index)
);

CREATE TABLE run (
  id UUID PRIMARY KEY,
  experiment_id UUID NOT NULL REFERENCES experiment(id),
  pair_block_id UUID NOT NULL REFERENCES pair_block(id),
  variant_id UUID NOT NULL REFERENCES experiment_variant(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  execution_status TEXT NOT NULL CHECK (execution_status IN
    ('CREATED','QUEUED','LEASED','PREPARING','RUNNING','GRADING',
     'PERSISTING','RETRY_WAIT','CANCEL_REQUESTED','COMPLETED','INFRA_FAILED','CANCELLED')),
  evaluation_outcome TEXT CHECK (evaluation_outcome IN ('pass','fail','invalid')),
  final_score NUMERIC(6,4),
  lease_generation BIGINT NOT NULL DEFAULT 0,
  active_attempt_id UUID,
  active_grading_run_id UUID,
  run_plan_fingerprint JSONB NOT NULL,
  selected_attempt_sha256 CHAR(64),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  next_attempt_at TIMESTAMPTZ,
  max_attempts INT NOT NULL DEFAULT 3,
  queued_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  UNIQUE(pair_block_id, variant_id)
);

CREATE TABLE run_attempt (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(id),
  attempt_no INT NOT NULL,
  lease_generation BIGINT NOT NULL,
  fencing_token UUID NOT NULL,
  worker_id TEXT,
  claimed_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  sandbox_ref JSONB,
  observed_fingerprint JSONB,
  error_code TEXT,
  error_detail JSONB,
  UNIQUE(run_id, attempt_no),
  UNIQUE(run_id, lease_generation),
  UNIQUE(fencing_token)
);

ALTER TABLE run ADD CONSTRAINT fk_active_attempt
  FOREIGN KEY (active_attempt_id) REFERENCES run_attempt(id)
  DEFERRABLE INITIALLY DEFERRED;
```

`variant_fingerprint` 只记录实验臂级内容：平台/Runner/Agent CLI 版本、模型与生成参数、Skill、工具、MCP、Memory/RAG、Sandbox 和价格表快照。`run_plan_fingerprint` 记录 Case/Grader hash、seed、`platform_compiled_prompt_hash`、编译后的上游配置 hash 和 image digest。`observed_fingerprint` 属于 Attempt，尽可能记录实际 provider/deployment/revision、request ID、区域、启动时间和运行时依赖；Engine 不暴露的字段保存 `null` 与 unavailable reason。Run 另保存选中 Attempt 的摘要 hash。托管模型只能声明“可审计、可重新运行”，不能承诺 bitwise reproducibility。

#### trace_event / evaluation_result

P0 不对 `trace_event` 分区，先保证 DDL 与约束可直接运行。正文、大日志和二进制产物写入内容寻址存储，数据库仅保存结构化索引与 URI；达到容量阈值后再以包含分区键的合法复合主键迁移。

```sql
CREATE TABLE trace_event (
  id UUID PRIMARY KEY,
  run_attempt_id UUID NOT NULL REFERENCES run_attempt(id),
  sequence_no BIGINT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  duration_ms INT,
  payload JSONB NOT NULL,
  artifact_uri TEXT,
  UNIQUE(run_attempt_id, sequence_no)
);

CREATE TABLE grading_run (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(id),
  parent_grading_run_id UUID REFERENCES grading_run(id),
  grading_config_snapshot JSONB NOT NULL,
  trigger_reason TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evaluation_result (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(id),
  grading_run_id UUID NOT NULL REFERENCES grading_run(id),
  judge_type TEXT NOT NULL,
  judge_version TEXT NOT NULL,
  score NUMERIC(6,4),
  passed BOOLEAN,
  evidence JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE run ADD CONSTRAINT fk_active_grading_run
  FOREIGN KEY (active_grading_run_id) REFERENCES grading_run(id)
  DEFERRABLE INITIALLY DEFERRED;
```

`grading_run` 保存父评分批次、触发人、rubric/grader/Judge 模型 hash 和原因。`:regrade` 创建新批次，不覆盖旧结果，也不重新执行 Agent。`run.final_score/evaluation_outcome` 只是 `active_grading_run_id` 的查询投影；切换活动批次必须显式审计，报告也必须写明使用哪个 grading run。

### 4.3 状态机

```text
CREATED → QUEUED → LEASED → PREPARING → RUNNING
       → GRADING → PERSISTING → COMPLETED

任意执行态 → RETRY_WAIT → QUEUED
可取消执行态 → CANCEL_REQUESTED → CANCELLED
耗尽可恢复重试 → INFRA_FAILED
```

`execution_status` 描述平台执行生命周期；`evaluation_outcome` 只取 `pass/fail/invalid`。Agent 正常结束但验收失败是 `COMPLETED + fail`，不是基础设施失败。任务预算内超时默认计 `COMPLETED + fail`；Provider 429、平台网络故障或镜像拉取失败计 `INFRA_FAILED + invalid`；故障注入 Case 中预期的 MCP 超时属于任务环境，不自动视为平台故障。分类规则必须在实验启动前冻结。

`run.lease_owner/lease_expires_at/lease_generation` 是活动租约唯一事实源；Attempt 只保留 claim 历史和 observed fingerprint。领取时原子递增 generation、设置活动 Attempt/owner/expiry 并生成 fencing token；续租和终态提交都校验旧状态、Attempt、generation、owner 和未过期租约。取消先进入 `CANCEL_REQUESTED`，Worker 确认进程树终止并清理资源后才能进入 `CANCELLED`。

Reaper 分支必须明确：普通过期 Run 在未超过 `max_attempts` 时进入 `RETRY_WAIT + next_attempt_at`，否则进入 `INFRA_FAILED`；尚未领取的 `CANCEL_REQUESTED` 可直接转 `CANCELLED`；已领取且租约过期的取消任务先 fence 旧 Attempt，由 Janitor 根据保存的 sandbox/container ID 清理后再转 `CANCELLED`。`CANCEL_REQUESTED` 永不进入重试队列。过期 Worker 可能继续产生外部成本，但其结果不得覆盖新 Attempt。

被 fencing 的 Attempt 已上传产物进入 `orphaned` 清单，后台 GC 在审计保留期后按引用计数清除。平台同时报告：

- primary conservative intention-to-treat：所有已分配 Variant，预注册恢复耗尽后的 invalid 按失败计；
- sensitivity capability estimate：只用完整有效 PairBlock，另报 invalid/缺失率及组间差异；
- physical attempts 与 duplicate cost：不把“逻辑幂等”误写成“绝不重复执行”。

---

## 5. 配置 DSL 与接口契约

### 5.1 Experiment YAML

```yaml
schema_version: ase/v1alpha1       # 平台 Schema，不是 skill-up Schema
experiment:
  name: code-review-skill-v1
  confirmatory: false
  repeats: 3
  randomize_order: true
  protocol: natural_trigger
  primary_metric: task_success_absolute_gain
runner:
  type: skill_up
  version: v0.5.0
  commit: 21618623c159b4dbb66e51098dbd669427bb00b8
  eval_path: evals/eval.yaml
  upstream_benchmark_enabled: false
  explicit_defaults:
    max_turns: 10
    timeout_seconds: 600
    retry_policy: {max_retries: 0, retry_on: []}
dataset:
  name: python-review-bench
  version: 1.0.0
variants:
  - name: without-skill
    role: control
    skill: null
  - name: code-review-v1
    role: treatment
    skill:
      artifact: skills/code-review-v1.tar.zst
      sha256: "..."
pairing:
  block_by: [case_id, repeat_index]
  require_complete_block: true
statistics:
  primary_sampling_unit: independence_group
  nested_unit: case_id
  estimand_weighting: equal_group
  bootstrap: hierarchical_cluster
  bootstrap_resamples: 10000
  confidence_level: 0.95
budget:
  max_total_cost_usd: 50
  max_wall_time_minutes: 240
```

平台编译器把每个 Variant 转换为固定的上游配置与独立运行目录，再调用 Runner。由于 `skill-up` 的文档和实现曾出现默认值差异，平台必须显式写出 `max_turns`、timeout 和重试设置，禁止依赖隐式默认值。

### 5.2 Case YAML

Case 的执行语义直接采用 `skill-up v0.5.0` 的 `v1alpha1` DSL，包括 prompt、fixture、Git、多轮会话、expect、judge 和 artifact 配置。本项目不再发明一套相似但不兼容的 Case YAML。上游 MCP 位于 EvalConfig 而不是 CaseConfig；平台 sidecar 只引用 `mcp_scenario_id`，Adapter 为每个场景生成独立临时 `eval.yaml`，或在 P2 通过平台 MCP Proxy 注入状态。

平台只维护官方 DSL 没有表达的 sidecar：

```yaml
schema_version: ase-case-meta/v1alpha1
case_ref: evals/cases/python-null-check-001.yaml
split: validation_search
group_keys:
  independence_group: example/project#null-check-family
  repository: example/project
  fork_lineage: example/project
  patch_family: null-check/user-service
  time_bucket: 2026-Q2
provenance:
  source_type: curated_private
  source_commit: "..."
  license: Apache-2.0
  contamination_risk: low
oracle:
  grader_sha256: "..."
  gold_evidence_uri: encrypted://...
tags: [bug_fix, medium]
```

多轮任务、轮间 gate 和工具断言原样传给上游 Runner。平台 sidecar 不得改变 Case 执行语义；任一编译产物都需保存 hash 供审计。

### 5.3 Runner Adapter

```python
class RunnerAdapter(Protocol):
    async def validate(self, request: RunnerRequest) -> ValidationReport: ...
    async def execute(
        self,
        request: RunnerRequest,
        event_sink: TraceEventSink,
    ) -> RunnerResult: ...
    async def cancel(self, execution_id: str) -> None: ...
    def compatibility(self) -> RunnerCompatibility: ...
```

`SkillUpRunnerAdapter` 负责生成/校验上游配置、创建干净 HOME、执行固定二进制、解析结构化 JSON/artifact 并归档原始目录。HTML 只是派生视图，归档但不作为解析真值；OTLP 由 Collector 接收，不当作普通报告文件。`RunnerResult` 只包含可观察结果：最终消息、退出原因、Token、耗时、可用的工具事件、产物和错误；不可观察字段为 `null/unavailable`，不要求模型暴露隐藏思维链。

Codex、Claude Code、Qoder CLI、Qwen Code 和自研 Agent 的差异由 `skill-up` 内置 Engine 或其 local/HTTP Custom Engine 契约处理。平台只有在上游契约无法表达研究需求时才新增 Runner，而不是再维护一套通用 Agent Adapter。

### 5.4 Evaluator

```python
class Evaluator(Protocol):
    name: str
    version: str

    async def evaluate(self, context: EvalContext) -> EvalResult: ...
```

Evaluator 必须返回分数、通过状态、机器可读证据、用户可读理由和可重放版本信息。

P0 的 Expect、Rule、Script 和 `RunnerAgentJudge` 可直接复用 Runner，但后两者必须标记 shared-runtime 边界；平台 Evaluator 负责上游结果标准化、跨 Run 聚合。严格 blind pairwise Judge、Judge 校准、人工复核和 regrade 谱系由 P1 `IsolatedLLMJudge` 实现。

### 5.5 Runner 兼容契约

每个受支持版本保存二进制 SHA256 和一组 Golden Contract：`--version`、配置校验、case 列表、最小成功/失败/超时/MCP/多轮执行，以及 `result.json`、`grading.json`、`eval_metadata.json` 和目录布局。Parser 必须：

- 对未知字段前向兼容并原样归档；
- 对 Token、成本、工具排序等不可观察字段使用 `null`；
- 区分 CLI 非零退出、Case fail、Judge error 与报告缺失；
- 读取逐 Case JSON 决定结果，不以进程退出码推断所有 Case 成败；
- 验证 artifact 路径不能逃逸运行目录；
- 只有在兼容测试全部通过后才允许升级 pinned Runner。

---

## 6. 执行引擎与沙箱

### 6.1 Worker 流程

1. Experiment Planner 冻结 Variant、PairBlock、Case sidecar、预算与统计计划；
2. P0 从本地计划领取 Run，P1 从 Redis Streams 消费组领取；
3. P0 创建 RunAttempt 并持有本地独占锁；P1 原子递增 lease generation 并获得 fencing token；
4. 建立全新 HOME、XDG、Agent 配置、Skill registry、workspace、MCP 状态和 Memory namespace；
5. 校验 Skill、fixture、grader、Runner 二进制和镜像 digest；
6. `SkillUpRunnerAdapter.validate()` 生成并校验固定的上游配置；
7. Adapter 调用上游 Runner 执行 Agent、Runtime、Expect/Judges 与 artifact 收集；
8. 保存 Runner 原始 JSON、日志与 OTLP，并转换为平台事件；超时或取消时终止完整进程树；
9. 平台执行额外的配对 Judge、轨迹诊断或研究模块评分；
10. P0 通过 manifest 原子 rename 提交；P1 仅允许有效 fencing token 事务提交终态与 Outbox；
11. 清理容器、临时凭据、网络和 namespace；P1 再 ACK 消息。

P0 不重新实现 Runner 内部的 workspace 准备、Skill 安装和基础评分流水线。平台只负责“实验编排前”和“结果持久化后”的差异化逻辑。

### 6.2 幂等、租约与重试

`idempotency_key = sha256(pair_block_id + variant_id)`。P0 用本地独占锁、Attempt 目录和原子 RunManifest 指针恢复；P1 Worker 采用至少一次投递，因此业务层只能保证逻辑提交幂等，不能保证昂贵外部调用绝不重复。以下租约规则属于 P1：

- 心跳周期：10 秒；
- 默认租约：60 秒；
- Reaper 仅在 `lease_expires_at + grace_period` 后回收，避免心跳和租约判据冲突；
- 基础设施错误可指数退避重试 2 次；
- 任务本身失败不自动重试，除非实验要求重复采样；
- Judge 错误可单独重跑评分，不重新执行昂贵 Agent Run。

Claim 在一个事务中使用 `FOR UPDATE SKIP LOCKED` 选择到期的 `QUEUED/RETRY_WAIT` Run，创建 Attempt，并递增 generation、写入 owner/expiry/active Attempt；消息本身不授予执行权。Renew 只允许当前 owner 在租约未过期时延长 expiry。Finalize 使用类似以下条件：

```sql
UPDATE run
SET execution_status = 'COMPLETED', finished_at = now()
WHERE id = :run_id
  AND active_attempt_id = :attempt_id
  AND lease_generation = :generation
  AND lease_owner = :worker_id
  AND lease_expires_at > now();
```

同一事务还校验 Attempt 的 `fencing_token` 并写入 selected Attempt hash、活动评分批次和 Outbox。条件失败表示结果已被 fencing，不得再次发布 artifact 索引或实验指标。Reaper 通过同样的 generation 条件把过期 Run 转入 `RETRY_WAIT/INFRA_FAILED`，禁止无条件覆盖。外部模型费用仍记录到对应物理 Attempt 的 duplicate cost。

### 6.3 Sandbox Profile

`runner_default`（P0）真实复用 `skill-up v0.5.0` 的 Docker/OpenSandbox 能力：固定 image digest、environment、entrypoint、workspace 和可选 `network:none`。它主要提供环境一致性，不具备本文其余强隔离保证，因此只运行人工审核的 Demo fixture 和公开 smoke grader。

`secure_external`（P1）由平台自研 SecureRuntime 在外层启动固定 Runner，额外实现：

- 非 root、`cap-drop=ALL`、禁止 privileged、seccomp/AppArmor；
- CPU、内存、PID、文件大小和执行时间限制；
- 只读根文件系统与独立可写 workspace；
- Agent 容器与 SecureGrader 容器分离，隐藏测试/gold oracle 不进入 Agent mount；
- 无模型调用默认禁网；真实 Agent 仅访问 Gateway 和声明的 MCP endpoint；
- 容器、volume、网络和临时凭据的确定性清理。

`strong_untrusted`（P2）使用 gVisor/Firecracker 或等价边界。Docker/OpenSandbox 不等同于强安全沙箱；域名白名单也不能阻止经允许端点的数据外泄，Gateway 还需限制请求体、模型、速率、预算和审计字段。报告必须写明每个 Run 的 SandboxProfile，禁止把 P1 设计目标描述成 P0 上游现有能力。

### 6.4 Skill 注入

Baseline 不应获得 Skill 的正文、名称或暗示。每次 Run 必须创建全新 `$HOME`、`XDG_CONFIG_HOME`、Agent 配置目录和 Skill registry，禁止挂载宿主的全局 Skill、`AGENTS.md`、缓存会话或用户配置。Treatment 使用 Runtime 的原生 Skill 安装机制；若 Runtime 不支持，才使用系统提示拼接兼容层。报告必须标记注入方式：`native_install`、`system_prompt` 或 `workspace_mount`。

为保证公平，两组的公共系统提示、工具、环境和任务文本完全一致；Skill 产生的上下文开销计入 treatment Token。

平台按上述可观察性分级保存 Skill 激活证据。`natural_trigger` 的主要结果按分配 Variant 进行 intention-to-treat 分析，实际触发率、触发 Precision/Recall 和负向 Case 误触发率作为诊断；不得只挑选“成功触发”的 Run 报告效果。

### 6.5 Variant 到 Runner 的映射

- 标准 without/with、Skill v1/v2 和多因素消融一律由平台按 PairBlock 的随机顺序生成独立上游配置；
- 上游 `benchmark.enabled` 固定为 `false`、iterations 固定为 1；一个 Runner 调用只生成一个 Variant 的一个逻辑 Run；
- baseline 编译结果必须显式写 `skills: []`；不能省略该字段，因为上游在特定目录布局下可能隐式发现本地 `SKILL.md`；
- 每个临时配置只安装该 Variant 的 Skill、工具和 Memory 快照，repeats 由平台逐次调度；
- Skill artifact 先校验 hash，再按安全解压规则 materialize，确认 manifest/`SKILL.md` 后编译成上游 `source: local_path`；上游不直接接收 `tar.zst`；
- 上游 `cases.retry_policy.max_retries` 固定为 0，所有物理重试由平台 RunAttempt 统一拥有和计费；
- 单 Case 调度：临时 manifest 只引用目标 canonical Case，不修改源 Case；编译产物和 hash 随 Run 归档；
- 每个 Variant 都从同一 fixture 快照重新创建 workspace，禁止将上一臂文件修改传递给下一臂。

### 6.6 Engine 能力与凭证引导

每个 Engine 注册 `EngineCapability`：认证模式、是否支持隔离 HOME、是否支持自定义 API Base/HTTP Proxy、可观察 Token/工具字段、取消语义和安全等级。真实 Engine 的 Golden Contract 必须覆盖“空 HOME 启动、凭证引导、模型调用、取消和清理”。

禁止复制整个宿主 HOME。P0 仅对人工审核 fixture 提供 `trusted_demo`：将 allowlist 中的最小认证材料或用户创建的短期 Token 写入私有 tmpfs，并在 Run 后销毁；无法隔离认证材料的 Engine 标记 `trusted_only`，不得执行外部不可信代码。

P1 Model Gateway 契约：

```text
Run lease → issue scoped gateway token
Agent CLI → provider-compatible base URL / HTTPS proxy
Gateway → validate run_id, model allowlist, body size, DLP, rate/cost budget
Gateway → inject provider credential, forward, record request_id/usage, redact logs
```

沙箱网络只允许 DNS、Gateway 和场景声明的 MCP endpoint。Scoped token 绑定 `run_id + provider + model + budget + expiry`；Gateway 拒绝任意目标 URL、超预算请求和高风险敏感字段。若某 CLI 不支持 Gateway/Proxy，报告必须标记降级安全配置，不能宣称达到不可信沙箱等级。

---

## 7. Evaluation Engine

### 7.1 评分流水线

```text
Execution Result
  → skill-up Expect / Rule / RunnerScriptJudge / RunnerAgentJudge
  → Platform SecureGrader（需要隐藏 oracle 时，P1）
  → Platform Result Normalization
  → Blind Pairwise Judge（按需）
  → Calibration / Consensus / Human Review
  → Pass/Fail + Evidence
```

P0 基础单 Run 评分复用 `skill-up v0.5.0`。需要隐藏 oracle 或强隔离时使用平台 P1 `SecureGraderRunner`；评分治理、成对盲评、可独立 regrade、Judge 可靠性度量和跨 Variant 聚合仍由平台负责。

### 7.2 Expect Gate

复用 Runner 的确定性 Expect：退出码、文件存在/不存在、文本包含/不包含、golden/file_contains 等；`tool_called` 和轮级工具断言属于上游 `rule_based` Judge，不归入 Expect。平台可在结果规范化阶段增加 JSON Schema、允许/禁止改动路径、最大时延和预算断言。Expect 失败时跳过后续昂贵 Judge，但必须保留失败证据。

### 7.3 Script Judge

`RunnerScriptJudge` 真实遵循 v0.5.0：在 Agent 同一个 Runtime/workspace 执行，只以退出码 0/非 0 判定，stdout 只是字符串 evidence，不解析结构化分数。它只能用于 Agent 可见、非敏感的 smoke grader；报告必须标记 `isolation=shared_runtime`，不能声称隐藏测试安全。

`SecureGraderRunner` 是平台 P1 自研能力：先只读快照 Agent 最终 workspace，再在独立、无模型、无网络的 grader 容器挂载隐藏 oracle。平台校验 grader hash，并约定结构化标准输出：

```json
{
  "passed": true,
  "score": 0.92,
  "summary": "hidden tests: 11/12",
  "evidence": [{"name": "testNullUser", "passed": true}]
}
```

脚本必须有版本和哈希。测试不得依赖外网和时间随机性。测试泄漏、修改 tests 或伪造测试输出都判失败。P0 未实现 SecureGraderRunner 前，只能称工程 Demo，不能把公开 smoke grader 当作保密 oracle。

### 7.4 LLM-as-a-Judge

`RunnerAgentJudge` 真实复用上游 v0.5.0：与被测 Agent 共用 Agent/Runtime，可能看到已安装 Skill、MCP 和网络能力；其 JSON 是 prompt 约定后解析，不是 provider 级强制 Schema。因此它只用于非敏感 smoke 评分，并标记 `isolation=shared_runtime, output_contract=prompt_json`，不得用于正式盲评或安全结论。

`IsolatedLLMJudge` 是 P1 平台能力，只用于难以机械验证的语义维度。它运行在全新 Runtime，不安装被测 Skill/MCP、无工具、无任意网络和 Secret，只能经 Model Gateway 调 Judge 模型。输入包括任务、rubric、最终结果、必要证据和经过截断/脱敏的轨迹，不包含 Variant、Skill 名称或条件标签。

要求：

- Provider 支持 structured output 时使用 provider 级 JSON Schema；否则本地严格校验，失败即 Judge error，不把修复后的任意文本当有效分数；
- 使用最低可用温度，但明确温度 0 不代表确定性；
- rubric 每个维度有锚点示例；
- Judge 版本、模型、提示哈希可追溯；
- Judge 无工具、无网络、无 Secret，只能读取脱敏输入；数据边界只是提示结构，不被当作安全边界；
- 与生成 Agent 尽量使用不同模型家族，并记录 `same_family`；
- 对边界分数或 Judge 冲突样本进入人工复核队列。

Judge 模式必须分开：pointwise rubric 对单 Run 给出绝对质量维度和门槛；blind pairwise 只回答 A/B/tie 的相对偏好。比较两个 Variant 时随机映射为 A/B，再用 B/A 逆序复评；顺序翻转后结论不一致则记 tie 或进入人工复核，不能挑选有利顺序。pairwise win 不得转换为单 Run `pass` 或 `semantic_score`。被评输出中的指令全部视为不可信数据。

### 7.5 多 Judge 共识

P1 将人工数据分成 `judge_dev` 与从未参与 rubric/prompt 调参的 `judge_locked_audit`。Demo 建议 dev 30～50 条并另留至少 30 条 audit；正式报告的 locked audit 目标 50～100 条、两位标注者和分歧仲裁。最终可靠性数字只来自 audit。Judge 校准报告：

- pass/fail：balanced accuracy、Precision、Recall、F1 和混淆矩阵；
- 连续分数：MAE、Spearman，适用时报告 ICC；
- 序数等级：weighted kappa；
- pairwise：位置翻转率、tie 率和重复一致率；
- prompt-injection attack success rate。

Cohen's kappa 只用于合适的分类标签。Judge 自报 `confidence` 只是解释字段，未经可靠性图或校准误差验证时不能称为校准置信度。确定性结果与 LLM 冲突时，客观正确性以确定性结果为主，LLM 只保留质量子分。

确认性实验使用 Judge 前必须预注册 `JudgeReliabilityGate`：audit 样本按正式任务类别/难度分层抽样；为 relevant metric 设最低 CI 下界和最大位置翻转/攻击成功率。若 locked audit 未达门槛，pointwise/pairwise 结果不得进入任务 pass 或主要结论：优先退回确定性评分，其次使用双人盲标加仲裁；若语义维度不可替代，则标记 `semantic_evaluation_invalid` 并停止确认性声明，而不是降低阈值。

### 7.6 聚合

Case 的通过条件应显式配置，例如：

```text
passed = expect_pass
      AND runner_script_pass
      AND secure_grader_score >= 0.8   # 仅启用 SecureGrader 时
      AND pointwise_semantic_score >= 0.6
```

不能用平均分让关键安全检查被其他高分抵消；blind pairwise 结果只作为相对质量指标，不参与上述绝对通过条件。`pointwise_semantic_score` 只有在对应 JudgeReliabilityGate 通过时才有效。

聚合规则、阈值、Judge 版本和冲突处置必须在实验启动前冻结。regrade 可用于 Judge 升级对比，但新旧评分分开报告，禁止静默改写历史实验结论。

---

## 8. Trace Intelligence 与失败诊断

### 8.1 统一事件模型

平台统一事件模型可以容纳：

- `session.started/finished`；
- `message.received/emitted`；
- `tool.requested/started/completed/failed`；
- `file.read/created/modified/deleted`；
- `command.started/completed`；
- `test.started/completed`；
- `retrieval.requested/result`；
- `memory.read/write`；
- `judge.started/completed`；
- `resource.sampled`。

每个事件带 trace_id、span_id、parent_span_id、sequence_no、时间、状态、摘要和 artifact URI。

但 `skill-up v0.5.0` 不保证通用 diagnostic JSONL，也不会在 OTLP 中统一暴露 prompt、文件内容或每个 CLI 的工具细节。每个 AgentProfile 必须声明 `TraceCapability`：

| 层级 | 来源 | 可用性要求 |
|---|---|---|
| Post-run result/artifact | Runner JSON 与输出目录 | P0 required |
| 高层 Runner/Runtime/Judge span | 上游 OTLP → Collector | supported/unsupported |
| 工具/文件/命令事件 | Agent 原生 telemetry 或 Custom Engine | observed/inferred/unavailable |
| MCP/RAG/resource 事件 | 平台 Proxy/Lab | 对启用该 Lab 的 Variant required |

缺失事件保存 `unavailable`，不伪造空序列。实时轨迹属于 P1：通过 OTLP Collector、Agent 原生流或平台 Custom Engine/Proxy 接入；只有 post-run artifact 的 Engine 只能在结束后展示轨迹摘要。

### 8.2 诊断分类

失败分类采用多标签层级体系，并始终允许 `UNKNOWN` 与 `ABSTAIN`，避免强迫模型给出单一原因。一级标签：

1. `TASK_UNDERSTANDING`：误解目标或约束；
2. `PLANNING`：步骤缺失、顺序错误、未重规划；
3. `TOOL_SELECTION`：工具选错或遗漏；
4. `TOOL_ARGUMENT`：参数、路径、SQL 等错误；
5. `TOOL_RECOVERY`：失败后重复、未回退；
6. `RETRIEVAL`：召回失败、排序错误、证据缺失；
7. `MEMORY`：错误写入、过期记忆、污染影响；
8. `SKILL_CONFLICT`：Skill 与任务、版本或 Runtime 冲突；
9. `VERIFICATION`：修改后未测试或误读测试；
10. `ENVIRONMENT`：依赖、网络、权限或沙箱故障；
11. `BUDGET`：Token、轮数或时间耗尽；
12. `JUDGE`：评分器失败或冲突。

`ENVIRONMENT/JUDGE` 首先用于解释 `invalid`，不得与 Agent 能力失败混在主成功率中。一个 Case 可以同时标记 `RETRIEVAL + VERIFICATION`，并区分 `root_cause`、`contributing_factor` 与 `observed_symptom`。

### 8.3 归因方法

采用三层策略：

1. 规则：超时、工具返回码、重复调用、未运行测试等确定性特征；
2. 统计：与成功轨迹比较步骤数、工具序列、重试率和检索质量；
3. LLM Analyzer：对剩余复杂轨迹提出分类假设、置信度和引用事件；
4. 人工裁决：处理高价值、低置信度或规则/模型冲突样本。

诊断结果必须引用稳定的 evidence ID、事件序号和工具错误，不得把隐藏思维过程或 LLM 解释当作事实。LLM 归因是可证伪的 hypothesis，而不是因果结论。

### 8.4 轨迹比较

配对 Run 支持并排时间线、工具序列 diff、文件 diff、检索文档 diff 和成本瀑布图。通过序列编辑距离和阶段聚类识别 Skill 导致的新增/减少步骤。

### 8.5 诊断质量验收

建立按失败类型分层的人工标注集，至少报告 micro/macro F1、每类 Precision/Recall、混淆矩阵、abstain coverage 和 evidence citation accuracy。若要声称 Skill 导致某类行为变化，必须增加去除 Skill 条目、替换检索结果、模拟工具恢复等受控消融；仅凭两条轨迹的相关差异不能称为因果归因。

---

## 9. 自动 Benchmark 生成

### 9.1 数据来源

- 新近创建的公开仓库 Issue 与对应 commit/PR，并显式标记预训练污染风险；
- 经授权的私有历史缺陷和人工构造、人工验证的 fresh task；
- 项目历史回归 Bug；
- 文档、FAQ 与操作手册；
- MCP Server 的工具 schema；
- RAG 知识库中的事实与冲突文档；
- 人工编写的高质量 seed case。

### 9.2 生成流水线

```text
Source Ingestion
 → Candidate Mining
 → Task Reconstruction
 → Fixture Freezing
 → Oracle/Grader Generation
 → Validation
 → Deduplication
 → Difficulty Calibration
→ Human Review
→ Dataset Version Publish
```

状态机为 `INGESTED → RECONSTRUCTED → VERIFIED → DEDUPED → REVIEWED → PUBLISHED/REJECTED`。每次转换保存输入、生成器、验证器、prompt hash、代码 commit 和人工 reviewer，不允许只保留最终 YAML。

### 9.3 软件工程 Case 重建

从“修复前 commit”创建 fixture，从“修复后 commit”提取补丁作为参考但不提供给 Agent。优先复用项目原有测试；若无测试，生成最小回归测试并由独立验证器确认：

1. 测试在修复前失败；
2. 测试在修复后通过；
3. 测试不依赖网络与时间；
4. 删除参考补丁后仍能稳定复现；
5. Agent 无法从测试文件直接读取答案。

验证器还需运行 mutation/coverage 检查，确认测试能杀死关键错误而不会只接受参考补丁。至少用一个替代人工/Agent 解尝试通过，避免验收过窄、误拒功能等价实现。正式数据集的 Generator、Verifier 和语义 Judge 使用不同模型家族；无法做到时记录 `same_family` 并标为非独立。最终被测 Agent 不参与任务生成或收录筛选。

### 9.4 任务生成与质量门

生成器输出 task、fixture、oracle、grader、分类、难度和 provenance。只有满足以下条件才进入候选集：

- 可复现；
- 可自动验收；
- 无敏感数据和许可证风险；
- 描述不泄露实现答案；
- 与现有 Case 语义/代码重复度低；
- oracle 对多种等价解有效，且 mutation/coverage 达到预设门槛；
- split、污染风险、许可证和 provenance 完整。

不得根据最终被测 Agent 的 0%/100% 表现决定是否收录，否则会产生选择偏差并可能放大 Skill 增益。极易和极难 Case 可以进入独立难度桶或诊断桶；难度校准只能使用与正式被测对象隔离的 pilot 模型池。

DatasetVersion 发布前冻结 mutation operator 集、最低 mutation score、coverage 条件、替代解数量和验收规则；看过候选 Agent 结果后不得调整质量门或删除不利 Case。

### 9.5 去重与污染控制

结合文本 embedding、代码 AST 指纹、patch overlap、Issue 模板和仓库/fork lineage 去重。按 repository、fork lineage、发布时间、Issue/补丁家族分组切分，禁止同源任务跨 split。记录来源 URL、发布时间、commit SHA、许可证、生成模型、生成 prompt hash、验证器和人工 reviewer。

`locked_test` 的 oracle 使用独立权限域；加密只能防止 Optimizer 直接读取，不能消除公开 GitHub 数据已进入模型预训练的污染。优先用新近、私有或 fresh synthetic-but-human-verified 任务，并在报告中按 contamination risk 分层。

### 9.6 难度校准

按独立 pilot 模型池的成功率、平均工具步数、跨文件数量和所需环境操作划分 easy/medium/hard；pilot 模型及结果不用于最终系统间优劣结论。难度是经验标签，不由 LLM 单次主观决定，也不要求只保留中间难度。

### 9.7 数据实体与最小纵切

`benchmark_job` 保存来源、预算和状态；`benchmark_candidate` 保存重建任务、fixture/oracle hash、质量门和拒绝原因；`provenance` 保存来源与完整生成/验证链；发布后生成不可变 `dataset_version`。

```yaml
BenchmarkJobSpec:
  source_refs: ["github:owner/repo@commit"]
  target_split: validation_search
  generator_profile: generator-v1
  verifier_profile: verifier-v1
  quality_gate: {before_fail_after_pass: true, mutation_score_min: 0.7}
  max_candidates: 20
  budget: {cost_usd: 20, wall_minutes: 120}
BenchmarkJobResult:
  published_dataset_version: null
  accepted_candidate_ids: []
  rejected: []
  total_cost_usd: 0
```

取消后不再启动新候选，正在执行的验证步骤在安全点终止；重试以 `candidate_id + stage + input_hash` 幂等，已通过的重建/mutation 产物不重复生成。预算超限进入 `BUDGET_EXHAUSTED`，不是质量失败。

最小纵切验收：从一个许可明确的仓库固定两个历史缺陷，自动重建 fixture，验证 before-fail/after-pass，运行 mutation test 与一个替代修复，通过人工 review 后发布一个 DatasetVersion。P2 扩展前必须先证明这条链路可审计，而不是只展示 LLM 生成 YAML。

### 9.8 MVP 实现基线（2026-07-13）

当前实现已将上述最小纵切冻结为 CLI 与持久化契约：

- `benchmark generate/status/review/publish` 分离自动生成和人工发布权限；
- 每个候选保存逐转换不可变快照、阶段输入/输出哈希、命令证据和拒绝原因；
- 通过 Git tree/blob 安全重建 before/after fixture，禁止 symlink、submodule 和路径逃逸；
- before、after、反向参考补丁 mutation、替代修复各重复验证至少三次；
- 质量门检查离线确定性、参考补丁泄露、许可证/provenance、Agent 分数选择独立性和同源去重；
- 只有全部质量门通过且人工批准的候选可进入不可覆盖的 DatasetVersion；
- 使用 MIT 许可 `more-itertools` 的两个真实历史缺陷作为离线验收样本，并记录公开历史数据的高污染风险。

详细可执行协议见 `docs/automatic-benchmark-generation.md`。本阶段仍不包含自动 GitHub 抓取、embedding 近似去重、难度校准服务或任何 Skill 搜索逻辑。

---

## 10. Benchmark-guided Skill Search

### 10.1 目标与边界

Optimizer 的目标是在不泄漏 locked test、不破坏安全约束和不过度增加成本的前提下，搜索验证集上的 Pareto 改进。更准确的名称是“Benchmark-guided Skill Search”，而不是保证成功的自我进化。产物始终是候选版本，必须经人工确认后发布。

### 10.2 优化闭环

```text
Current Skill
 → Train Runs
 → Failure Clustering
 → Hypothesis Generation
 → Candidate Mutation
 → Static Lint
 → Validation Subset
 → Full Validation
 → Pareto Ranking
 → Regression Dev
 → Freeze One Winner
 → One Frozen Locked-test Batch（control + winner）
 → Human Approval
```

Optimizer 实验的默认 `optimizer_control` 是冻结的 `base_skill_version`，不是 without-Skill。`locked_test` 只发起一次冻结评测批次，包含 base Skill 与唯一 winner，并在内部完成预注册 repeats。若还要回答 Skill 的绝对增益，预注册三臂 `without_skill/base_skill/winner` 及 `base−without`、`winner−base` 两个 contrast，并使用 simultaneous CI/Holm 校正。manual/random 若未预注册为确认性对照，只停留在 validation。任何逐 Case 或聚合反馈都不回流到搜索器；若看到 test 后再次修改 Skill，该 test 版本立即烧毁，最终结论必须换新的 locked test。

### 10.3 变异算子

- 增加遗漏步骤或检查清单；
- 删除冗余、冲突或过时指令；
- 调整指令顺序和触发条件；
- 将长说明拆到 references，减少默认上下文；
- 增加工具失败恢复策略；
- 增加“何时不要使用本 Skill”的负向边界；
- 替换版本特定命令；
- 增加验证步骤与完成标准；
- 修复引用路径；任何可执行脚本修改必须通过 allowlist、静态扫描与人工 review，且禁止改动 Benchmark grader。

### 10.4 候选搜索

P2 使用 beam search：每轮生成 3～5 个候选，先在 `validation_search` 运行 successive halving，淘汰明显劣质候选，再让冻结 shortlist 进入一次 `validation_confirm`。由于候选经历自适应选择，搜索期 bootstrap/CI 只作启发式，不能当确认性区间；正式效应只由未参与筛选的 locked test 给出。

候选采用多目标 Pareto 排序：

- 最大化通过率与质量；
- 最小化 Loss case、Token、时延和 Skill 长度；
- 安全约束和关键 Case 为硬门槛。

Validation 探索报告至少比较原始 Skill、人工规则改进版、相同预算的随机变异和 Optimizer 结果。搜索预算、失败候选和选择过程全部披露，不能只展示幸运 winner；这些已见 validation 结果本身不能证明 Optimizer 优于 manual/random。

### 10.5 防过拟合

- train/validation_search/validation_confirm/regression_dev/locked_test 严格隔离；
- 对 Task 做同义改写与 fixture 变体；
- 候选不得包含 Case ID、答案字符串或测试实现；
- 运行 Skill lint 检测 benchmark-specific 指令；
- 候选只在 validation_search、一次 validation_confirm 与 regression_dev 上筛选；最终 control/winner 在未见 locked test 上运行一个冻结批次；
- 报告完整搜索预算和所有候选，避免只展示幸运结果。

### 10.6 停止条件

满足任一条件停止：

- 在剩余预算下，没有候选达到预注册启发式效应边界和 Pareto 改进门槛；
- 达到成本/运行次数预算；
- 无候选通过安全与回归门槛；
- Skill 长度或 Token 开销超过上限；
- 人工终止。

小验证集无法可靠分辨“1 个百分点”，因此不使用“连续两轮小于 1pp”作为统计停止规则。早停策略及其窥视次数在搜索前冻结。

### 10.7 数据实体与最小纵切

`optimization_job` 保存数据版本、搜索算法、预算和状态；`skill_candidate` 保存父版本、mutation、内容 hash、lint 和各阶段结果；`candidate_lineage` 保存父子谱系与晋级/淘汰理由。状态为 `CREATED → SEARCHING → VALIDATING → REGRESSION_DEV → FROZEN → LOCKED_TESTED → AWAITING_REVIEW → PUBLISHED/REJECTED`。

```yaml
OptimizationJobSpec:
  base_skill_version: code-review@1.0.0
  train_dataset: code-review/train@1
  validation_search: code-review/validation-search@1
  validation_confirm: code-review/validation-confirm@1
  regression_dev: code-review/regression-dev@1
  search: {algorithm: beam, width: 4, seeds: [11, 29, 47]}
  constraints: {max_loss_cases: 0, max_token_overhead: 0.25}
  budget: {candidate_runs: 300, cost_usd: 80}
OptimizationJobResult:
  frozen_winner_id: null
  candidate_lineage_uri: artifacts://...
  validation_summary_uri: artifacts://...
  total_cost_usd: 0
```

取消与重试以 `candidate_id + evaluation_stage + dataset_hash + seed` 幂等；候选文本生成失败可重试，已完成 Agent Run 不重复执行。`locked_test` 不属于可重试搜索 stage，只能由独立 final-evaluation workflow 读取冻结 base Skill/winner。基础设施 invalid 只能按预注册恢复规则补齐失败 block，不能因为结果波动重新发起第二批。

最小纵切验收：针对一个只有 Markdown 指令、没有可执行脚本的 Skill，生成至少三个候选；在 validation_search 做 successive halving；冻结一个 winner；validation 报告 original/manual/random/search 四组；最终只发起一次 frozen-base/winner locked-test 批次，并输出完整成本。

若要声称搜索算法普遍优于 manual/random，需另建 `optimizer_comparison_protocol`：各算法以相同预算和 seed 策略独立冻结一个候选，再在同一未见 locked batch 上公平比较；实验覆盖多个独立 Skill/任务域和 search seed，并以 OptimizationJob/Skill 为统计单位。单 Skill 纵切或只看 validation 只能表述为工程案例，不能宣称算法普遍有效。

### 10.8 搜索控制器 MVP 实现基线（2026-07-13）

当前纵切实现到“冻结 validation winner”，尚未进入独立 locked-test workflow：

- 从冻结 base Skill 生成 original、manual、等 seed random 和至少三个 search 候选；
- 对候选执行大小、单文件 Markdown、安全和 benchmark ID/oracle token 泄漏 lint；
- 使用确定性 subset 做 successive halving，original/manual/random 始终进入 full validation；
- 在 pass rate、mean score、Token、时延和 Skill 长度上计算 Pareto 支配关系；
- 以 max loss case、Token overhead 和 candidate-case 预算作为硬约束；
- 完整保存父子谱系、失败候选、每阶段结果、淘汰理由和不可变候选内容；
- 支持严格 JSON Process Evaluator 接真实 Agent Runtime；离线 evaluator 强制标记 simulated；
- 搜索契约不包含 locked-test 字段，Job 固定记录 `locked_test_accessed=false`。

详细协议见 `docs/benchmark-guided-skill-search.md`。不得把本阶段 validation winner 表述为确认性性能提升。

### 10.9 Independent Final Evaluation MVP 实现基线（2026-07-13）

搜索 winner 必须在独立权限域中复评，搜索过程不得访问 `validation_confirm` 或
`locked_test`。最终评测只接受状态为 `FROZEN` 的 OptimizationJob，复制并复验 original
base 与唯一 winner 内容哈希，然后在单一 split DatasetVersion 上执行相同 case、Evaluator
和重复次数的配对实验。

`validation_confirm` 用于确认泛化；`locked_test` 为每个 OptimizationJob 原子写入唯一消费
凭证，失败也不得更换配置重试。最终报告保存逐 case W/T/L、成功率增益、Token 开销、独立
缺陷组数量、退化门结果和明确 claim limit。模拟模式只能证明控制器工程闭环，不能成为
Agent 性能证据。详细协议见 `docs/independent-final-evaluation.md`。

---

## 11. MCP 工具评测

### 11.1 评测对象

`skill-up v0.5.0` 已支持真实/模拟 MCP、stdio/HTTP transport 和基础工具断言，本项目不把“支持 MCP”本身作为创新。上游 per-case mocked response 仍是 proposal，因此 MCP Lab 通过“每个场景独立 eval.yaml”或平台自有 MCP Proxy 实现。Lab 固定 [MCP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) revision，增加有状态 Server、协议转录、故障注入、安全场景与最终状态 oracle。

评测分成互不替代的三层：

1. 协议合规：initialize、版本/能力协商、JSON-RPC、分页、取消、通知和错误响应；
2. Agent 行为：工具发现/选择、参数语义、恢复、证据利用和用户确认；
3. 最终环境状态：业务目标是否完成，是否产生越权、重复或破坏性副作用。

### 11.2 指标

- Tool Selection Precision/Recall 与必要工具覆盖率；仅在 Runtime 暴露完整候选排名时才报告 Top-k；
- Argument JSON Schema Validity、字段级 F1、语义正确性和权限约束；
- Plan Validity：允许合法 partial order 和多条等价工具路径，不要求固定完整序列；
- Recovery Rate：超时、429、部分结果或 schema 变更后的恢复率；
- Unnecessary Call Rate；
- Tool Latency、成功率和成本；
- Final-state invariants、重复副作用和幂等性；
- Policy Compliance 与 attack success rate：是否调用禁止工具、绕过确认、越权访问或外泄数据。

任务成功率、协议合规、policy violation rate 和 exfiltration attack success rate 分开报告，禁止合成一个“总 MCP 分”。每个安全 Case 预先声明攻击目标、授权状态、确认模拟器响应（approve/deny/timeout）和确定性成功 oracle。

### 11.3 Mock MCP Server

P1/P2 建立每个 Case 独立重置、可注入故障的有状态 Mock MCP Server：

- 固定成功响应；
- 超时与瞬态错误；
- 429 + retry-after；
- 返回空集合或分页结果；
- schema 新旧版本；
- 恶意工具描述和 Prompt Injection 文本；
- 权限拒绝、授权撤销和需要用户确认的破坏性操作；
- 重复请求、非幂等写入和 Server 重启。

所有场景保存初始状态、故障脚本、原始 JSON-RPC transcript、Server hash 和期望最终状态，并在 Case 间重置。MCP tool annotation 只是不可信提示，不作为安全保证；安全设计遵循[官方最佳实践](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)。

### 11.4 评分例子

任务“查询订单并向用户解释退款状态”可以有多条合法路径：

```text
orders.search(customer_id)
  → refunds.get(order_id)
  → final response with cited status
```

评分首先检查最终只读状态、证据引用和 `refunds.create` 未被调用，再检查必要工具覆盖、参数 schema/语义和合法 partial order。某条等价查询路径不应因与参考 sequence 不同被误判。

### 11.5 数据实体与最小纵切

`mcp_server_snapshot` 固定 spec revision、transport、schema 和实现 hash；`mcp_scenario` 保存初始状态、故障计划、权限和 final-state oracle；`tool_call` 保存原始请求/响应、重试关系和副作用；`mcp_protocol_event` 保存协商与通知。

最小纵切验收：订单只读场景分别注入正常、429、分页、权限撤销和 prompt injection；每次重置 Server；同时报告协议、Agent 行为和最终状态三个分数，并证明重复调用不会产生退款写入。

---

## 12. Memory/RAG 评测

### 12.1 分层模型

将 RAG 拆分为：索引 → 检索 → 重排 → 上下文组装 → Agent 使用 → 最终回答。每层单独记录指标，避免最终失败时无法定位。

仅评测 RAG 时，每个 Case 至少支持三臂消融：

- `closed_book`：不给检索上下文；
- `normal_retrieval`：使用冻结的真实 Retriever；
- `oracle_gold_context`：直接提供人工标注的 gold evidence。

三臂差异用于区分“模型本身不会答”“Retriever 没召回”和“已召回但 Agent 未正确使用”。Oracle 与 normal 使用相同 context schema、排序约定和 Token 预算；必要时加入等量无关 distractor，避免把更短/更干净上下文误认为 Retriever 增益。

若系统同时启用 RAG 与长期 Memory，主实验改为 `RAG on/off × Memory on/off` 的 2×2 因子设计，oracle context 只作额外诊断臂，不能把“无检索”和“无 Memory”合并。实验预注册 `normal−closed`、`oracle−normal` 或 2×2 主效应/交互 contrast，并对多个确认性对比使用 simultaneous CI/Holm 校正。

### 12.2 检索指标

- Recall@K、Precision@K、MRR、nDCG；
- Context Relevance 与覆盖率；
- 引用正确率、引用完整率；
- 检索时延与每次查询成本；
- 重复/冗余文档比例。

每个 Case 必须提供 gold document/chunk/fact 或 relevance judgment。记录 judgment pool 覆盖率；正式集由双人标注与仲裁，未判定文档不能默认当作不相关。若使用不完整 judgment，Precision/nDCG 明确标成估计。查询、文档、实体和时间家族分组切分，禁止同一文档的同义问题跨 split。

### 12.3 生成与忠实性

答案中的事实必须能映射到 retrieved chunk。可验证事实使用字符串/结构化比对，复杂语义使用带证据的 Judge。报告区分：

- 检索不到；
- 检索到了但未使用；
- 使用了错误/过期证据；
- 无证据生成；
- 引用位置错误。

### 12.4 长期 Memory

设计跨会话任务：偏好记忆、实体属性更新、时间推理、冲突纠正、知识更新、拒答和遗忘请求。每次 Run 使用全新 namespace 与冻结初始 Memory，分别评分 write、retrieve、use、update 和 forget；不能只看最终答案。

`Forget Compliance` 同时验证逻辑行为、物理删除/不可检索、缓存和访问控制，不接受“回答声称已忘记”作为唯一证据。

### 12.5 污染与鲁棒性

Challenge 集包含：

- 与事实冲突的旧文档；
- 高相似但无关文档；
- 文档中的 Prompt Injection；
- 伪造来源与错误时间戳；
- 用户纠正后的旧 Memory；
- 超长上下文中的关键事实。

系统评估 Agent 是否优先可信、新鲜、有权限的证据，并拒绝把不可信文档当系统指令。

实验必须冻结 corpus hash、权限快照、chunker、embedding 模型/版本、reranker、top-k、索引构建参数、Retriever 代码、Memory 初始状态和时间基准。任一变化都生成新 Variant 或新 IndexSnapshot。

### 12.6 Context Efficiency

默认可计算的是 chunk-level `SupportingChunkRatio = supporting_chunks / retrieved_chunks`。只有具备 token/claim 级支持标注时，才报告：

```text
UsefulContextRatio = labeled_supporting_tokens / retrieved_tokens
AnswerPer1KTokens = quality_score / (context_tokens / 1000)
```

当 `context_tokens=0` 时 AnswerPer1KTokens 为 `N/A`；不同 Case 的 quality scale 不同，不跨 Case 直接平均该比值，优先报告质量—Token Pareto 和配对成本差。删除单个 chunk 的消融只能估计边际贡献，报告中必须标记为 estimate，不能把它当成 token 级事实标签。RAG 报告至少分开 context relevance、answer faithfulness 与 answer relevance。

### 12.7 数据实体与最小纵切

`corpus_snapshot` 保存语料、权限和时间快照；`index_snapshot` 保存 chunker/embedding/reranker/top-k 与索引 hash；`retrieval_event` 保存 query、候选、rank、score 和时延；`relevance_judgment` 保存 gold document/chunk/fact；`memory_event` 保存 write/read/update/forget 与物理状态证据。

最小纵切验收：构造一个包含事实更新、旧文档干扰和遗忘请求的 10 Case 数据集；运行 RAG on/off × Memory on/off 的 2×2 设计，并增加等预算 oracle diagnostic；同时报告 Recall@K/nDCG、faithfulness、更新正确率和物理遗忘合规率。

---

## 13. API 与前端设计

### 13.1 REST API（P1）

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/skills` | 创建 Skill |
| POST | `/api/v1/skills/{id}/versions` | 上传不可变版本 |
| GET | `/api/v1/skills/{id}/diff` | 比较两个版本 |
| POST | `/api/v1/datasets` | 创建数据集 |
| POST | `/api/v1/datasets/{id}/versions` | 冻结数据集版本 |
| POST | `/api/v1/experiments` | 创建实验 |
| GET | `/api/v1/experiments/{id}/variants` | 查看不可变 Variant 与指纹 |
| POST | `/api/v1/experiments/{id}:start` | 启动实验 |
| POST | `/api/v1/experiments/{id}:cancel` | 取消实验 |
| GET | `/api/v1/experiments/{id}` | 实验概览 |
| GET | `/api/v1/experiments/{id}/report` | 获取报告 |
| GET | `/api/v1/runs/{id}` | Run 详情 |
| GET | `/api/v1/runs/{id}/events` | SSE 轨迹 |
| POST | `/api/v1/runs/{id}:regrade` | 仅重跑评分 |
| POST | `/api/v1/benchmarks:generate` | 创建 Benchmark 生成任务 |
| POST | `/api/v1/benchmark-candidates/{id}:review` | 人工接受/拒绝候选 |
| POST | `/api/v1/optimizations` | 创建 Skill 优化任务 |
| GET | `/api/v1/optimizations/{id}/lineage` | 候选谱系与晋级原因 |
| POST | `/api/v1/mcp-scenarios` | 创建有状态 MCP 场景 |
| POST | `/api/v1/memory-rag-evals` | 创建三臂 Memory/RAG 实验 |

所有写接口支持 `Idempotency-Key`；分页使用 cursor；错误返回统一 `code/message/detail/trace_id`。

### 13.2 前端页面

P0 只生成可离线打开的静态 HTML；以下交互页面属于 P1，不能阻塞首个可信配对实验：

1. Dashboard：首页展示运行中实验、成功率趋势、成本和最近回归；
2. Skill Registry：版本、hash、文件树、diff 和实验历史；
3. Dataset：Case 分类、split、来源、grader 状态和难度分布；
4. Experiment Builder：选择 Skill、Agent、Dataset、重复数和预算；
5. Experiment Report：配对指标、W/T/L、置信区间、成本和分类切片；
6. Run Detail：双栏轨迹、工具调用、文件 diff、日志和 Judge 证据；
7. Diagnosis：失败聚类、分类漏斗和代表样本；
8. Optimizer：候选谱系、Pareto 前沿、增益和回归门槛；
9. MCP/Memory Lab：故障注入、检索指标和污染测试。

### 13.3 可视化

- baseline/treatment 配对哑铃图；
- Win/Tie/Loss 矩阵；
- 成功率—成本 Pareto 散点图；
- 轨迹时间线与 span 瀑布图；
- 失败分类 Sankey/漏斗；
- RAG Recall@K、nDCG 曲线；
- Skill 版本回归热力图。

---

## 14. 可靠性、可观测性与安全

### 14.1 Transactional Outbox

P1 中，实验创建、Run 入库和待发布事件在同一 PostgreSQL 事务中写入；Publisher 异步把 Outbox 发到 Redis Streams。消费端通过 idempotency key 与 fencing token 控制唯一逻辑提交，解决数据库已提交但消息未发、消息已发但 ACK 丢失等问题。P0 本地执行不引入 Outbox。

### 14.2 可观测性

- 每个 Experiment/Run/Attempt 使用关联 trace_id；
- 完整保留 Runner 原始结果和 OTLP，不以平台转换后的事件替代源证据；
- API、队列等待、容器准备、Agent、工具、Judge 均建立 span；
- Prometheus 指标：队列长度、吞吐、P95 时延、失败率、重试率、孤儿容器数；
- 日志结构化并自动隐藏 Token、Cookie、密钥和个人信息；
- 告警：队列积压、Worker 失联、容器泄漏、Judge 错误激增和预算异常。

### 14.3 威胁模型

主要风险：不可信仓库代码、恶意 Skill、Prompt Injection、grader 泄漏、命令逃逸、Secret 泄漏、SSRF、对象存储越权和供应链污染。

控制措施：最小权限、除 Model Gateway 外网络默认拒绝、镜像 digest 固定、依赖锁定与 SBOM、产物扫描、短期凭证、路径规范化、防软链接逃逸、Agent/Grader 隔离、隐藏测试独立权限域、审计日志与内容脱敏。Prompt Injection 不能只靠提示词边界解决，必须依赖无工具 Judge、工具授权、输出验证和网络/数据权限。

### 14.4 数据与供应商策略

- 数据分为 `public/internal/confidential/secret`，Dataset 和 Experiment 声明最高允许级别；Secret 永不进入 prompt、trace 或 artifact；
- Gateway 在出站前执行规则/DLP，限制 provider、模型、区域、请求大小、预算，并记录供应商 request ID；
- 每个 ProviderProfile 固定 retention、训练使用、区域和删除能力；不满足数据等级要求时拒绝运行；
- 传输和静态存储加密，对象下载使用短期签名 URL，删除保留审计事件或供应商回执；
- 外部 archive 限制文件数、单文件/总大小和解压比例，拒绝绝对路径、`..`、symlink、hardlink、设备文件和解压炸弹；
- HTML/Markdown 报告把 Agent/Judge 输出视为不可信：严格转义、allowlist sanitizer、禁止内联脚本、设置 CSP；原始内容以纯文本或 sandboxed iframe 展示。

### 14.5 数据保留

- 元数据默认长期保留；
- 完整 stdout/stderr 默认 30 天；
- 大型 artifact 默认 14 天或按实验固定；
- 用户可删除实验产物，但审计记录保留哈希与删除事件；
- 禁止收集隐藏思维链，默认只存可观察交互轨迹。

### 14.6 可审计与重放包

每个发布报告可导出 replay bundle：冻结 Experiment/Variant/Case sidecar、上游配置、Runner/CLI 版本、全部 hash、镜像 digest、价格表、provider request ID、原始结果和评分谱系。对于 Mock Runner 与本地确定性模型可要求结果可重复；对于托管模型只承诺配置可追溯、实验可重新运行，并显式报告 provider 漂移风险。

---

## 15. 测试方案

### 15.1 单元测试

覆盖：平台 DSL/sidecar 解析、Variant hash、PairBlock、Case-cluster bootstrap、状态机、fencing 条件、评分聚合、重试分类、轨迹序列化和脱敏。

### 15.2 集成测试

P0 使用固定 `skill-up v0.5.0` 二进制和 Golden fixture 验证配置编译、逐 Case JSON、artifact 与 OTLP capability；专门把 eval 放在含 `SKILL.md` 的目录中，验证全新 HOME + 显式 `skills: []` 的 baseline 不会隐式安装目标 Skill。P1 再用 Testcontainers 启动 PostgreSQL、Redis 和对象存储，验证 Outbox、租约抢占、fencing、Worker 重启恢复与 regrade。

### 15.3 端到端测试

Mock Agent 场景：

- baseline fail / treatment pass；
- 两组均成功；
- treatment 回归；
- Agent 超时；
- Worker 执行中崩溃；
- Judge 暂时不可用；
- Redis 重复投递；
- 过期 Worker 在新 Attempt 完成后尝试回写；
- 取消实验；
- Treatment 安装但未触发 Skill；
- 恶意输出试图影响 LLM Judge，并验证 Judge 无网络/工具。
- Judge locked audit 未达门槛，系统禁止语义分驱动 pass 并切换预注册回退。

### 15.4 故障注入

注入 Docker pull 失败、磁盘满、MCP 429、网络抖动、数据库短暂断开、心跳丢失和对象存储失败，验证状态机和清理逻辑。

安全测试覆盖 Zip Slip、symlink/hardlink 逃逸、解压炸弹、报告 XSS、恶意 Markdown、Judge prompt injection、DLP 命中、过期/越权 Gateway token、对象存储跨实验访问和删除审计。

### 15.5 P0 验收清单

- [ ] 一条命令运行本地实验，无需先启动 Web、Redis 或 PostgreSQL；
- [ ] 演示 Skill 和 10～12 个 Case 可导入；
- [ ] 自动产生 `Case 数 × Variant 数 × repeats` 个逻辑 Run；
- [ ] 固定 `skill-up` tag/commit/二进制 SHA256，并通过 Golden Contract Test；
- [ ] 两组使用完全相同 fixture 与 grader；
- [ ] 每个 Run 使用全新 HOME、workspace、MCP 状态和 Memory namespace；
- [ ] Treatment 必须保存 installed 证据；其他激活阶段按 Engine 能力保存事件或 `unsupported/null`；
- [ ] 超时后无孤儿容器；
- [ ] HTML 报告可离线打开；
- [ ] 报告可查看完整可观察轨迹与原始 Runner 产物；
- [ ] Case 内聚合 repeats，并按 independence group 做 hierarchical cluster bootstrap 与 95% CI；
- [ ] Secret 扫描无泄漏；
- [ ] README 提供复现实验命令。

### 15.6 P1 可靠性验收

- [ ] Redis 消息重复/ACK 丢失可产生重复物理 Attempt，但只有有效 fencing token 可提交逻辑结果；
- [ ] Worker 崩溃、租约过期、Reaper 回收和指数退避状态均可恢复；
- [ ] `CANCEL_REQUESTED` 只有在进程树和资源清理后才进入 `CANCELLED`；
- [ ] Outbox 重放不重复发布逻辑终态，orphan artifact 可审计并按策略 GC；
- [ ] duplicate model cost、invalid rate 和重试原因可查询。

---

## 16. 开发路线

### 第 0 周：冻结范围

- 选择演示领域：Python 代码审查/缺陷修复 Skill；
- 固定 `skill-up v0.5.0` tag/commit、CLI/JSON 契约和许可证处理；
- 编写 10～12 个 Demo Case，其中至少 3 个反向/干扰样本；
- 预注册 Variant、主指标、invalid 归类、预算和验收；
- ADR 明确“上游 Runner + 本平台控制/研究层”和阶段边界。

### 第 1～2 周：单机闭环

- 建立 Python monorepo、Typer CLI、Pydantic contracts；
- 实现 MockRunnerAdapter 与 SkillUpRunnerAdapter；
- 实现干净 HOME/workspace、Variant 编译和内容寻址产物；
- 复用 Runner 的 Docker/OpenSandbox、Expect 与 Script Judge；
- 在 12 Case 上跑通 without/with 配对和静态 HTML 报告。

### 第 3～4 周：实验可信度与回归

- 实现 ExperimentVariant、PairBlock、Skill v1/v2 与 placebo；
- 实现 group/case hierarchical cluster bootstrap、W/T/L、成本和 invalid 双口径；
- 增加 runner compatibility matrix、Golden Contract 和 replay bundle；
- SQLite/manifest 保存实验历史；
- 完成 baseline 清洁度、Skill 激活链和 regrade 测试。
- 实现最小 SecureGraderRunner，为后续 hidden oracle 提供独立边界。

### 第 5～6 周：诊断与最小平台化

- 统一 Runner JSON/OTLP、配对轨迹 diff 和 evidence ID；
- 实现规则版多标签 Failure Attribution 与人工标注小集；
- 按实际时间选择 FastAPI + PostgreSQL 或先保留本地服务；
- 只做最小实验/Run 页面与 SSE，不追求完整 Dashboard；
- 若引入分布式 Worker，统一采用 Redis Streams + fencing + Outbox。

### 第 7～8 周：主研究纵深

- 主线实现“自动 Benchmark 候选 → Benchmark-guided Skill Search”；
- 完成两个仓库缺陷重建、mutation/替代解验证和 provenance；
- 完成 original/manual/random/search 对照与 candidate lineage；
- LLM Judge 只在主线确有语义评分需求时加入，并先做 Golden Set 校准。

### 第 9～10 周：插件纵切与可信报告

- 根据岗位方向二选一：MCP Lab 的“有状态订单 Server + 五种故障/攻击”，或 Memory/RAG Lab 的“10 Case 分层消融”纵切；
- 未选择的 Lab 只交付接口契约、一个 Golden fixture 和 roadmap，不计入已完成功能；
- 扩展到约 30 Case 做探索实验；资源允许时另建不少于 50 个 locked Case；
- 冻结 optimizer control（base Skill）/winner，只发起一次含预注册 repeats 的 locked-test batch，生成统计、成本、风险和失败报告；
- 完成 README、架构决策、演示视频和第三方许可证说明。

### 16.1 明确的范围闸门

- 第 2 周未跑通真实 Runner：停止 Web、数据库和高级模块开发；
- 第 4 周统计/隔离未验收：不得开始 Optimizer；
- 第 6 周时间不足：优先本地 HTML，推迟 Redis/Vue；
- 第 8 周主研究纵切未完成：MCP 与 Memory/RAG 都只保留接口、fixture 和测试，不做 UI；
- 任何时候都不得用 locked test 反馈换取“更漂亮”的候选结果。

---

## 17. 演示数据集与实验计划

### 17.1 工程 Demo Case

建议以 Python 代码审查/缺陷修复为垂直领域：

| 类别 | 数量 | 示例 |
|---|---:|---|
| 正例 | 4 | 空指针、资源泄漏、越界、错误异常处理 |
| 反例 | 2 | 正确代码、已正确处理 null 的代码 |
| 干扰 | 2 | 注释含 BUG、废弃目录含错误代码 |
| 复杂 | 2 | 跨文件状态错误、需运行测试才能发现 |
| 鲁棒性 | 2 | 测试命令失败、工具瞬态错误 |

每个 Case baseline/treatment 各执行 3 次，共 `12×2×3=72` 次 Run。这里独立样本仍只有 12 个，只能证明系统闭环和展示原始效果，README 必须标注“Demo，不支持稳定泛化结论”。

### 17.2 探索与正式实验

- 探索实验：约 30 个独立 Case × 2 Variant × 3 repeats，报告宽 CI，不做强结论；
- 首份可信报告目标：另建不少于 50 个 locked Case，覆盖多个独立 repository/lineage group，×2 Variant ×3 repeats，约 300 Runs；
- train、validation_search、validation_confirm 和 regression_dev 不计入 50 个 locked Case；
- Case 按 repository/fork/time/patch family 分组，避免同源泄漏；
- 启动前预注册主指标、最小效应、Loss 门槛、invalid 规则和成本预算；
- 如果关注约 10 个百分点或更小的提升，应另做功效分析，50 个 Case 不是通用充分样本量。

### 17.3 应展示的真实结果

最终 README 和简历只使用真实测量值，包括：

- Case 数、Skill 数、Agent 数和总 Run 数；
- baseline/treatment 通过率及置信区间；
- Win/Tie/Loss；
- Token、时延、成本变化；
- 失败分类分布；
- 自动搜索的 validation_search/validation_confirm/regression_dev 结果，以及冻结 base-Skill/winner batch 的 locked-test 表现；
- 确定性 Judge 与 LLM Judge 一致率；
- 至少一个 Skill 负向增益或回归案例。

严禁先编造“提升 XX%”再补实验。

### 17.4 五分钟 Demo

1. 展示一个 `SKILL.md` 及版本 hash；
2. 选择 Dataset 与 Agent，启动配对实验；
3. 打开实时 Run，观察工具调用与测试；
4. 查看最终 W/T/L、增益和成本；
5. 打开一个 Win 和一个 Loss 的配对轨迹；
6. 展示失败归因；
7. 运行 Optimizer 生成候选 v2；
8. 展示 validation_search/validation_confirm/regression_dev 结果，并说明 locked test 只发起一次 control/winner 冻结批次，批次内仍按预注册 repeats 执行。

---

## 18. 简历与面试材料

### 18.1 项目描述模板

**AgentSkill-Eval——面向大模型智能体的评测、诊断与优化平台**

- 基于 Python 构建 Agent Skill 评测平台，以固定版本 `skill-up` 为 Runner，通过防腐层统一编排 without/with、Skill v1/v2 和受控消融实验。
- 先按 Case 聚合 repeats，再按 repository/lineage independence group 做 hierarchical cluster bootstrap、W/T/L、成本和回归门禁，区分 Agent 失败与平台 invalid。
- 复用确定性执行评分并实现 blind pairwise LLM Judge、Golden Set 校准和可重放评分谱系，降低位置偏差、注入和静默重评分风险。
- 【完成 P1 后再写】实现 Redis Streams、租约 generation、fencing token 和 Transactional Outbox，允许重复物理 Attempt 但只提交一个逻辑结果。
- 基于 OpenTelemetry 风格事件采集工具调用、文件变化、检索和资源指标，自动归因规划、工具、Memory/RAG、Skill 冲突等失败类型。
- 实现带 provenance 的 Benchmark 候选生成与 benchmark-guided Skill search，并以 validation_search/validation_confirm/regression_dev/locked-test 隔离和 Pareto 门槛控制过拟合。
- 在【真实 Case 数】个任务、【真实 Run 数】次执行中，测得成功率【真实数据】、Token 变化【真实数据】，定位【真实数量】类失败模式。

### 18.2 高频追问

**为什么需要 baseline？** 只有固定模型、任务和环境后改变 Skill，才能隔离 Skill 的边际影响。

**为什么不直接使用 skill-up？** 本项目直接把固定版本 `skill-up` 作为执行内核；自研的是不可变 Variant、v1/v2 回归、Case 级统计、持久轨迹、失败诊断、自动 Benchmark 与 Skill Search。这样避免复刻 Runner，同时保留可替换边界。

**为什么不全部用 LLM Judge？** 代码、文件和工具状态可确定性验证，LLM Judge 有偏差、随机性和注入风险，只适合语义质量。

**如何保证队列不重复执行？** 至少一次投递无法保证物理执行绝不重复。平台用业务唯一键、lease generation 和 fencing token 保证只有一个逻辑结果提交，并单独记录重复成本。

**Worker 崩溃怎么办？** 心跳超时后 Reaper 回收租约，创建新 Attempt；终态写入与 Outbox 同事务。

**如何防止自动优化刷 Benchmark？** train/validation_search/validation_confirm/regression_dev/locked-test 隔离、隐藏 grader、候选 lint、同源分组切分和完整搜索日志；locked test 只发起一次冻结 control/winner batch，看到结果后修改就烧毁该 test。

**为什么不用 Kubernetes？** P0 的核心问题是实验可信度和闭环，Docker Compose 更易复现；并发和隔离需求达到阈值后再迁移 K8s Jobs。

---

## 19. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| 范围过大 | 无法按期完成 | P0 只做本地 Runner 防腐层、单领域和可信配对；主研究只做 Benchmark + Search |
| 上游 Runner 漂移 | 配置/报告解析失效 | 固定 v0.5.0 tag/commit、Golden Contract、兼容矩阵与迁移器 |
| Agent 随机性 | 结论不稳定 | 配对、多次重复、置信区间和效应量 |
| LLM Judge 偏差 | 误判 | 盲评、Schema、锚点、校准与人工复核 |
| 自动生成 Case 质量低 | 虚高/噪声 | before/after、mutation、替代解、去重、provenance 和人工发布 |
| 公开数据污染 | 错把记忆当能力 | fresh/private 任务、时间/lineage 分组与 contamination risk 分层 |
| Optimizer 过拟合 | 测试提升但泛化下降 | train/validation_search/validation_confirm/regression_dev/locked-test 隔离与 burn rule |
| Docker 隔离不足 | 主机或 Secret 风险 | Agent/Grader 分离、Model Gateway；不可信执行升级 gVisor/Firecracker |
| 成本不可控 | 无法完成实验 | 预算、缓存、successive halving、可重跑 Judge |
| 技术栈堆砌 | 面试难解释 | 每个组件对应明确问题，无需求不引入 |

---

## 20. 最终交付物

1. 可公开的 GitHub 仓库、规范 README、LICENSE 与 Third-Party Notices；
2. 无服务依赖的 P0 本地 CLI；P1 完成后再提供 Docker Compose；
3. 至少一个可审计、可重新运行的实验 Skill；
4. 10～12 个 Demo Case、约 30 个探索 Case；正式结论另用不少于 50 个 locked Case；
5. JSON/HTML 实验报告；
6. Dashboard 截图或演示视频；
7. 架构图、ER 图、状态机和 ADR；
8. 完整真实实验结果与失败案例；
9. 自动搜索前后对比报告；
10. 本开发设计文档。

项目完成的判断标准不是“页面和模块都写了”，而是平台能够以可复现、可解释、可统计验证的方式回答：

> 某个 Skill 在给定 Agent 和任务分布上是否真正有效，为什么有效或失败，以及如何在不产生回归的前提下改进它。

对于托管模型，上述“可复现”特指配置、证据与流程可审计且实验可重新运行，不保证相同输出逐字复现。

---

## 21. 参考实现与方法资料

- [`skill-up v0.5.0` 中文 README](https://github.com/alibaba/skill-up/blob/v0.5.0/README.zh.md)：Runner 能力边界与安装方式；
- [`skill-up` 评测配置说明](https://github.com/alibaba/skill-up/blob/v0.5.0/docs/zh/guide/writing-evals.md)：上游 `v1alpha1` Case DSL；
- [`skill-up` CLI Reference](https://github.com/alibaba/skill-up/blob/v0.5.0/docs/zh/guide/cli-reference.md)：CLI/JSON 防腐层；
- [`skill-up` Custom Engine](https://github.com/alibaba/skill-up/blob/v0.5.0/docs/design/custom-engine.md)：local/HTTP Agent 接入契约；
- [`skill-up` Observability](https://github.com/alibaba/skill-up/blob/v0.5.0/internal/observability/README.md)：OTLP 事件与指标；
- [`skill-up` Report Contract](https://github.com/alibaba/skill-up/blob/v0.5.0/internal/report/README.md)：结构化报告真值与派生视图；
- [SUP-0003 per-case mocked MCP proposal](https://github.com/alibaba/skill-up/blob/main/proposals/zh/0003-per-case-mocked-mcp-responses.md)：尚未成为 v0.5.0 CaseConfig 的能力边界；
- [`skill-up` Apache-2.0 License](https://github.com/alibaba/skill-up/blob/v0.5.0/LICENSE)：复用与署名要求；
- [Agent Skills Evaluation Guide](https://agentskills.io/skill-creation/evaluating-skills)：clean context、with/without 和盲评；
- [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/) 与 [SWE-bench Verified 污染分析](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)：Benchmark 验收缺陷、覆盖与污染风险；
- [LLM Judge Position Bias](https://arxiv.org/abs/2406.07791) 与 [Judge Prompt Injection](https://arxiv.org/abs/2505.13348)：盲化逆序评审和无工具隔离依据；
- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)：协议合规基线；
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)：授权、confused deputy 与 token 安全；
- [GEPA](https://arxiv.org/abs/2507.19457)：反思式 Prompt/策略搜索参考；
- [RAGAS](https://aclanthology.org/2024.eacl-demo.16/) 与 [ARES](https://aclanthology.org/2024.naacl-long.20/)：RAG 分层评测与少量人工校准；
- [LongMemEval](https://arxiv.org/abs/2410.10813)：长期 Memory 的信息提取、推理、更新与拒答任务。

---

## 22. MCP Tool Evaluation MVP 实现状态

当前已实现独立、离线、强制 simulated 的 MCP 评测纵切：严格 Case 与 Trace 契约、确定性 Mock
MCP Lab、工具选择/参数/序列/恢复/效率/副作用安全 grader、固定条件的 with/without guidance
配对统计、CLI 和离线 JSON/HTML 报告。Process adapter 只实现固定 executable 与 SHA-256、
无 shell、最小环境、超时进程组终止及响应复杂度限制的扩展边界。本阶段没有连接生产 MCP
Server、真实 Agent 或付费模型，也没有实现通用 MCP 管理平台。完整协议与命令见
[`docs/mcp-tool-evaluation.md`](./docs/mcp-tool-evaluation.md)。

---

## 23. Memory/RAG Evaluation MVP 实现状态

当前已实现独立、离线、强制 simulated 的 Memory/RAG 评测纵切：Retrieval 与
Generation/Grounding 分层指标、Memory 生命周期与安全评分、确定性 embedding/ranking fixture、
Mock Retriever/Memory、四类固定条件配对实验、专项 Trace、CLI 和无脚本 JSON/HTML 报告。
Process Retriever/Memory adapter 只实现固定 executable/SHA-256、无 shell、最小环境、超时终止
与响应复杂度限制的接入边界。本阶段没有 Milvus、自研向量库、生产知识库、真实 Agent、
Embedding 服务或付费模型调用。完整协议见
[`docs/memory-rag-evaluation.md`](./docs/memory-rag-evaluation.md)。
