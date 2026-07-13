# 一条命令运行 P0 配对实验

## 零凭据工程演示

在仓库根目录运行：

```bash
agentskill-eval demo run --workspace .agentskill-eval/demo
```

默认模式为 `mock`，会真实执行平台的计划、72 个逻辑 Run 的状态机与原子持久化、统计和
报告生成，但每个 Run 的 PASS/FAIL、Token 和时延来自确定性 fixture。该模式不调用模型，
不消耗 Agent 额度，适合 CI、答辩和本地 smoke test。

输出 JSON 包含 Experiment/Variant ID、逻辑/完成/invalid Run 数，以及 HTML/JSON 报告路径。
报告顶部强制显示 `SIMULATED DEMO`，Manifest 保存
`protocol_snapshot.evidence_mode=simulated_fixture`，禁止把模拟增益当作 Agent 或 Skill 性能。

默认实验规模：

```text
12 cases × 2 variants × 3 repeats = 72 logical runs
```

## 真实 Agent 实验

真实模式经过固定 `skill-up v0.5.0` 调用内置 Agent Engine。它可能消耗模型额度和费用，
所以必须显式添加 `--confirm-real-run`：

```bash
agentskill-eval demo run \
  --mode skill-up \
  --engine codex \
  --inherit-secret-env OPENAI_API_KEY \
  --confirm-real-run \
  --workspace .agentskill-eval/observed-demo
```

若希望使用 Engine 默认模型，不传 `--model`；需要冻结指定模型时显式传入。Secret 参数只
接受环境变量名称，值从当前进程读取并经 `RunnerRequest.secret_env` 传递，不进入命令参数、
Manifest、日志或最终 JSON。Runner 仍会验证固定版本与二进制 SHA-256。

真实模式逐 Run 在 stderr 输出进度，在 stdout 最后输出单个机器可读 JSON。公开合成
Dataset 与 smoke grader 仍不支持泛化结论，HTML 会显示 `PUBLIC SYNTHETIC DEMO`。

## 可重复性与失败处理

- 每次命令默认创建新的 Experiment UUID，防止覆盖旧证据；
- PairBlock、Variant 顺序和 bootstrap 使用冻结 seed；
- 两臂共享相同 Case、fixture、grader、Engine、模型与预算；
- treatment 唯一增加冻结的 `python-review-v1` Skill；
- Runner/Agent 错误保存为 invalid，不从统计中静默删除；
- 中途退出后可用 `storage recover` 检查未完成 Run，不会自动重放可能计费的外部调用。

缩小开发 smoke run 时可以指定 `--repeats 1 --bootstrap-resamples 100`。简历或报告中的演示
应保留预注册默认规模，不应通过反复调整 seed、Case 或 grader 挑选有利结果。

## 导出离线审计证据

完成实验后可用输出中的 `experiment_id` 生成并验证确定性 tar：

```bash
agentskill-eval experiment bundle \
  .agentskill-eval/demo EXPERIMENT_UUID /tmp/agentskill-eval-evidence.tar
agentskill-eval experiment verify-bundle /tmp/agentskill-eval-evidence.tar
```

该包用于审计和重新分析，不承诺重放外部模型服务端的一次具体生成。
