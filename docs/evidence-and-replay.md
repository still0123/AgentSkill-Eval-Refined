# 执行证据、安全扫描与审计包

## 证据问题

P0 不把“配置中声明了 Skill”当成“Skill 生效”。每个物理 Attempt 分别回答四类可验证问题：实际执行输入是什么、编译工作区是否安装或排除了 Skill、待持久化内容是否泄漏 Secret、报告能否由独立审查者重新分析。

## 输入冻结

`LocalExperimentPlanner.persist` 在外部 Runner 启动前冻结：

- 每个 Case 的 source Eval、fixture 与 grader 文件树；
- 每个 treatment Variant 的 Skill 文件树。

冻结目录为 `inputs/{input_kind}/{owner_id}/files/`，配套 `manifest.json` 保存逐文件 SHA-256 与树哈希。输入不得包含符号链接。Executor 根据 Case 在原 source 中的相对路径定位冻结 Case，并将冻结 Skill 路径传给 Adapter。

## Skill 激活证据

`skill-activation.json` 区分以下字段：

- `skill_expected`：实验协议是否要求本臂加载 Skill；
- `installed`：编译产物中是否观测到精确配置与文件；
- `baseline_clean`：baseline 是否显式为空且不存在 selected Skill；
- `discovered/read/activated/followed`：只有上游直接暴露相应事件时才能填写；
- `unavailable_reasons`：说明字段为什么无法观测。

真实 `skill-up` Adapter 记录编译 `eval.yaml` 和安装 Skill 树的 SHA-256。通用 Engine 仍无法证明
Agent 读取或遵循了 Skill，因此这些行为字段保持空值。qwen Process Agent 是显式例外：它在
SessionResult 中返回 `skill_context_loaded` 与内容 SHA-256，RealEvidenceRunner 在正式报告前
逐 Run fail-closed 校验；是否遵循 Skill 仍由独立 grader 和配对结果判断。

## Secret 扫描边界

Runner 可访问的 Secret 只通过 `secret_env` 传入。输出归档采用“先读完、后扫描、再整批写入”：

1. 验证产物路径、普通文件属性、大小和 Runner 观测哈希；
2. 在内存中组合产物、stdout 与 stderr；
3. 对每个已配置 Secret 执行精确 UTF-8 字节匹配；
4. 任一命中即删除本 Attempt 的临时 Runner 目录，把 Run 标记为 infra invalid，整个污染批次不写 Blob 或 raw-runner；
5. `security-scan.json` 只保存扫描器版本、计数、命中的变量名与 `clean/blocked/not_run` 状态。

该扫描器是最小、可解释的 P0 防线，不替代通用凭据识别、编码/分片泄漏检测或平台级 DLP。空 Secret 配置被视为不安全并阻断。

## 确定性审计与再分析包

创建：

```bash
agentskill-eval experiment bundle WORKSPACE EXPERIMENT_UUID evidence.tar
```

包包含 `experiments/{id}/` 下的不可变 Manifest、冻结输入、Attempt Trace/Diagnosis 与其他证据、Runner 原始产物和静态报告。成员按路径排序，统一 uid/gid、权限和 mtime，采用未压缩 PAX tar；相同实验状态产生相同字节。根目录的 `bundle-manifest.json` 固定成员集合、大小和 SHA-256。

验证：

```bash
agentskill-eval experiment verify-bundle evidence.tar
```

校验器拒绝重复/绝对/穿越路径、非普通文件、成员集合变化、大小变化和哈希变化。`index.sqlite*`、`run.lock` 与临时文件被排除，因为它们分别是可重建查询缓存、进程协调状态和未提交状态。

安全解包后，把 `experiments/{id}` 放回空 workspace，即可通过 `storage rebuild-index` 重建查询索引并重新生成统计报告。包不包含外部 Provider 的服务端采样状态、缓存或完整网络交换，所以这里的 replay 明确定义为“审计与再分析重放”，不是对外部模型响应的逐 Token 再现。
