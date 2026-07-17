# DeepSeek Proposal v7 Validation Search

本记录保存一次经授权的真实 validation_search。实验在第 2 个候选出现 Runner
`loop_detected` invalid 后按协议 fail-fast 停止。

## 执行信息

- Provider：`deepseek`
- Model：`deepseek-v4-pro`
- Dataset cases：`more-itertools-last-reversed-none`、`cachetools-cachedmethod-autospec`
- 已评测候选：2 / 4
- 逻辑计划 Runs：16
- 已消耗真实外部 Runs：6
- 复用 baseline Runs：2
- 实际费用：145739 microusd
- `simulated`：`false`
- `search_executed`：`true`
- regression / confirmation / locked test：均未执行

## 已完成候选

| Candidate | v1 Pass Rate | Candidate Pass Rate | Gain | W/T/L | Invalid |
|---|---:|---:|---:|---|---:|
| `enforce-post-edit-test-rerun` | 0.0 | 0.5 | +0.5 | 1/1/0 | 0 |
| `validate-test-coverage` | 0.0 | 0.5 | +0.5 | 1/1/0 | 1 |

第 2 个候选在 `cachetools-cachedmethod-autospec` 上触发 Qwen Code 的
`read_file_loop`，被归类为 `PLANNING` / `INFRA_FAILED` invalid。由于存在 invalid，系统没有继续评测
候选 3 和候选 4，也没有选择 winner。

## 证据边界

本记录只支持：在两个 validation-search Case 上，前两个候选产生了部分真实执行结果，且第 2 个候选触发了 invalid。它不支持 Skill 改进、跨数据集泛化、confirmation、locked test 或 Skill v2 发布结论。

原始 JSON/HTML、Trace 和 Runner 日志保存在本地审计 workspace，不提交绝对路径、模型输出原文或 Secret。
