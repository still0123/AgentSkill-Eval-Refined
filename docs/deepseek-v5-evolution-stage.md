# DeepSeek 主执行链路与 Proposal v5 阶段

本阶段将真实 Agent 执行模型切换为 DeepSeek，同时保留 Qwen + Proposal v4
作为已冻结的负向对照。DeepSeek 既可生成 Proposal v5 候选，也可作为被测
Agent 执行真实 Bug Fix；最终是否成功仍由独立测试和配对实验决定，不由模型
自评。

## 固定输入

Proposal v5 只能读取：

- `examples/skills/python-bug-fix-v1/SKILL.md`；
- `examples/optimizer/failure-guided/qwen3-cachetools-failure-bundle-v4.yaml`。

不得读取 validation、regression、confirmation 或 locked_test 的答案、补丁和
执行结果。配置见
[`qwen3-cachetools-real-proposal-v5.example.yaml`](../examples/optimizer/failure-guided/qwen3-cachetools-real-proposal-v5.example.yaml)。

## Agent smoke

DeepSeek Agent 配置见
[`deepseek-boltons-smoke-v1.example.yaml`](../examples/real-agent-evidence/deepseek-boltons-smoke-v1.example.yaml)。
它复用 Qwen Code 作为 OpenAI-compatible Process wrapper，但 provider/model 固定
为 `deepseek/deepseek-v4-pro`，不再使用本地 Qwen 模型。

第一步只运行两个真实 Case 的 baseline/treatment 配对，共 4 Runs：

```text
boltons-split-maxsplit-zero
boltons-lri-replacement
```

smoke 的必要条件是 4/4 valid、没有 HTTP/Runner 错误，并保存 Trace、Token、时延和
费用。smoke 之前必须报告模型、Run 数和最大预算并等待授权。

## Proposal v5 的优化方向

Proposal v4 主要强调工具 Schema 检查，但没有改善代码修复成功率。v5 应要求候选
围绕可执行闭环提出假设：

1. 先运行目标回归测试并读取失败输出；
2. 定位与测试对应的实现路径和调用约束；
3. 修改前确认当前文件内容，修改后确认工具返回成功；
4. 修改后重新运行目标测试，并根据失败结果继续修复；
5. 只在测试通过且 diff 可审计时结束。

这些是候选假设的方向，不是测试答案。候选生成完成后，先做 4-Run smoke；只有
smoke 通过，才允许在单独授权下进行不超过 12 Runs 的小规模候选筛选。

## 结论边界

smoke 只验证 DeepSeek Agent 链路可用，不证明 Proposal v5 有效。小规模筛选只
产生 provisional winner，不进入完整 `validation_search`、`regression_dev`、
`validation_confirm`、`locked_test` 或 Skill v2 发布。
