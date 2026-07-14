# DeepSeek Skill Proposal MVP

本阶段用一次显式授权的 DeepSeek 调用替换 Fake Process Generator，目标是根据脱敏 train
失败证据提出 3～5 个可审计的 Skill 修改方向。独立 `optimize proposal` 命令只生成候选，
不会创建 search job，也不会执行 `validation_search`、`regression_dev`、confirmation 或
`locked_test`。候选是否有效必须由后续阶段另行验证。

## 数据边界

生成器只接收：

- 冻结的 base `SKILL.md`；
- 脱敏的 train `FailureEvidenceBundle`；
- eligible failure label、规则标识和 Trace 事件编号；
- 结构化输出约束。

请求不包含 `validation_search`、`regression_dev`、`validation_confirm`、`locked_test`、参考
补丁或 Case 答案。原始失败 rationale、模型隐藏推理、API Key、原始请求和原始响应均不落盘。

## Provider 配置

第一版仅支持 DeepSeek 官方 OpenAI-compatible Chat Completion API。Proposal-only 示例见
[`deepseek-proposal.example.yaml`](../examples/optimizer/failure-guided/deepseek-proposal.example.yaml)。
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

先执行不产生费用的 preflight：

```bash
agentskill-eval optimize proposal preflight \
  examples/optimizer/failure-guided/deepseek-proposal.example.yaml
```

preflight 会输出 provider、model、候选数、prompt/schema/request hash、冻结参数、计划调用数和
保守费用上界。确认后运行：

```bash
agentskill-eval optimize proposal run \
  examples/optimizer/failure-guided/deepseek-proposal.example.yaml \
  --workspace .agentskill-eval/deepseek-proposal \
  --confirm-real-run \
  --max-calls 1 \
  --max-cost-microusd VALUE
```

本命令不存在 Agent Run 预算，因为它不会执行任何 evaluator。缺少确认、调用数或费用预算时，
会在 HTTP 请求前拒绝。重复运行同一已完成 Job 会校验并复用不可变 artifact，不再次调用模型。

检查审计产物：

```bash
agentskill-eval optimize proposal inspect PROPOSAL_JOB_DIR
agentskill-eval optimize proposal verify PROPOSAL_JOB_DIR
```

## 输出与审计

一次成功调用生成独立的 proposal job：

```text
proposal-jobs/<job-id>/
├── proposal-manifest.json
├── proposals.json
├── proposal-report.json
└── proposal-report.html
```

Manifest 和报告记录：

- provider、model 和生成器版本；
- prompt、schema、请求、响应与最终 hypotheses 的哈希；
- 每个候选的 failure lineage、改进假设、通用 instruction 和风险；
- 输入、cache hit、输出和 reasoning token 计数；
- latency、按冻结单价计算的费用；
- `raw_request_stored=false`、`raw_response_stored=false`、
  `hidden_reasoning_stored=false`、`secret_value_stored=false`。

同一 proposal job 的 artifact 已存在且哈希一致时，重复命令直接复用结果，不产生第二次模型费用。
响应 JSON 不合法、label 不属于 eligible 集合、候选数越界或 API 请求失败都会终止流程。

`proposals.json` 中每个候选包含 failure label、修改理由、可追加的通用 instruction、风险和
脱敏 diagnosis lineage。原始 rationale、请求正文、响应正文、API Secret 与隐藏推理不落盘。

## 结论边界

“DeepSeek 生成了合法候选”只证明 proposal 链路可用，不证明候选更好。只有候选通过真实
validation search、`regression_dev`，并在阶段 4 的 confirmation/locked evaluation 中通过后，
才能进入不可变 Skill v2 发布流程。少量 Case 的 smoke 只能作为链路证据，不能声称普遍提升。

## 真实 smoke 证据

2026-07-14 完成首次 proposal-only DeepSeek smoke：1 次 `deepseek-v4-pro` 调用生成 4 个
结构化候选，输入／输出为 998 / 559 Token，记录费用 921 microusd，低于 10,000 microusd
授权上限。Artifact verify 与 Secret 扫描通过，`search_executed=false`、
`locked_test_accessed=false`。脱敏记录见
[`real-llm-proposal-deepseek-v4-pro-smoke-2026-07-14`](../experiments/real-llm-proposal-deepseek-v4-pro-smoke-2026-07-14/README.md)。

输入 failure fixture 是 synthetic/simulated，因此该结果证明真实 Provider proposal 链路，
不证明候选来源于真实 Agent 失败，也不证明候选可以提升 Skill。
