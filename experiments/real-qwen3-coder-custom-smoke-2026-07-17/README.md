# Qwen3-Coder Custom Engine Smoke

本实验验证 GPU3 上本地 Qwen3-Coder 通过 skill-up Custom Engine 接入
AgentSkill-Eval 的真实执行链路。它不是性能实验，也不是 Skill 优化结论。

## 固定输入

- Provider: `qwen-local`
- Model: `qwen3-coder-local`
- Agent engine: `qwen_openai_process` / `qwen-openai-process-agent` 0.1.0
- Runner: `skill-up` 0.5.0
- DatasetVersion: `4bc234b5-7531-59cc-8ecb-325b9dc288e9`
- Cases: `more-itertools-islice-release`, `cachetools-lru-missing-item`
- Skill: `python-bug-fix-v1`
- Agent runs: 4（baseline 2 + treatment 2）
- `simulated=false`, `evidence_class=observed_agent`, `real_run_confirmed=true`

## 结果

- Completed: `4/4`
- Invalid: `0`
- Baseline pass rate: `0%`
- Treatment pass rate: `0%`
- W/T/L: `0 / 2 tie-negative / 0`
- Observed cost: `0 microusd`（本地无认证 vLLM；平台最大预算为 4 microusd）
- Replay bundle: `92fda91e-978b-5521-bfcf-a852df0897c6`

本次结果只证明 Agent → Runner → Trace → Grader → Report 的真实链路可执行；
两个 Case 的 0% 通过率说明当前 smoke 配置尚未达到 Bug Fix 任务成功条件，不能据此
声称 v1 或 v2 的优劣。下一步应先进行小规模失败诊断和候选验证，仍不消费 locked test。

