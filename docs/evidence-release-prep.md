# Stage 5 Evidence Release Prep

## 范围

本阶段只实现发布准备层，输入必须是已经冻结的 Fake/fixture 证据。模块不会调用
Agent、Provider、Runner、Evaluator，也不会执行真实 confirmation 或 locked test。

入口模块为：

```text
agentskill_eval_skill_optimizer.release_evidence
```

它接收：

- v1 SkillVersion Manifest；
- v2 SkillVersion Manifest；
- 实验报告；
- 带预期 SHA-256 的审计附件；
- 明确的 `expected_simulated` 与 `claim_limit`。

## 发布前安全门

所有输入必须位于同一个 `evidence_root`，且必须是普通文件。符号链接、根目录外文件、
绝对 bundle 路径、`..`、反斜杠和重复目标路径都会被拒绝。

发布开始前统一检查：

1. v1/v2 Skill 名称一致；
2. v2 的 `parent_content_sha256` 等于 v1 的 `content_sha256`；
3. v1/v2 内容哈希不同且格式合法；
4. 报告和 Manifest 只能包含一种证据类别；
5. `expected_simulated` 与输入证据一致；
6. 每个附件的实际 SHA-256 等于声明值；
7. JSON 字段和值中不存在常见凭据、Bearer Token、`sk-` Key、GitHub Token 或私钥；
8. 本地 evidence root 和 HOME 路径在发布报告中替换为占位符。

真实与模拟证据混合时整个发布失败，不能拆出一个聚合成功率。当前 Stage 5 Prep 只应传入
`expected_simulated=True`。

## 不可变发布目录

成功后生成：

```text
RELEASE_ROOT/
└── releases/RELEASE_ID/
    ├── release-manifest.json
    ├── release-manifest.sha256
    ├── reports/experiment-report.json
    ├── skill-versions/v1-manifest.json
    ├── skill-versions/v2-manifest.json
    ├── comparison/v1-v2.md
    └── audit/
        ├── artifact-manifest.json
        └── artifacts/...
```

内容先写入同目录临时目录，完成 fsync 后再以目录 rename 暴露。相同 `release_id` 已存在时
拒绝覆盖，也不提供更新操作；新证据必须使用新的 Release ID。

`release-manifest.json` 记录除自身与 sidecar 之外所有成员的路径、大小和 SHA-256。
`verify()` 会重新校验 sidecar、成员集合、普通文件属性、大小和哈希，任何额外、缺失或
被篡改文件都会导致失败。

## v1/v2 对比模板

`comparison/v1-v2.md` 只展示冻结谱系：Skill 名称、版本、内容哈希、父哈希、证据类别和
claim limit。它链接同一发布目录中的脱敏实验报告，但不会根据 Fake 数据推断真实 Agent
性能，也不会把 simulated gain 写成产品结论。

## Stage 4b/5 后续接线

Stage 4b 可以在 Fake promotion 发布完成后，将它产生的 v1/v2 Manifest 与 fixture report
传给本模块。阶段 3 的真实 winner 完成前，不得把真实候选、真实 confirmation 或 locked
test 接入该发布目录。

未来真实 Stage 5 必须使用独立 Release ID、`expected_simulated=False`，并在上游先完成
Secret 扫描、真实/模拟隔离、预算审计和人工批准。本模块不会替代这些上游安全门。
