# SkillVersion Promotion Core（阶段 4A）

## 1. 范围与证据边界

阶段 4A 提供 Skill winner 从独立确认、锁定测试、人工审批到不可变版本发布的基础框架。
当前实现只使用调用方传入的 `PromotionEvidenceRef`，不会读取阶段 3 的真实 winner，也不会
自行执行真实 `validation_confirm` 或 `locked_test`。

Fake／simulated evidence 只能验证：

- 状态机、完整性检查和拒绝路径正确；
- v1/v2 diff 与不可变 Manifest 可以生成和重放；
- 发布冲突不会覆盖已存在的 SkillVersion。

它不能证明 Skill 有效，不能作为真实 Agent 性能证据，也不能被表述为真实 v2 已发布。使用
模拟证据生成的 Manifest 固定记录 `simulated_evidence=true`，并带有明确 `claim_limit`。

## 2. 状态机

```text
CREATED
  ├─ validation_confirm = CONFIRMED → VALIDATION_CONFIRMED
  └─ 其他决定／人工拒绝             → REJECTED

VALIDATION_CONFIRMED
  ├─ locked_test = CONFIRMED → LOCKED_TEST_COMPLETED
  └─ 其他决定／人工拒绝      → REJECTED

LOCKED_TEST_COMPLETED
  ├─ approve → APPROVED
  └─ reject  → REJECTED

APPROVED
  ├─ 原子发布成功 → PUBLISHED
  └─ 冲突或写入失败 → REJECTED
```

`PUBLISHED` 和 `REJECTED` 是终态。每次迁移都记录连续序号、actor、时间、原因以及输入／输出
摘要哈希。乱序、重复 evidence 和非法状态迁移都会被拒绝。

## 3. 数据契约

### `PromotionEvidenceRef`

这是对独立 Final Evaluation 报告的内容寻址引用，不复制逐 Case 结果：

- `stage`：只能按 `validation_confirm`、`locked_test` 顺序进入；
- `final_evaluation_job_id` 和 `report_sha256`；
- `decision`；
- frozen base/winner Skill SHA-256；
- `simulated` 和 validator 版本。

Promotion Core 会校验 evidence 中的 base/winner 哈希与 Promotion 冻结输入完全一致。
非 `CONFIRMED` 决定会留下 evidence 和拒绝原因，然后进入 `REJECTED`。

### `SkillVersionPromotion`

保存目标版本、OptimizationJob、winner candidate、base/winner 哈希、证据、完整迁移历史和
终态。模型严格、不可变且禁止额外字段。只有两份确认 evidence 都存在时才能进入
`APPROVED`。

### `SkillVersionManifest`

发布 Manifest 冻结：

- Skill 名称和版本；
- Promotion、OptimizationJob 和 winner candidate ID；
- parent／winner 内容哈希与内容长度；
- v1/v2 diff 哈希；
- validation-confirm／locked-test evidence；
- simulated evidence 标志和 claim limit；
- 创建、发布时间及审计 metadata。

Manifest、`SKILL.md` 和 diff 任何内容变化都需要发布新版本，禁止原地修改。

## 4. 本地存储布局

```text
WORKSPACE/skill-version-promotion/
├── promotions/<promotion-id>/
│   ├── promotion.json
│   └── inputs/
│       ├── base-SKILL.md
│       └── winner-SKILL.md
└── versions/<skill-name>/<semver>/
    ├── SKILL.md
    ├── v1-v2.diff
    ├── manifest.json
    └── manifest.sha256
```

Promotion 输入用 exclusive create 冻结并复验 SHA-256。发布先写临时目录，再原子重命名到
最终版本目录。已存在同名版本时：

- 若属于同一 Promotion 且内容一致，视为崩溃恢复并继续完成状态迁移；
- 若来源或内容不同，拒绝发布、保留原版本，并将新 Promotion 标记为 `REJECTED`。

Manifest 使用项目统一 storage envelope，并额外保存文件 SHA-256 sidecar。读取时同时校验
envelope 和 sidecar；Skill 内容与 diff 也分别按 Manifest 哈希复验。

## 5. 核心调用方式

```python
core = SkillVersionPromotionCore(workspace)

promotion = core.create(
    skill_name="python-review",
    target_version="2.0.0-test",
    optimization_job_id=optimization_job_id,
    winner_candidate_id=winner_candidate_id,
    base_skill_path=base_skill_path,
    winner_skill_path=winner_skill_path,
    actor="skill-search",
)

core.record_validation_confirm(
    promotion.id,
    validation_evidence,
    actor="independent-validator",
)
core.record_locked_test(
    promotion.id,
    locked_test_evidence,
    actor="locked-test-worker",
)
core.approve(promotion.id, actor="reviewer", reason="promotion gates passed")
publication = core.publish(promotion.id, actor="publisher")
```

当前没有 CLI，避免与阶段 3 的 evolve CLI 产生冲突。阶段 3 合并后应新增单独的适配层：

1. 只接受状态为 `FROZEN` 的 OptimizationJob 和 frozen winner；
2. 从不可变 Final Evaluation 报告构造 evidence ref，并复验报告 SHA-256；
3. `validation_confirm` 通过后才允许申请一次性 locked test；
4. 人工审批真实证据后，使用正式 semver 发布 v2。

## 6. 安全与完整性规则

- base 与 winner 内容必须不同；
- evidence 阶段不可重复、不可乱序；
- evidence base/winner 哈希必须匹配 Promotion；
- validation-confirm 和 locked-test 必须都为 `CONFIRMED` 才能审批；
- locked test 的一次性消费仍由 Independent Final Evaluation 的 receipt 负责，Promotion
  不重复实现该权限边界；
- 发布失败必须进入 `REJECTED`，不能静默保留 `APPROVED`；
- 已发布版本不可覆盖；
- 幂等重放不重新创建 evidence 或版本；
- Fake evidence 必须保留 simulated 标识，禁止与真实证据混淆。

## 7. 自动化验证

Fake winner 测试覆盖：

- 完整 `validation_confirm → locked_test → approve → publish` 闭环；
- Manifest、Skill 和 diff 哈希以及幂等重放；
- validation-confirm 非确认时拒绝；
- locked-test 回归时拒绝；
- evidence hash 不一致、重复和乱序时拒绝；
- 发布版本冲突时进入 `REJECTED` 且不覆盖原版本；
- Manifest 被篡改后读取失败；
- Pydantic 合同不可变及 Schema 导出。

阶段 4A 完成不代表完整阶段 4 完成。真实阶段 4 仍依赖阶段 3 frozen winner、独立
validation-confirm、一次性 locked-test、人工审批和非模拟 SkillVersion 发布。
