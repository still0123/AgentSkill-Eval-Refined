# Proposal v3：failure evidence 对齐

本阶段针对 Qwen3 validation_search 的 `NO_WINNER` 结果做零费用诊断，不执行新的真实模型调用。

## 诊断结论

本次搜索中，大多数候选与 Skill v1 持平，两个候选产生回归，没有候选获得正向增益。复查原始 train
证据后发现，Qwen3 的代表性失败不是泛化的“验证不足”，而是 Agent 发出的编辑工具参数与本地
Process Agent 暴露的工具契约不一致：Agent 使用 `file/pattern/replacement`，工具需要
`path/old/new`，并且没有取得成功的工具结果。

原来的 Proposal v2 只把 `VERIFICATION` 标签和规则 ID 传给生成器，缺少可操作的观察摘要，因此
DeepSeek 生成了“记录输出”“重新运行测试”等泛化候选，无法针对真实失败行为改变 Agent。

## v3 修改

- 复核标签改为 `TOOL_ARGUMENT`；
- Proposal 请求加入脱敏 `observed_summary`，只包含失败行为摘要，不包含 Case ID、仓库名、补丁或隐藏推理；
- 生成器提示要求候选直接解决观察到的 Agent 行为，避免泛化日志建议；
- 摘要不会写入持久化优化上下文，且会移除疑似 Secret 和宿主机路径；
- `failure_label` 字段名称统一，兼容旧的本地 Fake Process Generator；
- 更新 Proposal v3 配置：
  `examples/optimizer/failure-guided/qwen3-cachetools-real-proposal-v3.example.yaml`。

## 当前边界

Proposal v3 尚未调用 DeepSeek。后续需先运行本地 preflight，确认输入只有 base Skill 和 train 摘要；
获得用户一次小额授权后才执行单次 Proposal smoke。Proposal 生成后仍不能直接声称 Skill 改进，必须
重新执行 `validation_search`，再由独立 `regression_dev` 验证。
