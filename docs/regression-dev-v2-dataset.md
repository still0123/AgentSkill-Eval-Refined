# regression_dev v2：Funcy 数据集

本阶段发布了一个独立、离线可复现的 `regression_dev` DatasetVersion，用来验证后续
Skill v1 是否在真实缺陷上失败，以及候选 Skill 是否修复失败而不引入回归。

## 数据范围

数据源是 [Suor/funcy](https://github.com/Suor/funcy)，许可证为 BSD-3-Clause。四个 Case
均来自固定的真实 Git 历史修复：fixture 使用修复前 commit，回归测试取自修复后 commit。
每个 Case 都经过 before 失败、after 通过、mutation 失败和 alternative repair 通过的三次
离线重复验证。alternative repair 与上游参考补丁不同，且候选选择不读取任何 Agent 成绩。

| Case | 测试 | 主题 |
|---|---|---|
| `funcy-throttle-timedelta` | `tests/test_flow.py::test_throttle` | `timedelta` 周期转换 |
| `funcy-cache-invalidate-idempotent` | `tests/test_calc.py::test_cache_invalidate` | 重复失效幂等性 |
| `funcy-retry-list-errors` | `tests/test_flow.py::test_retry_many_errors` | 列表异常类型重试 |
| `funcy-cache-mixed-arguments` | `tests/test_calc.py::test_cache_mixed_args` | 原始参数与缓存 key 分离 |

固定的 Funcy bundle SHA-256：

```text
3da2875f00495bb523447ec1ade7bc121e7329ec4bcfc663637ae1a7b6e291fa
```

## 发布物

本地发布目录由 `regression-dev publish` 生成，状态为
`AWAITING_OBSERVED_BASELINE_SCREENING`。它绑定了原 Optimization Benchmark Release、
Funcy bundle、DatasetVersion 内容哈希、四个 Case、仓库 lineage 以及生成器/验证器版本。
发布后 DatasetVersion 不可修改；`verify` 会检查 DatasetVersion 注册、内容哈希、split、
Case 顺序和仓库隔离。

```bash
agentskill-eval benchmark regression-dev validate PLAN --workspace WORKSPACE
agentskill-eval benchmark regression-dev publish PLAN \
  --workspace WORKSPACE --reviewer offline-reviewer \
  --publisher offline-publisher --confirm-offline-publication
agentskill-eval benchmark regression-dev verify RELEASE \
  --workspace WORKSPACE --plan PLAN
```

## 证据边界

本阶段没有运行 Agent、模型或 DeepSeek，`model_calls=0`、`agent_runs=0`、`paid_cost=0`。
因此这个 DatasetVersion 只证明数据构造和 oracle 质量，不证明 Skill v1/v2 的优劣，也不授予
validation、confirmation 或 locked test 的访问权。下一阶段应先运行观察性 baseline screening，
确认至少存在一个 v1 失败 Case，再决定是否进入 Skill 优化实验。
