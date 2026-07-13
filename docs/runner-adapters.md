# Runner 防腐层与 `skill-up v0.5.0` 兼容协议

## 目标

Runner 防腐层把外部 CLI 的配置、进程退出码和 JSON 产物转换成平台稳定契约。平台不导入、不复制 `skill-up` 内部 Go 包，只依赖公开的 `validate`、`run`、`--version`、`result.json` 与产物目录。

当前固定版本为 `skill-up v0.5.0`，源码提交为 `21618623c159b4dbb66e51098dbd669427bb00b8`。Darwin arm64 发布压缩包和解压后二进制的 SHA-256 记录在 `runner_compatibility/skill-up/v0.5.0/compatibility.json`。默认适配器会在运行前校验二进制哈希与版本文本，不满足即拒绝执行。

## 平台稳定契约

`RunnerAdapter` 提供三个异步操作：

- `validate(request)`：验证输入树、固定 Runner 和编译后的上游配置；
- `execute(request, event_sink)`：执行一个 Case 的一个 Variant，并返回 `RunnerResult`；
- `cancel(execution_id)`：终止整个 Runner 进程组。

`RunnerResult` 将任务状态和进程状态分离：

- `status` 保存 `PASS / FAIL / SKIP / ERROR`；
- `exit_reason` 区分正常通过、Case 失败、执行错误、CLI 错误、超时、取消和报告缺失；
- `process_exit_code` 只作为观测值，不能替代 `result.json` 中的 Case 结论；
- 未识别的 JSON 字段保存在 `raw_result`，从而允许上游向后兼容地增加字段。

`MockRunnerAdapter` 用于实验编排的确定性测试，支持预设结果、异步事件和取消。`SkillUpRunnerAdapter` 是生产适配器。

## 单次编译规则

每个物理 Attempt 都编译到独立目录：

```text
run-dir/
├── compiled/
│   ├── SKILL.md
│   ├── evals/
│   │   ├── eval.yaml
│   │   ├── cases/
│   │   └── fixtures/
│   └── skills/selected/       # 仅 treatment 存在
├── runner-home/
└── runner-output/iteration-1/result.json
```

编译器执行以下控制：

1. 拒绝源 Eval、Fixture 和 Skill 中的符号链接与路径逃逸；
2. 在编译根目录创建中性 `SKILL.md`，将上游的 Skill 根目录搜索限制在本次运行内；
3. baseline 显式写入 `skills: []`，treatment 只复制冻结的目标 Skill；
4. 每次只包含一个 Case，设置 `parallelism: 1`；
5. 关闭上游 benchmark 和 retry，由平台负责配对实验与物理重试；
6. 固定 `--iteration 1`，避免同一路径重跑后读取错误的 iteration；
7. 显式下发 `timeout_seconds`、`max_turns` 与产物规则。

编译后的 `eval.yaml` 使用 JSON 文本。JSON 是合法 YAML 1.2，因此无需在平台引入另一套 YAML 序列化依赖。

## 进程与安全边界

Runner 使用独立进程组启动。超时或取消时先发送 `SIGTERM`，宽限两秒后发送 `SIGKILL`，防止 Agent 子进程残留。

每次运行使用独立的 `HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME` 和 `TMPDIR`。凭据只能通过 `RunnerRequest.secret_env` 传入，且该字段不会出现在对象 repr 中；调用者不能覆盖平台管理的 HOME、PATH 与临时目录变量。stdout、stderr 和配置中不得主动写入 Secret。

产物采集只遍历 Runner 输出根目录，拒绝符号链接，并对每个普通文件计算 SHA-256 和大小。后续存储层再依据 Artifact Policy 做敏感信息分类与内容寻址持久化。

## 兼容测试

兼容验证分两层：

- Golden parser 测试始终运行，覆盖未知字段保留、Case 精确匹配、状态映射和产物哈希；
- 真实 CLI 集成测试在发现固定二进制后运行，通过本地 Custom Engine 验证 `validate → run → result.json` 完整链路，不需要模型凭据。

可通过环境变量指定二进制：

```bash
AGENTSKILL_EVAL_SKILL_UP_BIN=/absolute/path/to/skill-up \
  .venv/bin/python -m pytest tests/integration/test_skill_up_runner.py -v
```

升级 Runner 时必须新增版本目录、哈希、Golden Fixture 和集成测试；不得直接覆盖已有兼容记录。
