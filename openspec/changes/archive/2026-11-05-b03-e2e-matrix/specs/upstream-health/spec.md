# Spec Delta

## MODIFIED Requirements

### Requirement: stderr 启动摘要与 stdout 零污染

代理 SHALL 在启动完成时向 stderr 输出一行摘要（如 `x/y upstreams connected (server FAILED: reason)`）；`connected` 定义为握手成功（含 0 工具上游），失败者附原因；全部日志 SHALL 只走 stderr，stdout 作为 stdio 协议通道 SHALL 零污染。

#### Scenario: 摘要行

- **WHEN** 4 个上游中 3 个握手成功（其一为 0 工具）、1 个失败
- **THEN** stderr 含一行 `3/4 upstreams connected` 与失败者及原因

#### Scenario: stdout 零污染

- **WHEN** 代理经历启动、搜索、invoke 全流程
- **THEN** stdout 只含 MCP 协议消息，无任何日志混入
