# Qwen3-Coder validation_search + regression_dev

本实验在 GPU3 的本地 Qwen3-Coder 服务上运行 AgentSkill-Eval 的真实 Skill evolution 链路。它只访问 `validation_search` 和 `regression_dev`，不访问 `validation_confirm` 或 `locked_test`，也不发布 Skill v2。

## 固定实验条件

- Provider: `qwen-local`
- Model: `qwen3-coder-local`
- Agent engine: `qwen_openai_process` 0.1.0
- Runner: `skill-up` 0.5.0
- Skill v1 SHA-256: `5ff780e023c00cd08232688ec013a47f51926b4e1c8a5171465085ee967bc5d6`
- Winner candidate: `dcf2cd3c-9e74-53a9-b1ae-2728753b3249`
- Winner Skill SHA-256: `ede790a86217077a0d808f8bec9414babb0fb48f5b09efcc42bbd07ec591f1f2`
- Execution ID: `3619a365-54a1-5757-85be-1222ff8fa081`
- `simulated=false`, `real_run_confirmed=true`

## Search 结果

- 7 个候选，实际消耗 40/48 Agent Runs
- 0 microusd 本地模型费用
- 所有候选的 validation pass rate 均为 0%
- 系统选择 `search-record-verification-evidence` 作为 provisional winner；这是在所有候选得分相同情况下的搜索来源 tie-break，不是 Skill 已经变好的证明
- 2 次 HTTP 400 被记录为 `INFRA_FAILED`，没有计入成功率

## Regression 结果

- 16/16 Agent Runs，0 microusd
- regression_dev base pass rate: 0%
- winner pass rate: 0%
- W/T/L: `0 / 4 tie-negative / 0`
- loss cases: 0
- token overhead: `5.66%`
- regression gate: `COMPLETED`（表示没有回归且成本在阈值内，不表示质量提升）

## 结论边界

本实验证明了真实 Agent → 搜索 → 回归 → Trace/Diagnosis → immutable receipt 的链路，但没有证明 Skill v2 优于 v1。下一步应先改善 Agent 的可执行修复能力或候选假设，再重新进行 validation_search；confirmation、locked test 和正式发布必须保持 withheld。
