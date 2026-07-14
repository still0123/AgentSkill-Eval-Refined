# Automatic Benchmark Generation MVP

本模块从一个或多个固定的本地 Git 历史重建可执行 Benchmark 候选。它不抓取 GitHub、不调用
LLM，也不根据被测 Agent 的成绩筛选题目。目标是先证明一条可审计、可离线复现的
生成链路。

## 状态与信任边界

```text
INGESTED → RECONSTRUCTED → VERIFIED → DEDUPED → REVIEWED → PUBLISHED
                                                    └──────→ REJECTED
任一自动阶段失败 ─────────────────────────────────────────→ REJECTED
```

每次转换都在 `benchmark-jobs/<job-id>/candidates/<candidate-id>/history/` 写入新的不可变
manifest 快照。`candidate.json` 只是最新状态指针，拒绝原因不会删除。每个转换记录输入
哈希、输出哈希、执行者和时间。只有全部质量门通过且由显式 `benchmark review
--approve` 批准的候选可以发布。

发布结果位于 `dataset-versions/<dataset-version-id>/`。目录已存在时存储层拒绝覆盖；
DatasetVersion 内容哈希覆盖 case、fixture、grader 和 provenance 哈希。
人工审核和发布前会重新计算四套 fixture、两份补丁及全部命令日志的哈希；任何验证后
篡改都会阻断状态转换。

## 重建与验证协议

1. 校验本地仓库 origin、完整 before/after commit 和祖先关系；
2. 用 Git tree/blob API 重建 before/after，拒绝符号链接、submodule、路径逃逸和超限仓库；
3. 将 after commit 中指定的回归测试覆盖到 before fixture；
4. 只从生产代码路径提取参考补丁，且不把补丁放入 Agent fixture；
5. 从 after fixture 反向应用参考补丁，形成 mutation fixture；
6. 从 before fixture 应用独立编写的替代修复，形成 alternative fixture；
7. 在受控环境中对四个版本各重复执行至少三次。

预期结果是 before 稳定失败、after 稳定通过、mutation 稳定失败、alternative 稳定通过。
stdout/stderr 作为证据文件保存，manifest 只引用其 SHA-256。命令不经 shell，Python
解释器固定为当前 Runtime；环境固定 `PYTHONHASHSEED=0`、`TZ=UTC`，且不继承凭据。

## 质量门

- 选中的回归测试不得使用网络、当前时间、sleep 或未固定随机状态；
- task 和选中测试不得包含参考补丁中的非平凡新增实现行；
- SPDX、许可证哈希、仓库 URL、commit、生成器和验证器版本必须完整；
- before/after/mutation/alternative 各至少三次结果一致；
- 使用规范化任务、参考补丁、fork lineage 和显式 provenance family 确定性去重；
- 发布前扫描工作区内已有 DatasetVersion，同一 fork lineage 或 provenance family 不得跨 split；
- DatasetVersion 内容哈希覆盖 case、fixture、grader、provenance 和承载 split/group 的 metadata；
- 输入中不存在 Agent 分数，发布 manifest 记录 `selection_uses_agent_scores=false`；
- 公开历史数据必须记录 contamination risk，不能宣称为无污染测试。

当前 mutation 是“在修复后版本反向删除生产补丁”，因此 mutation score 为 1/1；替代
修复必须以不同实现通过同一 oracle，避免 grader 只接受参考补丁的唯一写法。

## 跨仓库离线真实样本

仓库内保存两个 MIT 许可真实项目的离线 Git bundle，每个仓库包含两个固定历史缺陷：

- `more-itertools.bundle`：iterator 与 strict sampling 回归；
- `cachetools.bundle`：cachedmethod introspection 与 cache-key pickle 回归。

无需网络即可重放：

```bash
git clone examples/benchmark-sources/more-itertools.bundle \
  .agentskill-eval/sources/more-itertools
git -C .agentskill-eval/sources/more-itertools remote set-url origin \
  https://github.com/more-itertools/more-itertools.git
git clone examples/benchmark-sources/cachetools.bundle \
  .agentskill-eval/sources/cachetools
git -C .agentskill-eval/sources/cachetools remote set-url origin \
  https://github.com/tkem/cachetools.git

cp examples/benchmark-sources/cross-repository-generation.example.yaml /tmp/generation.yaml
# 将 sources 中两个 repository_path 改为上面 clone 的绝对路径。

agentskill-eval benchmark generate /tmp/generation.yaml \
  --workspace .agentskill-eval/benchmark
agentskill-eval benchmark status .agentskill-eval/benchmark JOB_UUID
agentskill-eval benchmark review .agentskill-eval/benchmark JOB_UUID CANDIDATE_UUID \
  --reviewer YOUR_NAME --approve --reason "provenance and evidence reviewed"
agentskill-eval benchmark publish .agentskill-eval/benchmark JOB_UUID \
  --publisher YOUR_NAME
```

四个候选都必须单独 review。发布后可继续用原有 loader 校验：

```bash
agentskill-eval dataset validate \
  .agentskill-eval/benchmark/dataset-versions/DATASET_VERSION_UUID
```

仓库内的已验证离线重放结果位于
`experiments/cross-repository-benchmark-2026-07-14/`。其中记录 bundle、source spec、
四个候选和发布 DatasetVersion 的哈希，以及本阶段严格的 claim limit；不包含凭据、原始临时
workspace 或模型调用结果。

## Manifest 布局

```text
workspace/
├── benchmark-jobs/<job-id>/
│   ├── job.json
│   ├── source-spec.json
│   └── candidates/<candidate-id>/
│       ├── candidate.json
│       ├── history/0001.json ...
│       ├── evidence/{before,after,mutation,alternative}/
│       ├── evidence/patches/
│       └── fixtures/{before,after,mutation,alternative}/
└── dataset-versions/<version-id>/
    ├── dataset-version.json
    ├── dataset.yaml
    ├── metadata/
    ├── provenance/
    └── evals/{cases,fixtures}/
```

## 明确不做

MVP 不包含 GitHub 大规模抓取、难度模型、embedding 近似去重、Skill Optimizer、MCP、
Memory/RAG、FastAPI、Redis、Vue 或 Kubernetes。`locked_test` 虽被契约识别，但公开
Git 历史默认污染风险高。最初四 Case 验收记录只发布到 `validation_search`；扩展配置包含
十个独立缺陷家族，并通过 `real-bug-fix-split-plan.yaml` 明确分配到 train、
validation_search、regression_dev 和 validation_confirm。v1alpha1 单仓库配置保持兼容；
新跨仓库配置使用 v1alpha2。
