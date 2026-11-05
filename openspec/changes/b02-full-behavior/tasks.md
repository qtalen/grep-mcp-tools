# Tasks

## 1. 连接层：关闭行为与刷新单元

- [x] 1.1 `_http_transport` 给 `streamable_http_client` 传 `terminate_on_close=False`（D-27），并补 transport 层断言：不可达 HTTP 上游的单连接关闭不等 30s（容差异断言 <10s；进程级退出耗时用例归 B03 E 区，两处分层不重复）；验证对应测试通过
- [x] 1.2 先写失败单测（`FakeConnection` 记录调用序列）再实现 `UpstreamConnection.refresh(timeout, registry, index_root)`：锁被占用（模拟长 invoke）→ 跳过不等待（该上游维持上一轮快照）；`client` 为 None/任务已死 → stop+start 同回合自愈；活着 → 复用 client 在 `asyncio.timeout(handshake_timeout)` 内重拉 `list_tools`，拉取失败或超时 → stop+start 一次重试、终态即本轮结果；验证单测绿
- [x] 1.3 `handshake_all` 返回值改为全部上游连接（config 镜像，不再过滤 `client is None`），`close_all` 回收全部；既有单测/e2e 相应调整后全绿；验证 `uv run pytest tests/unit/` 通过
- [x] 1.4 真实 `UpstreamConnection` 两类单测（FakeConnection 测不到）：a) 曾 live → 重握手失败 → 断言 `client` 为 None 且 invoke 得"不可达"代理级错误（非"工具不存在"）；b) tiny stdio 子进程 terminate → refresh 恢复往返（stdio 自愈无传输特判）；验证单测绿
- [x] 1.5 刷新写盘失败不谎报：一轮刷新中索引写盘失败（只读目录注入）→ 该上游记 unreachable 且原因自包含、注册表与索引文件不出现"live 但无索引"态；验证单测绿

## 2. 刷新循环与状态同步

- [x] 2.1 先写失败单测再实现刷新循环：启动握手轮由 `run_proxy` 同步 await（急切契约），完成后起刷新任务（循环体 = 跑一轮 → 睡 `max(0, refresh_interval - 本轮耗时)`，首轮 t0 立即执行；轮内 `asyncio.gather` 并发 refresh → 轮尾同步块：重写 `_status.json`、`ProxyServer` 重建 `_known_tools`、计算 roster 签名）；轮与轮串行不重叠；验证假连接单测断言调用序与轮计数
- [x] 2.2 轮级异常兜底：单轮内异常（含轮尾 `_status.json` 写盘失败，如 Windows 文件被占用）只记一行 stderr、刷新循环不死，下一轮照跑；兜底含重注册块；验证单测注入异常后断言循环存活
- [x] 2.3 先写失败单测再实现 invoke 判死单条件化：`ProxyServer` 以 config 镜像路由（`connection.client is None` → 代理级错误自包含注册表原因）；补单测"启动时死→假刷新恢复→invoke 成功"路径；验证单测绿
- [x] 2.4 先写失败单测再实现"target 校验随刷新保鲜"：刷新后新工具可调、已删工具拒绝、刷新前维持旧校验（三场景用假连接+临时索引文件驱动）；验证单测绿
- [x] 2.5 刷新轮日志纪律：正常轮 stderr 零新增、失败轮一行（服务名+原因）、stdout 零污染（单测捕获 stderr 断言）；验证单测绿
- [x] 2.6 退出时序：`run_proxy` finally 先 cancel 刷新任务并 await 结束、再 `close_all`；单测断言 cancel 先于连接回收；验证单测绿

## 3. description 动态注入（纯函数 TDD）

- [x] 3.1 先写失败单测再实现 `render_search_description(...)`：静态触发文案+字典序清单（名+工具数）、超 `description_max_servers` 降级为前 N+`(of M)` 总数、`(k unavailable)` 后缀+指针、全挂文案、空配置文案（对照 tool-description spec 全场景）；验证单测绿
- [x] 3.2 `ProxyServer` 构造时以初始 roster 渲染并注册 `search_mcp_tool`；单测断言 tools/list 的 description 含清单；验证单测绿

## 4. list_changed 双线通知（D-25）

- [x] 4.1 先写失败单测再实现签名 diff 与重注册：签名 = 可见服务 `sorted{(server, tool_count)}` + live 总数 + 不可达计数；不变→不重注册不发通知；变化→`remove_tool`+重注册新 description，两步之间无 await；超上限时被隐藏服务工具数变化不触发；验证假连接单测绿
- [x] 4.2 实现旧代线：middleware 捕获 `ctx.session` 存服务端字段，签名变化时 `send_tool_list_changed()`（异常吞掉）；wrap `_lowlevel_server.create_initialization_options` 强制 `NotificationOptions(tools_changed=True)`，单测断言 initialize 能力位 `tools.listChanged` 为 true 及 session 方法被调/不被调；验证单测绿
- [x] 4.3 实现新代线：构造 `MCPServer` 注入自定义 SubscriptionBus，签名变化时 publish `ToolsListChanged`（无 listener 静默）；单测断言 publish 调用与无 listener 不报错；验证单测绿

## 5. 假上游 fixture

- [x] 5.1 `tests/e2e/demo_servers/fleet_gen.py`：Streamable HTTP、程序化 42 个 `region_<area>_report` 工具（"region" 植入）、绑 127.0.0.1+随机端口入参；验证代理握手后索引恰 42 行
- [x] 5.2 `tests/e2e/demo_servers/docs.py`：SSE 型 6 工具（形态照抄 audit.py）；验证经 SSE 握手并落 6 行索引
- [x] 5.3 `tests/e2e/demo_servers/broken.py`：启动即退出（非零退出码）；验证代理对其 fail-soft 跳过、stderr 有失败日志、其余上游不受影响（e2e）

## 6. probe 升级

- [x] 6.1 probe 演示 config 升级 5 上游（hr/itops/fleet_gen/docs/broken）+ `refresh_interval: 30`；验证 probe 拉起后 5 上游握手、stderr 摘要符合 B01 版本措辞（`x/y upstreams indexed`，本批不改文案）
- [x] 6.2 新增 `tools`（拉 tools/list 打印 `search_mcp_tool` description 验证注入）、`kill <server>`/`start <server>`（terminate/重启 probe 自己 Popen 的 itops）子命令，并给 probe 的 Client 挂 message handler 打印收到的 `notifications/tools/list_changed`（list_changed 演示可观测）；验证三命令与通知打印在管道驱动下可用

## 7. 集成验收

- [x] 7.1 全量 `uv run pytest` 绿（含既有用例无回归）；验证零失败
- [x] 7.2 按 B02 验收画面手动走查：启动（broken 跳过+日志、4 上游索引、description 清单含 `(1 unavailable)`）→ `kill itops` → 等一轮刷新（itops 翻 unreachable、计数 1→2、`_status.json` 同步、probe 打印 list_changed 通知）→ `start itops` → 下一轮恢复（清单回归、恢复通知）；验证逐项符合并记录到 design.md 记录节
- [x] 7.3 核对无漂移：architecture/decisions 引用（D-25/D-27 已在台账；handshake_timeout 适用面扩展已回写）、`config.example.json` 未动、开发测试期 `refresh_interval` 统一 30s 的临时 config 不入库；验证核对结论入记录

## Workflow follow-up

- 审核通过并实施完成后归档本 change（`openspec archive`），批次文档与 roadmap 指针随归档更新（B02→B03）。
- B03 开工时承接：E2E 全矩阵、新代协议全链路用例、进程级退出耗时断言、startup summary `connected` 措辞。
- SDK 升级复验点：`_lowlevel_server.create_initialization_options` wrap 与 `NotificationOptions` 默认值（钉 `mcp==2.3.0`）。
