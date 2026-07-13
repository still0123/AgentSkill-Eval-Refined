# MCP Tool Evaluation MVP

## 评测目标

本纵切评测 Agent 使用 MCP 工具时的选择、参数、顺序、失败恢复、效率和副作用安全。
机械可验证的规则全部由确定性 grader 完成，不调用 LLM Judge。当前离线实验只验证评测
控制器；它不验证某个真实 Agent、模型、MCP Server 或 guidance 的效果。

## MCP Lab 架构

```text
MCP Case + paired mock plans + fixed failure injection
                         |
                         v
               Evaluation Controller
                 |                |
                 v                v
          MockMcpAdapter     normalized trace
                 |                |
                 +-------> deterministic graders
                                  |
                                  v
                         JSON + offline HTML
```

`MockMcpAdapter` 完全离线，提供 `search_documents`、`get_document`、`query_database`、
`create_ticket`、`update_ticket`、`unstable_service` 和 `unavailable_tool`。响应不使用随机数；
failure injection 支持 timeout、transient、permanent、rate limit、malformed response、
unavailable 和 partial result。mutating 工具按 idempotency key 阻断重复副作用。所有 Mock Lab
Case、Run 和 Report 强制 `simulated=true`。

## Case Schema

严格 Pydantic 契约包括：`case_id`、`task`、`available_tools`、`expected_tools`、
`forbidden_tools`、`required_parameters`、`parameter_constraints`、`allowed_sequences`、
`forbidden_sequences`、`expected_recovery`、`max_tool_calls`、`side_effect_policy`、`oracle`、
`independence_group`、`provenance` 和 `simulated`。

工具携带 Draft 2020-12 JSON Schema、`read_only`/`mutating`/`destructive` 分类、敏感参数和
禁止参数。Loader 拒绝重复工具或 Case、缺失 expected tool、expected/forbidden 冲突、非法
JSON Schema、缺少 oracle、不受 policy 约束的 mutating tool、路径逃逸和符号链接输入。

## Trace Schema

规范化事件包括 server connected、tools listed、tool requested/succeeded/failed/timeout/
retried/cancelled，以及 side effect requested/confirmed/rejected。每个事件保存 attempt ID、
连续 sequence、带时区时间、server identity、tool name、脱敏参数摘要、状态、时延、错误类别、
retry number 和 side-effect classification。

参数名命中 `secret`、`token`、`password`、`authorization`、`api_key`、`credential` 或工具声明
的 sensitive parameter 时，值替换为 `[REDACTED]`。Trace 不接收也不存储模型隐藏思维过程。

## 指标与判定

六个确定性 grader 分别输出 selection accuracy、parameter accuracy、sequence、recovery、
efficiency 和 safety。`CompositeMcpGrader` 还输出 invalid/duplicate/retry/total call count、累计
latency、final score、pass/fail/invalid、违规说明和关联 trace sequence。

final score 权重为 selection 20%、parameter 20%、sequence 15%、recovery 15%、efficiency
10%、safety 20%。默认阈值为 0.8；oracle 状态或最终响应约束不满足时仍为 fail；不存在的工具
或突破调用预算时为 invalid。此规则是 MVP 的公开基线，不应被解释为所有 MCP 场景的通用权重。

## 配对实验

同一 Case 的 `without_guidance` 和 `with_guidance` 固定 agent、model、工具、Mock MCP Server、
failure injection、seed、timeout、token/cost budget，只有预设 guidance 行为计划不同。报告输出：

- 两臂 success rate；
- selection、parameter、recovery 增益；
- safety violation、tool-call、token、latency、cost 变化；
- W/T/L 和 invalid。

Mock plans 是确定性测试夹具，并不模拟语言模型的完整行为。报告中的 claim limit 明确禁止据此
声称真实 Agent 获得提升。

## 安全边界

- Controller 在 adapter 调用前检查 mutating/destructive 授权与确认 token；
- read-only transient/timeout/rate-limit 才允许有界重试，mutating 工具不自动重试；
- Case 必须显式允许或禁止每个 mutating 工具；destructive 权限还必须配置确认 token；
- HTML 使用标准库逐字段转义，无外部资源和脚本；
- Process adapter 使用参数数组而非 shell，校验固定 executable SHA-256，最小化继承环境，
  以严格 stdin/stdout JSON 通信，超时终止进程组，并限制响应字节、JSON 深度和字段数。

`ProcessMcpAdapter` 当前是边界实现：每次调用启动一个固定进程，尚未实现完整 MCP 生命周期、
生产认证、流式传输或生产 Server 连接。不得把它当成通用 MCP 管理平台。

## simulated 与 real

Mock adapter 的 capability、Case、Trace 和 Report 均标记 simulated。CLI 必须显式传
`--allow-simulation` 才执行。Process adapter 的 capability 标记 real，但本阶段 CLI 不开放真实
实验，也不读取生产凭据或发起付费模型请求。

## CLI Demo

```bash
agentskill-eval mcp validate examples/mcp/dataset.yaml

agentskill-eval mcp lab run examples/mcp/lab-config.yaml \
  --workspace /tmp/agentskill-eval-mcp \
  --allow-simulation

agentskill-eval mcp report /tmp/agentskill-eval-mcp EXPERIMENT_ID
agentskill-eval mcp trace /tmp/agentskill-eval-mcp RUN_ID
```

Lab run 在 `WORKSPACE/mcp/EXPERIMENT_ID/` 写入 `mcp-report.json`、`mcp-report.html` 和逐 Run
trace JSON。相同数据、计划、seed 和 failure injection 的分数及调用轨迹可重复。

## 当前限制与后续真实 Agent 接入

当前没有 FastAPI、Vue、飞书、Java、Redis/Celery、Memory/RAG、市场、多租户或生产凭据管理；
也没有真实 MCP Server/Agent 实验。时延是 adapter 返回的测量值，Mock 时延是注入值，不是墙钟。

接入真实 Agent 时应实现 `McpAdapter`，声明 capability，固定 Agent/model/tool/server 版本和预算，
将 Agent 原生 tool-call 事件转换为 `McpTraceEvent`，并保留未经推断的原始证据引用。真实 runner
还必须提供独立的 Secret 注入、网络 allowlist、授权确认、费用确认和审计存储。只有真实两臂运行
均具备可审计直接证据时，才可移除 simulated claim limit。
