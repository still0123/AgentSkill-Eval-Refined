# DeepSeek Optimization v2 Screening

本记录保存一次真实 DeepSeek validation-search 筛选实验的脱敏摘要。

## 实验范围

- Provider：`deepseek`
- Model：`deepseek-v4-pro`
- Dataset cases：`more-itertools-last-reversed-none`、`cachetools-cachedmethod-autospec`
- Candidate 数量：3
- 逻辑 Agent Runs：12
- 新增真实 Agent Runs：8
- 复用 baseline Runs：4
- 实际费用：183674 microusd
- `simulated`：`false`
- 搜索阶段：已执行
- regression / confirmation / locked test：均未执行

## 结果

| Candidate | Pass Rate | Gain | W/T/L | 结论 |
|---|---:|---:|---|---|
| `inspect-tool-schema-before-edit` | 1.0 | 0.0 | 0/2/0 | 与 v1 持平 |
| `verify-edit-result-and-retry` | 0.5 | -0.5 | 0/1/1 | 在 1 个 Case 上退化 |
| `map-edit-operation-to-tool-args` | 1.0 | 0.0 | 0/2/0 | 与 v1 持平 |

所有 8 次新增 Run 均有效，未发现连接错误或 invalid Run。没有候选满足“优于 v1”的唯一 winner 条件，因此不得进入 confirmation、locked test 或 Skill v2 发布。

## 证据边界

本实验只能支持：

> 在固定的两个 validation-search Case 上，对三个候选进行真实 Agent 筛选；候选 1 和 3 与 v1 持平，候选 2 在一个 Case 上退化。

不能据此声称 Skill 已改进、具备跨数据集泛化能力或已经可以发布 Skill v2。

原始 JSON/HTML 报告保存在本地实验 workspace，不提交包含绝对路径、模型输出原文、Trace 或任何 Secret 的文件。
