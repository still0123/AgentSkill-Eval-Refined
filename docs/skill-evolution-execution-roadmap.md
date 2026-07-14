# AgentSkill-Eval 后续阶段执行工作文档

版本：v1.0  
更新日期：2026-07-14  
执行仓库：`/Users/bytedance/Documents/skill`

## 1. 文档目的

本文档用于指导后续 AI 按阶段完成 AgentSkill-Eval 的核心目标：

> 从真实 Skill v1 的失败中找到改进方向，生成候选，使用真实 Agent 选择和确认候选，最终发布
> 有证据支持的不可变 Skill v2。

当前系统已经具备配对实验、Trace、失败诊断、Benchmark Generation、Skill Search、
Independent Final Evaluation、Real Agent Runtime、Failure-Guided Evolution 和 Fake Process
Proposal Generator。后续重点不是增加更多评分指标或基础设施，而是打通真实 v1→v2 证据链。

## 2. 执行原则

1. 严格按 `阶段 0 → 1 → 2 → 3 → 4 → 5` 顺序执行。
2. 每次只把一个阶段交给 AI，不同时开发多个阶段。
3. 每个阶段使用独立 `codex/*` 分支、独立提交并推送 GitHub。
4. 开始前确认 `pwd`、分支、工作区状态和前一阶段提交已经存在。
5. 不删除或重写已经完成的 Pairing、Trace、Benchmark、Search、Final Evaluation 和 Real Evidence。
6. 优先复用现有模块，不重复实现 Runner、Evaluator、报告或预算控制。
7. 真实模型调用前必须先报告模型、Run/调用数、预计 Token 和最大预算，等待用户明确授权。
8. 每个阶段完成后停止，报告 commit、分支、测试和 CI，再开始下一阶段。
9. 如果实验没有产生更好的候选，允许输出 `REJECTED`；不得为了得到正结果修改评测口径。

## 3. 当前基线

当前开发基线：

```text
branch: codex/audited-process-skill-proposal-mvp
commit: e2febda Add audited Process Skill proposal generator
```

当前分支比 `main` 多 6 个提交，包含：

- Unified Multi-Scenario Evaluation；
- Process Agent Scenario Evaluation；
- Interactive Scenario Agent Loop；
- Failure-Guided Skill Evolution；
- 终端宽度兼容修复；
- Audited Process Skill Proposal Generator。

因此必须先执行阶段 0，将当前成果合入 `main`。

## 4. 阶段总览

| 阶段 | 名称 | 核心产物 | 是否产生模型费用 | 状态 |
|---|---|---|---|---|
| 0 | Integration Baseline | 合并 PR、RC tag | 否 | 已完成 |
| 1 | Observed Failure Evidence Bridge | 真实 train FailureEvidenceBundle | 可先不产生 | 已完成 |
| 2 | Real Optimizer Evaluator | 真实候选选择信号 | smoke 已授权执行 | 已完成 |
| 3 | DeepSeek Skill Proposal | 真实模型生成的 3～5 个候选 | 是，需授权 | train smoke 证据不足，proposal 未调用 |
| 4 | SkillVersion Promotion | Confirm、Locked、Skill v2 Manifest | 终评需授权 | 待执行 |
| 5 | Real Evolution Evidence Release | 完整 v1→v2 实验报告 | 是，需授权 | 待执行 |
| 6 | Second Skill Family | MCP Skill 真实实证 | 可选 | 暂缓 |

---

## 5. 阶段 0：Integration Baseline

### 5.1 目标

将当前累计开发成果合并到 `main`，建立后续阶段唯一可信基线。

### 5.2 AI 执行任务

```text
完成 AgentSkill-Eval 当前优化链路的集成发布。

仓库：
/Users/bytedance/Documents/skill

当前分支：
codex/audited-process-skill-proposal-mvp

目标：
- 确认工作区干净、当前分支已推送
- 确认提交 e2febda 存在
- 创建 Pull Request，base=main
- PR 应包含当前分支相对 main 的全部 6 个提交
- 检查变更范围和 GitHub CI
- 修复本分支引入的 CI 问题
- CI 全部通过后合并 PR
- 更新本地 main 并确认与 origin/main 一致
- 创建并推送 v0.2.0-rc1 tag
- 不开发新功能
```

### 5.3 完成标准

- `main` 包含 `e2febda`；
- PR 已合并；
- Python、Dashboard、wheel 和 Secret Scan CI 全部通过；
- `v0.2.0-rc1` 已推送；
- 本地 `main` 干净并跟踪 `origin/main`。

### 5.4 完成记录

```text
PR：https://github.com/ranmaoxia0123/AgentSkill-Eval/pull/5
Merge commit：a4552daa6473611a010456050e551b212d991d5a
Tag：v0.2.0-rc1
CI：Python、Dashboard、wheel、Secret Scan 全部通过
完成日期：2026-07-14
```

---

## 6. 阶段 1：Observed Failure Evidence Bridge MVP

### 6.1 目标

把真实 Skill v1 的 train Run、Trace 和 FailureDiagnosis 自动转换为优化器可以读取的
`FailureEvidenceBundle`，替代手写的模拟失败 YAML。

### 6.2 AI 执行任务

```text
实现 Observed Failure Evidence Bridge MVP。

前提：
- 从最新 main 创建 codex/observed-failure-bridge-mvp
- 不修改既有 FailureEvidenceBundle 和 Trace 的核心语义

目标：
- 输入本地 workspace 和已冻结的真实 v1 train Experiment ID
- 读取 Experiment、Run、Attempt、Trace 和 FailureDiagnosis
- 只把 task fail 中可由 Skill 改变的 finding 导出为 eligible evidence
- infra invalid、取消、Provider/Runner/环境故障不进入 Skill 优化输入
- 对普通 task fail 补充最小规则归因，无法判断时保留 abstain
- 提供简单的人工 review/override YAML，不开发审核平台
- 每个 finding 保存 run_id、attempt_id、rule_id 和 trace event 引用
- 对相同 label/rule 做简单聚合，但保留原始证据引用
- 输出可直接供 optimize evolve 使用的 train FailureEvidenceBundle
- 新增 CLI、集成测试、示例和文档

建议 CLI：
agentskill-eval optimize prepare-failures WORKSPACE EXPERIMENT_ID \
  --output train-failures.yaml

本阶段不做：
- LLM 自动失败诊断
- embedding 聚类
- 向量数据库
- FastAPI 或 Dashboard
- 真实 Skill 候选生成
```

### 6.3 完成标准

- Fake/fixture 实验可端到端生成合法 bundle；
- 至少用一个真实 v1 train 实验验证导出流程；
- 目标是得到至少 3 个可追溯 eligible findings 或 clusters；
- 若真实数据不足，必须报告不足，不能把 infra failure 伪造成 Skill failure；
- 输出 bundle 可直接运行现有 `optimize evolve`；
- Ruff、mypy、pytest、wheel 和 GitHub CI 通过；
- 独立提交并推送分支。

### 6.4 完成记录

```text
Branch：codex/observed-failure-bridge-mvp
Commit：8f55849 Add observed failure evidence bridge
PR：https://github.com/ranmaoxia0123/AgentSkill-Eval/pull/7
真实 Experiment：282b1e61-8045-56c8-8806-30054d747b18
Eligible findings/clusters：真实实验 0/0（INSUFFICIENT）；observed fixture 3/3
CI：Ruff、mypy、182 pytest、wheel、Dashboard、Secret Scan 全部通过
```

---

## 7. 阶段 2：Real Optimizer Evaluator MVP

### 7.1 目标

让候选搜索和 `regression_dev` 使用真实 Agent 任务结果，而不是关键词模拟分数。这个阶段先使用
确定性 Generator，以单独验证真实候选选择信号。

### 7.2 AI 执行任务

```text
实现 Real Optimizer Evaluator MVP。

前提：
- 阶段 1 已合并
- 从最新 main 创建 codex/real-optimizer-evaluator-mvp

目标：
- 复用现有 Real Agent Evidence、SkillUpRunnerAdapter 和 Process Evaluator
- 不重写真实 Agent Runtime
- CandidateEvaluator 支持 simulated=false
- Search 和 regression_dev 均可运行真实 Agent
- 支持 original/base、manual、random 和 search candidate
- 逐 candidate/case 保存 pass/fail/invalid、Token、费用、时延和 Trace 引用
- 已完成 candidate/case 组合幂等复用
- 真实失败不得回退 Mock 或 simulation
- 继续复用已有 --confirm-real-run、最大 Run 数和最大预算参数
- 先用确定性 Generator 完成 Fake Process CI
- 在用户授权后执行一次最小真实 smoke

Benchmark 范围：
- 将现有真实 Bug Fix Benchmark 扩展到约 10～16 个 Case
- 至少来自 3 个仓库或独立缺陷家族
- 明确划分 train、validation_search、regression_dev、validation_confirm
- 不追求 50 个 Case，不做大规模 GitHub 抓取

本阶段不做：
- DeepSeek 候选生成
- 多 Provider
- 新评分体系
- FastAPI、Redis、Kubernetes
```

### 7.3 付费执行前检查点

完成全部代码和 Fake CI 后，AI 必须先报告：

```text
Provider/model：
Candidate 数：
Case 数：
预计 Agent Run 数：
预计 Token：
最大预算：
执行命令：
```

收到明确授权后才执行真实 smoke。

### 7.4 完成标准

- 确定性 Generator 产生的候选可以由真实 Agent evaluator 比较；
- Search 可以冻结 winner，也可以诚实返回没有合格 winner；
- 真实 smoke 生成候选级和 Case 级证据；
- 报告明确 `simulated=false`；
- Ruff、mypy、pytest、wheel 和 GitHub CI 通过；
- 独立提交并推送分支。

### 7.5 完成记录

```text
Branch：codex/real-optimizer-evaluator-mvp
Commit：c381596 Add real optimizer evaluator evidence
PR：https://github.com/ranmaoxia0123/AgentSkill-Eval/pull/9
Smoke Job：8cbac6bf-ac4b-5d54-90fd-34f6f2725e8f（FROZEN）
Frozen Candidate：search-protocol-boundaries
Run 数：20 Attempt（18 completed，2 invalid；授权上限 24）
费用：460,557 microusd（授权上限 1,300,000 microusd）
证据：5 个 replay bundle、1,216 文件全部校验通过；Secret pattern scan 0 命中
解释限制：两 Case adaptive validation，仅证明真实优化器链路，不是 locked-test 确认
CI：本地 Ruff、mypy、185 pytest、wheel 全部通过；GitHub CI 待推送
```

---

## 8. 阶段 3：DeepSeek Skill Proposal MVP

### 8.1 目标

将现有 Fake Process Proposal Generator 替换为单一 DeepSeek/OpenAI-compatible Generator，
基于真实 train failure evidence 生成 3～5 个 Skill 改进候选。

### 8.2 AI 执行任务

```text
实现 DeepSeek Skill Proposal MVP。

前提：
- 阶段 2 的真实 evaluator 已稳定
- 从最新 main 创建 codex/deepseek-skill-proposal-mvp

目标：
- 只接入 DeepSeek，不开发多 Provider 框架
- 复用现有 Audited Process Skill Proposal Generator 协议
- 输入仅包含 base Skill、脱敏 train FailureEvidenceBundle 和输出 Schema
- 不向 Generator 提供 validation、regression、confirmation 或 locked Case
- 冻结 provider、model、生成参数、Generator prompt/schema hash
- 每轮生成 3～5 个结构化候选 mutation
- 每个候选保存 failure lineage、修改假设和风险说明
- 候选必须通过现有 Skill lint
- 非法输出或生成失败明确报错，不自动回退确定性 Generator
- 记录 Generator Token、时延和费用
- 先完成 Fake API/Process 测试
- 用户授权后完成一次小额真实 proposal smoke

本阶段不做：
- Claude、OpenAI、Qwen 等其他 Generator
- 自动反复生成直到成功
- 多轮进化
- locked test
- 自动发布 Skill v2
```

### 8.3 完成标准

- DeepSeek 实际生成至少 3 个合法候选；
- 候选均能追溯到真实 train failure evidence；
- 所有候选及失败候选均保留，不只展示 winner；
- 阶段 2 的真实 evaluator 能对候选执行小规模筛选；
- 没有合格 winner 也算正确结果；
- Ruff、mypy、pytest、wheel 和 GitHub CI 通过；
- 独立提交并推送分支。

### 8.4 完成记录

```text
Branch：codex/deepseek-skill-proposal-mvp
Code commit：c45813c
CI fix commit：cf050d3
PR：https://github.com/ranmaoxia0123/AgentSkill-Eval/pull/10（Draft）
Generator model：deepseek-v4-pro
Train Experiment：41ff1ca2-ab2e-5990-b05b-70b7aa1f274d
Train Runs：首次 4（2 completed，2 invalid）；工具预算修正后复跑 4（2 completed，2 invalid）
Train Agent 费用：首次 101266 / 300000；复跑 100941 / 220000 microusd
Failure Bridge：两次均 INSUFFICIENT；复跑 treatment 因 turn_limit invalid，仍无 eligible task failure
候选数：0；证据不足，proposal 按协议未调用
Generator 费用：0 / 100000 microusd
CI：Python、Dashboard、Secret Scan 全部通过
```

---

## 9. 阶段 4：SkillVersion Promotion MVP

### 9.1 目标

把当前 `AWAITING_INDEPENDENT_FINAL_EVALUATION` handoff 串联到 confirmation、一次 locked test、
人工审核和不可变 Skill v2 发布。

### 9.2 AI 执行任务

```text
实现 SkillVersion Promotion MVP。

前提：
- 阶段 3 已能产生真实候选和 frozen winner
- 从最新 main 创建 codex/skill-version-promotion-mvp

流程：
Frozen winner
→ validation_confirm
→ regression/final decision
→ human approve/reject
→ one locked_test
→ publish Skill v2 or REJECTED

目标：
- 复用现有 IndependentFinalEvaluator，不重写 Final Evaluation
- validation_confirm 未通过时不得发布 v2
- locked test 只消费一次
- 提供简单 CLI 人工 approve/reject
- 通过后生成本地不可变 SkillVersion 目录和 manifest
- manifest 保存 name/version、parent v1 hash、v2 hash、diff、hypotheses、
  search/regression/confirmation/locked evidence references 和审批结果
- 支持查看 v1/v2 diff
- 支持把 active version 指回 v1，但不修改历史 v2 内容
- Dashboard 只增加只读演化链展示

建议 CLI：
agentskill-eval skill promote confirm HANDOFF
agentskill-eval skill promote locked PROMOTION_ID
agentskill-eval skill promote approve PROMOTION_ID --version v2
agentskill-eval skill promote reject PROMOTION_ID --reason "..."

本阶段不做：
- 在线 Skill Registry
- 自动发布到 Skill 市场
- 用户权限系统
- Dashboard 写操作
- 无人工审核的自动发布
```

### 9.3 完成标准

- 完整跑通 `v1 → frozen winner → confirm → locked → v2`；
- 任一门失败会生成明确 `REJECTED`，不会创建 active v2；
- SkillVersion manifest 能完整追溯父版本、diff 和实验报告；
- 发布后的 v2 内容不可修改；
- Dashboard 可只读展示失败→假设→候选→确认→locked→v2；
- Ruff、mypy、pytest、wheel、Dashboard 和 GitHub CI 通过；
- 独立提交并推送分支。

### 9.4 完成记录

```text
Branch：
Commit：
PR：
Promotion ID：
SkillVersion：
Decision：
CI：
```

---

## 10. 阶段 5：Real Skill Evolution Evidence Release

### 10.1 目标

使用前四个阶段的能力完成一次真实、可复现的 Skill v1→v2 单轮演化。这是项目的最终研究验收，
主要工作是实验和报告，不再扩建平台。

### 10.2 AI 执行任务

```text
完成一次真实 Skill v1→v2 演化实验并发布脱敏证据。

前提：
- 阶段 4 已合并
- 从最新 main 创建 codex/real-skill-evolution-evidence

实验范围：
- 一个明确版本的 Python Bug Fix Skill v1
- 约 10～16 个真实 Git 历史 Case
- 至少 3 个仓库或独立缺陷家族
- train、validation_search、regression_dev、validation_confirm、locked_test 分离
- DeepSeek Proposal Generator
- 一个固定真实 Agent Runtime
- confirmation/locked 的每个 Case 每臂至少 3 次

完整流程：
Skill v1
→ observed train failures
→ DeepSeek proposals
→ real validation search
→ regression_dev
→ validation_confirm
→ unique winner
→ one locked_test
→ approve/reject Skill v2

报告必须展示：
- v1/v2 pass rate 和 absolute gain
- W/T/L
- 改进 Case 与回归 Case
- Token、时延和费用
- Failure 类型变化
- 所有候选和淘汰原因
- validation 与 locked 结果
- 样本规模和结论边界

如果 v2 没有提升：
- 发布 REJECTED 报告
- 不修改评测口径
- 不重复消费 locked test
```

### 10.3 完成标准

- 真实调用前完成 Run 数、Token 和预算确认；
- 实验完成或诚实输出 `REJECTED`；
- 发布脱敏 JSON、HTML、README 和必要哈希；
- 不提交 API Key、原始缓存或不安全日志；
- README 增加完整 v1→v2 案例；
- GitHub CI 通过；
- 创建并推送 `v0.3.0-rc1` tag。

### 10.4 完成记录

```text
Branch：
Commit：
PR：
Experiment ID：
Skill v1/v2：
Run 数：
总费用：
Decision：
Tag：
CI：
```

---

## 11. 阶段 6：Second Skill Family（可选）

阶段 5 完成后，核心项目已经成立。若需要进一步证明通用性，只选择一个新的 Skill family，优先
选择 MCP Tool Skill。

### 11.1 建议任务

```text
完成 MCP Tool Skill 的真实 v1→v2 实证。

目标：
- 接入一个本地真实 MCP Server
- 设计 8～10 个 selection、parameter、sequence、recovery 和 side-effect Case
- 使用真实 Agent 运行 without/with Skill 和 v1/v2
- 复用现有 failure→proposal→search→promotion 流程
- 发布第二份真实实验报告

不做：
- 同时开发 Memory/RAG
- 生产 MCP 管理平台
- 多租户和远程调度
```

Memory/RAG 可以作为更后面的独立选题，不应阻塞核心项目。

## 12. 当前明确暂缓的内容

在阶段 5 完成前，不开发：

- FastAPI、PostgreSQL、Redis、Celery；
- Kubernetes、分布式 Worker、多租户；
- Dashboard 写操作和复杂权限；
- 多 Agent/多 Provider 排行榜；
- 大规模 GitHub 自动抓取；
- LLM Judge 体系；
- 自动发布 Skill；
- 同时扩展 MCP、Memory/RAG、Browser 和 Document；
- 新的综合总分。

这些功能不会直接帮助证明 Skill v1 可以可靠迭代为 v2。

## 13. 每阶段通用完成检查

每个阶段结束前执行：

```text
[ ] pwd 和分支正确
[ ] git status 无意外文件
[ ] Ruff 通过
[ ] mypy 通过
[ ] pytest 通过
[ ] wheel 构建和安装导入通过
[ ] 相关 CLI smoke 通过
[ ] 文档与实际实现一致
[ ] 未提交 Secret、缓存或原始不安全日志
[ ] 独立 Git commit
[ ] 推送 codex/* 分支
[ ] GitHub CI 通过
[ ] 回填本文档的完成记录
[ ] 停止并向用户报告，不自动开始下一阶段
```

## 14. 项目完成判定

阶段 0～5 完成后，AgentSkill-Eval 的核心目标即视为完成：

```text
真实 Skill v1
→ 真实失败证据
→ 真实 LLM 候选
→ 真实 Agent 搜索与回归
→ 独立 confirmation
→ 一次 locked test
→ 不可变 Skill v2 或可解释 REJECTED
```

阶段 6 及后续平台化均属于加分项，不属于核心完成条件。
