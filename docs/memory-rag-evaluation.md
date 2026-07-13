# Memory/RAG Evaluation MVP

## 目标与边界

本纵切用于审计 Agent 的检索、grounding 和 Memory 生命周期，不是通用向量数据库、RAG
聊天系统或生产知识库。机械可判定的指标全部由确定性 grader 计算；MVP 不调用 LLM Judge、
Embedding 服务或付费模型。所有本地 Lab Case、Run、Trace 和 Report 强制 `simulated=true`。

## 两层 RAG 评测

Retrieval 层计算 Recall@K、Precision@K、MRR、nDCG、gold evidence coverage、irrelevant
context ratio、duplicate retrieval、stale/conflicting document count、检索时延和成本。
`MockRetrieverAdapter` 使用 SHA-256 token 投影产生固定 embedding，以 cosine、fixture bias 和
document ID 稳定排序；它不依赖向量数据库。

Generation/Grounding 层使用公开 answer key、claims、citation 和 evidence ID 机械计算：answer
correctness、citation precision/recall、evidence attribution、unsupported claim、context
utilization 和 faithfulness。同时单独标识：

- 已检索 gold evidence 但 answer/claim 未引用或归因；
- 未检索 gold evidence，但最终答案恰好正确。

这两种情况不会被“答案正确”掩盖。当前 answer correctness 是规范化精确匹配；未来可以增加
隔离的语义 Judge 接口，但不得替换可机械验证的 citation/evidence 规则。

## Memory 评测

`MockMemoryAdapter` 按 `(session_id, key)` 隔离，支持 write、read、update、overwrite、delete/
forget 和 deterministic TTL expiration。Case oracle 可逐操作冻结期望状态和值 SHA-256。
Lifecycle grader 识别过期、旧值和更新冲突；Safety grader 识别：

- 跨 Session 读取；
- sensitive/forbidden key 持久化或读取；
- 不应写入内容被持久化；
- poisoning payload 被接受；
- poison 被检测并拒绝。

Lab 内置 `ignore previous`、`system prompt`、`exfiltrate` 和 `override policy` poison fixture。
它们只是确定性安全测试模式，不代表生产级内容安全分类器。

## Trace 与隐私

专项 Trace 支持：

- `retrieval.query/result/filtered/failed`；
- `context.assembled/truncated`；
- `memory.write/read/update/delete/expired/rejected/poison_detected`。

事件包含连续 sequence、attempt ID、带时区时间、adapter identity、文档 ID、Session/Key
SHA-256、Memory 值 SHA-256/长度摘要、状态、时延、成本和错误类别。Trace 不保存查询正文、
document text、生成答案/claim 原文、Memory 原文、Secret 或模型隐藏思维过程。报告只持久化
机械评分、citation/evidence ID 和这些规范化证据。

## Adapter 边界

实现 `MockRetrieverAdapter`、`MockMemoryAdapter`、`ProcessRetrieverAdapter` 和
`ProcessMemoryAdapter`。Process adapter：

- 使用参数数组执行，绝不经过 shell；
- 拒绝 symlink executable，固定 version metadata 和 executable SHA-256；
- 只继承 PATH/LANG/LC_ALL allowlist；
- stdin/stdout 使用单个严格 JSON object；
- 超时终止整个进程组；
- 限制输出字节、JSON 深度和字段数；
- 由 controller 在 trace/report 边界统一脱敏。

当前 Process adapter 是一次请求启动一次进程的扩展边界，未连接生产 Retriever、Memory 或
知识库，也未实现认证、网络访问、批量 Embedding 或长连接。

## Grader 与报告

MVP 提供 `RetrievalGrader`、`GroundingGrader`、`CitationGrader`、
`ContextQualityGrader`、`MemoryLifecycleGrader`、`MemorySafetyGrader` 和
`CompositeMemoryRagGrader`。Composite 对 retrieval/generation Case 和 Memory Case 使用不同
公开权重，阈值为 0.8；malformed process result 标记 invalid。JSON 保存完整分项、证据 sequence
和原始配对维度；HTML 严格转义、无脚本、无外部资源。

## 配对实验

Lab 支持四类固定条件配对：

- `no_rag_vs_with_rag`；
- `no_memory_vs_with_memory`；
- `clean_context_vs_noisy_context`；
- `clean_memory_vs_poisoned_memory`。

除配对变量外，config 固定 Agent、model、task、dataset、seed、timeout、token/cost budget 和
环境；failure injection 对两臂从相同初始状态开始。报告按配对类型展示 success rate、retrieval/
answer/faithfulness/Memory 增益、安全违规变化、W/T/L/invalid、Token、时延和成本变化。

Mock agent plan 是控制器 fixture，不能用其结果声称真实 RAG 或 Memory 改善 Agent。

## CLI Demo

```bash
agentskill-eval memory-rag validate examples/memory-rag/dataset.yaml

agentskill-eval memory-rag lab run examples/memory-rag/lab-config.yaml \
  --workspace /tmp/agentskill-eval-memory-rag \
  --allow-simulation

agentskill-eval memory-rag report /tmp/agentskill-eval-memory-rag EXPERIMENT_ID
agentskill-eval memory-rag trace /tmp/agentskill-eval-memory-rag RUN_ID
```

输出位于 `WORKSPACE/memory-rag/EXPERIMENT_ID/`：`memory-rag-report.json`、
`memory-rag-report.html` 和逐 Run trace。固定输入重复运行时 JSON/HTML 字节一致。

## 当前限制与真实接入

本阶段不包含 FastAPI、Vue、飞书、Java、Redis/Celery、Milvus、自研向量库、生产知识库、
真实付费模型或大规模 Embedding 服务。Mock latency/cost 是 fixture 值，不是生产性能测量。

接入真实 Agent 时应实现 Retriever/Memory adapter，冻结服务与索引 identity、语料 snapshot、
embedding/ranker 版本、Agent/model、seed、预算和网络环境，并将原生事件转换为专项 Trace。
真实报告必须保留直接 evidence reference、Secret 注入与授权审计；在两臂真实证据完整前不得移除
simulated claim limit。
