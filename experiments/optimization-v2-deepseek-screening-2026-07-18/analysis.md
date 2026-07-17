# Screening 后离线分析

## 观察

`verify-edit-result-and-retry` 在 `cachetools-cachedmethod-autospec` 上失败。Runner 的确定性脚本报告：修复前后的目标测试均被执行，但 treatment 仍因 `_DescriptorBase.__get__` 的 `obj=None` 分支触发 `TypeError` 而失败。Agent 最终响应声称“所有 46 个测试通过”，与验证结果不一致。

因此当前可审计的失败类别是：

```text
VERIFICATION / agent_claim_not_supported_by_observed_test
```

不能进一步断言是某一个工具参数或编辑动作造成了退化，因为本轮 Trace 明确标记 `tool_file_command` 不可用。

## 对候选的判断

- `inspect-tool-schema-before-edit`：在两个 Case 上与 v1 持平；没有增益证据。
- `map-edit-operation-to-tool-args`：在两个 Case 上与 v1 持平；没有增益证据。
- `verify-edit-result-and-retry`：在一个 Case 上退化；不能进入后续确认。

## 下一轮 proposal 的输入约束

下一轮候选应优先补足“结果声明必须由观察到的测试证据支持”的流程，而不是继续强化泛化的工具重试规则。候选应明确：

1. 编辑后必须执行目标回归测试；
2. 只有读取到非零测试结果时才可声明通过；
3. Agent 最终响应中的成功声明不能覆盖失败的确定性 grader；
4. 失败时优先回到根因分析，不重复同一修改；
5. 不能引入测试名称、补丁内容或 Case 特化。

这只是下一轮 proposal 的离线设计依据，不代表候选已经生成，也不代表 Skill 已改进。
