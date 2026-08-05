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

新生成的 observed bundle 还冻结：

- provider/model；
- Runner version/SHA-256；
- Agent config SHA-256；
- DatasetVersion SHA-256；
- source experiment、real-run 和 report SHA-256；
- 非 Secret 的 Secret-scan receipt。

缺少这些 provenance，或 source experiment/report/Runner/Agent/Dataset 的绑定发生漂移时，
真实 Proposal 和 Optimization v2 preflight 会拒绝继续执行。

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

没有匹配事件时继续保持 `UNKNOWN` 和 `abstained`。规则不读取或保存模型隐藏思维过程。

对于已经由 Trace 证明的 `VERIFICATION` task failure，桥接器还可以从 Artifact Manifest
哈希绑定的单一 `session-result.json` 补充有限的通用行为摘要。只有同时满足以下条件才会输出
“仅观察到只读检查，未观察到编辑或测试命令”：

- session artifact 的大小和 SHA-256 与不可变 Manifest 完全一致；
- transcript 中的 tool-call 数量与 Runner 声明值一致；
- 每个工具调用都能被严格识别为只读文件检查或只读 shell pipeline；
- 命令中没有重定向、命令替换、复合执行或未知 executable。

出现测试命令、未知工具、无法解析的 shell、Schema 漂移或多份 session artifact 时，桥接器
保持沉默，不把“缺少可见事件”表述为“动作没有发生”。`workspace_diff` 不参与该判断，因为它
可能包含 Agent 启动前宿主 worktree 已存在的改动。Proposal 只看到固定通用摘要，不会收到
工具参数、命令文本、路径、Patch 或测试输出。

## 人工 review/override

可通过一个简单 YAML 排除误归因，或为 task fail 的 UNKNOWN finding 提供人工确认标签：

```bash
agentskill-eval optimize prepare-failures WORKSPACE EXPERIMENT_ID \
  --output train-failures.yaml \
  --review examples/optimizer/failure-guided/failure-review.example.yaml
```

Review 不能让 `INVALID` Run 进入优化。每个决定以 `run_id + rule_id` 定位，审计报告会标记
`review_applied=true`。

## 派生旧证据

旧 bundle 是不可变输入。不要补写其中的 provider/model 或 provenance。使用以下命令从其关联的
完成态 observed-Agent experiment 派生一个新 bundle：

```bash
agentskill-eval optimize derive-failures WORKSPACE EXPERIMENT_ID PARENT_BUNDLE \
  --output derived-train-failures.yaml
```

派生 bundle 在 provenance 中记录 `parent_bundle_sha256`，并验证 parent 中的每个 diagnosis
都对应 source experiment 的 task-failed Skill treatment Run 和选中 Attempt。输出路径不能覆盖
parent；重复同一输入只接受字节完全相同的已有输出。该命令重新进行 exact Secret scan，但不保存
Secret 值。验证派生 bundle 时，除了 parent hash 和 source provenance 外，还会重新计算 parent
的确定性脱敏 diagnosis；因此即使保留原 provenance，手工篡改 finding、split 或派生名称也会被拒绝。

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
