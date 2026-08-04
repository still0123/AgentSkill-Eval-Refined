# Trace Intelligence 与规则诊断

## 目标和证据边界

Trace Intelligence 回答“平台实际观测到了什么”和“哪些失败可以由确定性证据解释”。它不保存隐藏思维过程，也不把 LLM 的事后解释当成事实。缺失事件以 Capability `unavailable` 表示，而不是伪造一个空事件序列。

## 统一事件

每个 `TraceEvent` 包含连续 `sequence_no`、带时区时间、事件类型、来源、状态和有限 JSON 摘要。当前规范覆盖平台、Runner、Agent 和 Judge 来源。Executor 固定记录：

```text
platform.validation started/completed/failed
platform.runner_execution started/completed/failed
runner.started / runner.finished（Adapter 实际提供时）
platform.run_terminal completed/failed
```

Adapter 后续可发送 `tool.*`、`file.*`、`command.*`、`test.*` 和 `judge.*`。摘要最多保留三层、30 个对象字段、20 个数组成员和每字符串 1000 字符；默认最多接收 10000 个事件，超限写入 `platform.trace_truncated` 与丢弃计数；非有限浮点转换为占位符；配置 Secret 在对象键、字符串和回退表示中精确替换。

## Capability 声明

`TraceManifest.capabilities` 逐 Attempt 描述：

- `post_run_result`；
- `runner_lifecycle`；
- `tool_file_command`。

只有对应来源事件真实出现才标记 `observed`。否则标记 `unavailable` 并写明原因。未来由 Agent telemetry 或平台 Proxy 提供事件时无需改变持久化模型。

## 规则诊断

`RuleFailureDiagnoser` 当前只作可证伪的确定性判断：

- timeout、轮数或预算终态 → `BUDGET`；
- Judge/Grader 基础设施失败 → `JUDGE`；
- 其他 infra invalid → `ENVIRONMENT`；
- task pass → `no_failure`；
- task fail 且缺少足够工具/验证证据 → `UNKNOWN + abstained`。

每个 finding 保存 `root_cause/contributing_factor/observed_symptom` 角色、规则 ID、置信度、理由和引用事件序号。当前实现不会仅根据最终失败猜测 Planning、Tool 或 Skill Conflict；这些标签要等相应事件、规则和人工标注集到位后才能启用。

## 配对轨迹差异

`PairTraceDiff` 在同一 PairBlock 内比较 control/treatment：

- 各事件类型计数和 treatment−control 差；
- 事件类型序列的 Levenshtein 编辑距离；
- 两臂 Run/Attempt 证据 ID。

该差异描述相关行为变化，不构成 Skill 导致行为变化的因果证明。静态报告展示聚合数量和平均编辑距离，机器 JSON 保留全部 PairTraceDiff。

## CLI

```bash
agentskill-eval trace show WORKSPACE EXPERIMENT_UUID RUN_UUID
agentskill-eval trace compare WORKSPACE EXPERIMENT_UUID PAIR_BLOCK_UUID \
  --control CONTROL_VARIANT_UUID --treatment TREATMENT_VARIANT_UUID
```

`trace show` 输出选中 Attempt 的 Trace 与 Diagnosis；`trace compare` 输出一个配对块的结构化差异。所有文件同时进入确定性审计包。
