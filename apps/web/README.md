# AgentSkill-Eval Dashboard

Vue 3 + TypeScript 的只读本地报告 Dashboard。启动、支持格式与安全边界见
[`docs/dashboard-mvp.md`](../../docs/dashboard-mvp.md)。

Promotion 视图额外支持只读导入：

- `ase/skill-version-promotion/v1alpha1`：proposal lineage、confirmation、locked test、人工审核和发布/拒绝状态；
- `ase/skill-version/v1alpha1`：不可变 SkillVersion、父版本/候选/diff 哈希和 claim limit。
- `ase/promotion-workflow/v1alpha1`：Stage 4b proposal/evolution/search lineage、confirmation、locked test 与人工审核；
- `ase/promotion-release/v1alpha1`：Stage 4b APPROVED/REJECTED 决定及不可变 release/SkillVersion/diff 哈希。

缺失字段显示为 `Unavailable / not provided`，Dashboard 不会据此推断 capability 或执行结果。内置 Promotion fixture 仅用于 UI/控制链路测试，不构成真实 Agent 或 Skill 效果证据。
