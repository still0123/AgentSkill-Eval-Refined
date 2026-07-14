# Stage 4C：Skill Evolution Timeline Dashboard MVP

## 目标与边界

Skill Evolution 页面把已经冻结的 Proposal、Search、Regression、Final Evaluation、Human
Review、Promotion 与 SkillVersion 证据聚合成一条只读时间线：

```text
Failure → Proposal → Search → Regression → Confirm → Locked → Review → Published
```

页面不会生成候选、运行 Agent、访问 Provider、批准版本或修改 Manifest。所有文件只在当前浏览器
标签页内读取；关闭或点击“清除本地数据”后不会持久化。

## 支持的数据入口

页面支持同时选择多个文件，也支持通过“导入 Release 目录”选择本地
`evolution-release/`：

- `evolution-report.json`：`ase/evolution-evidence-report/v1alpha1`；
- `release-manifest.json`：`ase/evolution-evidence-release/v1alpha1`；
- `evidence-index.json`：`ase/evolution-evidence-index/v1alpha1`；
- `proposal-report.json` / Proposal Manifest：`ase/real-llm-proposal-*/v1alpha1`；
- `search-report.json`、Promotion Release、SkillVersion Manifest；
- `skill-diff.patch` 或 `.diff`。
- `release-manifest.sha256`：发布 Manifest 的 64 位小写 SHA-256 sidecar。

目录中的 HTML、tar、README 和哈希 sidecar 不会被执行。Patch 只以 Vue 文本插值写入 `<pre>`，
不使用 `v-html`，不会加载外部脚本。

## 本地运行

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm run dev
```

打开 `http://localhost:5173/`，点击“加载 Synthetic Demo”可查看完整 Fake 发布链。也可以选择
真实的本地 Evidence Release 目录。生产验收命令：

```bash
pnpm run typecheck
pnpm run lint
pnpm test
pnpm run build
```

## 页面字段

- Overview：v1/v2 版本与哈希、父版本、Provider/model、费用、证据类别和 claim limit；
- Timeline：八个固定阶段的状态、摘要和 evidence role；
- Proposal Candidates：failure label、修改理由、instruction、risk、failure lineage 和 winner；
- v1/v2 Comparison：W/T/L、Pass Rate、Token、Latency、Cost 与回归 Case；
- Skill Diff：可折叠的转义纯文本 Patch；
- Evidence：输入指纹、DatasetVersion、Runner、人工审核、Artifact 路径与哈希，以及 unavailable capability。

数值缺失显示 `Unavailable`，不会变成 `0`。Artifact 路径只是审计引用，不会自动读取或打开。

## 状态语义

Read Model 只使用六种状态：

| 状态 | 语义 |
|---|---|
| `NOT_STARTED` | 没有证据表明阶段已经启动 |
| `RUNNING` | 导入证据明确声明仍在运行 |
| `PASSED` | 导入证据明确满足该阶段门槛 |
| `FAILED` | 执行完成但失败或发布证据矛盾 |
| `REJECTED` | Regression、Confirm 或人工审核明确拒绝 |
| `UNAVAILABLE` | 上游明确声明 capability/evidence unavailable |

缺少字段不会显示为通过。即使存在 Release Manifest，只有 Regression、Confirm、Locked 和 Human
Review 全部通过时，Published 才能显示 `PASSED`。

## 真实与模拟证据边界

- Proposal 生成成功不代表 Skill 已改进；
- Search winner 只是 adaptive set 上的候选，不等于发布的 Skill v2；
- locked 未执行时不允许显示 Published；
- `simulated=true` 始终显示警示，不能包装成真实证据；
- 真实 Provider 调用若输入是 `simulated_fixture`，页面单独标记“real call / simulated input”；
- 公开 locked set 应由报告声明 `high-contamination`；
- 小样本结论通过 `claim_limit` 保持 descriptive evidence 边界。

仓库中的 `real-llm-proposal-smoke.json` 是 Stage 1 脱敏展示：它只证明一次真实 Provider proposal
链路，输入为 simulated fixture，未执行 Search 或 locked test，不支持 Skill improvement claim。

## Fake fixture 覆盖

默认完整 fixture 来自 Stage 4B / Evidence Release 的字段结构。自动化测试在同一 fixture 上覆盖：

1. 完整通过并发布；
2. regression rejected；
3. confirmation rejected；
4. locked not started；
5. human review rejected；
6. simulated / real provider input 边界；
7. 字段缺失与 capability unavailable；
8. Candidate、review 和 Patch 中的 HTML 注入字符串。

这些 fixture 均为合成数据，不伪造 DeepSeek 实验结果。

## 当前限制

- 不验证 release 目录的哈希完整性；完整性真值仍由 `agentskill-eval evolution release verify` 提供；
- 不实现 ZIP/tar 解包、远程 URL、后端上传或数据库；
- 不做跨 release 历史趋势、写操作、批准、回滚或实验调度；
- Provider、Runner、DatasetVersion 等字段若旧版 Evidence Report 未提供，只显示 Unavailable。
