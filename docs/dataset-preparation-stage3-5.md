# 阶段 3～5 数据准备与隔离

## 目标

在阶段 3 搜索、阶段 4 确认发布和阶段 5 真实实验之间冻结数据边界，避免搜索过程
间接读取 confirmation 或 locked-test 信息。本阶段只提供数据契约、隔离审计和模板；不执行
真实模型调用，也不发布真实 Skill v2。

## Split 用途

| Split | 用途 | 是否允许用于修改 Skill |
|---|---|---:|
| `train` | 产生真实失败轨迹、生成候选 | 是 |
| `regression_dev` | 防止已知能力回归 | 是 |
| `validation_search` | 排序和冻结阶段 3 winner | 是，仅限搜索控制器 |
| `validation_confirm` | 独立确认 frozen winner | 否 |
| `locked_test` | 对精确冻结产物做一次最终评测 | 否 |

`locked_test` 只能由 Independent Final Evaluation 的一次性凭证机制消费。数据准备工具只处理
其公开 Manifest 和内容哈希，不应把 Case 正文暴露给 Proposal Generator。

## 冻结前质量门

每个 DatasetVersion 必须：

- 使用单一、明确的 split；
- 固定 Case、fixture、oracle/grader、provenance 和内容哈希；
- 完全离线运行，不依赖网络、当前时间或外部随机状态；
- 在固定环境连续复验，结果保持一致；
- 保存许可证、来源 revision 和污染风险；
- 发布后不可原地修改，只能创建新版本。

跨 split 审计分成两层：

- Case ID、patch family 和 independence group 不得跨任何 protected split；
- repository 和 fork lineage 不得跨越暴露域：
  `train/validation_search/regression_dev` 为 adaptive，
  `validation_confirm/locked_test` 为 holdout。

开发域内的三个 split 都可能影响 Skill，因此允许共享仓库但禁止共享缺陷家族。候选进入
confirmation 前必须冻结内容哈希，confirmation 后不得再修改；因此 confirm 与 locked 可以
共享 holdout 仓库，但必须是不同缺陷家族。任何仓库或 fork 从开发域进入留出域都会被拒绝。

当前 12 Case 计划将 `more-itertools` 全部放在 adaptive 域，将 `cachetools` 全部放在 holdout
域，并为 locked_test 分配 4 个 Case。执行前必须运行：

```bash
agentskill-eval benchmark audit-split-plan \
  examples/benchmark-sources/real-bug-fix-split-plan.yaml
```

## 阶段依赖

```text
阶段 3
  train + regression_dev + validation_search
  → 冻结 winner hash
                  ↓
阶段 4A（当前可并行）
  Fake evidence 验证 Promotion Core
                  ↓
阶段 4B
  validation_confirm → locked_test → approve/reject
  → 发布不可变 SkillVersion v2
                  ↓
阶段 5
  使用已发布 v2 做完整真实配对实验
```

## 当前边界

公开 locked Case 带有高预训练污染风险，只能支持本项目的一次性终评工作流声明。历史 Stage 3
smoke 曾把 `cachetools` Case 用作 train；这些运行保留为历史执行证据，但不符合 v1alpha2 暴露
域计划，不能进入真实 Promotion。新的 `train`、confirmation 和 locked DatasetVersion 必须从
通过审计的计划重新生成、复验和人工审核，不能把 synthetic demo 或旧运行重新标记为合规证据。
