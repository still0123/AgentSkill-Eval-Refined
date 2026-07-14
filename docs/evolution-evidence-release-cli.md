# Stage 5A.2：Evidence Release CLI Integration

## 目标与边界

Stage 5A.2 将已经完成的 Fake Promotion 证据整理为可离线查看、可重复验证的发布目录。它只读取
冻结文件，不启动 Agent、Generator、Evaluator 或 Provider，也不上传 GitHub Release。

```text
Fake handoff
→ validation_confirm
→ locked_test
→ approved fixture SkillVersion
→ evolution release prepare
→ verify / inspect
```

当前入口强制 `simulated=true`、`evidence_class=simulated`。产物只能证明 Stage 3～5 控制链路和
审计边界可用，不能作为真实 Agent 性能或真实 Skill v2 发布证据。

## 配置

配置文件使用 YAML：

```yaml
schema_version: ase/evolution-evidence-release-config/v1alpha1
evidence_root: /absolute/frozen/workspace
promotion_release_manifest: promotion-workflows/WORKFLOW_ID/release-manifest.json
v1_manifest: release-inputs/v1-manifest.json
v2_manifest: skill-version-promotion/versions/python-review/2.0.0-fixture/manifest.json
confirmation_report: final-evaluations/jobs/CONFIRM_JOB/reports/final-report.json
locked_test_report: final-evaluations/jobs/LOCKED_JOB/reports/final-report.json
human_review: release-inputs/human-review.json
evolution_report: evolution-jobs/EVOLUTION_ID/evolution-report.json
search_report: optimization-jobs/JOB_ID/reports/search-report.json
skill_diff: skill-version-promotion/versions/python-review/2.0.0-fixture/v1-v2.diff
evidence_class: simulated
simulated: true
```

除 `evidence_root` 外的路径都相对该根目录解析。输入必须是根目录内的普通文件，符号链接和路径
穿越会被拒绝。`human_review` 必须与 `PromotionReleaseManifest.human_review` 完全一致；配置不能
覆盖或放宽 Promotion 的 `claim_limit`。

## CLI

准备发布：

```bash
agentskill-eval evolution release prepare CONFIG \
  --workspace .agentskill-eval/release
```

相同配置重复执行不会重写文件，而是验证已有目录并返回 `idempotent_replay=true`。如果已存在目录
属于其他输入，命令失败关闭。

验证和查看：

```bash
agentskill-eval evolution release verify \
  .agentskill-eval/release/evolution-release

agentskill-eval evolution release inspect \
  .agentskill-eval/release/evolution-release
```

`inspect` 会先执行完整验证，不会绕过完整性检查。

## 输出

```text
evolution-release/
├── release-manifest.json
├── release-manifest.sha256
├── evolution-report.json
├── evolution-report.html
├── skill-diff.patch
├── evidence-index.json
├── audit-bundle.tar
└── README.md
```

JSON/HTML 报告包含：

- Skill v1/v2 内容哈希、版本和父版本关系；
- Promotion、proposal、failure 和 evidence artifact 谱系；
- validation_search、regression_dev、validation_confirm、locked_test；
- W/T/L、Token、时延和费用；
- 人工审核决定；
- `claim_limit`、`simulated` 和 `evidence_class`。

HTML 不加载脚本或外部资源，动态内容统一转义，并设置离线 CSP。

## 完整性验证

`verify` 按顺序检查：

1. 发布目录只能包含固定成员，且不能包含符号链接；
2. `release-manifest.sha256` 与 Manifest 内容一致；
3. Manifest 声明的每个文件大小和 SHA-256 一致；
4. `skill-diff.patch` 与 SkillVersion 的 diff hash 一致；
5. Skill v2 `parent_content_sha256` 等于 Skill v1 `content_sha256`；
6. Evidence Index 与 Manifest 使用相同输入指纹；
7. audit tar 只包含九个预期普通文件，不含重复、链接或路径穿越成员；
8. tar 每个成员的大小和哈希与 Evidence Index 一致。

修改 Manifest、报告、diff、README 或 audit tar 后，验证都会失败。即使攻击者重新计算外层文件
哈希，父版本语义校验和 tar 内层成员哈希仍会拦截伪造。

## 不在本阶段实现

- 不调用 DeepSeek 或其他真实模型；
- 不运行真实 Agent、confirmation 或 locked test；
- 不建立新的服务化发布平台；
- 不改造 FastAPI、Redis 或 Vue；
- 不自动创建 GitHub Release。
