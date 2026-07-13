# Benchmark-guided Skill Search MVP

本阶段实现自适应 Skill 搜索的控制纵切：从一个冻结的 Markdown `SKILL.md` 出发，生成
manual、random 和 search 候选，在 `validation_search` 做 successive halving，并用多目标
Pareto 约束冻结一个 search-origin winner。

它不是“自动进化成功”的证明，也不读取 `locked_test`。搜索结果只说明候选在已被搜索
过程反复观察的 validation 上表现如何。

## 搜索流程

```text
Frozen base Skill
  ├── original comparator
  ├── manual comparator
  ├── equal-seed random comparator
  └── search mutations (>= 3)
          ↓
Static leakage/safety lint
          ↓
Deterministic validation subset
          ↓
Successive halving
          ↓
Full validation_search
          ↓
Pareto dominance + hard regression/cost constraints
          ↓
Freeze exactly one search-origin winner
```

original、manual、random 三组始终进入 full validation，避免报告只有搜索算法挑中的幸运
候选。search 候选按 subset 的 pass rate、mean score、Token 和 Skill 长度排序，只有冻结
数量的候选晋级。所有淘汰候选、subset 结果和原因都保存在 manifest 中。

## 防泄漏边界

- `OptimizationSearchSpec` 没有 `locked_test` 字段，严格模型会拒绝额外输入；
- Job manifest 将 `locked_test_accessed` 限定为字面值 `false`；
- 候选 lint 拒绝 validation case ID 和预注册 oracle/test leakage token；
- MVP 只接受单个 `SKILL.md` 和可选 `metadata.yaml`，不允许脚本或未冻结资源；
- 候选、输入 Skill、validation manifest 和每次状态转换均按内容哈希保存；
- 已完成外部 Agent Run 应由 Process Evaluator 自身保证幂等，搜索器按候选-case 预算计数；
- validation 看过之后产生的 winner 不得宣称具有确认性泛化收益。

最终 locked-test 只能由未来独立 final-evaluation workflow 读取 frozen base/winner 哈希并
发起一次预注册批次。该 workflow 不属于当前搜索进程。

## Evaluator 边界

### Process Evaluator

生产接入使用 `evaluator.type: process`。搜索器以 stdin 发送：

```json
{
  "schema_version": "ase/process-evaluator-request/v1alpha1",
  "skill_file": "/absolute/frozen/SKILL.md",
  "dataset_file": "/absolute/validation-search.yaml",
  "dataset_sha256": "...",
  "case_ids": ["case-a", "case-b"],
  "stage": "validation_subset"
}
```

进程必须按相同 case 顺序返回严格 JSON：

```json
{
  "results": [
    {
      "case_id": "case-a",
      "passed": true,
      "score": 1.0,
      "input_tokens": 100,
      "output_tokens": 20,
      "latency_ms": 500,
      "cost_microusd": 1200
    }
  ]
}
```

搜索器不通过 shell 执行命令、不继承凭据，并冻结 command、版本和可执行文件哈希。
每个 Evaluator 还必须显式声明 `simulated: true/false`；外部进程不会因为采用 Process
接口就自动被当作真实证据。Process 模式只接受全部 case 都属于 `validation_search` 的
独立 DatasetVersion，并复制到 Job 输入区；控制器在每次评测前后复验 Skill 和数据集
组合哈希，因此搜索进程无法修改冻结输入，也无法借同一目录
读取 `validation_confirm` 或 `locked_test`。

### Simulated Keyword Evaluator

仓库演示使用显式 `simulated_keyword`。它只检查 Skill 是否覆盖模拟 case 的 required
terms，用于验证搜索控制器，不是 Agent 能力评测。CLI 必须显式传入
`--allow-simulation`；JSON/HTML、Job 和每次 Evaluation 都写入 `simulated=true`，报告
展示醒目的 claim limit。

## 离线演示

```bash
agentskill-eval optimize search \
  examples/optimizer/python-review-search/search.example.yaml \
  --workspace .agentskill-eval/optimizer \
  --allow-simulation

agentskill-eval optimize status \
  .agentskill-eval/optimizer OPTIMIZATION_JOB_UUID
```

固定演示生成 7 个候选，使用 4-case subset、晋级 3 个 search 候选，在 80 个
candidate-case 上限内使用 76 个，最终冻结一个 winner。报告覆盖
original/manual/random/search 四组，并完整展示未晋级和未被选择的候选。

## 状态与目录

候选状态：

```text
CREATED → LINTED → SCREENED → PROMOTED → FULL_VALIDATED → FROZEN
                    └────────→ ELIMINATED                 └→ ELIMINATED
CREATED/LINTED ───────────────────────────────────────────→ REJECTED
```

```text
optimization-jobs/<job-id>/
├── job.json
├── search-spec.json
├── validation-search.json
├── inputs/{base-SKILL.md,manual-SKILL.md}
├── candidates/<candidate-id>/
│   ├── SKILL.md
│   ├── candidate.json
│   └── history/0001.json ...
└── reports/{search-report.json,search-report.html}
```

## 当前不做

- 不读取或运行 locked test；
- 不声称 search 普遍优于 manual/random；
- 不实现 LLM hypothesis generator；mutation 来自冻结的显式 hypotheses；
- 不实现 validation_confirm、regression_dev 和最终发布审批；
- 不实现 FastAPI、Redis、Vue、MCP 或 Memory/RAG。

该纵切已经实现为 [Independent Final Evaluation](./independent-final-evaluation.md)：独立
权限域只读取 frozen base/winner，并在 `validation_confirm` 或一次性 `locked_test` 上配对
复评；如果查看结果后再次修改 Skill，原确认结论不再适用于新版本。
