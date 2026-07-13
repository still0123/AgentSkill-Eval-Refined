# P0 Python Review Demo Dataset

## 定位

`examples/datasets/python-review-demo` 是 AgentSkill-Eval 的首个可导入数据集，用来验证
Dataset → PairBlock → Runner → Measurement → Statistics → Report 工程链路。它包含公开、
人工编写的合成代码和公开 `expect` grader，因此只能提供描述性 Demo 结果：

- 不能证明 Skill 对真实 Python 项目的泛化效果；
- 不能把 6 个设计分组解释为 6 个自然抽样的独立软件项目；
- 不能把公开关键词 grader 描述为隐藏执行式 oracle；
- Agent 或 Skill 看过这些样本后，它们只能留在 `regression_dev/challenge`，不能转为
  `locked_test`。

## 组成

| 类别 | 数量 | 目的 |
|---|---:|---|
| positive | 4 | 空值、资源、边界和异常传播缺陷 |
| negative | 2 | 正确 guard 与 context manager，观察误报 |
| distractor | 2 | 注释中的 BUG 和不可达 deprecated 代码 |
| complex | 2 | 跨模块 cache key 与 normalization contract |
| robustness | 2 | 环境变量布尔解析与 retry budget |

Case 被放入 6 个 `synthetic/*` independence group。该分组用于验证等组权重和层次
bootstrap 的实现，不构成正式统计抽样依据。

## 双层契约

执行语义完全使用固定 `skill-up v0.5.0` 的 Case YAML：`input`、`context.repo_fixture`、
`expect`。平台 sidecar 位于 `metadata/*.meta.yaml`，只保存上游 DSL 没有的字段：

- split 和 category；
- `skill_applicable`；
- independence group、repository、fork lineage 与 patch family；
- source revision、license、synthetic 和 contamination risk；
- oracle 类型、预期信号和 tags。

`DatasetLoader` 使用严格 Pydantic 模型，拒绝未知字段、重复 ID、路径穿越、符号链接、
缺失 fixture、Case/sidecar ID 不一致和类别配额不足。数据集标识包含 manifest、sidecar、
Case、相关 fixture、prompt 和 grader 的规范哈希；fixture 变化会产生新的 Dataset ID。

## 验证

```bash
make demo-validate
```

或直接运行：

```bash
agentskill-eval dataset validate examples/datasets/python-review-demo
```

命令输出冻结的 Dataset UUID/SHA-256、Case ID、类别数量和 independence groups。测试环境
发现受支持的固定二进制时，还会逐 Case 运行真实 `skill-up validate`，验证平台编译器、
Skill 安装声明、fixture 路径与上游 schema 的兼容性。

配套 Skill 位于 `examples/skills/python-review-v1`。`metadata.yaml` 冻结版本和
`SKILL.md` SHA-256；修改内容必须升级版本并更新哈希，不能原地覆盖已产生实验的版本。

## 下一步实验

真实 Agent 实验应以 12 Case × 2 Variant × 3 repeats 产生 72 个逻辑 Run，并记录模型、
Runner、Skill、环境和价格快照。报告必须显示 `demo_only=true` 或等价声明，只陈述本次
运行的观察值。正式结论需要另建来源清晰、locked、至少 50 个独立 Case 的数据集。
