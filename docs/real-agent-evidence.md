# Real Agent Evaluation Evidence MVP

本模块用现有配对实验、`SkillUpRunnerAdapter`、Trace Intelligence 和审计包能力评测一个
真实 Agent。它不引入新的 Provider 编排框架，也不会把模拟结果包装成真实证据。

## 支持范围

MVP 仅支持一个经固定可执行文件接入 `skill-up v0.5.0` 的 Agent Engine。Agent 可以是
Codex/skill-up 当前支持的 Engine，或 OpenAI-compatible Process Agent；Provider 差异仅存在于
配置和 Agent 可执行文件内，实验编排层只依赖统一 Runner Adapter。

配置冻结 Runner 与 Agent 的名称、版本、路径和 SHA-256，以及 Provider、model、temperature、
seed、max turns、timeout、工具能力、Skill/DatasetVersion 哈希、价格表和环境指纹。Runner 不提供
的 request id、区域或镜像 digest 会明确记为 `capability unavailable`。

已验证的首个真实组合是 `skill-up v0.5.0 + Qwen Code 0.19.9 + DeepSeek V4 Pro`。Qwen Code
作为 OpenAI-compatible Process Agent 接入；实验中的证据 Provider 仍记录为 `deepseek`，而
`engine_provider: openai` 只描述线协议，二者不会混淆。

## 安装、Secret 与真实 Benchmark

```bash
python -m pip install -e ".[dev]"
export OPENAI_API_KEY='从安全凭据系统取得的值'
sha256sum /absolute/path/to/skill-up /absolute/path/to/agent
/absolute/path/to/skill-up --version
/absolute/path/to/agent --version
```

Secret 名称写入配置，值只能来自环境变量。平台只将白名单 Secret 和 Runner 管理的最小环境传给
子进程；Manifest、Trace、日志和报告只保存变量名。输出在持久化前进行精确 Secret 扫描。

macOS 可将 DeepSeek Key 保存在 Keychain，运行时临时映射到 Qwen Code 读取的变量：

```bash
OPENAI_API_KEY="$(security find-generic-password \
  -a "$USER" -s agentskill-eval-deepseek -w)" \
  agentskill-eval real preflight CONFIG
```

不要将 Key 写入 YAML。`home_config_files` 只允许在每个 Run 的隔离 HOME 中生成非 Secret JSON
配置；路径逃逸和 Secret 值会被拒绝，文件权限固定为 `0600`，其内容哈希进入冻结输入。DeepSeek
配置使用 `base_url: https://api.deepseek.com`，并在 `.qwen/settings.json` 中设置
`generationConfig.reasoning: false`，使 V4 明确发送 `thinking: {type: disabled}`，避免持久化隐藏推理。
Qwen smoke 还应禁用不必要的 `agent` 子 Agent，并设置 `maxWallTimeSeconds`、`maxToolCalls`、
`sessionTokenLimit` 和循环检测；这些限制属于冻结 Agent 配置，不能在双臂间变化。
首轮真实 smoke 显示 12 turns 会让较难 Bug Fix Case 的双臂同时 invalid，因此当前示例冻结为
24 turns、600k 会话 Token，同时继续保留 240 秒墙钟和 24 次工具调用硬门。

真实数据集必须由 Automatic Benchmark Generation 发布：fixture 来自修复前 commit，oracle 在
修复前失败、修复后通过，包含许可证和 provenance，离线可复现且版本不可变。当前 smoke 使用两个
`more-itertools` Git 历史候选。`python-bug-fix-v1` 是不含 Case ID、补丁或答案的通用 Skill，
preflight 会验证 metadata 哈希并执行 leakage lint。

## 配置与 Preflight

复制 `examples/real-agent-evidence/observed-agent.example.yaml`，替换绝对路径、哈希、版本、
Provider、线协议 Provider、model、base URL、无 Secret HOME 配置和价格。真实配置必须为
`evidence_class: observed_agent`、`simulated: false`。

```bash
agentskill-eval real preflight /absolute/path/to/observed-agent.yaml
```

Preflight 不调用 Agent，但会验证 Case、Skill、Secret、可执行文件哈希和版本，并输出 smoke/evidence
Run 数和单 Run 估算 Token/费用。

## 预算安全门与运行

```bash
agentskill-eval real smoke CONFIG \
  --workspace .agentskill-eval/real \
  --confirm-real-run --max-cost-microusd 100000 --max-agent-runs 4

agentskill-eval real run CONFIG \
  --workspace .agentskill-eval/real \
  --confirm-real-run --max-cost-microusd 300000 --max-agent-runs 12
```

命令在第一次调用前打印 Provider、model、Run 数、最大预算和估算 Token/费用。缺少参数、估算超出
授权、哈希/版本漂移或 Secret 缺失都会失败。达到预算后不再创建新 Run；一次已在飞行中的请求可能
使实测费用最多超出一个 Run，所以还须在 Agent 侧设置单次 Token 上限。真实失败不回退 Mock 或
simulation。已完成实验幂等读取，不再次收费；未完成的付费实验禁止自动恢复。

对于支持缓存计费的 Provider，价格表必须分别记录 cache miss/cache hit 单价及预计命中 Token。
Qwen Code 的 Runner 适配器会汇总隔离 HOME 下本 Run 的本地 usage 记录，包括主 Agent 与子 Agent，
而不能只信任 skill-up 主会话中的 Token。用户中断会递归终止嵌套进程组，并将 Run/Experiment 标记为
`CANCELLED`；取消的付费实验仍禁止自动恢复。

`smoke` 使用 2 Case × 2 臂 × 1 次，共 4 Run，只验证真实链路。`run` 使用 2 Case × 2 臂 ×
3 次，共 12 Run，PairBlock 顺序按冻结 seed 随机化。唯一实验变量是是否加载 Skill。由于只有两个
Case 且来自同一仓库，报告只能作为 descriptive evidence，不能声称普遍提升。

## 证据边界、报告与审计

逐 Attempt 保存 FrozenInputManifest、baseline cleanliness/SkillActivationEvidence、脱敏 Runner
输出、最终消息哈希、Trace、工具/命令/测试/文件事件、ArtifactManifest、环境指纹、Token、时延、
费用、pass/fail/invalid 和 FailureDiagnosis；不保存模型隐藏思维过程。

真实结构强制包含 `simulated=false`、`evidence_class=observed_agent`、Provider、model、
`real_run_confirmed=true` 和 `claim_limit`。CI Fake Process 强制标为 `simulated=true` 和
`process_integration`，真实 CLI 拒绝执行。报告拒绝混合 real/simulated、Provider 或 model；缺失
事件记为 unavailable，不能推断为“没有发生”。

成功实验生成 `real-experiment-report.json`、离线 `real-experiment-report.html` 和 replay/audit
tar。报告展示 DatasetVersion、Agent/model/Runner、Skill hash、双臂通过率、增益、W/T/L、invalid、
Token、时延、费用、cost per success、Case 配对结果、Trace/诊断链接、unavailable 和 claim limit。
HTML 采用严格 CSP、转义动态内容且不执行外部脚本。

```bash
agentskill-eval real status WORKSPACE EXPERIMENT_ID
agentskill-eval real report WORKSPACE EXPERIMENT_ID
agentskill-eval experiment verify-bundle WORKSPACE/real-evidence-bundles/EXPERIMENT_ID.tar
```

## 故障排查

- `hash/version mismatch`：确认安装来源，不要仅为通过检查而修改期望值。
- `Secret ... missing`：只在当前 shell 导出变量，不写入 YAML。
- `estimated cost ... exceeds authorization`：缩小协议或人工确认后提高上限。
- `BUDGET_EXHAUSTED`：保留已完成证据但不生成成功聚合报告。
- `capability unavailable`：Runner 没有直接事件，不等于 Agent 没有执行。
- `invalid`：基础设施、超时、取消或 Runner 结果错误，不计为成功。

Fake Process 测试只验证接口、预算、Trace、Secret、报告和幂等语义，不产生费用，也不构成性能证据。

## 首个完整真实 smoke

2026-07-13 使用 Qwen Code 0.19.9、DeepSeek V4 Pro、skill-up 0.5.0 和两个
`more-itertools` Git 历史 Case 完成 4/4 Run，0 invalid。baseline/treatment 均为 100% 通过，
因此该 smoke 只证明真实链路与审计闭环，不证明 Skill 增益；总记录费用为 75,207 microusd，
四次 Secret 扫描均为 clean，replay bundle 校验通过。仓库只提交
`experiments/real-deepseek-v4-pro-smoke-2026-07-13/` 下的脱敏配置与聚合结果，原始日志、缓存、
会话和审计包保持本地且不跟踪。

## 首个 12 Run evidence 实验

同日继续使用相同冻结配置执行 2 Case × 2 arm × 3 repeats。12 个分配 Run 中 9 个有效完成并
通过，3 个因 Runner 返回 `ERROR`/`execution_error` 被分类为 infra invalid，且未自动补跑。
baseline/treatment 通过率为 4/6 与 5/6，绝对差为 +16.7 个百分点；Case 级 W/T/L 为 1/1/0。
配对聚合中 treatment 的 Token、时延和费用分别低约 6.2%、19.7% 和 5.0%，总记录费用为
231,195 microusd，低于 750,000 microusd 授权上限。

上述结果只能作为两个同源 Case 的 descriptive evidence：样本量极小，三个 invalid 会影响比较，
不能据此声称 Skill 存在普遍增益。12 次持久化 Secret 扫描与一次覆盖 12,341 个文件的 Key 精确
扫描均为 0 命中，389 文件 replay bundle 校验通过。公开仓库仅保存
`experiments/real-deepseek-v4-pro-evidence-2026-07-13/` 下的脱敏配置、聚合结果和哈希。
