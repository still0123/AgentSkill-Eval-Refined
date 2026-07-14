# Real Optimizer Evaluator MVP

本模块让 Benchmark-guided Skill Search 使用真实 Agent 任务结果选择候选。它复用已有
`RealAgentEvidenceRunner`、`SkillUpRunnerAdapter`、配对实验存储、Trace 和失败诊断，
不实现第二套 Agent Runtime。

## 比较对象

每个候选和 Case 仍运行一组配对实验：

```text
同一个 Candidate + Case
├── baseline：不加载 Skill
└── treatment：加载 Candidate SKILL.md
```

Search 的候选分数来自 treatment Run；baseline 同时作为激活与环境一致性证据保留在
真实实验目录中。候选包括 `original`、`manual`、`random` 和确定性 Generator 产生的
`search-*`。

## 配置

在现有 Optimization Search YAML 中使用：

```yaml
evaluator:
  type: real_agent
  real_agent_config_path: /absolute/path/to/real-agent-evidence.yaml
  version: real-optimizer-v1
  simulated: false
```

`real_agent_config_path` 使用与 `agentskill-eval real smoke` 相同的配置契约。Search 会覆盖
其中的 Skill、DatasetVersion 和 Case pair，但不会覆盖 Provider、模型、Runner、价格、
超时或工具能力声明。

当前 MVP 使用现有两 Case 配对 Runner，因此 `subset_size` 和完整数据集减去 subset 后的
Case 数都必须是偶数。

## 预算门

真实 Search 必须同时提供三个参数：

```bash
agentskill-eval optimize search SEARCH.yaml \
  --workspace .agentskill-eval/optimizer \
  --confirm-real-run \
  --max-agent-runs RUNS \
  --max-cost-microusd BUDGET
```

缺少任一参数都会在外部调用前拒绝。控制器按完整 successive-halving 计划计算所需
Candidate/Case 组合；每个组合对应 baseline 和 treatment 两个 Agent Run。授权不足时，
不会先执行一部分候选。

CLI 在执行前向 stderr 输出 Provider、模型、候选数、Case 数、计划 Run 数、估算费用和
授权上限。真实失败不会回退 Process Mock 或 simulated evaluator。

## 幂等与证据

候选 Skill 被包装成内容哈希固定的 Skill 目录。每个已完成的
`evaluator + Skill hash + DatasetVersion hash + Case ID` 结果写入本地缓存；subset 中已完成
的 Case 在 full validation 中直接复用。缓存不包含 Secret。

每个真实 `SearchCaseResult` 保存：

- `pass`、`fail` 或 `invalid`；
- input/output Token、时延和费用；
- Experiment、Run、Attempt ID；
- Trace 与 FailureDiagnosis 路径。

完整 baseline/treatment Manifest、原始脱敏 Runner 产物、Trace、审计包和离线报告仍由
Real Agent Evidence 模块保存。Search 报告明确标记 `simulated=false`，同时声明它只是
adaptive validation 结果，不是独立终评。

## regression_dev

`FailureGuidedSkillEvolution.run(..., real_authorization=...)` 会把同一个授权对象传给 Search
和 regression gate。真实 regression 输入必须是不可变、全部标记为 `regression_dev` 的
DatasetVersion；base 与 winner 都使用同一 evaluator 执行。

## CI 边界

CI 不访问付费 Provider。Fake Process Agent 以 observed 配置运行真实适配器链，验证：

- 明确授权和预算预检；
- `simulated=false` 结果；
- treatment Case 的 Token、费用和 Trace 引用；
- 候选/Case 缓存回放不重复调用 Agent；
- 缺少确认时 CLI 在调用前拒绝。

真实 smoke 只会在用户单独确认 Provider、模型、候选数、Case 数、Run 数和预算后运行。
