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

跨 split 审计按以下身份拒绝交叉：

- Case ID；
- repository；
- fork lineage；
- patch family；
- independence group。

这比只比较 Case ID 更严格：同一缺陷的改写、fork 或同仓库相邻补丁也不能被拆到搜索集和
确认集。

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

本阶段完成的是隔离机制和 Fake 数据测试。真实 `train`、`validation_confirm`、
`locked_test` DatasetVersion 必须由阶段 3 数据流水线生成、复验和人工审核后才能冻结；不能
把当前 synthetic demo 数据重新标记为真实证据。
