# 五分钟演示

> 零费用、可复现、可验证的完整配对评测演示。

## 1. 安装

下载 GitHub Release 中的 wheel 后：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install agentskill_eval-0.3.0rc2-py3-none-any.whl
```

wheel 已内置离线 Dataset 与 Skill，不要求当前目录存在源码仓库。

## 2. 运行离线 Demo

```bash
agentskill-eval demo run \
  --workspace .agentskill-eval/portfolio-demo
```

默认使用确定性 Mock Runner，不访问网络、不调用模型、不产生费用。预期完成：

```text
12 Cases × 2 Arms × 3 Repeats = 72 Runs
Invalid: 0
W/T/L: 5 / 6 / 1
Evidence Class: SIMULATED DEMO
```

相同参数在同一 workspace 重跑会复用稳定 Experiment ID，不会新增实验或覆盖成另一份结果。

## 3. 查看配对结果

离线打开：

```text
.agentskill-eval/portfolio-demo/experiment-report.html
```

重点说明：

- Control 和 Treatment 使用同一个 Case、Runner、环境和预算；
- 唯一实验变量是是否加载 Skill；
- 单次 Run 判定为 PASS、FAIL 或 INVALID；
- 配对 Case 汇总为 WIN、TIE 或 LOSS。

## 4. 查看 Trace

```bash
ls .agentskill-eval/portfolio-demo/trace
python3 -m json.tool \
  .agentskill-eval/portfolio-demo/trace/<run-id>.json
```

Trace 用于检查执行事件、工具调用与判分依据。Demo Trace 明确属于模拟证据。

## 5. 验证证据包

```bash
agentskill-eval demo verify \
  --workspace .agentskill-eval/portfolio-demo
```

预期关键字段：

```json
{
  "valid": true,
  "total_runs": 72,
  "invalid_runs": 0,
  "simulated": true,
  "evidence_class": "SIMULATED_DEMO",
  "audit_bundle_verified": true
}
```

证据包包括：

```text
portfolio-demo/
├── experiment-report.json
├── experiment-report.html
├── paired-results.json
├── trace/
├── skill-diff.patch
├── evidence-index.json
└── audit-bundle.tar
```

## 6. 解释 PASS、FAIL 与 INVALID

| 状态 | 含义 | 是否作为 Skill 失败 |
|---|---|---|
| PASS | 确定性 Grader 验收通过 | 否 |
| FAIL | Agent 完成执行，但任务验收失败 | 是 |
| INVALID | 超时、环境或 Runner 基础设施异常 | 否 |

INVALID 与 FAIL 必须分开，否则会把基础设施问题错误归因给 Skill。

## 7. 解释发布门禁

发布路径为：

```text
Search → Regression → Locked Test → 人工审核 → Release
```

候选没有稳定增益或出现不可接受的回归时，系统保留负结果并拒绝发布。

## 结论边界

本 Demo 只能证明：

- 配对评测链路可运行；
- 三态分类和 W/T/L 汇总可检查；
- 离线证据包可验证；
- 发布门禁能保留负结果。

本 Demo 不能证明：

- Skill 对真实模型具有普遍增益；
- 模拟结果等价于真实 Agent 证据；
- 系统已经发布真实 Skill v2。
