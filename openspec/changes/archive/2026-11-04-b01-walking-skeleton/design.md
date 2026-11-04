# Design

## Context

greenfield 仓库首次落地代码。完整设计账本在 `docs/design/`：`architecture.md` 是全部语义/数值契约的唯一来源（settings 键表、排序、匹配语义、`_status.json` 字段等），`batches/B01-walking-skeleton.md` 是本批验收画面与实施要点，`decisions.md` 是 ADR 台账（D-01~D-23）。本文件只记录实施取向，不重写账本内容。开发机 Windows（cmd、cp936 默认编码、localhost 解析 IPv6 的已知坑），目标运行时 Python 3.11+，包管理与测试经 uv。

## Goals / Non-Goals

**Goals:**

- 走通全链路：config → 三传输握手 → JSONL 索引 → 搜索 → 透传 → 健康快照，以 `tests/e2e/probe.py` 人工验收。
- 纯逻辑（config 校验、搜索引擎、结果格式化）TDD：失败单测先于实现。
- 索引与 `_status.json` 全部 UTF-8、原子写；stdout 零污染。

**Non-Goals:**

- 无定时刷新/自愈/list_changed/description 注入（B02）；无 E2E 自动化矩阵（B03）；无 agent 实验台（B04）；无发包（B05）。
- 无孤儿进程专项清理（B03 用例驱动，本批仅正常退出路径的 AsyncExitStack 逆序回收）。

## Decisions

- **包布局**：`src/grep_mcp_tools/` 布局（可发布的标准形态），模块按读写路径切：`config.py`（解析+校验，纯函数）、`index.py`（握手循环+JSONL 写盘）、`search.py`（读路径语义机，纯函数）、`health.py`（注册表+`_status.json`）、`server.py`（三工具注册与下游服务）、`__main__.py`（argv+启动编排）。console script `grep-mcp-tools`。
- **依赖**：官方 `mcp` SDK 钉较新版本；测试 `pytest` + `pytest-asyncio`。不引入其他运行时依赖（黄金律 5）。
- **下游服务端用高层 `MCPServer`**（v2 的 FastMCP 改名件），三工具 description 为静态英文文案。**首验 A 已通过（mcp 2.3.0）**：装饰器工具直接返回手建 `CallToolResult` 时 SDK 原样透传（`convert_result` 对 `isinstance(result, CallToolResult)` 直接 return），content 列表与 `is_error` 不加料，100KB 载荷往返无损，有无返回注解均可——无需降级低层 `Server` API。invoke 透传用无返回注解的工具函数（不发布 output schema，零干扰）。
- **上游连接**：每上游一条高层 `Client`（v2 API：`StdioServerParameters` / `streamable_http_client` / `sse_client`），由一条专用任务持有自己的 `AsyncExitStack`（anyio cancel scope 要求 enter/exit 同任务），退出逆序回收；握手跨上游 `asyncio.gather` 并发，每上游以 `asyncio.timeout(handshake_timeout)` 有限等待；同一上游 invoke 以每上游一把 `asyncio.Lock` 串行，跨上游天然并发；HTTP/SSE 客户端读超时置无限（D-06），loopback 绕过系统代理（D-24）。
- **搜索引擎为纯函数**：输入 = 本轮 live 服务的索引行集合 + keywords，输出 = 结构化结果（命中列表/样品/路标），无 IO、无状态——单测可直接构造索引行驱动全部语义机分支（含 off-by-one、排序确定性、双零路标）。
- **JSONL 序列化**：`json.dumps(..., ensure_ascii=False)` 单行 + 强制 `encoding="utf-8"`——中文以明文入库保住"人可直接打开看"的性质；tmp+rename 原子替换；`_status.json` 同纪律。
- **健康注册表**：启动握手循环的副产品（内存 dict），三态与"本轮"定义按 architecture；B01 为启动快照，不主动探测。
- **probe.py 拉起方式**：subprocess 走真实启动路径（console script + `--config`）；demo config 运行时生成于临时目录，上游用绝对路径、随机空闲端口、HTTP/SSE 显式绑 127.0.0.1；交互循环读到 EOF 为止（可管道驱动）；每次搜索后打印上下文账单（结果体积 chars/4 vs 索引行总字符数/4）与索引目录位置。
- **假上游**：`tests/e2e/demo_servers/` 下 `hr.py`（stdio，8 工具）、`itops.py`（Streamable HTTP，12 工具，含 `firehose`/`boom`/与 hr 同名的 `search`）、`audit.py`（SSE，2~3 工具，含描述只写 "audit trail retention" 的工具）；"region" 植入 ≥6 个工具定义；dead_http 条目指向探针选定但不起服务的端口。**首验 B 已通过（mcp 2.3.0）**：SSE 服务端 transport 官方仍在（`mcp/server/sse.py` 的 `SseServerTransport` + `MCPServer.run_sse_async(host, port)`），`Client(sse_client(url))` 握手/list_tools/call_tool 全通——audit.py 直接用 `run(transport="sse")`，无需 stdlib 手写 fixture。
- **错误与日志文案**：代理生成的用户可见文本一律英文；stderr 启动摘要一行 `x/y upstreams indexed (server FAILED: reason)`。

## Risks / Trade-offs

- [SDK 的 SSE 服务端 transport 已弃用，可能缺失或不稳定] → 首验 B 已通过：transport 官方仍在且全链路可用（spike `tests/spikes/test_spike_b_sse.py`），风险消除。
- [FastMCP 对 content/isError 的转发可能加料（改写、包装、截断）] → 首验 A 已通过：手建 `CallToolResult` 原样透传（spike `tests/spikes/test_spike_a_passthrough.py`），风险消除。
- [SDK 2.x 与设计账本的 1.x 词汇漂移（FastMCP→MCPServer、ClientSession→Client、httpx→httpx2、字段 snake_case）] → 钉 `mcp==2.3.0`；代码一律用 v2 API（`MCPServer`/`Client`/`sse_client`/`streamable_http_client`/`StdioServerParameters`）；设计账本（architecture 技术选型、B01）在 9.3 收尾时同步 v2 词汇。
- [Windows：默认 cp936、localhost→IPv6、进程组回收] → 全部文件 IO 显式 UTF-8；demo 服务显式绑 127.0.0.1；孤儿进程治理留 B03。
- [OR 兜底样品数默认 10 为拟定值] → 探针实测过小/过大可调，调整回写 decisions 台账（D-04）。
- [同上游串行化 + invoke 无超时的组合：挂死上游长期占队列] → 已入 decisions 已接受风险表；B01 观察不处理。

## Migration Plan

greenfield 首次落地，无迁移；回滚 = 删除新增文件。`config.example.json` 入仓库作模板，真实 config 不入库。

## 记录

- 2026-11-04 开工：依赖钉 `mcp==2.3.0`（PyPI 稳定线 2.x，v2 API：`FastMCP`→`MCPServer`、高层 `Client` 统一三传输、协议类型 snake_case、`httpx`→`httpx2`、`mcp-types` 独立包）。
- 首验 A 结论：高层 `MCPServer` 装饰器工具返回手建 `CallToolResult` 原样透传（content + `is_error`，100KB 通过；有/无返回注解均可，正式实现用无注解形态）——服务端选型定案为高层 `MCPServer`，不降级。spike 留存 `tests/spikes/test_spike_a_passthrough.py`。
- 首验 B 结论：SSE 服务端 transport 官方可用（`SseServerTransport` + `run_sse_async`/`run(transport="sse")`），`Client(sse_client(...))` 往返全通——audit 假上游定案为 `MCPServer` SSE 形态，不手写 stdlib fixture。spike 留存 `tests/spikes/test_spike_b_sse.py`。
- 实施记录（探针验收）：全链路走查通过——AND 命中（leave 3 条）与上下文账单（结果 ~90 token vs 直挂 ~1689 token）、invoke 透传、region 超量收窄（演示 threshold=5）、OR partial 兜底、双零路标（计数行+指针）、status 三活+死条目、stderr 摘要 `3/4 upstreams indexed`。
- 实施记录（连接模型修正）：SDK 传输基于 anyio，cancel scope 要求 enter/exit 同任务——上游连接改为每上游一条专用任务持有 `AsyncExitStack`（`index.UpstreamConnection`），否则 `RuntimeError: Attempted to exit cancel scope in a different task`。
- 实施记录（D-24 loopback 代理坑）：探针实测发现受限 env 下 httpx2 回落 Windows 系统代理，127.0.0.1 死上游被代理接走并返回非 2xx（原因变成 `Server returned an error response`）。修复：loopback 上游 `trust_env=False`，远程上游保持代理；回归测试 `tests/unit/test_transport_proxy_bypass.py`。
- 实施记录（拟定值核对）：OR 兜底样品数默认 10 在 23 工具演示规模下未受压（OR 命中 4/4 全展示），维持默认；B03 的 40+ fleet_gen 大目录再验证，如需调整回写 D-04。
- 实施记录（其他）：探针端口选择需去重（连续 `free_port()` 会返回相邻/重复端口，死条目可能撞上活服务）；用户可见输出全面 ASCII（破折号等字符在 cp936 控制台乱码）；零工具上游不落索引文件但计入 live 清单。
- 审核修订（2026-11-04）：0 工具上游删除上一轮索引文件（防幽灵工具）；HTTP/SSE 读超时置无限对齐 D-06；索引写盘失败 fail-soft 记 unreachable；`fullmatch` 防尾换行服务名；settings 未知键/非正数 fail-fast；相对 `index_dir` 按 config 目录解析；config 容忍 BOM；空配置路标文案自洽；demo 工具补 `destructiveHint` 注解并新增真实链路 e2e（firehose 100KB / boom 错误 / `[destructive]` 标记）；spikes 打 `spike` marker 并从默认测试集排除。

