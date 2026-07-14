# AgentSkill-Eval Read-only Dashboard MVP

## 定位

Dashboard 是现有 JSON 评测证据的纯浏览器只读视图。它不会运行实验、调用 Agent、上传报告或修改 Manifest 真值源。本阶段前端完全独立于核心 Python 包，现有契约变化通过 `apps/web/src/parser.ts` 的适配层处理。

## 启动与构建

需要 Node.js 20.19+ 或 22.12+。

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm run dev
```

Vite 默认入口为 `http://localhost:5173/`。生产构建：

```bash
pnpm run typecheck
pnpm run lint
pnpm test
pnpm run build
```

静态产物位于 `apps/web/dist/`，可由任意静态文件服务器托管。

## 支持的报告

- 实验静态报告 `report.json`：`ase/report/v1alpha1`；
- Skill Search 报告、Job 或 Candidate：`ase/optimization-report/v1alpha1`、`ase/optimization-job/v1alpha1`、`ase/skill-candidate/v1alpha1`；
- Benchmark Generation Job、Candidate、DatasetVersion，以及 Dashboard 聚合报告：对应 `ase/benchmark-*/v1alpha1`；
- `TraceManifest`、`FailureDiagnosis`、`PairTraceDiff`：`ase/v1alpha1`，通过必需字段区分。
- Evolution Evidence Release、Evidence Index、Real LLM Proposal 和 `skill-diff.patch`，用于
  [Stage 4C Skill Evolution Timeline](./evolution-timeline-dashboard.md)。

导入多个原子 Benchmark/Trace 文件时，Dashboard 会在对应视图并列展示。当前 Python 生成的 `report.json` 内嵌 `trace_intelligence` 也可直接展示。未知 schema version 会被拒绝并显示不兼容提示；缺失必需字段会显示具体错误。

## 数据安全边界

- 文件仅通过浏览器 File API 读取，未实现上传、遥测或后端请求；
- 单文件限制为 5 MiB；对象最大深度 20，单数组最多 10,000 项，单对象最多 5,000 个键，单字符串最多 20,000 字符；
- 丢弃 `__proto__`、`prototype`、`constructor` 键，解析结果写入无原型对象；
- 名称匹配 Secret、password、credential、API key、token 或 private key 的字段值统一显示为 `[REDACTED]`；
- 所有导入文本均通过 Vue 文本插值展示，代码中不使用 `v-html`；JSON 中的 HTML、脚本和 URL 不会执行；
- 外部地址仅允许显式 `https:`，并使用新窗口的 `noopener noreferrer`；`file:`、`javascript:`、`data:` 和明文 HTTP 不会成为链接；
- “清除本地数据”会释放当前标签页内存中的报告。MVP 不使用 localStorage、IndexedDB 或 Cookie 持久化报告；
- Trace 只展示可观察事件、Capability 声明和规则诊断，不展示或推断隐藏思维链。

浏览器本身、扩展、操作系统和静态托管服务不在应用信任边界内。若未来托管于服务端，必须补充严格 CSP、安全响应头、依赖供应链扫描和独立渗透测试。

## Synthetic / simulated Fixture

`apps/web/public/fixtures/` 提供配对实验、Trace/Diagnosis、Benchmark Generation、Skill Search、
Promotion 和 Evolution Evidence Release 脱敏演示数据。Fixture 的 ID、结果、repository 和时间线均为
合成内容；页面始终显示 `SYNTHETIC / SIMULATED` 警示。Fixture 只能验证界面、解析器和控制链路，
不能伪装为真实 Agent/Skill 评测或支持任何性能、泛化和因果声明。

## 当前不支持

- FastAPI、登录、权限、PostgreSQL、Redis、Celery 或服务端存储；
- 实验启动、重跑、取消、预算控制或 Manifest 修改；
- Skill 编辑、Candidate 生成、Benchmark 审核或发布操作；
- Final Evaluation、locked-test 访问或确认性结论；
- 飞书、Java、MCP Runtime、Memory/RAG Runtime；
- ZIP/tar 解包、跨文件引用完整性验证和数字签名验证。Evolution Release 本地目录可选择导入，
  但完整性仍须使用 CLI `evolution release verify` 验证。

## 后续接入 FastAPI 与飞书

FastAPI 接入应保留当前只读 UI 与解析边界：服务端只返回版本化 DTO，并由前端继续校验 schema。建议新增独立 `/api/v1/reports` 只读端点，服务端从不可变报告存储读取，使用内容哈希/ETag，默认不返回原始 stdout、Secret 或敏感 Artifact。任何实验或 Manifest 写操作必须使用不同权限域、端点和审计日志，不能复用 Dashboard 的读取凭据。

飞书接入建议采用“摘要卡片 + 安全深链”：机器人仅发送脱敏指标、报告哈希和 Dashboard URL，不把完整 Trace、源码或潜在 Secret 放入消息。身份映射、文档权限和链接有效期由服务端处理；飞书回调不得直接触发实验、修改 Manifest 或访问 locked test。若需要导出文档，应由独立导出服务生成静态、已转义的快照。

## 与其他分支的契约依赖

本 MVP 未修改 `packages/contracts/` 或 Final Evaluation 代码。后续若 Final Evaluation 引入新报告版本，应新增版本化前端适配器，不要就地改变 `v1alpha1` 解释。若需要统一聚合 Benchmark report，当前 Dashboard 临时识别 `ase/benchmark-report/v1alpha1`；核心分支尚无此公共聚合契约，合并时应保留为 web 私有输入格式，或在独立契约变更中正式定义。
