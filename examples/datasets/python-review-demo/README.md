# Python Review Demo Dataset

这是用于验证 AgentSkill-Eval P0 工程闭环的 **合成演示集**，不是用于发表模型能力或
Skill 泛化结论的正式 Benchmark。数据集包含 12 个 Case、6 个设计分组：

- 4 个直接缺陷；
- 2 个无缺陷反例；
- 2 个干扰样本；
- 2 个跨模块复杂缺陷；
- 2 个鲁棒性缺陷。

每个 Case 使用 `skill-up v0.5.0` 原生 Case YAML，平台只在 `metadata/` 保存 split、
group、provenance、oracle 类型和 Skill applicability 等 sidecar。所有 fixture 和评分规则
均公开，因此它们只能作为 smoke grader，不能声称是隐藏 oracle。

```bash
agentskill-eval dataset validate examples/datasets/python-review-demo
```

正式实验应另建 locked dataset，避免公开 prompt、fixture 和 grader，并使用不少于 50 个
独立 Case、预注册协议和一次性冻结批次。
