# 配对统计与静态报告

## 分析入口

统计器只读取 Manifest 真值和选中 Attempt 的 `RunMeasurement`，不从 HTML、stdout 或文件名推断结果。报告要求目标 control/treatment 的所有 Run 已进入终态；发现未终态 Run、缺失 PairBlock 或同一 PairBlock/Variant 重复 Run 时直接拒绝生成，避免把进行中的实验包装成最终结论。

```bash
agentskill-eval report generate WORKSPACE EXPERIMENT_UUID \
  --control CONTROL_VARIANT_UUID \
  --treatment TREATMENT_VARIANT_UUID \
  --bootstrap-resamples 10000 \
  --bootstrap-seed 2026 \
  --majority-threshold 0.5 \
  --min-independent-groups 2
```

输出位于：

```text
workspace/experiments/{experiment_id}/reports/
├── report.json
└── report.html
```

JSON 是机器可读派生结果，HTML 是无脚本离线视图；两者均可重新生成，不替代底层 Manifest 和原始 Runner 证据。

## RunMeasurement

每个选中 Attempt 保存不可变 `RunMeasurement`：

- Runner status 与 exit reason；
- 进程退出码；
- duration、turns；
- input/output/cached Token；
- 可观察的 tool-call 数；
- 可选 `cost_microusd`。

不可观察值保存 `null`，不会伪造为 0。`cost_microusd` 使用整数微美元，避免浮点货币作为持久化真值。Measurement 在终态 Run 指针发布前落盘，并与 Attempt ID 绑定。

## 主口径：assignment-based effectiveness

对 Case `c`、Variant `v` 和 repeat `r`：

```text
y_cvr = 1  当且仅当 evaluation_outcome = pass
y_cvr = 0  当 outcome = fail / invalid / cancelled
p_cv  = mean_r(y_cvr)
```

先在 Case 内聚合 repeats，再在 independence group 内等权平均 Case，最后对 group 等权平均。大型 repository 中较多的 Case 不会压过其他独立 group。

```text
p_gv = mean_case(p_cv)
PassRate_v = mean_group(p_gv)
AbsoluteGain = PassRate_treatment - PassRate_control
```

control pass rate 为 0 时，relative gain 报告 `N/A`，不加 epsilon。

## 敏感性口径：capability estimate

只有 control 与 treatment 都得到有效 `pass/fail` 的 PairBlock 才进入 capability estimate。任何一臂 invalid/cancelled，该 block 从敏感性口径排除。报告同时展示：

- complete block ratio；
- valid paired block ratio；
- 每个 Variant 的 pass、fail 和 invalid 数；
- 主口径与 capability 口径的差异。

敏感性结果不能替代保守主结果；它只帮助判断 observed invalid 是否掩盖了任务能力。

## W/T/L

W/T/L 只用于 Case 级可解释展示。每个 Case 先聚合 repeats，再用预注册 `majority_threshold` 二值化：

- Win：control 未达到阈值，treatment 达到；
- Tie+：两臂都达到；
- Tie−：两臂都未达到；
- Loss：control 达到，treatment 未达到。

默认阈值为 `0.5`，因此恰好 50% 视为达到；报告会明确保存阈值，不能在看到结果后修改。

## 层级 bootstrap

置信区间采用固定 seed 的两级 cluster bootstrap：

1. 有放回抽取 independence group；
2. 在每个被抽 group 内有放回抽取 Case；
3. 保持 Case 内 repeats 的既有聚合；
4. 对每个 resample 重算等 group 权重点估计。

默认 10,000 次，使用百分位 95% CI。报告冻结 resample 数、seed、最小独立组门槛和 weighting。实际独立组少于 `min_independent_groups` 时仍可显示描述性点估计与探索性区间，但标记 `inference_ready=false`，不得做确认性声明。

## Token、时延与成本

效率指标同样先把 repeat 聚合到 Case，再按 group 等权计算 control/treatment mean，不能退回简单的全 Run 平均。报告提供：

- control/treatment group-weighted mean；
- relative overhead；
- Case 级 paired median delta；
- overhead 和 median delta 的层级 bootstrap 95% CI；
- 有值的 Run 数和完整观测 pair 数；
- 总成本与 cost per successful Run。

任一分母为 0 或观测缺失时返回 `N/A/null`。只有所有已分配 Run 都有成本观测时才计算 Variant 总成本和 cost per success，避免用部分账单冒充总成本。

## HTML 安全

HTML 将 Experiment 名称、Variant 名称、independence group 和所有派生文本视为不可信输入：

- 使用 HTML entity 严格转义；
- 不包含 JavaScript；
- 不加载外部字体、图片或样式；
- CSP 为 `default-src 'none'; style-src 'unsafe-inline'`；
- 原始 Agent 输出不嵌入主页面。

机器证据保存在相邻 `report.json`。测试包含恶意 `<script>`、`<img onerror>` 和 SVG payload，确保报告不能把数据解释成可执行 DOM。

报告末尾的 Evidence 表使用受控相对路径链接到每个选中 Attempt 的 Artifact Manifest；若该 Runner 保存了 `raw-runner/result.json`，同时提供原始结果链接。报告不复制或解释原始 Agent HTML，审阅者可以从汇总结论回溯到哈希证据。
