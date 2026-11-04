# B01 walking-skeleton —— 最小代理全链路 + 交互探针

> 状态：✅ 已归档 2026-11-04（`openspec/changes/archive/2026-11-04-b01-walking-skeleton/`）
> 形态：功能批

## 验收画面

`uv run python tests/e2e/probe.py` 一条命令：代理以 stdio 拉起、按 demo config 连 4 个上游——hr（stdio）、itops（Streamable HTTP）、audit（SSE）、dead_http（HTTP，指向无人监听端口，注定不可达）——前三个握手建索引，dead_http 走 fail-soft 落入健康注册表。在探针里输入 `leave` → AND 命中列表（每条含可复制 target、description、参数及类型）与上下文账单（本次结果 ~X token vs 直挂上游 ~Y token 常驻）；输入 `invoke hr__xxx ...` → 透传成功返回；输入 `region` → 超量收窄提示（演示 config 设 `settings.threshold=5`，"region" 植入 ≥6 个工具定义即触发；此为演示值，契约默认值见 architecture 键表）；输入乱词 → 双零路标（live 名单 + 不可达计数行 + `check_mcp_status` 指针）；输入 `status` → 可用性列表：三个 live 服务 + dead_http 条目带原因与"提醒用户"强提示。stderr 启动摘要一行：`3/4 upstreams indexed (dead_http FAILED: connection refused)`。

## 边界（本批不做什么）

- 无定时刷新、无 fail-soft 自愈、无 list_changed（留 B02）
- 无 description 动态注入（留 B02；本批三个工具的 description 均为静态文案）
- 错误处理覆盖主路径即可，长尾细节（超时、孤儿进程等）留 B03 用例驱动
- 不发包、不写 README 部署节（留 B05）

## 实施要点

- **工程骨架**：`pyproject.toml`（uv，模块名 `grep_mcp_tools`，console script `grep-mcp-tools`，依赖官方 `mcp` SDK 钉版本——**实施结论：钉 `mcp==2.3.0`，用 v2 API（`MCPServer`/高层 `Client`/`httpx2`）**；上游工具缺 `title`/`annotations` 时 JSONL 写 `null` 保形）；`--config <path>` argv 参数。**开发流程 TDD：搜索引擎、config 校验等纯逻辑先写失败单测再实现。**
- **config 解析与 fail-fast 校验**：Claude Code mcpServers 同形 + `settings` 块（键表见 architecture）。校验规则以 architecture『config 格式』契约为唯一来源（结构类错误 + 服务名白名单 + 传输消歧），启动时报错退出且信息明确；`mcpServers: {}` 为合法启动。
- **握手建索引**：三传输逐上游握手（stdio / Streamable HTTP / SSE），跨上游并发、急切执行——代理应答自身 initialize 前完成全部握手与索引落盘（总时长 ≈ 单上游 handshake_timeout 上限，有限等待、不因单个故障中止）；握手失败（连不上/超时/秒退）→ 跳过 + stderr 日志（启动 fail-soft；自愈归 B02）→ `tools_index/<server>.jsonl`，一行一工具：`server/name/title/description/inputSchema/annotations`；`json.dumps` 保证单行（description 含换行也安全）；强制 `encoding="utf-8"`；tmp+rename 原子替换。**开工首验**：官方 SDK 的 SSE 服务端 transport（弃用件）仍可用于 fixture；不可用则改用 stdlib 手写最小 SSE fixture（事件流 + 消息 POST 端点）——B02/B03 的 SSE 用例同样依赖它，退批不解决可得性问题。**实施结论：v2.3.0 官方支持（`SseServerTransport` + `run(transport="sse")`），首验通过。**
- **健康注册表**：握手循环副产品，`server → live N 工具 / live 0 工具 / unreachable+原因` 三态；驱动：路标与空关键词方向（计数+指针）、`check_mcp_status`（详情）、invoke 错误文案、stderr 启动摘要、`tools_index/_status.json`（启动写快照，B02 刷新更新）。**陈旧索引隔离**：搜索只加载本轮握手成功服务对应的索引文件。
- **搜索引擎**：完整实现 architecture『读路径』语义机及其全部契约（整行扫描匹配域、AND/OR 优先级、阈值与样品数、排序定死、partial 标注、路标计数行、性能预算——均以 architecture 为唯一来源，本文件不重复数值）。
- **结果格式化**：每条含 target（可原样复制）、description、参数及类型描述；`destructiveHint=true` 条目带 `[destructive]` 标记；响应文本英文。
- **invoke_this_tool**：target 解析 `server__tool`（从首个 `__` 切分）；params 为自由 JSON 对象原样透传；上游错误原样透传；代理级错误两分——服务不在 config / 服务在 config 但不可达（自包含原因），文案与上游错误可区分，提示从搜索结果复制 target。同一上游 invoke 串行化不分传输（每上游一把锁），跨上游并发。**开工首验**：FastMCP 能否原样转发 content 列表与 `isError` 标志（firehose 100KB 是压力测试），不行则降级低层 `Server` API。**实施结论：v2 的 `MCPServer`（原 FastMCP）对装饰器工具返回的手建 `CallToolResult` 原样透传（content + `is_error`），100KB 通过，无需降级。** 常驻上游连接以 `AsyncExitStack` 持有（每上游专用任务持有，anyio 同任务约束），正常退出逆序回收。
- **check_mcp_status**：无参；返回健康注册表快照（不主动探测重连）；有死者 → 逐服务状态+原因+强提示（要求 agent 提醒用户不可用服务及原因）；全健康 → 安静清单；空配置 → "no upstream services configured"。
- **日志**：全部走 stderr（stdout 是协议通道）；启动摘要一行 `x/y upstreams indexed (...)`。
- **probe.py**：subprocess+stdio 拉起代理（真实启动路径：console script + `--config`）；运行时在临时目录生成 demo config（上游绝对路径、随机空闲端口、HTTP/SSE 服务显式绑 127.0.0.1——本机 `localhost` 解析 IPv6 而服务绑 IPv4 的坑）；交互循环读到 EOF 为止（输关键词 → 结果；`invoke <target> <json>` → 透传；`status` → 可用性列表；可管道驱动自动化）；每次搜索后打印上下文账单（结果体积 chars/4 估算 vs 直挂常驻估算=索引行总字符数/4）+ 索引文件位置。
- **假上游 ×3 + 1 死条目**：`tests/e2e/demo_servers/hr.py`（stdio，8 个工具：员工/请假/薪酬）、`itops.py`（Streamable HTTP，12 个工具：工单/部署/日志）、`audit.py`（SSE，2~3 个工具，含描述只写 "audit trail retention" 的工具——验证 OR 兜底/路标）。机关：hr 与 itops 各有一个同名 `search` 工具（验证命名空间）；"region" 植入 ≥6 个工具定义（threshold=5 下触发超量路径）；itops 有 `firehose` 返回 100KB（透传演示）、`boom` 抛错（错误透传）；dead_http 条目指向探针选定但不起服务的端口。
- `config.example.json` 入仓库当模板（三传输条目 + settings 全键示例；真实 config 不入库）。

## 依赖与关联

- 前置批次：无
- 关联决策：D-01~D-10、D-12、D-14~D-16、D-18、D-22、D-23、D-24

## 记录

- 2026-11-04 落盘。OR 兜底样品数默认 10 为落盘时拟定值（D-04），实施时若探针实测不合适可调，调整需回写台账。
- 2026-11-04 审核修订：超量演示阈值说明（threshold=5 演示值）；读路径语义机去双写（唯一来源=architecture）；config 校验补服务名 `__` 禁令；补引 D-14。
- 2026-11-04 探索修订（开工前讨论定案）：HTTP/SSE 三传输从 B02 提前进本批（B02 移除该项）；匹配域明确为整行扫描（D-22 补边界）；新增健康注册表与四通道可见性、`check_mcp_status` 第三工具（D-01 修订、D-23 新增）、陈旧索引隔离、config 传输消歧与服务名白名单、`[destructive]` 标记、排序定死、TDD 流程；假上游方案 B（hr=stdio / itops=HTTP / audit=SSE + dead_http 死条目）。
- 2026-11-04 实施记录（本批完成）：SDK 钉 `mcp==2.3.0`（v2 API）；两个开工首验均一次通过（高层 `MCPServer` 原样透传 `CallToolResult`；官方 SSE 服务端可用）；新增 D-24（loopback 上游绕过系统代理——探针实测：受限 env 下 Windows 系统代理劫持 127.0.0.1，死上游原因失真）；上游连接改为每上游专用任务持有 `AsyncExitStack`（anyio cancel scope 同任务约束）；探针全链路验收通过（AND 命中+账单、invoke 透传、region 超量、OR partial、双零路标、status 三活+死条目、stderr 摘要），132 项测试全绿。
- 2026-11-04 审核修订：0 工具上游删除上一轮索引文件；HTTP/SSE 读超时置无限（D-06 补注）；索引写盘失败 fail-soft；`fullmatch`/BOM/settings 非正数与未知键校验；相对 `index_dir` 按 config 目录解析；空配置路标文案；demo 工具补 `destructiveHint` 并新增真实链路 e2e；spikes 打 marker 移出默认测试集。全量 145 passed（4 spike deselected）。
- 2026-11-04 归档附注（计划冻结）：验收证据——`uv run pytest` 145 passed / 4 deselected、`uv run pytest -m spike` 4 passed、`openspec validate` valid、probe 全链路走查通过（AND 命中+账单、invoke 透传、region 超量、OR partial、双零路标、status 三活+死条目、stderr 摘要、`[destructive]` 标记）。契约回写 architecture（0 工具删旧索引、读超时无限、BOM、settings 校验、相对 `index_dir`）；新裁决 D-24 入台账。遗留转开放项总账三条：startup summary 措辞、`terminate_on_close` 多等 30s、json 文件内重复键检测。OR 样品数默认 10 未在 23 工具演示规模受压，B03/B04 大目录复验。
