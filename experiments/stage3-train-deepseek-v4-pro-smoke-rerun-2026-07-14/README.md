# Stage 3 train smoke rerun with explicit tool budget

本目录保存提高并冻结工具调用上限后的第二次 Stage 3 真实 train smoke 脱敏证据。实验仍使用
相同两个 train Case、相同 `python-bug-fix-v1`、Qwen Code 0.19.9、skill-up 0.5.0 和
DeepSeek `deepseek-v4-pro`。唯一运行配置变化是将独立的工具调用硬上限从 24 调整为 48；
session turns 仍为 24，循环检测保持启用。

4 个计划 Run 中 2 个完成、2 个 invalid。`cachetools-lru-missing-item` 两臂均通过；
`more-itertools-islice-release` 的 baseline 被 action-stagnation 循环检测中止，treatment 达到
session-turn 上限。后续离线重放分别将其精确归类为 `loop_detected`/`PLANNING` 和
`turn_limit`/`BUDGET`，不可变原始运行证据不重写。

Observed Failure Bridge 只允许已经完成评分的 treatment task failure 进入优化；本次 treatment
是 invalid budget termination，因此返回 `INSUFFICIENT`。已授权的 DeepSeek proposal 调用没有
执行，调用数与费用均为 0。不能关闭循环检测、把 invalid 改写为 task failure，或借用 validation
Case 来强行生成候选。

- Experiment：`8120329e-5ec1-575d-8977-6295cd0398e5`
- Agent Runs：`4`（2 completed，2 invalid）
- Agent 实际费用：`100941 microusd`（授权上限 `220000`）
- 实际墙钟时间：约 6 分 51 秒
- Failure Bridge：`INSUFFICIENT`（0 eligible）
- Proposal：`0/1` Call，`0/100000 microusd`
- Replay bundle SHA-256：
  `8f1010acc5540dda4f8fc57998df23b8908e46e5415dd87c92b6841010aea2b3`

证据边界：只有两个 Case 且其中一对完全 invalid，只能验证真实运行、预算门和不足证据停止逻辑，
不能评价 Skill 效果或支持生成 Skill v2。
