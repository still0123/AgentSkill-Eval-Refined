# AgentSkill-Eval 项目收口状态

更新日期：2026-07-14

基线：`main@5e35f91`

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
| Benchmark | 12 个真实 Git 历史缺陷家族、split 隔离、provenance、mutation 与替代修复验证 | 仅覆盖首个 Python Bug Fix family |
| Skill Search | 候选生成、真实/模拟 evaluator、successive halving、regression_dev | adaptive search 结果不等同 locked-test 结论 |
| 独立终评 | confirmation、一次性 locked test、burn rule | 工程链路已完成 |
| Promotion | 人工审核、父版本哈希、不可变 SkillVersion、回滚指针 | 当前真实 v2 未发布；Fake fixture 只证明流程 |
| 多场景 | 软件工程、MCP、Memory/RAG 统一入口与专项指标 | MCP、Memory/RAG 仍是离线 simulated Lab |
| 可视化 | 本地只读 Dashboard 展示实验、Trace、候选和版本谱系 | 不承担写操作或在线调度 |

## 3. 已有真实证据

真实 Agent 证据使用 Qwen Code 0.19.9、skill-up 0.5.0 和 DeepSeek V4 Pro：

- 4-Run smoke：4 次完成，验证真实执行、Secret 隔离和审计链路；
- 12-Run evidence：9 次完成、3 次 invalid，Baseline 66.7%、Treatment 83.3%；
- Real Optimizer smoke：20 个 Attempt，18 次完成、2 次 invalid，验证真实候选筛选链；
- 两次 Stage 3 train smoke：均未产生 eligible treatment task failure，proposal 调用数为 0。

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

## 8. 与 Stage 5A.2 的边界

Stage 5A.2 Evidence Release CLI Integration 在独立 worktree/分支开发。本收口阶段不修改其
CLI、`release_evidence.py`、Promotion manifest、测试或发布目录。Stage 5A.2 合并后，只需把
其最终 CLI 命令和产物链接补充到项目导航，不需要重新设计本报告。
