# AgentOps-Eval：面向大模型智能体的评测、诊断与自优化平台

> 开发设计文档 v1.0  
> 定位：个人秋招主项目 / 小型 Agent Research System  
> 技术路线：Python、FastAPI、PostgreSQL、Redis、Celery、Docker、OpenTelemetry、Vue 3

---

## 0. 文档说明

本文档是 AgentOps-Eval 的开发基线，用于指导需求冻结、架构设计、编码、测试、实验和演示。平台不只是一个“Skill 测试脚本”，而是围绕 Agent 系统形成完整闭环：

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

---

## 1. 项目背景与目标

### 1.1 背景

现代 Agent 的效果由模型、系统提示、Skill、工具、Memory、RAG、规划策略和运行环境共同决定。仅查看最终文本无法回答以下工程问题：

- Skill 是否带来了真实增量，还是只增加上下文与 Token？
- Agent 失败是规划错误、工具错误、检索错误，还是环境故障？
- Skill 从 v1 升级到 v2 后是否引入回归？
- 相同 Skill 在不同模型、Agent Runtime 和 MCP Server 上是否稳定？
- 能否从失败样本自动产生更好的 Skill，并用保留集验证？

AgentOps-Eval 通过受控实验、轨迹级可观测性、确定性执行验证和自动优化闭环回答这些问题。

### 1.2 项目目标

1. 对同一 Case 自动运行 without-Skill / with-Skill 配对实验。
2. 记录任务成功率、质量分、Token、成本、时延、工具调用和稳定性。
3. 支持规则、脚本、LLM-as-a-Judge 及多 Judge 共识评分。
4. 自动分析轨迹并对失败进行可解释归因。
5. 从仓库、Issue、文档和人工种子自动生成 Benchmark 候选。
6. 评测 MCP 工具选择、参数、调用顺序、恢复和效率。
7. 评测 Memory/RAG 的检索、引用、抗污染和上下文效率。
8. 基于失败样本自动生成 Skill 候选，并通过训练集、验证集和保留集筛选。
9. 支持 Skill v1/v2、模型 A/B 和配置变更的持续回归。

### 1.3 非目标

P0 阶段不实现：

- 自研基础模型训练；
- 大规模 Kubernetes 多租户平台；
- 企业级计费与复杂 RBAC；
- 保存或展示模型隐藏思维链；
- 无人工监督地把自动优化结果直接发布为正式 Skill；
- 覆盖所有 Agent 框架。

### 1.4 成功标准

P0 验收必须同时满足：

- 可导入一个 Skill 和不少于 10 个 Case；
- 可对每个 Case 各运行 baseline 与 treatment；
- 每次 Run 在独立 Docker 沙箱中执行；
- 至少支持 Mock Agent 和一个真实 CLI/HTTP Agent；
- 至少支持 Expect、Script Judge 两种确定性评分；
- 可查看 Run 轨迹、文件差异、日志与评分明细；
- 可生成 HTML/JSON 实验报告；
- 可比较成功率、W/T/L、Token 和时延；
- 失败后可重试，重复消息不会生成重复 Run；
- 核心流程具备自动化测试与一键 Docker Compose 演示。

---

## 2. 实验方法与指标体系

### 2.1 受控配对实验

同一个 `case_id` 在相同模型、温度、最大轮数、代码快照、镜像、网络策略和评分器下运行两次，唯一实验变量为 Skill：

```text
case_i ─┬─ baseline:  system + task
        └─ treatment: system + skill + task
```

随机种子可控时固定种子；不可控时每个条件重复 `n≥3` 次。任务顺序随机化，避免缓存、服务时段和顺序效应系统性偏向某组。

### 2.2 数据集拆分

- `train`：供 Skill Optimizer 查看和迭代；
- `validation`：候选排序与早停；
- `test`：最终报告，只在版本冻结后运行；
- `challenge`：边界、扰动、工具故障、Memory 污染等压力样本。

禁止 Optimizer 访问 test 的标准答案、验证脚本源码和隐藏断言，防止过拟合与测试泄漏。

### 2.3 核心指标

设第 i 个 Case 的通过变量为 `y_i∈{0,1}`：

```text
PassRate = Σy_i / N
AbsoluteGain = PassRate_with - PassRate_without
RelativeGain = AbsoluteGain / max(PassRate_without, ε)
```

配对结果按 Case 分类：

- Win：baseline 失败，treatment 成功；
- Tie+：两组均成功；
- Tie-：两组均失败；
- Loss：baseline 成功，treatment 失败。

必须报告 W/T/L，而不能只报告平均分。Skill 的负向影响往往隐藏在总体均值中。

### 2.4 效率与成本

记录：输入/输出 Token、缓存 Token、模型费用、总时延、首 Token 时延、工具调用数、无效调用数、容器 CPU 峰值和内存峰值。

```text
TokenOverhead = (Tokens_with - Tokens_without) / Tokens_without
LatencyOverhead = (Latency_with - Latency_without) / Latency_without
CostPerSuccess = TotalCost / SuccessfulRuns
```

综合分只用于排序，不替代原始指标：

```text
Utility = 0.60 × Success + 0.20 × Quality
        + 0.10 × Robustness + 0.10 × Efficiency
```

权重属于实验配置，报告中必须展示，禁止以一个不可解释总分掩盖各维度。

### 2.5 稳定性与统计

- 每个条件至少重复 3 次；
- 报告均值、标准差和 95% bootstrap 置信区间；
- 二元配对结果使用 McNemar 检验；
- 连续配对分数使用 Wilcoxon signed-rank 检验；
- 多重比较时使用 Benjamini-Hochberg 控制 FDR；
- 除 p 值外必须报告效应量与样本数。

---

## 3. 总体架构

### 3.1 逻辑架构

```text
┌──────────────── Vue 3 Dashboard ────────────────┐
│ Skills | Datasets | Experiments | Runs | Reports │
└──────────────────────┬──────────────────────────┘
                       │ REST / SSE
┌──────────────────────▼──────────────────────────┐
│ FastAPI Control Plane                           │
│ Registry | Experiment | Scheduler | Report API  │
└────────────┬───────────────────┬────────────────┘
             │                   │
        PostgreSQL          Redis Streams
                                 │
                    ┌────────────▼────────────┐
                    │ Python Worker Pool      │
                    │ Lease / Retry / Heartbeat│
                    └────────────┬────────────┘
                                 │
┌────────────────────────────────▼───────────────┐
│ Agent Runtime + Docker Sandbox                 │
│ Adapter | Skill Injector | MCP | Memory/RAG    │
└───────────────────────┬────────────────────────┘
                        │ OpenTelemetry Events
┌───────────────────────▼────────────────────────┐
│ Evaluation & Intelligence                     │
│ Expect | Script | LLM Judge | Trace Analyzer  │
│ Benchmark Generator | Skill Optimizer          │
└────────────────────────────────────────────────┘
```

### 3.2 技术选型

| 层 | P0 | P1/P2 演进 | 选择理由 |
|---|---|---|---|
| API | FastAPI + Pydantic | 保持 | 类型清晰、异步友好、自动 OpenAPI |
| ORM/迁移 | SQLAlchemy 2 + Alembic | 保持 | 成熟、便于事务与迁移 |
| 数据库 | PostgreSQL 16 | 分区/只读副本 | JSONB 与关系查询兼顾 |
| 队列 | Redis Streams | Ray/Temporal 可选 | P0 部署轻，支持消费组 |
| Worker | Celery 或自研 Stream Worker | Ray | 支持并行、超时与重试 |
| 沙箱 | Docker Engine | gVisor/K8s Jobs | 可复现与隔离 |
| 轨迹 | OpenTelemetry 数据模型 | Tempo/ClickHouse | 标准化 span/event |
| 对象存储 | 本地 MinIO | S3 | 保存日志、补丁、产物 |
| 向量检索 | pgvector | Milvus | P0 降低组件数量 |
| 前端 | Vue 3 + TypeScript + ECharts | 保持 | Dashboard 开发效率高 |
| 实验追踪 | 内置表 + MLflow 可选 | MLflow | 避免 P0 双写复杂度 |

### 3.3 仓库结构

```text
agentops-eval/
├── apps/
│   ├── api/                 # FastAPI 控制面
│   ├── worker/              # 执行 Worker
│   └── web/                 # Vue Dashboard
├── packages/
│   ├── contracts/           # Pydantic/JSON Schema
│   ├── agent_runtime/       # Adapter、Tool、Memory
│   ├── sandbox/             # Docker runtime
│   ├── evaluators/          # Expect/Script/LLM Judge
│   ├── trace_intelligence/  # 轨迹归因
│   ├── benchmark_gen/       # Benchmark 自动生成
│   └── skill_optimizer/     # Skill 优化闭环
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

---

## 4. 领域模型与数据设计

### 4.1 核心实体

1. `Skill`：逻辑 Skill；
2. `SkillVersion`：不可变版本，绑定内容哈希与对象存储 URI；
3. `Dataset` / `DatasetVersion`：数据集及冻结快照；
4. `EvalCase`：任务、fixture、约束和 Judge 配置；
5. `AgentProfile`：Agent 引擎、模型、工具和参数快照；
6. `Experiment`：一次受控实验定义；
7. `Run`：单个 Case、单个条件的一次逻辑运行；
8. `RunAttempt`：重试产生的物理尝试；
9. `TraceEvent`：执行轨迹事件；
10. `EvaluationResult`：单 Judge 结果；
11. `Diagnosis`：失败分类及证据；
12. `OptimizationJob` / `SkillCandidate`：自动优化任务与候选。

### 4.2 关键表

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
  skill_version_id UUID REFERENCES skill_version(id),
  dataset_version_id UUID NOT NULL,
  agent_profile_snapshot JSONB NOT NULL,
  config_snapshot JSONB NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE run (
  id UUID PRIMARY KEY,
  experiment_id UUID NOT NULL REFERENCES experiment(id),
  case_id UUID NOT NULL,
  condition TEXT NOT NULL CHECK (condition IN ('baseline','treatment')),
  repeat_index INT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  final_score NUMERIC(6,4),
  passed BOOLEAN,
  queued_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  UNIQUE(experiment_id, case_id, condition, repeat_index)
);

CREATE TABLE run_attempt (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(id),
  attempt_no INT NOT NULL,
  worker_id TEXT,
  lease_expires_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  error_code TEXT,
  error_detail JSONB,
  UNIQUE(run_id, attempt_no)
);
```

#### trace_event / evaluation_result

轨迹量大，按月对 `trace_event.occurred_at` 分区。正文、大日志和二进制产物写入对象存储，数据库仅保存结构化索引与 URI。

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

CREATE TABLE evaluation_result (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES run(id),
  judge_type TEXT NOT NULL,
  judge_version TEXT NOT NULL,
  score NUMERIC(6,4),
  passed BOOLEAN,
  evidence JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.3 状态机

```text
CREATED → QUEUED → LEASED → PREPARING → RUNNING
       → GRADING → PERSISTING → SUCCEEDED

任意执行态 → RETRY_WAIT → QUEUED
任意执行态 → FAILED / TIMEOUT / CANCELLED
```

状态迁移必须通过带旧状态条件的更新完成，例如 `UPDATE ... WHERE status='RUNNING'`，防止并发覆盖。终态不可逆。

---

## 5. 配置 DSL 与接口契约

### 5.1 Experiment YAML

```yaml
schema_version: v1alpha1
experiment:
  name: code-review-skill-v1
  repeats: 3
  randomize_order: true
agent:
  adapter: codex_cli
  model: fixed-by-runtime
  max_turns: 12
  timeout_seconds: 600
skill:
  name: code-review
  version: 1.0.0
dataset:
  name: java-review-bench
  version: 1.0.0
conditions: [baseline, treatment]
sandbox:
  image: agentops-eval/java17:1.0
  cpu: 2
  memory_mb: 4096
  network: deny
evaluation:
  fail_fast_on_expect: true
  aggregate: weighted_mean
report:
  formats: [json, html]
```

### 5.2 Case YAML

```yaml
id: java-null-check-001
title: 识别并修复可复现的空指针
split: test
category: bug_fix
difficulty: medium
prompt: |
  修复当前项目中导致测试失败的问题，保留现有接口，并运行测试验证。
fixture:
  artifact: fixtures/java-null-check-001.tar.zst
  sha256: "..."
constraints:
  timeout_seconds: 300
  network: deny
expect:
  exit_code: 0
  files_changed:
    allow: ["src/**"]
    deny: ["tests/**", "pom.xml"]
judges:
  - type: script
    command: ["bash", "/grader/run.sh"]
    weight: 0.8
  - type: llm
    rubric: rubrics/code_quality.yaml
    weight: 0.2
```

### 5.3 Agent Adapter

```python
class AgentAdapter(Protocol):
    async def health_check(self) -> HealthStatus: ...
    async def prepare(self, request: AgentRequest) -> PreparedSession: ...
    async def run(
        self,
        session: PreparedSession,
        event_sink: TraceEventSink,
    ) -> AgentResult: ...
    async def cancel(self, session_id: str) -> None: ...
```

`AgentResult` 只包含可观察结果：最终消息、退出原因、Token、耗时、产物和错误；不要求模型暴露隐藏思维链。Adapter 必须把工具调用、工具结果和文件变化转换为统一 TraceEvent。

### 5.4 Evaluator

```python
class Evaluator(Protocol):
    name: str
    version: str

    async def evaluate(self, context: EvalContext) -> EvalResult: ...
```

Evaluator 必须返回分数、通过状态、机器可读证据、用户可读理由和可重放版本信息。

---

## 6. 执行引擎与沙箱

### 6.1 Worker 流程

1. 从 Redis 消费组领取 Run；
2. 使用数据库唯一键确认幂等；
3. 创建 RunAttempt 并获得租约；
4. 下载并校验 fixture、Skill、grader；
5. 创建隔离容器和只读 grader mount；
6. 按条件注入或不注入 Skill；
7. 启动 Agent，持续写入 trace；
8. 超时或取消时终止进程树；
9. 收集 final answer、Git diff、日志和资源指标；
10. 执行 Expect 与 Judges；
11. 事务写入终态与 Outbox；
12. 删除容器和临时凭据；
13. ACK 队列消息。

### 6.2 幂等、租约与重试

`idempotency_key = sha256(experiment_id + case_id + condition + repeat_index)`。Worker 采用至少一次投递，因此业务层必须幂等。

- 心跳周期：10 秒；
- 默认租约：60 秒；
- 30 秒无心跳则由 Reaper 标记为失联；
- 基础设施错误可指数退避重试 2 次；
- 任务本身失败不自动重试，除非实验要求重复采样；
- Judge 错误可单独重跑评分，不重新执行昂贵 Agent Run。

### 6.3 Docker 安全基线

- 非 root 用户；
- `--cap-drop=ALL`，禁止 privileged；
- seccomp/AppArmor 默认策略；
- CPU、内存、PID、文件大小和执行时间限制；
- 根文件系统只读，单独可写 workspace；
- grader 和隐藏测试只读且不暴露源码给 Agent；
- 默认禁网，按域名白名单开放；
- Secret 通过短期文件或进程环境注入，日志自动脱敏；
- 容器销毁后清理 volume 与临时网络；
- 对来自外部仓库的内容按不可信输入处理。

### 6.4 Skill 注入

Baseline 不应获得 Skill 的正文、名称或暗示。Treatment 使用 Runtime 的原生 Skill 安装机制；若 Runtime 不支持，才使用系统提示拼接兼容层。报告必须标记注入方式：`native_install`、`system_prompt` 或 `workspace_mount`。

为保证公平，两组的公共系统提示、工具、环境和任务文本完全一致；Skill 产生的上下文开销计入 treatment Token。

---

## 7. Evaluation Engine

### 7.1 评分流水线

```text
Execution Result
  → Expect Gate
  → Deterministic / Script Judges
  → Semantic LLM Judges
  → Consensus & Aggregation
  → Pass/Fail + Evidence
```

### 7.2 Expect Gate

支持：退出码、文件存在/不存在、允许/禁止改动路径、文本包含/不包含、JSON Schema、最大时延、最大工具调用数。Expect 失败时可直接终止后续昂贵 Judge。

### 7.3 Script Judge

Script Judge 在独立 grader 容器执行，Agent 无权修改。标准输入为结果清单 JSON，标准输出为：

```json
{
  "passed": true,
  "score": 0.92,
  "summary": "hidden tests: 11/12",
  "evidence": [{"name": "testNullUser", "passed": true}]
}
```

脚本必须有版本和哈希。测试不得依赖外网和时间随机性。测试泄漏、修改 tests 或伪造测试输出都判失败。

### 7.4 LLM-as-a-Judge

只用于难以机械验证的语义维度。Judge 输入包括任务、rubric、最终结果、必要证据和经过截断/脱敏的轨迹，不包含条件名称，避免偏向 treatment。

要求：

- 强制 JSON Schema；
- 温度 0 或最低可用值；
- rubric 每个维度有锚点示例；
- Judge 版本、模型、提示哈希可追溯；
- 防 Prompt Injection：把被评输出放在明确的数据边界中；
- 对边界分数或 Judge 冲突样本进入人工复核队列。

### 7.5 多 Judge 共识

P1 支持规则/脚本、Judge A、Judge B 的共识。计算一致率、分数方差和 Cohen's kappa。确定性结果与 LLM 冲突时，客观正确性以确定性结果为主，LLM 仅保留质量子分。

### 7.6 聚合

Case 的通过条件应显式配置，例如：

```text
passed = expect_pass
      AND script_score >= 0.8
      AND semantic_score >= 0.6
```

不能用平均分让关键安全检查被其他高分抵消。

---

## 8. Trace Intelligence 与失败诊断

### 8.1 统一事件模型

事件类型包括：

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

### 8.2 诊断分类

一级分类：

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

### 8.3 归因方法

采用三层策略：

1. 规则：超时、工具返回码、重复调用、未运行测试等确定性特征；
2. 统计：与成功轨迹比较步骤数、工具序列、重试率和检索质量；
3. LLM Analyzer：对剩余复杂轨迹给出分类、置信度与引用事件。

诊断结果必须引用可观察证据，如事件序号和工具错误，不得把隐藏思维过程当作事实。

### 8.4 轨迹比较

配对 Run 支持并排时间线、工具序列 diff、文件 diff、检索文档 diff 和成本瀑布图。通过序列编辑距离和阶段聚类识别 Skill 导致的新增/减少步骤。

---

## 9. 自动 Benchmark 生成

### 9.1 数据来源

- GitHub 仓库的已修复 Issue 与对应 commit/PR；
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

### 9.3 软件工程 Case 重建

从“修复前 commit”创建 fixture，从“修复后 commit”提取补丁作为参考但不提供给 Agent。优先复用项目原有测试；若无测试，生成最小回归测试并由独立验证器确认：

1. 测试在修复前失败；
2. 测试在修复后通过；
3. 测试不依赖网络与时间；
4. 删除参考补丁后仍能稳定复现；
5. Agent 无法从测试文件直接读取答案。

### 9.4 任务生成与质量门

生成器输出 task、fixture、oracle、grader、分类、难度和 provenance。只有满足以下条件才进入候选集：

- 可复现；
- 可自动验收；
- 无敏感数据和许可证风险；
- 描述不泄露实现答案；
- 与现有 Case 语义/代码重复度低；
- 基线 Agent 既非 0% 也非 100% 长期通过，具备区分度。

### 9.5 去重与污染控制

结合文本 embedding、代码 AST 指纹、patch overlap 和仓库 lineage 去重。记录来源 URL、commit SHA、许可证和生成器版本。test 集只保存加密/受控 oracle，Optimizer 无读取权限。

### 9.6 难度校准

按多模型 pilot run 的成功率、平均工具步数、跨文件数量和所需环境操作划分 easy/medium/hard。难度是经验标签，不由 LLM 单次主观决定。

---

## 10. Skill 自动优化

### 10.1 目标与边界

Optimizer 的目标是在不泄漏 test 集、不破坏安全约束和不过度增加成本的前提下，提高验证集表现。产物是“候选版本”，必须经人工确认后发布。

### 10.2 优化闭环

```text
Current Skill
 → Train Runs
 → Failure Clustering
 → Hypothesis Generation
 → Candidate Mutation
 → Static Lint
 → Fast Validation
 → Full Validation
 → Pareto Ranking
 → Holdout Regression
 → Human Approval
```

### 10.3 变异算子

- 增加遗漏步骤或检查清单；
- 删除冗余、冲突或过时指令；
- 调整指令顺序和触发条件；
- 将长说明拆到 references，减少默认上下文；
- 增加工具失败恢复策略；
- 增加“何时不要使用本 Skill”的负向边界；
- 替换版本特定命令；
- 增加验证步骤与完成标准；
- 修复脚本或引用路径，但禁止改动 Benchmark grader。

### 10.4 候选搜索

P1 使用 beam search：每轮生成 3～5 个候选，先在小型 validation subset 运行 successive halving，淘汰明显劣质候选，再进入完整验证。

候选采用多目标 Pareto 排序：

- 最大化通过率与质量；
- 最小化 Loss case、Token、时延和 Skill 长度；
- 安全约束和关键 Case 为硬门槛。

### 10.5 防过拟合

- 训练/验证/test 严格隔离；
- 对 Task 做同义改写与 fixture 变体；
- 候选不得包含 Case ID、答案字符串或测试实现；
- 运行 Skill lint 检测 benchmark-specific 指令；
- 最终候选必须在未见 holdout 上提升且无显著回归；
- 报告完整搜索预算和所有候选，避免只展示幸运结果。

### 10.6 停止条件

满足任一条件停止：

- 连续 2 轮验证集增益小于 1 个百分点；
- 达到成本/运行次数预算；
- 无候选通过安全与回归门槛；
- Skill 长度或 Token 开销超过上限；
- 人工终止。

---

## 11. MCP 工具评测

### 11.1 评测对象

平台把 MCP Server 注册的 tools/resources/prompts 快照化，记录 schema、版本和权限。测试不仅看最终任务成功，还看 Agent 是否选择正确工具、构造正确参数、遵守调用顺序并从错误中恢复。

### 11.2 指标

- Tool Selection Accuracy / Top-k；
- Argument Exact Match、JSON Schema Validity、字段级 F1；
- Sequence Success：关键工具顺序是否正确；
- Recovery Rate：超时、429、部分结果或 schema 变更后的恢复率；
- Unnecessary Call Rate；
- Tool Latency、成功率和成本；
- Policy Compliance：是否调用了禁止工具或越权资源。

### 11.3 Mock MCP Server

P0/P1 建立可注入故障的 Mock MCP Server：

- 固定成功响应；
- 超时与瞬态错误；
- 429 + retry-after；
- 返回空集合或分页结果；
- schema 新旧版本；
- 恶意工具描述和 Prompt Injection 文本；
- 权限拒绝。

所有 Mock 场景可重放，避免真实 SaaS 状态导致评测不稳定。

### 11.4 评分例子

任务“查询订单并向用户解释退款状态”可能要求：

```text
orders.search(customer_id)
  → refunds.get(order_id)
  → final response with cited status
```

评分分别检查工具选择、参数、顺序、返回证据引用、禁止的 `refunds.create` 是否未调用，以及异常后的恢复。

---

## 12. Memory/RAG 评测

### 12.1 分层模型

将 RAG 拆分为：索引 → 检索 → 重排 → 上下文组装 → Agent 使用 → 最终回答。每层单独记录指标，避免最终失败时无法定位。

### 12.2 检索指标

- Recall@K、Precision@K、MRR、nDCG；
- Context Relevance 与覆盖率；
- 引用正确率、引用完整率；
- 检索时延与每次查询成本；
- 重复/冗余文档比例。

### 12.3 生成与忠实性

答案中的事实必须能映射到 retrieved chunk。可验证事实使用字符串/结构化比对，复杂语义使用带证据的 Judge。报告区分：

- 检索不到；
- 检索到了但未使用；
- 使用了错误/过期证据；
- 无证据生成；
- 引用位置错误。

### 12.4 长期 Memory

设计跨会话任务：偏好记忆、实体属性更新、时间衰减、冲突纠正和遗忘请求。指标包括 Write Precision、Read Recall、Update Correctness、Stale Memory Rate 和 Forget Compliance。

### 12.5 污染与鲁棒性

Challenge 集包含：

- 与事实冲突的旧文档；
- 高相似但无关文档；
- 文档中的 Prompt Injection；
- 伪造来源与错误时间戳；
- 用户纠正后的旧 Memory；
- 超长上下文中的关键事实。

系统评估 Agent 是否优先可信、新鲜、有权限的证据，并拒绝把不可信文档当系统指令。

### 12.6 Context Efficiency

```text
UsefulContextRatio = supporting_tokens / retrieved_tokens
AnswerPer1KTokens = quality_score / (context_tokens / 1000)
```

通过删除单个 chunk 的消融实验估计其边际贡献，识别“检索很多但没有帮助”的上下文浪费。

---

## 13. API 与前端设计

### 13.1 REST API

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/skills` | 创建 Skill |
| POST | `/api/v1/skills/{id}/versions` | 上传不可变版本 |
| GET | `/api/v1/skills/{id}/diff` | 比较两个版本 |
| POST | `/api/v1/datasets` | 创建数据集 |
| POST | `/api/v1/datasets/{id}/versions` | 冻结数据集版本 |
| POST | `/api/v1/experiments` | 创建实验 |
| POST | `/api/v1/experiments/{id}:start` | 启动实验 |
| POST | `/api/v1/experiments/{id}:cancel` | 取消实验 |
| GET | `/api/v1/experiments/{id}` | 实验概览 |
| GET | `/api/v1/experiments/{id}/report` | 获取报告 |
| GET | `/api/v1/runs/{id}` | Run 详情 |
| GET | `/api/v1/runs/{id}/events` | SSE 轨迹 |
| POST | `/api/v1/runs/{id}:regrade` | 仅重跑评分 |
| POST | `/api/v1/benchmarks:generate` | 创建 Benchmark 生成任务 |
| POST | `/api/v1/optimizations` | 创建 Skill 优化任务 |

所有写接口支持 `Idempotency-Key`；分页使用 cursor；错误返回统一 `code/message/detail/trace_id`。

### 13.2 前端页面

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

实验创建、Run 入库和待发布事件在同一 PostgreSQL 事务中写入；Publisher 异步把 Outbox 发到 Redis。消费端通过 idempotency key 去重，解决数据库已提交但消息未发、消息已发但 ACK 丢失等问题。

### 14.2 可观测性

- 每个 Experiment/Run/Attempt 使用关联 trace_id；
- API、队列等待、容器准备、Agent、工具、Judge 均建立 span；
- Prometheus 指标：队列长度、吞吐、P95 时延、失败率、重试率、孤儿容器数；
- 日志结构化并自动隐藏 Token、Cookie、密钥和个人信息；
- 告警：队列积压、Worker 失联、容器泄漏、Judge 错误激增和预算异常。

### 14.3 威胁模型

主要风险：不可信仓库代码、恶意 Skill、Prompt Injection、grader 泄漏、命令逃逸、Secret 泄漏、SSRF、对象存储越权和供应链污染。

控制措施：最小权限、网络默认拒绝、镜像 digest 固定、依赖锁定与 SBOM、产物扫描、短期凭证、路径规范化、防软链接逃逸、隐藏测试隔离、审计日志与内容脱敏。

### 14.4 数据保留

- 元数据默认长期保留；
- 完整 stdout/stderr 默认 30 天；
- 大型 artifact 默认 14 天或按实验固定；
- 用户可删除实验产物，但审计记录保留哈希与删除事件；
- 禁止收集隐藏思维链，默认只存可观察交互轨迹。

---

## 15. 测试方案

### 15.1 单元测试

覆盖：DSL 解析、状态机、幂等键、Skill hash、评分聚合、指标计算、重试分类、轨迹序列化和脱敏。

### 15.2 集成测试

使用 Testcontainers 启动 PostgreSQL、Redis、MinIO 和 Docker fixture。验证 Outbox、租约抢占、Worker 重启恢复、regrade、artifact 校验与 Mock MCP。

### 15.3 端到端测试

Mock Agent 场景：

- baseline fail / treatment pass；
- 两组均成功；
- treatment 回归；
- Agent 超时；
- Worker 执行中崩溃；
- Judge 暂时不可用；
- Redis 重复投递；
- 取消实验；
- 恶意输出试图影响 LLM Judge。

### 15.4 故障注入

注入 Docker pull 失败、磁盘满、MCP 429、网络抖动、数据库短暂断开、心跳丢失和对象存储失败，验证状态机和清理逻辑。

### 15.5 P0 验收清单

- [ ] 一条命令启动开发环境；
- [ ] 演示 Skill 和 10 个 Case 可导入；
- [ ] 自动产生 20×repeats 个 Run；
- [ ] 两组使用完全相同 fixture 与 grader；
- [ ] Worker 崩溃后 Run 可恢复；
- [ ] 同一消息重复消费不重复执行；
- [ ] 超时后无孤儿容器；
- [ ] HTML 报告可离线打开；
- [ ] Run 页面可查看完整可观察轨迹；
- [ ] Secret 扫描无泄漏；
- [ ] README 提供复现实验命令。

---

## 16. 开发路线

### 第 0 周：冻结范围

- 选择演示领域：建议“代码审查 Skill”；
- 编写 10～12 个 Case，其中至少 3 个反向/干扰样本；
- 冻结 P0 DSL、指标和验收；
- 创建 ADR，记录关键技术决策。

### 第 1～2 周：单机闭环

- 建立 monorepo、contracts 和数据库迁移；
- 实现 Skill/Dataset Registry；
- 实现 Mock Agent、Docker Sandbox；
- 实现 Expect、Script Judge；
- CLI 方式跑通一对 baseline/treatment。

### 第 3～4 周：平台化

- FastAPI 实验 API；
- Redis Streams、租约、心跳、重试与 Outbox；
- 并发 Worker；
- artifact 与 trace 存储；
- 生成 JSON/HTML 报告。

### 第 5～6 周：可视化与诊断

- Vue 实验页、Run 轨迹页；
- SSE 实时日志；
- 配对图表与失败切片；
- 规则版 Failure Attribution。

### 第 7～8 周：P1 差异化

- LLM Judge 与校准；
- Mock MCP 评测；
- RAG 检索分层指标；
- GitHub Issue → Benchmark 候选生成；
- Skill v1/v2 回归。

### 第 9～10 周：研究闭环

- Skill Optimizer beam search；
- validation/holdout 防过拟合；
- 多 Judge 共识；
- 统计检验、Pareto 排序与完整实验报告。

---

## 17. 演示数据集与实验计划

### 17.1 首批 Case

建议以代码审查为垂直领域：

| 类别 | 数量 | 示例 |
|---|---:|---|
| 正例 | 4 | 空指针、资源泄漏、越界、错误异常处理 |
| 反例 | 2 | 正确代码、已正确处理 null 的代码 |
| 干扰 | 2 | 注释含 BUG、废弃目录含错误代码 |
| 复杂 | 2 | 跨文件状态错误、需运行测试才能发现 |
| 鲁棒性 | 2 | 测试命令失败、工具瞬态错误 |

每个 Case baseline/treatment 各执行 3 次，共 `12×2×3=72` 次 Run。完成后再扩展为 30～50 个 Case。

### 17.2 应展示的真实结果

最终 README 和简历只使用真实测量值，包括：

- Case 数、Skill 数、Agent 数和总 Run 数；
- baseline/treatment 通过率及置信区间；
- Win/Tie/Loss；
- Token、时延、成本变化；
- 失败分类分布；
- 自动优化前后及 holdout 表现；
- 确定性 Judge 与 LLM Judge 一致率；
- 至少一个 Skill 负向增益或回归案例。

严禁先编造“提升 XX%”再补实验。

### 17.3 五分钟 Demo

1. 展示一个 `SKILL.md` 及版本 hash；
2. 选择 Dataset 与 Agent，启动配对实验；
3. 打开实时 Run，观察工具调用与测试；
4. 查看最终 W/T/L、增益和成本；
5. 打开一个 Win 和一个 Loss 的配对轨迹；
6. 展示失败归因；
7. 运行 Optimizer 生成候选 v2；
8. 展示 validation 提升与 holdout 回归门槛。

---

## 18. 简历与面试材料

### 18.1 项目描述模板

**AgentOps-Eval——面向大模型智能体的评测、诊断与自优化平台**

- 基于 FastAPI、PostgreSQL、Redis Streams 与 Docker 构建 Agent 评测平台，支持 Skill with/without 配对实验及多版本回归，统一管理 Agent、数据集、轨迹与评分产物。
- 设计 Expect、隐藏脚本和 LLM-as-a-Judge 分层评分体系，以执行式验证为主，并通过 Judge 校准与冲突复核降低模型评分偏差。
- 实现带租约、心跳、幂等消费和 Transactional Outbox 的异步执行引擎，支持超时、失败重试、任务取消和 Worker 故障恢复。
- 基于 OpenTelemetry 风格事件采集工具调用、文件变化、检索和资源指标，自动归因规划、工具、Memory/RAG、Skill 冲突等失败类型。
- 实现 Benchmark 候选生成、MCP 故障注入、RAG 分层评测及 failure-driven Skill 优化，通过验证集与 holdout 回归门槛控制过拟合。
- 在【真实 Case 数】个任务、【真实 Run 数】次执行中，测得成功率【真实数据】、Token 变化【真实数据】，定位【真实数量】类失败模式。

### 18.2 高频追问

**为什么需要 baseline？** 只有固定模型、任务和环境后改变 Skill，才能隔离 Skill 的边际影响。

**为什么不全部用 LLM Judge？** 代码、文件和工具状态可确定性验证，LLM Judge 有偏差、随机性和注入风险，只适合语义质量。

**如何保证队列不重复执行？** 至少一次投递配合业务唯一键、条件状态更新、租约和幂等 artifact 路径。

**Worker 崩溃怎么办？** 心跳超时后 Reaper 回收租约，创建新 Attempt；终态写入与 Outbox 同事务。

**如何防止自动优化刷 Benchmark？** 数据集隔离、隐藏 grader、候选 lint、同义/fixture 变体、holdout 验证和完整搜索日志。

**为什么不用 Kubernetes？** P0 的核心问题是实验可信度和闭环，Docker Compose 更易复现；并发和隔离需求达到阈值后再迁移 K8s Jobs。

---

## 19. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| 范围过大 | 无法按期完成 | P0 只做单领域、一个真实 Agent、两个确定性 Judge |
| Agent 随机性 | 结论不稳定 | 配对、多次重复、置信区间和效应量 |
| LLM Judge 偏差 | 误判 | 盲评、Schema、锚点、校准与人工复核 |
| 自动生成 Case 质量低 | 虚高/噪声 | 可复现门槛、grader 验证、去重和人工发布 |
| Optimizer 过拟合 | 测试提升但泛化下降 | train/validation/test 隔离与 holdout |
| Docker 隔离不足 | 主机风险 | 非 root、cap-drop、禁网、资源限制，后续 gVisor |
| 成本不可控 | 无法完成实验 | 预算、缓存、successive halving、可重跑 Judge |
| 技术栈堆砌 | 面试难解释 | 每个组件对应明确问题，无需求不引入 |

---

## 20. 最终交付物

1. 可公开的 GitHub 仓库与规范 README；
2. Docker Compose 一键运行环境；
3. 至少一个可复现实验 Skill；
4. 10～30 个带隐藏验证的 Case；
5. JSON/HTML 实验报告；
6. Dashboard 截图或演示视频；
7. 架构图、ER 图、状态机和 ADR；
8. 完整真实实验结果与失败案例；
9. 自动优化前后对比报告；
10. 本开发设计文档。

项目完成的判断标准不是“页面和模块都写了”，而是平台能够以可复现、可解释、可统计验证的方式回答：

> 某个 Skill 在给定 Agent 和任务分布上是否真正有效，为什么有效或失败，以及如何在不产生回归的前提下改进它。

