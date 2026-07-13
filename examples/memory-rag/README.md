# Offline Memory/RAG evaluation demo

This deterministic fixture validates the evaluation controller only. Every artifact is marked
`simulated=true`; it is not evidence of real Agent, RAG, retrieval, or Memory improvement.

```bash
agentskill-eval memory-rag validate examples/memory-rag/dataset.yaml
agentskill-eval memory-rag lab run examples/memory-rag/lab-config.yaml \
  --workspace /tmp/agentskill-eval-memory-rag --allow-simulation
```
