# AgentSkill-Eval 项目收口状态

更新日期：2026-08-04

基线：`still0123/AgentSkill-Eval-Refined`。Refined 版仅保留 Python Bug Fix 主线，
原始研究版包含 MCP 和 Memory/RAG Lab 能力，详见 [ranmaoxia0123/AgentSkill-Eval](https://github.com/ranmaoxia0123/AgentSkill-Eval)。

状态：核心工程 MVP 已完成；真实 Skill v2 研究闭环保留为后续实验课题

## 1. 当前结论

AgentSkill-Eval 已经能够完整展示一个可信的 Agent Skill 评测系统：冻结 Skill、数据集、
Agent、Runner 和实验协议，运行 without-Skill/with-Skill 配对实验，保存 Trace、失败诊断、
成本与审计证据，并支持 Benchmark 生成、候选搜索、独立终评和版本发布控制。

项目不再通过反复调用真实模型来“碰”一个有利的失败样本。两次 Stage 3 train smoke 均没有
产生可用于修改 Skill 的 completed treatment task failure：简单 Case 两臂均通过，困难 Case
分别因循环或预算边界成为 invalid。系统正确返回 `INSUFFICIENT`，没有把基础设施失败伪装成
Skill 缺陷，也没有消费已授权但不满足前提的 proposal 调用。

因此当前最准确的项目状态是：

> 评测、诊断、搜索与发布控制链路已经完成；真实 v1→v2 的正向案例尚未产生。该负结果是
> 证据门生效的证明，不是需要继续付费重试的工程故障。

## 2. 已完成能力

| 能力层 | 已完成内容 | 证据边界 |
|---|---|---|
| 配对评测 | without/with Skill、v1/v2、PairBlock、重复实验、W/T/L | 支持 simulated 与 observed-Agent 严格隔离 |
| 执行证据 | 不可变 Manifest、Trace、工具/命令/文件事件、审计包 | 不保存模型隐藏思维过程 |
| 失败诊断 | 规则诊断、terminal reason、FailureEvidenceBundle | invalid 不进入 Skill 优化输入 |
| Benchmark | 20 个真实 Git 历史缺陷 Case、5 个独立仓库、五段 DatasetVersion、provenance、mutation 与替代修复验证 | locked Case 公开且高污染，仅覆盖首个 Python Bug Fix family |
| Skill Search | 候选生成、真实/模拟 evaluator、successive halving、regression_dev | adaptive search 结果不等同 locked-test 结论 |
| Real Evolution Dry Run | Stage 2/3A 绑定、adaptive Process 演练、confirm/locked 收据隔离 | simulated Process 集成证据；未调用模型或 Agent |
| Real Evolution Execution | 逐阶段预算授权、真实 validation_search、regression_dev、幂等 receipt 与 confirmation handoff | Stage 3C 执行器和 Fake Process 测试已完成；尚未授权本阶段真实付费实验 |
| 独立终评 | confirmation、一次性 locked test、burn rule | 工程链路已完成 |
| Promotion | 人工审核、父版本哈希、不可变 SkillVersion、回滚指针 | 当前真实 v2 未发布；Fake fixture 只证明流程 |
| Evidence Release | `prepare`、`verify`、`inspect` CLI，离线 HTML、diff、索引和审计包 | Stage 5A.2 已用 Fake Promotion fixture 完成端到端验收 |
| 可视化 | 本地只读 Dashboard 展示实验、Trace、候选、版本谱系和 Skill Evolution Timeline | 不承担写操作、批准或在线调度 |

## 3. 已有真实证据

真实 Agent 证据使用 Qwen Code 0.19.9、skill-up 0.5.0 和 DeepSeek V4 Pro：

- 4-Run smoke：4 次完成，验证真实执行、Secret 隔离和审计链路；
- 12-Run evidence：9 次完成、3 次 invalid，Baseline 66.7%、Treatment 83.3%；
- Real Optimizer smoke：20 个 Attempt，18 次完成、2 次 invalid，验证真实候选筛选链；
- 两次 Stage 3 train smoke：均未产生 eligible treatment task failure，proposal 调用数为 0。
- Proposal-only Stage 1 smoke：真实 DeepSeek 调用 1 次，生成 4 个结构化候选，费用
  921 microusd；输入为 synthetic 脱敏 train fixture，未执行 search 或 locked test。

这些结果都是描述性证据。Case 数量小、来源集中，不能声称 Skill 具有普遍增益。

## 4. 当前一键演示验收

在无凭据、无网络、无模型费用条件下运行：

```bash
agentskill-eval demo run --workspace .agentskill-eval/completion-demo
```

2026-07-14 的收口验收结果：

```text
12 cases × 2 variants × 3 repeats = 72 logical runs
completed: 72
invalid: 0
W/T/L: 5 wins / 6 ties / 1 loss
audit bundle files: 980
audit bundle verification: valid
simulated: true
```

演示中的成功率、Token 和时延来自确定性 fixture，只用于证明平台闭环，不能作为真实 Agent
或 Skill 性能结果。脱敏记录见
`experiments/project-completion-demo-2026-07-14/result.sanitized.json`。

## 5. 五分钟答辩路径

1. 用 README 的架构图解释唯一变量配对实验；
2. 执行一键 Demo，展示 72 个 Run 与离线 HTML 报告；
3. 打开一个 Trace，说明 task failure 与 infrastructure invalid 的区别；
4. 展示真实 DeepSeek smoke/evidence 的脱敏报告和 claim limit；
5. 展示 Automatic Benchmark 的 before-fail、after-pass、mutation-fail、alternative-pass；
6. 展示 Promotion 谱系，同时明确真实 Skill v2 尚未发布；
7. 以 Stage 3 的 `INSUFFICIENT` 负结果说明系统不会为了正向结论篡改评测口径。

## 6. 简历表述建议

> 设计并实现 Agent Skill 评测、诊断与迭代优化系统，支持有无 Skill 配对实验、真实 Agent
> Runtime、轨迹级失败归因、Git 历史 Benchmark 重建、候选搜索、独立 locked evaluation 和
> 不可变 SkillVersion 发布；完成 12 个真实缺陷家族的离线验证及 DeepSeek/Qwen Code 真实
> 执行证据，并通过证据门隔离 task failure、invalid 与 simulated 结果。

面试中应主动说明：真实实验提供的是系统可用性与描述性证据，而不是大样本泛化结论；真实
v1→v2 正向案例仍是后续研究目标。

## 7. 暂停项与恢复条件

现在不继续执行：

- 为得到失败样本而重复付费 smoke；
- 未经独立证据确认就发布真实 Skill v2；
- FastAPI、Redis、Kubernetes 等与当前项目价值无关的平台扩张；
- 同时扩展多个 Skill family。

只有满足下列任一条件时才恢复真实 Skill 优化实验：

1. 获得一个自然产生、已完成评分且可由 Skill 改变的 treatment task failure；
2. 新增一个与目标 Skill 明确匹配、经过独立审核的真实 Benchmark family；
3. 预先批准一次完整的 train→proposal→search→confirmation→locked 预算，而不是逐次碰运气。

恢复后仍必须沿用 train、validation_search、regression_dev、validation_confirm 和 locked_test
隔离，不得借用验证集生成候选。

## 8. Stage 5A.2 集成状态

Stage 5A.2 Evidence Release CLI Integration 已通过 PR #15 合入 `main`。它复用 Promotion
Release Manifest 和既有发布准备层，提供 `evolution release prepare/verify/inspect`，并使用
Fake Promotion fixture 验证从 handoff 到离线发布包的完整链路、幂等 prepare、哈希校验和篡改
检测。该结果证明发布工具可用，但仍不表示真实 Skill v2 已产生或通过 locked test。

## 9. Stage 4C 展示状态

Stage 4C 在现有 Vue Dashboard 中增加只读 Skill Evolution 页面，直接消费 Evolution Evidence
Release、Proposal、Search、Final Evaluation、Promotion 和 SkillVersion 证据。页面按
`NOT_STARTED / RUNNING / PASSED / FAILED / REJECTED / UNAVAILABLE` 展示阶段状态；缺失 locked
或人工审核证据时不会显示 Published，null 指标不会显示为 0。默认 Fake fixture 与 Stage 1
脱敏 Proposal 仅用于展示协议和边界，不构成真实 Skill v2 改进结论。

## 10. Stage 3B 执行准备状态

Stage 3B 将 Stage 2 五段不可变 DatasetVersion 与 Stage 3A 真实执行计划进行一致性绑定。它只
打开并校验 `validation_search`、`regression_dev`，通过哈希和版本固定的本地 Process 演练
自适应阶段；`validation_confirm`、`locked_test` 只保留不含路径和 Case key 的收据。输出状态
为 `AWAITING_REAL_AUTHORIZATION`，明确标记 `simulated=true`，不产生模型费用、不运行 Agent，
也不声称 Skill 改进。

## 11. Stage 3C 自适应执行状态

Stage 3C 已提供 `evolution execute preflight/search/regression/inspect/verify`。Search 与 regression
必须分别显式授权并遵守 Stage 3A 冻结的 Run/费用上限；相同完成阶段的重放直接验证不可变
receipt，不重复创建 Agent Run。通过 regression 后只生成 confirmation handoff，不读取
`validation_confirm` 或 `locked_test`，也不自动发布 Skill v2。当前只完成无费用代码与 Fake
Process 集成验收，尚未将其表述为真实 Skill 改进证据。

## 12. 首次受限真实正向闭环尝试

2026-08-05 的受限实验获得了一个有效的 observed Skill v1 `VERIFICATION` task failure，并用
同一脱敏 train bundle 生成两个通用候选。validation_search 中 v1 与两个候选均为 FAIL，
两个候选相对 v1 的 W/T/L 都是 `0 / 1 / 0`。执行链、Skill 安装、Grader 和 Secret Scan 均
正常，因此结论是候选无增益，而不是 Runtime invalid。

实验在 Search 阶段按预算门停止，没有执行 regression、confirmation 或 locked test，没有发布
Skill v2，也没有启动第二 Skill family。详见
[Real Positive Skill Loop Attempt](./real-positive-skill-loop.md)。
