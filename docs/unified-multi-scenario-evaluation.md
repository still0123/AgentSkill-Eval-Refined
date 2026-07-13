# Unified Multi-Scenario Evaluation MVP

## 目标

这一层把现有软件工程、MCP 和 Memory/RAG 纵切接入同一个入口，但不抹平各自的
Case、Trace 和 Grader 语义：

```text
UnifiedScenarioSpec
        ↓
ScenarioAdapter
        ↓
existing native runner + grader
        ↓
UnifiedEvaluationResult + native reports
```

统一层负责冻结实验目标、Skill、证据等级、对照臂、通用指标和产物哈希。原生纵切继续负责
工具参数、检索、引用、记忆生命周期、测试结果等专项指标。

## 支持场景

| scenario | Adapter | 当前执行方式 | 证据边界 |
|---|---|---|---|
| `software_engineering` | `SoftwareEngineeringScenarioAdapter` | P0 paired Mock Runner | simulated controller |
| `mcp_tool` | `McpScenarioAdapter` | deterministic MCP Lab | simulated controller |
| `memory_rag` | `MemoryRagScenarioAdapter` | deterministic Memory/RAG Lab | simulated controller |

现有真实软件缺陷修复仍由 `agentskill-eval real` 的显式费用安全门负责。统一 CLI 不会把本地
simulation 自动升级为真实调用，也不会把预编译计划报告成真实 Agent 遵循 Skill 的证据。

## 公共契约

- `UnifiedScenarioSpec`：场景、comparison、原生配置、Skill 和 claim limit；
- `EvaluationPlan`：数据集、Case 数、Agent、模型、两臂、Trace capability 和内容哈希；
- `ScenarioAdapter`：`build_plan` 与 `run` 两个薄接口；
- `UnifiedEvaluationResult`：通用成功率、absolute gain、W/T/L/invalid、专项指标和原生报告；
- `ArtifactReference`：原生 JSON/HTML 的绝对路径和 SHA-256。

`comparison=skill_ab` 必须提供冻结的 Skill 名称、版本、`SKILL.md` SHA-256 和 activation
mode。`native_install` 表示 Runner 实际加载 Skill；`precompiled_plan` 表示本地确定性计划只模拟
Skill 指导后的行为，报告必须保留限制声明。

## CLI

验证三个示例，不执行 Agent：

```bash
agentskill-eval scenario validate examples/unified/software-engineering.yaml
agentskill-eval scenario validate examples/unified/mcp-tool.yaml
agentskill-eval scenario validate examples/unified/memory-rag.yaml
```

执行本地模拟场景必须显式授权：

```bash
agentskill-eval scenario run examples/unified/mcp-tool.yaml \
  --workspace .agentskill-eval/unified \
  --allow-simulation
```

读取统一结果：

```bash
agentskill-eval scenario report \
  .agentskill-eval/unified EXPERIMENT_ID
```

每个实验生成：

```text
unified/<experiment-id>/
├── unified-report.json
├── unified-report.json.sha256
├── unified-report.html
└── unified-report.html.sha256
```

原生报告和 Trace 仍保存在原纵切目录，统一结果只通过带哈希的 artifact reference 引用。

## 指标边界

统一层只提供可跨场景解释的最小字段：

- control/treatment success rate；
- absolute gain；
- wins/ties/losses；
- invalid。

MCP 的 selection/parameter/recovery/safety，Memory/RAG 的 Recall@K/citation/faithfulness/
memory safety，以及软件工程测试和 Trace 诊断都保存在 `scenario_metrics` 或原生报告。禁止将这些
异质指标机械加权成一个“通用 Skill 总分”。

## 当前限制与下一步

- 三个统一示例均为 `simulated=true`；
- MCP 与 Memory/RAG 使用 `precompiled_plan`，尚不是观察真实 Agent 加载 Skill 的证据；
- 软件工程统一示例使用 Python Review Demo，真实 Bug Fix 证据继续通过 `real` 命令运行；
- 本阶段没有 FastAPI、MQ、远程队列或多租户。

下一步应为 MCP 与 Memory/RAG 增加 Process/真实 Agent 的 Skill 激活适配，同时保持相同的
`EvaluationPlan` 和 `UnifiedEvaluationResult`，之后再把失败诊断接入 Skill v1→v2 优化闭环。
