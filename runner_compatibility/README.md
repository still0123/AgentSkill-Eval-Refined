# Runner compatibility

本目录保存外部 Runner 的不可变兼容记录。每个版本目录必须包含来源版本、源码提交、发布物/二进制哈希、能力矩阵和 Golden Contract fixture。

当前支持：

- `skill-up/v0.5.0`：公开 CLI/JSON 适配，Darwin arm64 二进制已做真实集成验证。

新增 Runner 或升级版本时创建新目录，不覆盖历史记录。适配策略与验证方法见 [`docs/runner-adapters.md`](../docs/runner-adapters.md)。
