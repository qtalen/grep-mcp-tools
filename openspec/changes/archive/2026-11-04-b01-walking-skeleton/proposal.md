# Proposal

## Why

直挂上游 MCP 服务会让 agent 常驻吃下 20k~60k token 的工具定义，上下文爆炸、能力盲视。本项目以代理层形态破局：下游只暴露三个工具（`search_mcp_tool` / `invoke_this_tool` / `check_mcp_status`），上游全部工具在启动握手时落成明文 JSONL 索引，agent 按需关键词检索、按 target 透传调用，常驻降到 ~650 token。本 change 交付 B01 walking skeleton——最小但完整的全链路，走通"启动握手 → 索引 → 搜索 → 透传"四步并可用交互探针人工验收。

## What Changes

- 新增 Python 包 `grep_mcp_tools`（uv 工程、console script `grep-mcp-tools`、依赖官方 `mcp` SDK），零代码仓库首次落地。
- 代理以 stdio 对下游提供服务，暴露三工具：搜索（AND/OR 语义机 + 阈值 + 路标）、透传调用（`server__tool` 命名空间 + 每上游串行化）、健康快照（注册表三态）。
- config 解析与 fail-fast 校验（Claude Code mcpServers 同形 + `settings` 块；结构错误、服务名白名单、传输消歧）。
- 三传输上游握手（stdio / Streamable HTTP / SSE）并发急切执行，落明文 JSONL 索引；上游故障 fail-soft 不阻塞启动，记入健康注册表。
- 健康注册表三态驱动四通道：搜索路标计数行、`check_mcp_status` 详情、stderr 启动摘要、`tools_index/_status.json`。
- 交付交互探针 `tests/e2e/probe.py` 与假上游 ×3（stdio/HTTP/SSE 各一）+ 1 死条目，作为人工验收载体。
- 不做（留后续批次）：定时刷新与自愈、description 动态注入、list_changed（B02）；E2E 全自动化（B03）；发包（B05）。

## Capabilities

### New Capabilities

- `upstream-config`: config 文件格式（mcpServers 同形 + settings 键表）、fail-fast 校验规则（结构错误 / 服务名白名单 / 传输消歧）与空配置语义。
- `tool-index`: 上游握手（三传输、并发、急切、有限等待）、明文 JSONL 索引落地（一行一工具、原子替换、UTF-8）与 0 工具上游行为。
- `tool-search`: `search_mcp_tool` 的读路径语义机——匹配域与匹配语义、AND/OR 优先级、阈值与样品、排序定死、路标与空关键词方向、陈旧索引隔离。
- `tool-invoke`: `invoke_this_tool` 的透传语义——target 命名空间解析、参数原样透传、错误两分（代理级 vs 上游级）、每上游串行化。
- `upstream-health`: 健康注册表三态、`check_mcp_status` 快照语义、`_status.json` 持久快照契约、四通道可见性惯例（被动信道给计数与指针，主动信道给详情）。

### Modified Capabilities

（无——greenfield，零存量 spec）

## Impact

- **代码**：全新仓库落地 `src/grep_mcp_tools/`（包结构见 design.md）、`tests/`（单测 + e2e 探针 + demo_servers）、`config.example.json`。
- **依赖**：官方 `mcp` SDK（钉版本）；Python 3.11+；uv 工程。
- **语义契约唯一来源**：`docs/design/architecture.md`（settings 键表、排序、匹配语义等数值/语义以它为准，本 change 产物只引用不重写）。
- **关联设计文档**：`docs/design/batches/B01-walking-skeleton.md`（验收画面与实施要点）、`docs/design/decisions.md`（D-01~D-23）。
- **平台**：Windows 开发机首验（UTF-8 强制、127.0.0.1 显式绑定防 IPv6 坑）；无外部二进制依赖（黄金律 5）。
