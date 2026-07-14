# Optimization Benchmark Split v1

## 目标与边界

本阶段为 Skill v1→v2 优化冻结五个彼此隔离的真实 Git 历史 DatasetVersion。它只证明
Benchmark 的来源、可执行性、隔离和发布链路，不包含 Agent 调用，也不证明 Skill 已经提升。

本版本采用比平台通用 exposure-zone 契约更严格的发布规则：**一个 repository/fork lineage
只能属于一个 split**。20 个 Case 来自 5 个许可证清楚的 Python 开源仓库，每个 split 固定
4 个 Case，每个 Case 使用独立 patch family。

| Split | Repository | License | Optimizer 可见 |
|---|---|---|---:|
| `train` | more-itertools | MIT | 是 |
| `validation_search` | cachetools | MIT | 是 |
| `regression_dev` | boltons | BSD-3-Clause | 是 |
| `validation_confirm` | humanize | MIT | 否 |
| `locked_test` | pydash | MIT | 否 |

完整候选、commit、任务、测试命令和替代修复位于
`examples/benchmark-sources/optimization-split-v1/`。五个离线 Git bundle 固定真实历史，运行时
不访问网络。

## 质量门

每个 Case 必须同时通过以下确定性验证：

1. 从 `before_commit` 重建 fixture，并叠加修复提交中的回归测试；
2. before fixture 连续 3 次失败；
3. after fixture 连续 3 次通过；
4. 删除或反转关键修复逻辑后连续 3 次失败；
5. 与参考补丁不同的替代修复连续 3 次通过；
6. 测试不使用网络、当前时间或外部随机状态；
7. 测试源码不泄露参考补丁；
8. provenance、许可证、fixture、grader、patch 和 metadata 均冻结 SHA-256；
9. repository、fork lineage、patch family 和 independence group 通过跨 split 审计；
10. 逐候选人工审核后才允许发布不可变 DatasetVersion。

验证器设置 `PYTHONDONTWRITEBYTECODE=1` 并禁用 pytest cache provider，避免测试运行修改冻结
fixture。`src` 布局仓库使用只包含名称和版本的临时 `.dist-info` shim，以便离线从源码导入；
该 shim 不包含生产代码，也不写入 DatasetVersion。

## CLI

校验完整计划，不执行候选：

```bash
agentskill-eval benchmark split validate \
  examples/benchmark-sources/optimization-split-v1/plan.yaml \
  --workspace .agentskill-eval/optimization-split-v1
```

执行 20 Case × 4 variants × 3 repeats，共 240 次离线命令，并发布五个 DatasetVersion：

```bash
agentskill-eval benchmark split publish \
  examples/benchmark-sources/optimization-split-v1/plan.yaml \
  --workspace .agentskill-eval/optimization-split-v1 \
  --reviewer REVIEWER \
  --publisher PUBLISHER \
  --confirm-offline-publication
```

发布操作没有模型费用，但仍要求显式确认。重复执行同一命令会校验并返回已有不可变 release，
不会覆盖 DatasetVersion。

```bash
agentskill-eval benchmark split verify \
  .agentskill-eval/optimization-split-v1/optimization-benchmark-releases/\
python-bug-fix-optimization-v1/2026.07.14.1/release-manifest.json \
  --workspace .agentskill-eval/optimization-split-v1

agentskill-eval benchmark split inspect RELEASE_MANIFEST
```

## Locked boundary

完整 release manifest 是发布者和独立评测器的审计对象。Optimizer 只能接收同目录下的
`optimizer-view.json`：其中包含 train、validation_search 和 regression_dev 的路径；
validation_confirm 与 locked_test 只暴露 Case 数量和收据哈希，不暴露路径或 Case key。

这些 Case 来自公开 Git 历史，因此“locked”是流程隔离而不是密码学保密，污染风险标记为
`high`。第一次独立终评前，Optimizer 运行环境不得挂载完整 release manifest 或两个 holdout
DatasetVersion。

## 已验证发布

2026-07-14 的离线发布结果：

- release SHA-256：`aa0b0ad1a38c8f6580cc0c962140565b5f4cba0db17441999df7e1e9cdf5b7ab`；
- 20 Case、5 repositories、20 independence groups；
- 每个 split 4 Case；
- 每个 split 48 条命令证据，总计 240 条；
- 0 model calls，0 paid cost；
- `benchmark split verify` 通过。

脱敏结果见 `experiments/optimization-benchmark-split-v1-2026-07-14/`。公开历史和较小样本只适合
支撑后续受控 Skill 优化流程，不支持总体泛化或真实 Agent 性能结论。
