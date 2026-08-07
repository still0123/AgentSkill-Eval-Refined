# AgentSkill-Eval 答辩与面试讲解手册

本手册用于项目演示、秋招面试和简历项目追问。所有数字必须以仓库内已提交的脱敏证据为准，
不得把 simulated 数据描述为真实模型效果，也不得把小样本描述性结果扩展成普遍结论。

## 1. 三十秒项目介绍

> AgentSkill-Eval 是一个面向 Agent Skill 的评测、诊断与迭代优化系统。它通过冻结 Agent、
> 模型、数据集、环境和预算，只改变是否加载 Skill 或 Skill 版本，运行配对实验并保存 Trace、
> 成本和失败诊断。系统还能从真实 Git 历史重建 Benchmark、筛选 Skill 候选、执行独立 locked
> evaluation，并用不可变 Manifest 管理 SkillVersion。项目既有零费用确定性 Demo，也完成了
> Python Bug Fix Skill v1→v2 的 observed 正向闭环、Test Generation 无增益对照，以及
> 6 仓库 19 Case、114 Runs 的第二 Runtime 扩展验证。

## 2. 面试官应该记住的三个点

1. **评测对象是 Skill 的边际价值**：不是单纯给 Agent 打一个绝对分数。
2. **结果之外还分析过程**：Trace、工具调用、终止原因和 invalid 分类用于定位改进方向。
3. **优化受证据约束**：train 失败生成候选，validation/regression/locked 数据严格隔离，证据
   不足时系统停止，不为了正结果反复试验。

## 3. 五分钟 Demo 脚本

### 0:00–0:40：问题与实验设计

打开项目 README，说明同一个 Case 的三条路径：without-Skill、Skill v1、Skill v2。强调配对
实验中唯一变量是 Skill，模型、Runner、数据、预算和执行顺序必须冻结。

### 0:40–1:30：运行零费用演示

```bash
agentskill-eval demo run --workspace .agentskill-eval/interview-demo
```

解释输出：

- 12 Case × 2 Variant × 3 Repeat = 72 Run；
- 每个 Run 都有不可变 Manifest 和 Attempt；
- 报告明确标记 `simulated=true`；
- 该命令证明工程闭环，不证明真实 Agent 性能。

### 1:30–2:20：查看报告与 Trace

打开命令输出的 `report.html`，依次展示：

- Baseline/Treatment 通过率；
- W/T/L，而不是只看一个总分；
- Token 和时延开销；
- Case 级结果；
- Trace 与 FailureDiagnosis。

选择一个失败 Run，说明系统如何区分：

```text
task failure     → 可能成为 Skill 优化证据
runner/provider  → infrastructure invalid
timeout/budget   → invalid 或预算终止
```

### 2:20–3:10：展示真实 Agent 证据

打开：

- `experiments/real-positive-skill-loop-v2-2026-08-05/`
- `experiments/test-generation-runtime-fix-2026-08-05/`

说明真实实验固定 Agent、DeepSeek V4 Pro、skill-up 0.5.0、Case、预算和 grader。Python Bug
Fix 在 Search 获得 1 个独立 WIN，后续 Regression/Confirmation/Locked 均无 LOSS，合计
W/T/L `1/3/0`；Test Generation 修复 Runtime 混杂后仍为 `0/2/0`、0 INVALID。两者都是小样本
描述性证据，不支持普遍增益声明。

再打开 `experiments/python-bug-fix-v2-generalization-2026-08-06/result.sanitized.json`：
本地 MLX + Qwen2.5-Coder process Runtime 完成 114/114 Runs，得到
`0 WIN / 19 TIE_NEGATIVE / 0 LOSS / 0 INVALID`。结论是 `NOT_CONFIRMED`，不是回归；两版
0/19 是 Runtime floor effect，不能推断 Skill 普遍无效。

### 3:10–4:00：展示 Benchmark 可信度

打开 `experiments/train-benchmark-expansion-2026-07-14/`，说明每个候选必须满足：

```text
修复前连续失败 × 3
历史修复后通过 × 3
关键逻辑 mutation 后重新失败 × 3
不同写法的替代修复通过 × 3
```

再说明 provenance、许可证、commit、fixture、grader 和 split 都有内容哈希；同源缺陷不能跨
任何 split，repository/fork 不能从 adaptive 开发域进入 confirmation/locked holdout 域。

### 4:00–4:40：展示优化与版本发布

说明完整目标链路：

```text
observed failure
→ sanitized FailureEvidenceBundle
→ proposal candidates
→ validation search
→ regression_dev
→ confirmation
→ one-shot locked test
→ AI-assisted / human review
→ immutable SkillVersion
```

真实 `python-bug-fix@2.0.0` 已通过 AI-assisted review、confirmation 和 one-shot locked test
发布；SkillVersion 与 Evidence Release 均不可变。Fake fixture 只保留用于无费用回归测试。

### 4:40–5:00：用负结果收尾

Test Generation corrected replay 中，两臂都没有通过 before-fail / after-pass Oracle，因此
系统保留 W/T/L `0/2/0`，没有生成 Proposal、增加 Case 或发布 Skill。强调这是可信系统的重要
行为：Runtime invalid 不能伪装成 task failure，真实无增益也不能为了演示效果被删除。

## 4. 架构讲解顺序

面试中不要从 FastAPI、数据库或页面开始。按照实验可信链路讲：

```text
DatasetVersion / SkillVersion
          ↓
PairBlock + Frozen Variant
          ↓
Runner Adapter + Agent Runtime
          ↓
Attempt Evidence + Normalized Trace
          ↓
Deterministic / Script / Semantic Evaluation
          ↓
Statistics + Failure Diagnosis
          ↓
Candidate Search + Independent Final Evaluation
          ↓
Review-gated Immutable Promotion
```

### 核心设计选择

- `skill-up` 作为固定执行内核，自研层不重复实现 Agent CLI；
- Adapter 隔离不同 Runner/Agent 的参数、版本和能力差异；
- Manifest 采用内容哈希和原子写入，发布对象不可就地修改；
- PairBlock 固定两臂顺序，避免时间、缓存和环境漂移；
- 确定性 grader 优先，LLM Judge 只处理无法机械判断的语义质量；
- simulated、process integration 和 observed-Agent 是不同证据类别，禁止混合聚合；
- Case/缺陷家族跨所有 split 隔离；仓库与 fork 在 adaptive 开发域和 frozen holdout 域之间隔离。

## 5. 高频面试问题

### 为什么不直接使用 skill-up？

`skill-up` 已经擅长安装 Skill、运行 Agent 和基础评分，本项目直接固定并复用它。自研重点是
不可变 Variant、配对实验、v1/v2 回归、Trace 诊断、自动 Benchmark、候选搜索、独立终评和
版本发布证据，因此不是重复造 Runner。

### 为什么必须有 without-Skill 基线？

只看加载 Skill 后的成绩，无法区分模型本身能力和 Skill 增量。固定其他变量后做配对比较，
才能估计 Skill 的边际影响，并分析哪些 Case 改进、持平或退化。

### 为什么不能只看通过率？

相同通过率可能对应完全不同的行为：一个版本可能 Token 翻倍、频繁重试或依赖偶然工具结果。
因此报告同时保留 W/T/L、invalid、Token、时延、费用、工具调用和 Trace。

### invalid 为什么不能算普通失败？

Provider 错误、Runner 崩溃、预算耗尽与任务答案错误的原因不同。把它们统一算 task failure 会
误导 Skill Optimizer，让它用提示词修改去修复基础设施问题。系统保留 assignment-based
敏感性分析，但优化输入只接受满足资格的 task failure。

### LLM Judge 是否可信？

代码测试、文件、JSON、数据库状态和数值结果优先使用确定性脚本。LLM Judge 只评价语义质量，
并应保存 rubric、模型版本和原始分项；必要时使用多 Judge 一致性与人工抽查。当前主线不依赖
LLM Judge 代替可执行 oracle。

### 如何避免 Skill Optimizer 刷测试？

候选只读取 train 的脱敏失败证据，不能看到 validation/locked grader；同一缺陷家族不能跨
split；候选经过 leakage lint、regression_dev、confirmation 和一次性 locked test；搜索日志和
失败候选全部保留。

### 为什么 locked test 只能使用一次？

看到 locked 结果后再次修改 Skill，就等于把测试集变成训练信号。系统采用 burn rule：一个
冻结候选批次只消费一次 locked test，失败后必须使用新的独立测试版本。

### 真实 Skill v2 的正向结果是否足以证明普遍提升？

不足。首次闭环的 4 个 Case 得到 `1/3/0`；扩展到第二 Runtime、6 个仓库、19 个未见 Case 后，
两版均为 0/19，决策为 `NOT_CONFIRMED`。这证明系统能保存正向和无增益证据，但不能证明 Skill
普遍有效或普遍无效。公开 Git 历史还具有高污染风险。

### 如何保证 API Key 不泄露？

Secret 只从显式允许的环境变量注入，不写入命令参数、Manifest、Trace 或报告；Runner 使用最小
环境继承；真实实验完成后执行 Secret pattern 与精确 Key 扫描；公开仓库只保存脱敏配置、聚合
结果和审计哈希。

### 如果要扩展到 MCP 或 RAG，哪些部分复用？

实验、Variant、Runner、Trace、证据分类、报告和版本协议复用；变化的是 Scenario Adapter、
环境状态与专项 grader。MCP 关注工具选择、参数、副作用和恢复；RAG 关注 Recall@K、引用、
faithfulness、污染和记忆更新。

## 6. 简历三条

- 基于固定的 `skill-up v0.5.0` 执行内核，自研 Agent Skill 配对评测控制层，冻结
  Agent/模型/Case/预算，仅改变 Skill，统一输出 PASS/FAIL/INVALID、W/T/L、成本和 Trace。
- 设计不可变 DatasetVersion/SkillVersion、Evidence Gap 门禁和内容哈希收据，实现
  Search→Regression→Confirmation→Locked→Review 发布链，并阻止 invalid 或无增益候选发布。
- 完成真实 Python Bug Fix v1→v2 闭环（W/T/L `1/3/0`、0 LOSS）和 Test Generation
  corrected no-gain 对照（`0/2/0`、0 INVALID），并在第二 Runtime 完成 114 Runs 扩展验证
  （`0/19`、`NOT_CONFIRMED`）；发布 `v0.4.0` 本地 clean-room artifact、SHA256 与可复核
  build provenance。

边界必须主动说明：Agent Loop、Skill 安装和基础执行复用 `skill-up`；配对协议、证据契约、
Benchmark 重建、失败诊断、演化门禁和不可变发布是本项目自研。

## 7. 项目难点的 STAR 讲法

### 难点一：真实 Agent 结果不稳定

- Situation：真实模型会超时、循环、耗尽预算或返回不完整工具轨迹；
- Task：既要保留真实行为，又不能把基础设施失败当作 Skill 能力；
- Action：设计 Attempt、terminal reason、invalid taxonomy、幂等恢复和 observed evidence gate；
- Result：真实 Evidence 保留 3 个 invalid，Stage 3 在证据不足时停止，没有污染优化输入。

### 难点二：自动生成 Benchmark 容易泄漏答案

- Situation：直接从修复 commit 生成任务，测试可能只接受参考补丁或泄漏答案；
- Task：重建可执行、可复现且不绑定唯一实现的 Case；
- Action：冻结 pre-fix fixture，验证 before/after，增加 mutation 与 distinct alternative repair，
  保存 provenance 和 split family；
- Result：形成 20 个真实 Git-history Case，五段 DatasetVersion 各包含四个隔离 Case。

### 难点三：优化结果容易过拟合

- Situation：候选可以在已知 Case 上提升，却在其他 Case 退化；
- Task：让 v2 发布结论具备独立证据；
- Action：拆分 search、regression、confirmation、locked test，记录 W/T/L 和父版本谱系，并要求
  人工审核；
- Result：首轮无增益候选被停止；第二轮通用候选在独立 Search 获得 WIN，且后续 0 LOSS，
  最终发布不可变 `python-bug-fix@2.0.0`。

## 8. 不应使用的表述

不要说：

- “Skill 成功率提升了 33%”——该数字来自 simulated Demo；
- “系统证明 Skill v2 普遍更好”——当前只有 4 个独立评测 Case；
- “支持 MCP/RAG”——Refined 版已主动移除这些未形成真实证据的场景；
- “12 个 Case 证明普遍有效”——样本量和独立来源不足；
- “LLM Judge 能保证正确”——语义评分必须与确定性证据分工。

推荐说：

- “完成了可审计的 Skill 配对评测与优化控制链”；
- “真实实验验证了 Agent Runtime、成本门和证据隔离”；
- “系统在证据不足时拒绝发布，并在独立证据满足门禁后发布 v2”；
- “Benchmark 使用真实历史缺陷，并通过 mutation 和替代修复验证 grader”；
- “现有结果是描述性工程证据，正式能力结论需要更多独立 locked Case”。

## 9. 演示前检查清单

- 使用最新已合并 `main`；
- `git status` 干净；
- 零费用 Demo 使用全新 workspace；
- 提前确认 HTML 报告可离线打开；
- 不在终端显示任何 Secret；
- 真实实验只展示仓库内脱敏结果；
- 准备一个 win、一个 loss、一个 invalid Trace；
- 明确指出 simulated/observed-Agent 标签；
- 最后展示 claim limit，而不是只展示最好看的百分比。
