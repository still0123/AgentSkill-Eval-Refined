# Independent Final Evaluation MVP

## 1. 目标

Benchmark-guided Skill Search 会反复观察 `validation_search`，因此搜索 winner 在该 split
上的领先只能用于候选选择，不能证明泛化。Independent Final Evaluation 在独立权限边界中
只接收已经冻结的 OptimizationJob、base Skill、winner Skill 和一个未参与搜索的数据集，
执行严格配对复评。

本阶段支持两种证据层级：

- `validation_confirm`：确认 winner 是否在独立缺陷家族上保持收益；
- `locked_test`：对精确冻结产物进行一次最终测试，同一 OptimizationJob 只能消费一次。

## 2. 信任边界

```text
Search domain
  OptimizationJob(FROZEN)
  base hash + winner hash
             │ only frozen artifacts
             ▼
Final-evaluation domain
  validation_confirm OR locked_test DatasetVersion
             │
             ├── paired base/winner runs
             ├── alternating execution order
             ├── repeated measurements
             ├── pre/post input hash verification
             └── immutable report / locked-test receipt
```

搜索配置没有 confirmation 或 locked-test 路径。最终评测器也不会重新生成、修改或重新选择
winner。生产 Process Evaluator 只收到最终 Job 输入目录中的两份 Skill 副本和单 split
DatasetVersion 副本。

本地 MVP 的路径隔离是应用协议边界，不等同于操作系统安全沙箱。Process Evaluator 仍必须
是受信任、固定版本的 Runner adapter；不可信 Agent 代码应继续在 Runner 的 Docker 沙箱中
运行。

## 3. 数据集要求

真实最终评测只接受平台 `DatasetVersion` 目录，并执行以下质量门：

- 所有 case 必须属于请求的唯一 split；
- 非模拟 Evaluator 拒绝 `demo_only` 数据集；
- DatasetVersion 在复制后及每次评测前后复验组合哈希；
- 若搜索使用了真实 DatasetVersion，则 final split 与 `validation_search` 的 repository、
  fork lineage、patch family 和 independence group 均不得重叠；
- base 和 winner 必须使用完全相同的 case 顺序、Evaluator 哈希和重复次数。

模拟模式使用显式 `simulated: true` 的 `final-validation.yaml`，仅验证控制器行为，不能作为
Agent 性能或 Skill 有效性的证据。

## 4. 配对协议

每次 repeat 运行 base 与 winner。为降低顺序偏差，偶数 repeat 先 base，奇数 repeat 先
winner。每次调用前后均复验：

- base/winner Skill 内容哈希；
- DatasetVersion 组合哈希；
- Evaluator 声明哈希；
- case 集合和返回顺序。

逐 case 聚合重复运行的通过率和平均分，并分类为：

- `WIN`：winner 通过率高于 base；
- `LOSS`：winner 通过率低于 base；
- `TIE_POSITIVE`：通过率相同且至少为 0.5；
- `TIE_NEGATIVE`：通过率相同且低于 0.5。

## 5. 判定规则

决策按下列优先级执行：

1. loss case 超过上限：`REGRESSION`；
2. Token 开销超过上限：`REGRESSION`；
3. 独立缺陷组不足：`DESCRIPTIVE_ONLY`；
4. independence-group bootstrap 的 95% 增益下界达到预注册阈值：`CONFIRMED`；
5. 其他情况：`NOT_CONFIRMED`。

成功率和增益按 independence group 等权聚合，避免同一缺陷家族包含更多 case 而获得更高
统计权重。Bootstrap seed 和 resample 数量均冻结在 spec 与最终报告中。

`CONFIRMED` 的含义受 split 限制：confirmation 通过仍不代表 locked test 已通过；locked
test 结论也只适用于报告中精确冻结的 Skill、Evaluator 和 DatasetVersion 哈希。

## 6. Locked-test 单次消费

首次启动 locked-test 评测时，系统使用原子 `O_EXCL` 写入：

```text
final-evaluations/locked-test-receipts/<optimization-job-id>.json
```

凭证冻结 final job、dataset 和 evaluator 哈希。相同 final job 可以幂等读取完成报告；任何
不同配置、Evaluator 或 DatasetVersion 都不能再次消费同一 OptimizationJob 的 locked
test。即使首次执行失败，凭证仍保留，避免通过反复尝试窥探测试集。

## 7. CLI

先运行搜索并冻结 winner：

```bash
agentskill-eval optimize search \
  examples/optimizer/python-review-search/search.example.yaml \
  --workspace .agentskill-eval/optimizer \
  --allow-simulation
```

再运行独立 confirmation：

```bash
agentskill-eval final evaluate \
  examples/optimizer/python-review-search/final.example.yaml \
  --workspace .agentskill-eval/optimizer \
  --allow-simulation
```

查看不可变结果：

```bash
agentskill-eval final status .agentskill-eval/optimizer FINAL_JOB_UUID
```

生产配置应改用：

```yaml
evaluator:
  type: process
  command: [/absolute/path/to/final-evaluator]
  version: pinned-version
  simulated: false
```

Process Evaluator 沿用严格 stdin/stdout JSON 合约；`stage` 明确为
`validation_confirm` 或 `locked_test`。

## 8. 存储布局

```text
final-evaluations/
├── jobs/<job-id>/
│   ├── job.json
│   ├── inputs/
│   │   ├── base-SKILL.md
│   │   ├── winner-SKILL.md
│   │   └── final-dataset/ or final-validation.json
│   └── reports/
│       ├── final-report.json
│       ├── final-report.sha256
│       └── final-report.html
└── locked-test-receipts/<optimization-job-id>.json
```

## 9. MVP 限制与下一步

本阶段不实现：

- Web 服务、Redis、Vue 或 Kubernetes；
- sequential testing、提前停止或反复打开 locked test；
- 自动发布 winner 到生产；
- 以 LLM Judge 替代确定性 grader；
- 对不可信 Process Evaluator 提供 OS 级隔离。

下一阶段可以将 confirmation/locked-test 报告接入 Skill 版本注册与回归发布门，并为真实
Runner 增加容器化权限隔离和费用上限。
