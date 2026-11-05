# Design

## Context

B01 已交付启动定格的全链路（`handshake_all` 启动握手一次 → 索引/注册表/`_status.json` → `run_stdio_async` 阻塞）。现有代码事实：`index.UpstreamConnection` 是可重启对象（`start()`/`stop()` 一对，专用任务持 `AsyncExitStack`，`client` 字段判死，每上游一把 `asyncio.Lock` 串行化 invoke）；`handshake_all` 返回时过滤掉 `client is None` 的连接，`ProxyServer` 以该字典路由 invoke 并在构造时定格 `_known_tools`；`settings.refresh_interval` 已解析校验、无消费者。语义契约唯一来源 `docs/design/architecture.md`（注入面、连接模型、键表），裁决台账 `docs/design/decisions.md` D-25/D-26/D-27 与 B02 批次文档（2026-11-04 探索定案全量决议）。

## Goals / Non-Goals

**Goals:**

- 刷新轮与握手轮同构：一轮刷新 ≈ 一次"逐上游：活着重拉 / 死了重握手 + 落索引 + 翻注册表"，复用 B01 全部索引纪律与 fail-soft 语义。
- 纯逻辑（roster 文案、签名 diff、刷新状态机）TDD：假连接驱动，E2E 留 B03。
- probe 人工验收可演示完整自愈环（死亡→翻转→恢复→通知）。

**Non-Goals:**

- 不做上游 list_changed 事件驱动重索引（D-17，定时为准）。
- 不做 E2E 自动化矩阵与新旧双代协议全链路 E2E（B03 E 区）。
- 不改 startup summary 措辞（现状 `x/y upstreams indexed`；`connected` 措辞属 B01 遗留，B03 A 区断言时再改）。

## Decisions

- **刷新循环宿主与轮内并发**：启动握手轮由 `run_proxy` **同步 await** 完成（急切契约：应答 initialize 前索引落盘、轮尾依赖的 `ProxyServer` 已构造），之后再起刷新任务 `asyncio.Task`——循环体为"跑一轮 → 睡 `max(0, refresh_interval - 本轮耗时)`"（首轮在 t0 立即执行，对齐固定频率——启动时不可达上游尽快获得一次自愈机会；下一轮按上轮开始时刻 + interval 对齐，实现"实际间隔 = max(interval, 轮耗时)"的轮契约）；轮与轮串行（慢轮自然顺延，无定时器并发触发）。**轮内跨上游并发**（`asyncio.gather`，对齐 B01 握手轮：一轮最坏耗时 ≈ 单上游 `handshake_timeout`，不随上游数线性叠加）。启动轮与刷新轮共用同一"单上游刷新单元"函数。
- **刷新单元 = `UpstreamConnection.refresh(timeout, registry, index_root)`**：对单个上游——先非阻塞检查 `lock.locked()`（检查与 `acquire` 之间无 await 点、事件循环不会插入任务，无竞态；不用 `wait_for(acquire, 0)`——CPython 3.13 对 `timeout<=0` 必抛 TimeoutError，实测语义为函数体未跑即取消），忙则跳过本轮返回"未刷新"；拿到锁后若 `client` 为 None 或连接任务已死 → `stop()` + `start()`（同回合自愈）；活着则复用 `client` 在 `asyncio.timeout(handshake_timeout)` 内重拉 `list_tools`（超时按失败处理，防半死上游挂住整轮），拉取失败同样 `stop()` + `start()`（同回合内一次重试，重试结果即本轮终态）。成功路径沿用 `_run` 的既有纪律：写索引失败记 unreachable 不谎报 live、0 工具删残留文件、注册表翻转。**不变式：任何进入非 live 态的路径（`stop()`、重握手失败）必须把 `client` 置 None**——否则 invoke 单条件判死失真，曾 live 后死亡的服务会落到"工具不存在"错误分支（`_known_tools` 每轮按 live 集重建已不含它），违反"不可达错误文案不变"契约。
- **`connections` 改 config 镜像**：`handshake_all` 返回**全部**上游的连接对象（不再过滤 `client is None`），`ProxyServer.connections` 结构恒等于 config.servers；invoke 判死退化为单条件 `connection.client is None`（原因从注册表取）。`close_all` 相应回收全部对象（`stop()` 对从未成功的连接是安全空跑）。消除"启动时死→刷新救活→invoke 查无此人"的字典脱节：自愈是同一对象的 `client` 从 None 变有值，字典不动。
- **`_known_tools` 每轮无条件重建**：刷新轮结束后由 `ProxyServer` 重跑 `_load_known_tools()`（从注册表 live 集 + 索引文件重建）；不做增量 diff——重建是幂等读盘，成本可忽略，换取"校验表与索引永不脱节"的不变式。
- **description 渲染为纯函数**：`render_search_description(trigger_text, live_servers, unavailable_count, max_servers) -> str`，输入 `LiveServer` 列表与上限，输出静态文案+清单（字典序、超限前 N+`(of M)` 总数、`(k unavailable)` 后缀、空配置/全挂文案）。与 `search.py` 的 `_render_service_list`（搜索路径多行清单）形态不同、独立维护，不共享常量。注册：`ProxyServer` 构造时以初始 roster 注册；每轮刷新后计算签名，变化时 `remove_tool("search_mcp_tool")` + 重新 `@mcp.tool` 注册（SDK 2.3.0 实证 `add_tool`/`remove_tool` 公共 API、`list_tools()` 每请求读时求值）；**remove 与重注册之间不得有 await**（防并发 tools/list 在窗口期看不到 `search_mcp_tool`）。
- **签名 = description 文本的决定因素**：可见服务的 `sorted{(server, tool_count)}` + live 总数 + 不可达计数。未超上限时可见集合即 live 全集（与全集签名等价）；超上限时被降级隐藏的服务工具数变化不触发重注册与通知（description 文本未变），对齐 D-25 否掉"无条件通知"（无谓重拉与缓存抖动）的初衷。
- **list_changed 双线（D-25）**：旧代——构造 `MCPServer` 时挂 middleware，捕获首个下游请求的 `ctx.session` 存为服务端字段；签名变化时若 session 存在则 `await session.send_tool_list_changed()`（异常吞掉：尽力而为）。新代——构造 `MCPServer(subscriptions=自定义 SubscriptionBus 实例)`；签名变化时向 bus `publish ToolsListChanged`（无 listener 时静默）。两线都发、都 best-effort，无重试。**能力位修正（SDK 2.3.0 源码实证）**：`MCPServer.run_stdio_async` 无参调用 `create_initialization_options()`，`NotificationOptions` 默认全 False——旧代握手期 `tools.listChanged` 能力位为 false，严格按能力过滤的客户端可能不装 handler。修法：构造后 wrap `_lowlevel_server.create_initialization_options` 强制 `NotificationOptions(tools_changed=True)`（MCPServer 只无参调用它，wrap 安全；依赖私有属性，SDK 升级需复验）。新代线能力位无需处理：`subscriptions` 非 False 时 `ListenHandler` 注册 `subscriptions/listen`，2026-07-28+ 协议版本下能力位由 `listen_served` 推导为 true。备选"只做旧代"被否：对已实现 subscriptions/listen 的客户端是哑铃铛；"只做新代"被否：真实下游（langchain-mcp-adapters 等）全在旧代。
- **`terminate_on_close=False`（D-27）**：`_http_transport` 里 `streamable_http_client(url, http_client=..., terminate_on_close=False)`。内网本地上游 DELETE 无收益（会话内存态），不可达上游反而吃满 30s connect 超时拖慢退出。SSE/stdio 无此参数，不动。
- **退出时序**：`run_proxy` 的 `finally` 先 `cancel()` 刷新任务并 `await` 其结束，再 `close_all()`——防刷新重握手与 teardown 竞态产生孤儿 stdio 进程。刷新循环的 sleep 用 `asyncio.sleep`（取消即醒，退出不拖延）。
- **刷新轮后统一落盘**：每轮结束后重写 `_status.json`（原子写既有函数）+ 重建 `_known_tools` + 计算 roster 签名（变化才重注册+双线通知）。三件事是"轮尾同步块"，顺序固定、单测可断言。**轮级异常兜底**：循环体整体 try/except——轮内单上游意外异常（`gather` 冒泡）与轮尾写盘失败（如 Windows 上 `_status.json` 被其他进程打开导致 `os.replace` PermissionError）只记一行 stderr（复用失败日志形态），刷新循环 SHALL NOT 因单轮异常死亡；兜底范围含重注册块（中途异常不得让 `search_mcp_tool` 永久消失）。
- **fixture**：`fleet_gen.py`（Streamable HTTP，程序化生成 42 个 `region_<area>_report` 工具，"region" 词植入对齐 OR 兜底演示）、`docs.py`（SSE，6 工具，形态照抄 `audit.py`）、`broken.py`（main 里 `sys.exit(1)`，启动即崩）。probe：演示 config 升级 5 上游 + `refresh_interval: 30`；新增 `tools`（拉 tools/list 打印 `search_mcp_tool` description）、`kill <server>` / `start <server>`（terminate/重启 probe 自己 Popen 的 itops，驱动死亡与自愈演示）。
- **测试策略**：unit 用 `FakeConnection`（`client` 可控翻转、`start/stop/refresh` 记录调用序列）驱动刷新状态机（跳过/自愈/同回合重试）、roster 文案全分支、签名 diff（变/不变/被隐藏服务不变三态）、通知双线（断言 session.send 与 bus.publish 被调/不被调）与轮级异常兜底（轮尾抛异常后下一轮照跑）；**真实 `UpstreamConnection` 补两类单测**——"曾 live → 重握手失败 → `client` 为 None → invoke 得不可达错误（非工具不存在）"与"tiny stdio 子进程 terminate → 刷新恢复"往返（`FakeConnection` 测不到这两个类）；既有真实链路 e2e（transport 层）只补 `terminate_on_close` 与 stop+start 重连的真实往返。

## Risks / Trade-offs

- [刷新持锁 stop+start 期间该上游 invoke 排队] → 刷新重握手有界（`handshake_timeout`），排队上界 ≈ 单次握手超时；重握手成功后排队 invoke 经同一连接对象照常执行（lock 内重读 `connection.client`，若恰被置 None 则抛错走代理级错误自包含原因，下轮自愈）。
- [middleware 捕获 session 的时机：首个请求前签名就变化] → 首轮刷新在 t0 立即执行（先跑后睡），此时确无会话——但 t0 轮的 roster 与启动轮相同（同一握手结果），签名不变即无通知需求；极端情况下 session 未捕获即跳过推送，属"尽力而为"已接受语义。
- [wrap `_lowlevel_server.create_initialization_options` 依赖 SDK 私有结构] → 钉 `mcp==2.3.0`；升级 SDK 时复验此点（已列入任务）。新代能力位由 `ListenHandler` 注册自动推导，无此依赖。
- [每轮重写 jsonl/`_status.json` 的写放大与写盘失败] → 600s 默认一轮、本地小文件原子写，成本可忽略；写盘失败由轮级异常兜底捕获（一行 stderr、循环存活），换来"盘上状态=内存状态"的强不变式。
- [probe `kill`/`start` 只覆盖 probe 自己 Popen 的 itops（hr 等 stdio 由代理 spawn，探针无句柄）] → 验收画面已按此设计；stdio 自愈由真实连接单测（terminate → 刷新恢复往返）与 B03 覆盖。
- [Windows：stderr 编码、localhost IPv6] → 沿用 B01 防线（显式 UTF-8、显式绑 127.0.0.1），不新增风险面。

## Migration Plan

行为面新增，无数据/接口迁移：索引文件格式、config 键表、三工具契约均不变。回滚 = revert 本 change 提交（`config.example.json` 不曾改动，交付面无感）。

## 记录

- 2026-11-04 审核回写（reviewer 独立审核 + 主智能体逐条复核后修复）：刷新重拉补 `handshake_timeout` 上界（防半死上游挂住整轮，键适用面扩展至"握手与刷新重拉"并回写 architecture）；`client=None` 不变式显式化（stop/重握手失败必须置 None，防"曾 live 后死亡"落到工具不存在错误分支）；刷新循环补轮级异常兜底（Windows `_status.json` 被占用等写盘失败不杀循环）；启动轮改为 `run_proxy` 同步 await（守住急切契约与轮尾依赖）；签名定义改为 description 文本决定因素（可见集合+总数+计数，超上限时被隐藏服务不触发无谓通知）；旧代能力位 `tools.listChanged=false` 发现与 wrap 修法（SDK 源码实证 `run_stdio_async` 无参 `create_initialization_options`）；`wait_for(acquire, 0)` 备选删除（CPython 3.13 必抛 TimeoutError）；startup summary 现状措辞更正（indexed，非 connected）；remove/重注册之间禁 await；updated_at 场景措辞"不早于"。
- 2026-11-04 实施记录（关键实现事实）：invoke 传输层死亡的 `MCPError(CONNECTION_CLOSED)` 须转代理级错误（B01 的"except MCPError: raise"会把连接死亡当上游业务错误透传，违反"中途死亡 invoke 报代理级错误"契约）；`remove_tool`/`add_tool` 均为同步 API，重注册原子段天然无 await 点。
- 2026-11-04 实施记录（通知协议现实，SDK 2.3.0 实测）：Client 直连与 stdio 传输默认协商 2026-07-28 新代协议；此模式下变更通知只对**已开 subscriptions/listen** 的会话投递——不开 listen 的客户端收不到任何通知（probe 初版 message_handler 零接收实证）；开了 listen 的会话同时收到 bus 事件（ToolsListChanged）与旧代推送（notifications/tools/list_changed）。含义：旧代推送线的受众是旧代 initialize 客户端（langchain-mcp-adapters 等，B03 用真实旧代客户端验证）；SDK 2.3.0 系新代宿主要收通知必须主动 listen——D-25 双线设计在这个协议分叉下恰好各就其位。probe 的通知演示以 `client.listen(tools_list_changed=True)` 为观察点。
- 2026-11-04 实施记录（验收走查，probe 管道驱动）：启动 4/5 indexed（broken FAILED）+ 清单 `(1 unavailable)` → kill itops → 一轮刷新内 itops 翻 unreachable（stderr 一行失败日志、status `2 unavailable`、description 清单去 itops、双线通知各一次）→ start itops → 下一轮恢复 live（清单回归、通知再次触发）。全挂文案/空配置文案由单测覆盖。走查中观察窗口须 ≥ 间隔+慢轮余量（重拉超时最长 handshake_timeout 顺延整轮）。
- 2026-11-05 代码审核回写（reviewer 独立审核 + 逐条复核后修复）：① 重注册的签名提交移到成功之后 + remove_tool 对缺席态容错（异常不再可能让 search_mcp_tool 永久消失，附回归测试）；② invoke 锁内复检 client is None（刷新重握手窗口排队的 invoke 报"不可达"自包含原因，不再落 AttributeError 文案）；③ 轮尾 _status.json 写盘与 sync_after_refresh 分阶段兜底（持续写盘失败不再冻结校验表与 description）；④ _notify_list_changed 两线各自 try（换自定义 bus 不阻断另一线）；⑤ start() 开头主动清 client（不变式对任何调用点成立）；⑥ probe listener 生命周期 try/finally、invoke JSON 容错、kill/start 改前缀匹配；⑦ 测试证据补齐：middleware 捕获 session 真实断言、broken e2e stderr 断言、真实半死 HTTP 超时穿透、真实"启动死→刷新复活"、stop 显式断言；⑧ 首刷时序措辞对齐（实现为先跑后睡、首轮 t0，制品原文写反）；architecture 键表与批次文档的 handshake_timeout 适用面残留修正。
