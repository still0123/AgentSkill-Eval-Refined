# Stage 4b：Promotion Workflow Integration

## 范围

Stage 4b 把 `AWAITING_INDEPENDENT_FINAL_EVALUATION` Evolution handoff 接入阶段 4a 的
Promotion Core，并复用现有 `IndependentFinalEvaluator` 完成：

```text
Fake frozen handoff
→ validation_confirm
→ one-shot locked_test
→ explicit human review
→ APPROVED / REJECTED
→ immutable fixture SkillVersion + release lineage manifest
```

本阶段强制 `simulated=true`，只接受 Fake／fixture evolution 和 final-evaluation evidence。
它不会读取阶段 3 的真实候选，不调用真实 Agent，不消费真实 locked test，也不发布真实 Skill
v2。

## Handoff 门禁与谱系

`begin` 会从 OptimizationStore 重新读取 original 和 frozen winner，不能只相信 handoff 中的
路径。以下内容必须一致：

- handoff 状态为 `AWAITING_INDEPENDENT_FINAL_EVALUATION`；
- `locked_test_accessed=false`、`auto_publish=false`；
- OptimizationJob 为 `FROZEN`，winner candidate ID 唯一；
- base/winner Skill 内容与声明 SHA-256 一致；
- EvolutionReport、RegressionGate 与 handoff 的 evolution/job/candidate/hash 一致；
- regression gate 已通过；
- evolution evidence 明确为 simulated。

每个 Workflow 冻结并哈希五类 lineage artifact：

1. final-evaluation handoff；
2. evolution report；
3. regression gate；
4. hypotheses/proposal lineage；
5. search report。

最终 `PromotionReleaseManifest` 再关联 confirmation、locked-test、human review、SkillVersion
Manifest 和 v1/v2 diff hash，形成父版本到候选发布的完整谱系。

## 状态机

```text
AWAITING_CONFIRMATION
  ├─ CONFIRMED     → AWAITING_LOCKED_TEST
  └─ other decision → REJECTED

AWAITING_LOCKED_TEST
  ├─ CONFIRMED     → AWAITING_HUMAN_REVIEW
  └─ other decision → REJECTED

AWAITING_HUMAN_REVIEW
  ├─ approve → APPROVED + immutable fixture SkillVersion
  └─ reject  → REJECTED
```

真实 locked-test 的“一次消费”仍由 `IndependentFinalEvaluator` 的原子 receipt 保证，Workflow
不会复制或绕过该边界。

## CLI

创建 workflow：

```bash
agentskill-eval skill promote begin FINAL_HANDOFF \
  --skill-name python-review \
  --target-version 2.0.0-fixture \
  --workspace .agentskill-eval-workspace
```

执行 Fake confirmation 和 locked test：

```bash
agentskill-eval skill promote confirm WORKFLOW_ID CONFIRM_SPEC \
  --workspace .agentskill-eval-workspace \
  --allow-simulation

agentskill-eval skill promote locked WORKFLOW_ID LOCKED_SPEC \
  --workspace .agentskill-eval-workspace \
  --allow-simulation
```

人工审批或拒绝：

```bash
agentskill-eval skill promote approve WORKFLOW_ID \
  --reviewer fixture-reviewer \
  --reason "Fake gates passed" \
  --confirm-human-review \
  --allow-simulation \
  --workspace .agentskill-eval-workspace

agentskill-eval skill promote reject WORKFLOW_ID \
  --reviewer fixture-reviewer \
  --reason "Fixture policy rejection" \
  --confirm-human-review \
  --workspace .agentskill-eval-workspace
```

只读状态：

```bash
agentskill-eval skill promote status .agentskill-eval-workspace WORKFLOW_ID
```

approve 缺少人工确认或 simulation opt-in 时拒绝执行。Stage 4b final step 使用非 simulated
Evaluator 时同样失败关闭，不会回退到 Fake。

## 存储

```text
promotion-workflows/<workflow-id>/
├── workflow.json
├── release-manifest.json
└── release-manifest.sha256

skill-version-promotion/
├── promotions/<promotion-id>/...
└── versions/<skill>/<fixture-version>/
    ├── SKILL.md
    ├── v1-v2.diff
    ├── manifest.json
    └── manifest.sha256
```

release manifest 和 SkillVersion 目录均不可覆盖。Workflow 状态更新使用进程锁和原子替换，
重复读取或批准同一已完成 Workflow 不会重新消费 locked test。

## Stage 5 Evidence Release Prep

并行准备模块只打包已经存在的冻结证据：

- 生成脱敏 experiment report；
- 校验 v1/v2 parent/content hash；
- 生成 v1/v2 Markdown 对比；
- 校验 audit artifact member/size/hash；
- 拒绝 Secret、symlink、路径穿越、hash mismatch 和 real/simulated 混合；
- 原子发布 `releases/<release-id>/`，禁止覆盖。

详细说明见 [Evidence Release Prep](./evidence-release-prep.md)。该模块不运行 Agent 或
Evaluator。

## 结论限制

Stage 4b 完成只说明发布控制器、谱系、CLI 和 Dashboard 可以在 Fake evidence 下闭环。
真实 Stage 4 仍需等待阶段 3 frozen winner，再使用独立数据权限和真实 Runner 重新执行
confirmation、locked test 与人工审批。
