# AgentSkill-Eval 项目收口状态

更新日期：2026-08-06

基线：`still0123/AgentSkill-Eval-Refined`。Refined 版仅保留 Python Bug Fix 主线，
原始研究版包含 MCP 和 Memory/RAG Lab 能力，详见 [ranmaoxia0123/AgentSkill-Eval](https://github.com/ranmaoxia0123/AgentSkill-Eval)。

状态：稳定版 `v0.3.0` 已发布；真实 Python Bug Fix v2 与 Test Generation 无增益证据均已冻结

## 1. 当前结论

AgentSkill-Eval 已完成可信 Skill 配对评测、失败诊断、候选搜索、独立终评、不可变发布和
Evidence Release。Python Bug Fix v2 在冻结协议中取得 W/T/L `1/3/0`，后续阶段 0 LOSS，
并发布 `python-bug-fix@2.0.0`；Test Generation corrected replay 为 `0/2/0`、0 INVALID，
保留为同一冻结两 Case 上的真实无增益证据。

当前最准确的项目状态是：

> `v0.3.0` 已达到可安装、可演示、可审计状态。正向与负向结果均为小样本描述性证据，不支持
> 普遍性能提升或通用 Agent 排名。

## 2. 已完成能力

| 能力层 | 已完成内容 | 证据边界 |
|---|---|---|
| 配对评测 | without/with Skill、v1/v2、PairBlock、重复实验、W/T/L | 支持 simulated 与 observed-Agent 严格隔离 |
| 执行证据 | 不可变 Manifest、Trace、工具/命令/文件事件、审计包 | 不保存模型隐藏思维过程 |
| 失败诊断 | 规则诊断、terminal reason、FailureEvidenceBundle | invalid 不进入 Skill 优化输入 |
| Benchmark | 20 个真实 Git 历史缺陷 Case、5 个独立仓库、五段 DatasetVersion、provenance、mutation 与替代修复验证 | locked Case 公开且高污染，仅覆盖首个 Python Bug Fix family |
| Skill Search | 候选生成、真实/模拟 evaluator、successive halving、regression_dev | adaptive search 结果不等同 locked-test 结论 |
| Real Evolution Dry Run | Stage 2/3A 绑定、adaptive Process 演练、confirm/locked 收据隔离 | simulated Process 集成证据；未调用模型或 Agent |
| Real Evolution Execution | 逐阶段预算授权、真实 validation_search、regression_dev、幂等 receipt 与 confirmation handoff | Python Bug Fix observed 闭环已完成 |
| 独立终评 | confirmation、一次性 locked test、burn rule | 真实 v2 confirmation/locked 均为 0 LOSS、0 INVALID |
| Promotion | AI-assisted review、父版本哈希、不可变 SkillVersion、回滚指针 | `python-bug-fix@2.0.0` 已发布 |
| Evidence Release | `prepare`、`verify`、`inspect` CLI，离线 HTML、diff、索引和审计包 | 真实 Bug Fix 与 Test Generation 脱敏 evidence 已进入稳定 Release |
| 可视化 | 本地只读 Dashboard 展示实验、Trace、候选、版本谱系和 Skill Evolution Timeline | 不承担写操作、批准或在线调度 |

## 3. 已有真实证据

真实 Agent 证据使用固定 Process Agent、skill-up 0.5.0 和 DeepSeek V4 Pro，包括：

- 4-Run smoke：4 次完成，验证真实执行、Secret 隔离和审计链路；
- 12-Run evidence：9 次完成、3 次 invalid，Baseline 66.7%、Treatment 83.3%；
- Real Optimizer smoke：20 个 Attempt，18 次完成、2 次 invalid，验证真实候选筛选链；
- Python Bug Fix v1→v2：Search 获得 1 个 WIN，后续 0 LOSS，合计 `1/3/0`；
- Test Generation corrected replay：4 Runs、0 INVALID、W/T/L `0/2/0`。

这些结果都是描述性证据。Case 数量小、来源集中，不能声称 Skill 具有普遍增益。

## 4. 当前一键演示验收

在无凭据、无网络、无模型费用条件下运行：

```bash
agentskill-eval demo run --workspace .agentskill-eval/completion-demo
```

`v0.3.0` stable wheel 的 2026-08-06 clean-room 验收结果：

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
4. 展示 Python Bug Fix `1/3/0` 与 Test Generation `0/2/0` 的脱敏报告和 claim limit；
5. 展示 Automatic Benchmark 的 before-fail、after-pass、mutation-fail、alternative-pass；
6. 展示 `python-bug-fix@2.0.0` Promotion 谱系与 AI-assisted review；
7. 以 Test Generation 无增益结果说明系统不会为了正向结论篡改评测口径。

## 6. 简历表述建议

> 基于固定 `skill-up v0.5.0` 执行内核，自研 Agent Skill 配对评测与证据控制层，支持真实
> Git-history Benchmark、Trace 失败归因、独立 locked evaluation 和不可变 SkillVersion；
> 完成 Python Bug Fix v1→v2 observed 闭环（W/T/L `1/3/0`）及 Test Generation corrected
> no-gain 对照（`0/2/0`），发布稳定 `v0.3.0` 并为附件生成 SHA256 与 SLSA provenance。

面试中应主动说明：真实结果是小样本描述性证据，不是大样本泛化结论；Agent 基础执行复用
`skill-up`，配对协议、证据契约、Benchmark、演化门禁和不可变发布为本项目自研。

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

停止后已修复 FailureBridge 的脱敏行为摘要：它从 Manifest 哈希绑定的结构化 session transcript
中仅导出固定、Case 无关的只读检查/编辑/测试行为结论，并对未知命令保持 abstain。该修复已用
保留的 train replay 离线验证，但没有重新调用 Proposal，因此不改变上述失败结论。

## 13. 第二次受限实验与真实 Skill v2

用户单独授权的第二次实验仅改变脱敏 FailureBundle 和 Proposal request，复用原五个冻结 Case。
修订候选 `require-post-fix-verification` 在 validation_search 产生一个独立 WIN；regression、
confirmation 和 locked 均为 TIE、0 LOSS、0 INVALID。合计 W/T/L 为 `1 / 3 / 0`。

Promotion Gate 已通过，并以 `AI-assisted review (OpenAI Codex)` 明确记录审核身份。不可变
`python-bug-fix@2.0.0` 已发布，SkillVersion ID 为
`a434afe8-cc6b-5d80-a4af-cd6819d53e64`。结果仅是四个公开独立 Case 上的 descriptive evidence，
不构成普遍性能提升。

主闭环成功后新增最小 Python Test Generation family。两个真实 Git-history Case 的有效配对
结果为 W/T/L `0 / 2 / 0`、0 INVALID；without/with-Skill 均未生成通过 before-fail /
after-pass Oracle 的测试。后验 Trace 诊断确认 4 个 Run 都是真实任务失败，但实验复用了
Bug Fix 专用 Process Agent，且 Treatment Skill 未进入模型上下文，因此不能据此判断
Test Generation Skill 无效。修复 Runtime 后复用同一 DatasetVersion 和两个 Case 重跑：
4 Runs、0 INVALID、W/T/L 仍为 `0 / 2 / 0`，且 Treatment Skill context 已直接验证。该结果
现可解释为冻结两 Case 上的有效无增益证据，但不支持一般化结论；不进入自动优化或版本发布。
详见 [Test Generation Negative-Result Diagnosis](./test-generation-negative-result-diagnosis.md)
和 [Runtime Fix and Corrected Replay](./test-generation-runtime-fix.md)。
