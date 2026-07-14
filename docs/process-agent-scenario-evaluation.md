# Process Agent Scenario Evaluation MVP

## 目标

上一阶段的 MCP 与 Memory/RAG 示例直接从 YAML 读取 baseline/treatment `AgentPlan`。本阶段
增加一个真正的进程决策边界：每个 Case、每个 Variant 启动一个哈希固定的本地 Agent 进程，
由进程根据任务和可见环境生成计划，再交给既有 Controller、Mock Tool/Retriever/Memory 和
Grader 执行。

```text
Case + visible environment              Case + visible environment + SKILL.md
              │                                         │
       baseline process                         treatment process
              └──────────────┬──────────────────────────┘
                             ▼
                  strict AgentPlan JSON
                             ▼
          MCP / Memory-RAG Controller + deterministic Lab
                             ▼
                   Trace + Grader + unified report
```

这证明 Skill 激活、Agent 决策、环境执行和评分之间的集成链路，不证明真实模型能力。因为当前
CI 使用 Fake Process Agent，工具和 Memory/RAG 后端仍是 deterministic simulation，所以结果固定为
`simulated=true, evidence_class=process_integration`。

## Process Agent 契约

Agent 从 stdin 接收一个 JSON 对象：

```json
{
  "schema_version": "ase/process-agent-request/v1alpha1",
  "scenario": "mcp_tool",
  "case_id": "document-lookup",
  "variant": "with_guidance",
  "case": {},
  "skill": {
    "name": "mcp-tool-use-v1",
    "version": "1.0.0",
    "sha256": "...",
    "content": "..."
  },
  "output_contract": "agent_plan_only_no_hidden_reasoning"
}
```

baseline 的 `skill` 必须为 `null`；treatment 才包含经过 SHA-256 校验的 Skill。`case` 只包含
Agent 在真实任务中应当可见的信息，不包含 expected tools、gold answer、memory expectations、
oracle 或参考补丁。

Agent stdout 必须只包含：

```json
{
  "schema_version": "ase/process-agent-response/v1alpha1",
  "plan": {}
}
```

MCP `plan` 使用现有 `AgentPlan(actions, final_response, token_count, cost_usd)`；Memory/RAG
`plan` 使用现有 retrieval、generation 和 memory action 契约。额外的 reasoning、thoughts、日志
或 Markdown 会被拒绝；stderr 不进入报告。

## 安全与审计

- 可执行文件必须是非 symlink 普通文件并匹配 SHA-256；
- `--version` 输出必须与冻结配置完全一致；
- 使用参数数组和 `shell=false`；
- 只继承显式允许的最小环境；Secret-like 环境变量名直接拒绝；
- 每次决策使用新进程组，超时、取消或异常时终止整个进程组；
- stdout 有大小、深度、字段数和严格 Schema 限制；
- 不保存模型隐藏思维过程；
- 不保存请求正文、Skill 正文、Case 敏感数据或 stderr；
- 只保存 executable/Skill/request/response SHA-256、Variant、耗时和退出码；
- 统一结果存在时直接幂等读取，不再次启动 Agent。

逐决策证据写入 `process-agent-decisions.json`，并作为带 SHA-256 的 artifact reference 进入统一
报告。baseline evidence 必须显示 `skill_present=false, skill_sha256=null`，treatment 必须绑定
冻结 Skill hash。

## 配置与运行

复制模板：

```bash
cp examples/unified/mcp-tool.process.example.yaml /tmp/mcp-process.yaml
```

替换 executable、版本和 SHA-256：

```bash
shasum -a 256 /absolute/path/to/process-agent
/absolute/path/to/process-agent --version
```

然后执行：

```bash
agentskill-eval scenario validate /tmp/mcp-process.yaml
agentskill-eval scenario run /tmp/mcp-process.yaml \
  --workspace .agentskill-eval/process-scenarios \
  --allow-simulation
```

`--allow-simulation` 仍然必须显式提供，因为真实的是“Agent 决策进程”，不是工具环境或性能
证据等级。此命令不会读取 API Key；需要真实 Provider 的实验继续走 `agentskill-eval real` 的
确认、费用和 Run 数安全门。

## 当前边界

- 支持 MCP 与 Memory/RAG；软件工程继续复用成熟的 skill-up/real Runner；
- Process Agent 一次生成完整计划，不是逐工具回合的交互式 Agent loop；
- 工具、Retriever 和 Memory 后端仍为 deterministic Lab；
- 没有 Provider Token、真实 MCP Server、生产知识库或付费模型；
- 不将 Fake Agent 的 100% treatment 通过率解释为 Skill 性能提升。

下一步可以在保持同一请求/响应和证据契约的前提下，实现逐步 tool loop，或由现有 Real Agent
Runner 产生同样的标准 `AgentPlan`。只有经过显式真实运行授权后，证据等级才能升级。
