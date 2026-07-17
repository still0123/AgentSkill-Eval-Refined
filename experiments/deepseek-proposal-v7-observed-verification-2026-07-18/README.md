# DeepSeek Proposal v7：Metadata-aligned Observed Verification

本记录保存一次经授权的 DeepSeek proposal-only 调用。v7 使用带有冻结
provider/model 元数据的 observed train failure bundle，修复了 v6 无法进入优化预检的问题。

## 执行信息

- Provider：`deepseek`
- Model：`deepseek-v4-pro`
- Generator version：`proposal-observed-verification-v7`
- Proposal calls：1
- Candidates：4
- 实际费用：850 microusd
- `simulated`：`false`
- `search_executed`：`false`
- `locked_test_accessed`：`false`
- `hidden_reasoning_stored`：`false`
- `secret_value_stored`：`false`

## 候选方向

1. `enforce-post-edit-test-rerun`：编辑后重新执行确定性回归测试，并依据退出码和输出判断成功。
2. `validate-test-coverage`：确认回归测试覆盖修改路径后再报告结果。
3. `parse-test-output-for-failures`：解析测试输出，任何失败信号都必须进入诊断流程。
4. `invalidate-test-cache`：使用干净状态和新进程执行回归测试，避免缓存造成假阳性。

## 结论边界

本次调用只证明真实 DeepSeek proposal 链路生成了合法候选。候选尚未经过
validation_search 或 regression_dev，不能声称 Skill 已改进，也不能进入 confirmation、
locked test 或 Skill v2 发布。

完整 JSON/HTML 产物保存在本地审计 workspace，不提交原始响应、隐藏推理、Secret 或绝对路径。
