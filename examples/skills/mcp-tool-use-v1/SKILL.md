# MCP Tool Use

When a task can be completed with the available MCP tools:

1. Read tool names, descriptions, input schemas, and side-effect declarations first.
2. Select the smallest relevant tool sequence; do not substitute an unrelated tool.
3. Supply every required parameter and never invent identifiers.
4. Retry transient failures at most once, then use an explicit fallback or report failure.
5. Never perform mutating or destructive calls without the required confirmation.
6. Base the final response on successful tool observations.
