# Stage 3 real train failure collection smoke

本目录保存 Stage 3 DeepSeek Skill Proposal 前置 train smoke 的脱敏证据。实验使用两个由真实
Git 历史重建、属于冻结 train split 的 Case，对 `python-bug-fix-v1` 执行一次 baseline/treatment
配对运行。

结果是一个应当保留的负结果：4 个计划 Run 中 2 个完成、2 个因工具调用预算耗尽 invalid。
`cachetools-lru-missing-item` 的两臂均通过；`more-itertools-islice-release` 的两臂均 invalid。
Skill treatment arm 没有可优化的 task failure，Observed Failure Bridge 因而返回
`INSUFFICIENT`，没有生成 `FailureEvidenceBundle`。

原始运行由旧解析器以通用 `execution_error`/`ENVIRONMENT` 保存。后续离线重放确认 Runner 的
直接原因是两臂都在第 25 次工具调用超过冻结上限 24，现更正为
`budget_exhausted`/`BUDGET`；不可变原始证据未被重写。根据 Stage 3 协议，平台预算耗尽不能作为
Skill 修改依据。因此已授权的 1 次 DeepSeek
proposal 调用没有执行，也没有产生 proposal 费用。该结果证明系统会在证据不足时停止，而不会
为了生成候选而修改 label、借用 validation Case 或回退 simulated failure YAML。

证据边界：本实验只有两个 Case 且其中一个 Case 的两臂均 invalid，只能说明真实 train 数据采集
和不足证据门正常工作，不能评价 Skill 的普遍效果。仓库不保存 API Key、原始 Runner 日志、缓存
或本地绝对路径。

- Experiment：`41ff1ca2-ab2e-5990-b05b-70b7aa1f274d`
- Provider/model：DeepSeek / `deepseek-v4-pro`
- Agent/Runner：Qwen Code 0.19.9 / skill-up 0.5.0
- Agent 实际费用：`101266 microusd`（授权上限 `300000`）
- Proposal 调用：`0/1`
- 更正后失败类型：`budget_exhausted` / `BUDGET`（observed tool calls：25，上限：24）
- Replay bundle SHA-256：
  `e158f1f9ba4edcb29c819dfcc4e158cbe578e623bbdecbeeebb753f8cb723579`
