# P0 本地存储与恢复协议

## 真值边界

P0 的真值源是 `workspace/experiments/` 下的 Manifest 与 `workspace/objects/` 下的内容寻址对象。`index.sqlite` 只用于查询；删除数据库及其 WAL/SHM 后，可以从 Manifest 完整重建。

```text
workspace/
├── experiments/{experiment_id}/
│   ├── experiment.json
│   ├── variants/{variant_id}.json
│   ├── inputs/{case_source|skill}/{owner_id}/
│   │   ├── manifest.json
│   │   └── files/...
│   ├── pair-blocks/{block_id}.json
│   ├── runs/{run_id}/
│   │   ├── run.json
│   │   ├── run.lock
│   │   └── attempts/{attempt_no}/
│   │       ├── attempt.json
│   │       ├── measurement.json
│   │       ├── skill-activation.json
│   │       ├── security-scan.json
│   │       ├── trace.json
│   │       ├── failure-diagnosis.json
│   │       ├── raw-runner/
│   │       └── artifacts/manifest.json
│   ├── reports/report.json
│   ├── reports/report.html
│   └── index.sqlite
├── objects/sha256/{prefix}/{digest}
└── quarantine/
```

## Manifest 完整性信封

每个 JSON Manifest 使用 `ase/storage/v1` 信封：

```json
{
  "storage_schema_version": "ase/storage/v1",
  "model_name": "ExperimentVariant",
  "payload_sha256": "...",
  "semantic_sha256": "...",
  "payload": {}
}
```

`payload_sha256` 对规范化 JSON 计算；`semantic_sha256` 保存 Variant、PairBlock、Run 等领域对象的语义指纹。读取时依次验证信封 Schema、payload hash、Pydantic payload 和语义 hash，任一失败都视为损坏。

## 原子提交

写入顺序为：

1. 在目标目录创建 `.tmp-{uuid}.{target_name}`；
2. 写入完整内容并 `fsync` 临时文件；
3. 使用同文件系统 `os.replace` 原子替换目标；
4. `fsync` 父目录，持久化目录项变化。

Attempt 提交额外遵循：

1. 验证 Run ID、终态和 lease generation；
2. 将 `attempt.json` 推进到终态，终态后禁止修改；
3. 写入与 Attempt ID 绑定的不可变 `measurement.json`；
4. 写入不可变 `skill-activation.json` 与 `security-scan.json`；
5. 写入不可变 `trace.json` 与 `failure-diagnosis.json`；
6. 写入不可变 Artifact Manifest；
7. 最后更新 `run.json` 的 active Attempt 和 selected Attempt hash。

因此，第七步前崩溃只会留下可审计的物理 Attempt 和证据，不会让逻辑 Run 指向半写入结果。`reports/` 是可重建派生视图，不属于提交真值链。

## 冻结执行输入

计划持久化先复制每个 Case source 和 treatment Skill，再创建可执行 Run。输入树拒绝根目录或内部符号链接；每个普通文件写入内容寻址对象库，并由 `FrozenInputManifest` 保存规范相对路径、大小、媒体类型、SHA-256 和树哈希。Executor 只把冻结路径交给 Runner，因此实验创建后修改原始 Dataset 或 Skill 不会悄悄改变执行内容。

## 启动恢复

`agentskill-eval storage recover WORKSPACE` 执行以下操作：

- 临时 Manifest 完整且目标不存在：原子晋升；
- 临时 Manifest 与现有目标完全相同：删除临时副本；
- 临时 Manifest 与目标冲突：隔离临时副本；
- 目标损坏但临时 Manifest 完整：隔离目标并晋升临时副本；
- Manifest hash、Schema 或领域契约损坏：移动至 `quarantine/`；
- 扫描非终态 Run，返回可恢复的 Run ID；
- 从剩余有效 Manifest 重建各实验 SQLite 索引。

恢复不会自动把非终态 Run 判定为成功或失败；后续 Worker 根据预注册重试策略决定继续、重试或标记基础设施失败。

## 本地并发

P0 对 `runs/{run_id}/run.lock` 使用非阻塞 POSIX advisory lock。锁只负责避免同一台机器上的重复领取；跨主机租约、fencing token 条件提交和 Redis Streams 属于 P1。

## 已知边界

- P0 依赖 POSIX `flock` 与同文件系统原子替换，目标运行环境是 macOS/Linux。
- SQLite 不是审计真值源，不允许只备份数据库而丢弃 Manifest。
- 内容寻址 Blob 在读取时验证 hash 和大小；崩溃留下的未完成 Blob 临时文件会隔离，调用方可重新生成。
- P0 本地锁不能阻止已经失联的外部 Provider 请求继续产生费用；物理 Attempt 成本必须独立记录。
