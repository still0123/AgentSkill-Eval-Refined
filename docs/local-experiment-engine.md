# P0 本地配对实验引擎

## 目标与边界

本地实验引擎把冻结的 `ExperimentManifest`、Variant、Case 和运行时配置展开成可审计的 PairBlock/Run，并按 PairBlock 内预先随机化的顺序调用 Runner Adapter。它是单机 P0 的控制面，不依赖数据库服务、Redis、Celery 或 Web API。

当前目标只负责：

- 确定性生成 PairBlock、Run ID、执行顺序和 Run Plan Fingerprint；
- 冻结并持久化 Experiment、Variant、PairBlock 与初始 Run；
- 严格推进 Run/Attempt 生命周期；
- 依次执行每个 PairBlock 的 Variant；
- 区分任务 `PASS/FAIL`、基础设施 `INVALID` 和取消；
- 校验、归档并内容寻址保存 Runner 原始产物；
- 已完成 Run 的幂等重放。

自动基础设施重试、跨进程续跑、实验统计和 HTML 报告属于后续目标。本目标默认 `max_attempts=1`；崩溃恢复会报告未终态 Run，但不会在无法判断外部模型是否已计费时静默重复调用。

## 计划模型

`LocalExperimentPlanner.build()` 接受：

- 状态为 `FROZEN` 的 `ExperimentManifest`；
- 与 Manifest 引用和指纹完全一致的 `ExperimentVariant`；
- 每个 Variant 唯一对应的 `VariantRuntimeSpec`；
- 一个或多个 `CaseExecutionSpec`；
- 预注册的 repeats、random seed 和 max attempts。

计划器拒绝以下输入：

- 少于两个 Variant；
- Manifest 引用缺失、多余或指纹不一致；
- Variant/运行时配置非一一对应；
- Case 文件不存在或哈希格式非法；
- 计划 Run 数超过 `budget_snapshot.max_runs`。

### 确定性标识与顺序

```text
block_id = UUIDv5(experiment_id, case_id + repeat_index)
run_id   = UUIDv5(block_id, variant_id)
seed     = SHA256(master_seed + case_id + repeat_index) 的确定性整数
order    = Random(seed).shuffle(experiment.variant_references)
```

同一冻结输入重复规划会得到相同 PairBlock ID、Run ID、seed、执行顺序和指纹。`persist()` 可重复调用：已有 Run 的 PairBlock、Variant 和 Plan Fingerprint 一致时保持当前状态，不会把已执行 Run 回退为 `CREATED`。

`RunPlanFingerprint` 包含 Case、Grader、平台编译 Prompt、Variant、Engine、Environment、artifact 规则、timeout、max turns 和镜像摘要。Secret 不进入指纹和 Manifest。

## 执行状态机

单个 Run 的正常路径：

```text
CREATED → QUEUED → LEASED → PREPARING → RUNNING
        → GRADING → PERSISTING → COMPLETED
```

对应 Attempt：

```text
CLAIMED → PREPARING → RUNNING → COMPLETED
```

Runner 配置校验异常、Adapter 异常、报告缺失、Skip 或产物归档失败会得到：

```text
Run:     当前执行阶段 → INFRA_FAILED + INVALID
Attempt: 当前执行阶段 → FAILED + error_code/error_detail
```

Case 的确定性评分失败属于有效实验结果：Run 保存 `COMPLETED + FAIL`，而不是基础设施失败。取消会经过 `CANCEL_REQUESTED → CANCELLED`，只有 Adapter 已完成进程树终止后才发布终态。

Attempt 在 `CLAIMED/PREPARING/RUNNING` 阶段持续原子更新。Attempt ID、Run ID、lease generation 和 fencing token 不允许变化；终态 Attempt 不可修改。最终发布顺序为：

1. 归档原始 Runner 字节；
2. 写入内容寻址对象；
3. 写终态 Attempt；
4. 写不可变 RunMeasurement；
5. 写 Artifact Manifest；
6. 原子更新 Run 的 active Attempt、selected Attempt hash 和终态。

## 原始产物

Runner 返回的每个 artifact 在复制前重新验证：

- 路径是规范相对 POSIX 路径，不含 `..`；
- 源文件是普通文件且不是符号链接；
- 当前大小和 SHA-256 与 Adapter 观测一致。

验证通过后写入：

```text
workspace/
├── objects/sha256/xx/{digest}
└── experiments/{experiment_id}/runs/{run_id}/attempts/1/
    ├── attempt.json
    ├── raw-runner/
    └── artifacts/manifest.json
```

stdout/stderr 使用 `platform-stdout.log` 和 `platform-stderr.log`，避免与上游同名产物冲突。任何归档错误都记为 `artifact_archival_failed`，不能发布看似有效的 Case 结果。

## 最小 Python 调用流程

```python
store = LocalExperimentStore(workspace)
planner = LocalExperimentPlanner(store)
plan = planner.build(
    experiment,
    variants,
    runtime_specs,
    cases,
    repeats=3,
    random_seed=2026,
)
planner.persist(plan)

executor = LocalExperimentExecutor(store, runner_adapter)
summary = asyncio.run(executor.execute(plan))
```

真实兼容测试使用无模型凭据的 Custom Engine，完整执行 baseline/treatment 两臂，并验证 `result.json`、Artifact Manifest 和原始产物均已持久化。
