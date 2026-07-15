# Funcy regression_dev v2 离线发布记录

本记录对应一次不调用模型的确定性数据集构造与发布。

| 项目 | 值 |
|---|---|
| DatasetVersion ID | `7909bad9-171e-59ac-a32b-58a19ee2f271` |
| DatasetVersion content hash | `4f0fb6273b64326ab96d29b1c297b0bfacb1260f9ea850b3f5b048c6c34ac7c3` |
| Release content hash | `079ba1f5f07ae28553d5101ace913e1924c09f3097518421daedbd5d21553ba8` |
| Base Optimization Release hash | `aa0b0ad1a38c8f6580cc0c962140565b5f4cba0db17441999df7e1e9cdf5b7ab` |
| Funcy bundle SHA-256 | `3da2875f00495bb523447ec1ade7bc121e7329ec4bcfc663637ae1a7b6e291fa` |
| status | `AWAITING_OBSERVED_BASELINE_SCREENING` |
| model_calls / agent_runs | `0 / 0` |
| paid_cost | `0 microusd` |

四个固定 Case 均通过：before oracle 稳定失败、after oracle 稳定通过、mutation 稳定失败、
alternative repair 稳定通过。每种结果重复 3 次，共 48 条离线测试命令；没有根据 Agent 成绩
挑选或删除 Case，Funcy 与其他仓库保持独立。

下一阶段是观察 Skill v1 baseline。该发布物本身不能声称 Skill 有提升，也不能访问 confirmation
或 locked test。
