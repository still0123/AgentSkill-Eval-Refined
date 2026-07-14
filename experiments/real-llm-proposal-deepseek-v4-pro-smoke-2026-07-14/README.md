# Real LLM Skill Proposal smoke — 2026-07-14

本目录记录 Stage 1 Proposal-only 的首次真实 DeepSeek smoke。调用使用
`deepseek-v4-pro`，只读取冻结的 Python Review v1 `SKILL.md` 与脱敏的 train failure
fixture；没有创建 search job，也没有访问 regression、confirmation 或 locked test。

## 结果

- 真实 Provider 调用：1 次；
- 结构化候选：4 个；
- 输入／输出 Token：998 / 559；
- 记录费用：921 microusd（授权上限 10,000 microusd）；
- latency：约 10.03 秒；
- proposal artifact 校验：通过；
- Secret 实值与通用 Key pattern 扫描：通过；
- `search_executed=false`；
- `locked_test_accessed=false`。

## 候选方向

1. normalization boundary check；
2. exception cleanup plan；
3. retry accounting guidance；
4. runtime evidence requirement。

这些候选尚未经过 validation search 或独立终评。本 smoke 只证明真实 LLM proposal-only
链路和审计边界可用，不证明任何候选优于 base Skill。

原始请求、原始响应、隐藏推理和 API Secret 均未保存。本目录只提交脱敏配置与结果摘要；
本地不可变完整 artifact 保存在忽略的 `.agentskill-eval` workspace 中。
