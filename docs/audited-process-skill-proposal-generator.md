# Audited Process Skill Proposal Generator MVP

该阶段把 Failure-Guided Skill Evolution 的确定性映射扩展为可插拔的本地 Process Generator。
Generator 根据 base Skill 和脱敏后的 `train` FailureDiagnosis 提出结构化改进假设；控制器再将提案
转换为带证据谱系的 MutationSpec，复用已有 Search、`regression_dev` 和 Independent Final
Evaluation handoff。

```text
train labels + base Skill
          │
          ▼
hash/version-pinned Process Generator
          │
          ▼
strict hypotheses (no rationale)
          │
          ▼
existing Search → regression_dev → frozen handoff
```

## Generator 可见内容

请求只包含：

- 固定 schema version；
- `source_split=train`；
- base `SKILL.md` 正文及 SHA-256；
- eligible failure 的 label、rule ID、confidence 和 trace sequence references；
- 最大 hypothesis 数；
- `no_case_answers/no_hidden_reasoning` 输出契约。

请求不包含诊断 rationale、excluded failure、validation Case、locked test、oracle、grader、参考补丁、
逐 Case 最终分数或 Secret。响应不能自报 evidence reference；控制器只根据匹配的 train label 生成
`diagnosis://run/rule` 谱系，防止伪造来源。

## Process 安全门

- executable 必须是非 symlink 普通文件，且 SHA-256 与配置完全一致；
- 每次首次生成前执行严格版本探测；
- 使用参数数组启动，不经过 shell；
- 只继承极小环境白名单，拒绝 Key、Token、Secret、Password、Auth 和 Credential 类名称；
- 默认不继承 HOME、Provider Key、代理配置或其他无关环境；
- 进程组超时后强制终止；
- 请求、响应字节数及 JSON 深度/字段数有冻结上限；
- 外层和 hypothesis 使用 `extra=forbid` 的结构化 Schema；
- stderr、原始请求、原始响应和隐藏推理均不落盘；
- 非零退出、超时、非法 JSON、重复 ID、越权 label 和数量超限全部 fail closed。

当前边界是“受信任的、哈希固定的本地 executable”，不是不可信代码沙箱。MVP 不阻断该进程的
本机文件或网络访问；接入第三方生成器前仍需 Docker/Seatbelt 等 OS 级隔离。

## 幂等与审计证据

首次成功后，`hypotheses.json` 原子保存 hypotheses 与 `GeneratorInvocationEvidence`：

- generator name/version；
- executable SHA-256；
- version verified；
- request/response/hypotheses SHA-256；
- hypothesis count、duration、exit code；
- 实际继承的环境变量名称；
- raw request/response、stderr、hidden reasoning 均为 `stored=false`。

Evolution ID 由语义配置及 base/manual/failure/validation/regression 内容哈希共同决定，不包含机器
本地绝对路径。相同 Evolution 重放先校验已有 artifact 和 request hash，不再次启动 Generator，
因此不会重复产生调用或费用。

## 本地 Fake Process 演示

示例 executable 仅用于证明进程边界，不调用模型：

```bash
agentskill-eval optimize evolve run \
  examples/optimizer/failure-guided/process-evolution.example.yaml \
  --workspace .agentskill-eval/process-evolution \
  --allow-simulation
```

配置固定了 executable hash、版本输出、超时、请求/响应大小、JSON 复杂度和环境白名单。结果仍是
`simulated=true`，不能声称真实 LLM 自动生成的 Skill 更好。

## 下一步

后续接入真实 LLM Generator 时必须另建付费安全协议，至少增加显式确认、最大调用数、最大
microusd、Provider/model/temperature/seed 冻结、Secret Gateway、已完成调用的费用幂等，以及
`validation_confirm` 后唯一 winner 的 locked test。不得把当前本地 Process 开关直接解释为真实
Provider 授权。
