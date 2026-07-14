# Failure-Guided Skill Evolution MVP

本阶段把已有的 Trace Intelligence、Failure Diagnosis、Benchmark-guided Skill Search 与
Independent Final Evaluation 连成一条受控链路：

```text
train FailureDiagnosis
        │
        ├── eligible Agent / Skill failures
        └── excluded infra / budget / Judge / unknown failures
        │
        ▼
sanitized OptimizationContext
        │
        ▼
ImprovementHypothesis + explicit MutationSpec
        │
        ▼
existing successive-halving Skill Search
        │
        ▼
regression_dev gate
        │
        ▼
frozen candidate handoff
        │
        ▼
existing Independent Final Evaluation
```

它解决的是“失败如何转化为下一版 Skill 的可验证修改方向”，而不是自动宣布一个 Skill 已经
变好。输出始终是候选，不能自动发布为 Skill v2。

## 信任边界

- 失败证据必须显式标记为 `split: train`。
- Optimizer 契约没有 `locked_test` 路径，也不接收 final grader、逐 Case locked 分数或轨迹。
- `ENVIRONMENT`、`BUDGET`、`JUDGE`、`UNKNOWN` 与 abstained 诊断不得转化为 Skill 指令。
- 优化上下文只保存 label、rule ID、置信度和事件序号，不保存诊断 rationale、模型隐藏思维或
  原始 Secret。
- 候选只使用既有 `validation_search` 排序；胜者还必须通过独立 `regression_dev` 门。
- 最终交接固定为 `AWAITING_INDEPENDENT_FINAL_EVALUATION`，且
  `locked_test_accessed=false`、`auto_publish=false`。
- 当前 MVP 拒绝 `simulated=false` evaluator，避免绕过真实模型的确认、Run 数和预算安全门。

## 失败资格与假设生成

确定性生成器按失败标签映射为可审计的改进假设和一条明确增量指令。至少需要三个不同、受支持
的 eligible failure labels，防止单个偶然失败主导搜索。每个假设保存如下信息：

- 稳定 hypothesis ID；
- 原始 failure label；
- 可证伪的改进假设；
- 追加到候选 Skill 的增量指令；
- `diagnosis://run-id/rule-id` 证据引用；
- 已知副作用风险。

默认生成器不是 LLM。系统也支持哈希固定的本地 Process Generator，完整信任边界见
[Audited Process Skill Proposal Generator](./audited-process-skill-proposal-generator.md)；Fake Process
示例仍只证明诊断到候选的控制链路、谱系和隔离规则，不证明真实模型的自动写作质量。

## 候选搜索和回归门

生成的 `MutationSpec` 交给已有 `BenchmarkGuidedSkillSearch`，继续复用：

- Skill lint 与大小限制；
- original、manual、random、search 对照；
- successive halving 和固定随机种子；
- 固定 candidate-case evaluation 预算；
- Pareto 选择、最大退化 Case 数与 Token overhead 限制；
- 不可变 Candidate lineage 和 search report。

搜索冻结的 validation winner 随后在独立 `regression_dev` 数据集上与 base Skill 对比。只有 loss
Case 数和 Token overhead 同时满足预注册阈值，才会生成 final-evaluation handoff。
`regression_dev` 仍属于开发数据，不能替代 `validation_confirm` 或 `locked_test`。

## 配置与运行

示例配置：

- `examples/optimizer/failure-guided/evolution.example.yaml`
- `examples/optimizer/failure-guided/train-failures.yaml`
- `examples/optimizer/failure-guided/regression-dev.yaml`
- `examples/optimizer/failure-guided/process-evolution.example.yaml`

运行确定性演示：

```bash
agentskill-eval optimize evolve run \
  examples/optimizer/failure-guided/evolution.example.yaml \
  --workspace .agentskill-eval/evolution \
  --allow-simulation
```

读取不可变状态：

```bash
agentskill-eval optimize evolve status \
  .agentskill-eval/evolution EVOLUTION_ID
```

缺少 `--allow-simulation` 时演示会拒绝执行。相同输入重放使用稳定 evolution ID 和既有不可变
产物，不重复执行 regression evaluator。

## 审计产物

每个 `evolution-jobs/EVOLUTION_ID/` 包含：

- `optimization-context.json`：脱敏资格决策；
- `hypotheses.json`：生成器身份、假设、指令、风险和证据引用；
- `regression-gate.json`：base/winner 的逐 Case 开发回归结果；
- `final-evaluation-handoff.json`：冻结 winner 及独立终评状态；
- `evolution-report.json`：机器可读汇总；
- `evolution-report.html`：无外部脚本、严格转义的离线报告。

候选正文、评测与谱系继续保存在既有 optimization job 中。已存在的同路径产物若内容变化，系统
会拒绝覆盖。

## 结论限制与后续方向

当前示例是 `simulated=true`，只能证明 Failure → Hypothesis → Search → Regression → Handoff
管线正确。下一步不是让 Optimizer 直接读取 locked test，而是：

1. 用真实但有预算安全门的 evaluator 运行冻结搜索协议；
2. 为 hypothesis generator 增加受控的 Process/LLM Adapter、结构化输出和成本上限；
3. 引入独立 `validation_confirm` 以降低 validation search 选择偏差；
4. 由现有 Independent Final Evaluation 对唯一冻结 winner 做一次预注册终评；
5. 只有人工审核和发布门通过后，才创建不可变 Skill v2。
