# Observed Failure Evidence Bridge

Observed Failure Evidence Bridge 将已经完成的真实 Skill v1 treatment Run 转换为
Failure-guided Evolution 使用的 `FailureEvidenceBundle`。它不重新运行 Agent，也不调用模型。

## 输入边界

命令只接受满足以下条件的本地实验：

- `RealEvidenceRunManifest` 为 `observed_agent`、`simulated=false` 且状态为 `COMPLETED`；
- 实验中恰好有一个带 Skill 快照的 treatment/candidate Variant；
- Run、选中 Attempt、Trace 和 FailureDiagnosis 均来自本地不可变 Manifest；
- 只有该 Skill Variant 的 `EvaluationOutcome.FAIL` 可以进入优化输入。

`INVALID`、取消、未完成、环境、预算和 Judge 故障只保留在审计报告中。成功 Run 不产生
failure finding。

## 使用方法

```bash
agentskill-eval optimize prepare-failures \
  .agentskill-eval/real-workspace \
  EXPERIMENT_ID \
  --output train-failures.yaml
```

输出：

- `train-failures.yaml`：可直接作为 `failure_bundle_path` 传给 `optimize evolve run`；
- `train-failures.yaml.audit.json`：记录 eligible/excluded finding、Trace event 引用、聚合结果、
  Skill hash 和来源实验 hash。

如果 Skill treatment arm 没有可优化的 task failure，命令返回 `INSUFFICIENT`，只生成审计报告，
不会把 infra failure 伪造成优化输入。

## 最小规则补充

现有 `diagnosed` finding 会直接复用。普通 task fail 如果原诊断为 `abstained/UNKNOWN`，桥接器只在
Trace 明确记录失败事件时补充以下标签：

- Skill conflict；
- Tool selection、argument、recovery；
- Verification/test；
- Retrieval；
- Memory；
- Planning。

没有匹配事件时继续保持 `UNKNOWN` 和 `abstained`。规则只使用规范化 Trace，不读取或保存模型
隐藏思维过程。

## 人工 review/override

可通过一个简单 YAML 排除误归因，或为 task fail 的 UNKNOWN finding 提供人工确认标签：

```bash
agentskill-eval optimize prepare-failures WORKSPACE EXPERIMENT_ID \
  --output train-failures.yaml \
  --review examples/optimizer/failure-guided/failure-review.example.yaml
```

Review 不能让 `INVALID` Run 进入优化。每个决定以 `run_id + rule_id` 定位，审计报告会标记
`review_applied=true`。

## 真实实验验证

2026-07-14 使用本地 DeepSeek observed-Agent evidence 实验
`282b1e61-8045-56c8-8806-30054d747b18` 验证读取链路：

- treatment Run：6；
- task fail：0；
- invalid：1，标签为 `ENVIRONMENT`；
- eligible finding：0；
- 结果：`INSUFFICIENT`，未生成 FailureEvidenceBundle。

这个结果说明现有两 Case evidence 更适合证明真实执行链路，还不足以作为 Skill v1 优化训练证据。
后续需要在 train split 中积累真实 task fail，而不是复用 invalid Run。
