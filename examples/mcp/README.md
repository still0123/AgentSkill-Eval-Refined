# Offline MCP evaluation demo

This fixture uses a deterministic mock agent and Mock MCP Lab. It is simulated controller
validation, not evidence that MCP guidance improves a real agent.

```bash
agentskill-eval mcp validate examples/mcp/dataset.yaml
agentskill-eval mcp lab run examples/mcp/lab-config.yaml \
  --workspace /tmp/agentskill-eval-mcp --allow-simulation
```
