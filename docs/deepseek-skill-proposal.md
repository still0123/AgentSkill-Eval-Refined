# DeepSeek Skill Proposal MVP

本阶段用一次显式授权的 DeepSeek 调用替换 Fake Process Generator，目标是根据真实 train
失败证据提出 3～5 个可审计的 Skill 修改方向。生成器只负责提出候选；候选是否有效仍由
固定 Benchmark、真实 Agent evaluator、`regression_dev` 和后续独立终评决定。

## 数据边界

生成器只接收：

- 冻结的 base `SKILL.md`；
- 脱敏的 train `FailureEvidenceBundle`；
- eligible failure label、规则标识和 Trace 事件编号；
- 结构化输出约束。

请求不包含 `validation_search`、`regression_dev`、`validation_confirm`、`locked_test`、参考
补丁或 Case 答案。原始失败 rationale、模型隐藏推理、API Key、原始请求和原始响应均不落盘。

## Provider 配置

第一版仅支持 DeepSeek 官方 OpenAI-compatible Chat Completion API。示例见
[`deepseek-evolution.example.yaml`](../examples/optimizer/failure-guided/deepseek-evolution.example.yaml)。
需要冻结：

- `base_url`、`model`、temperature 和最大输出 token；
- prompt 版本及其 SHA-256；
- 输出 schema SHA-256；
-用于预算核算的输入 cache miss/hit 与输出单价。

价格字段属于实验配置，不会被代码静默更新。执行真实实验前应根据 DeepSeek 官方文档核对：

- <https://api-docs.deepseek.com/api/create-chat-completion>
- <https://api-docs.deepseek.com/guides/json_mode>

## Secret

生成器只读取 `OPENAI_API_KEY`，不接受命令行 Key，也不会把值写入 Manifest 或报告：

```bash
export OPENAI_API_KEY="$(security find-generic-password \
  -a "$USER" -s agentskill-eval-deepseek -w)"
```

运行结束后可执行 `unset OPENAI_API_KEY`。本地 Fake API 测试使用同名假值，CI 不需要真实 Key。

## 预算门与执行命令

缺少确认、最大调用数或最大预算时，CLI 会在发起 HTTP 请求前拒绝执行。生成器授权与真实
Agent evaluator 授权相互独立，simulation 不会自动升级为真实调用，失败也不会回退 Fake。

只验证 DeepSeek 候选生成链路、使用模拟 evaluator：

```bash
agentskill-eval optimize evolve run \
  examples/optimizer/failure-guided/deepseek-evolution.example.yaml \
  --workspace .agentskill-eval/deepseek-proposal \
  --allow-simulation \
  --confirm-generator-run \
  --max-generator-calls 1 \
  --max-generator-cost-microusd 1300000
```

若同时使用阶段 2 的真实 evaluator，还必须另行添加：

```text
--confirm-real-run
--max-agent-runs <COUNT>
--max-cost-microusd <VALUE>
```

这两个预算不能合并理解：前者约束一次候选生成，后者约束多个 candidate/case Agent Run。

## 输出与审计

一次成功调用生成不可变 `hypotheses.json`，并在 evolution report 中记录：

- provider、model 和生成器版本；
- prompt、schema、请求、响应与最终 hypotheses 的哈希；
- 每个候选的 failure lineage、改进假设、通用 instruction 和风险；
- 输入、cache hit、输出和 reasoning token 计数；
- latency、按冻结单价计算的费用；
- `raw_request_stored=false`、`raw_response_stored=false`、
  `hidden_reasoning_stored=false`、`secret_value_stored=false`。

同一 evolution 的 artifact 已存在且哈希一致时，重复命令直接复用结果，不产生第二次模型费用。
响应 JSON 不合法、label 不属于 eligible 集合、候选数越界或 API 请求失败都会终止流程。

## 结论边界

“DeepSeek 生成了合法候选”只证明 proposal 链路可用，不证明候选更好。只有候选通过真实
validation search、`regression_dev`，并在阶段 4 的 confirmation/locked evaluation 中通过后，
才能进入不可变 Skill v2 发布流程。少量 Case 的 smoke 只能作为链路证据，不能声称普遍提升。
