# B02 full-behavior —— 行为面补全

> 状态：⬜ 未开工（B01 已归档 2026-11-04）
> 形态：功能批

## 验收画面

config 挂 5 个假上游（hr stdio、itops Streamable HTTP、fleet_gen Streamable HTTP 型、docs SSE 型、broken 启动即崩——命名与传输对齐 B01/B03 假上游全家）：broken 被跳过、stderr 日志有记录、其余四个正常索引；tools/list 中 `search_mcp_tool` 的 description 含服务清单 "hr (8 tools), itops (12 tools), fleet_gen (42 tools), docs (6 tools)... (1 unavailable)"；把刷新间隔调到 30s 演示：probe 停掉自己拉起的 itops demo server（probe 持有 Popen 句柄，一行 terminate；hr 等 stdio 型自愈不在手动验收，交 unit 假连接覆盖）→ 等一轮刷新：itops 翻 unreachable、description 的不可达计数从 `(1 unavailable)` 变为 `(2 unavailable)`（broken 恒占 1）；重新启动 itops → 下一轮恢复 live、清单回归，注册表与 `tools_index/_status.json` 同步翻转；服务清单变化时下游收到 list_changed 通知、重拉 tools/list 看到新清单。

## 边界

- 不做治理/白名单、不做代理 HTTP 形态（Non-Goals）
- 不做上游 list_changed 事件驱动的即时重索引（定时为准，D-17）
- E2E 全面自动化留 B03，本批以 probe + 手动验收为主

## 实施要点

- **三传输行为面**（握手与调用已入 B01，本批补时间维度行为）：Streamable HTTP 与 SSE 上游参与定时刷新、自愈、list_changed 的路径与 stdio 完全一致（同一抽象下，不得分传输特判）。
- **fail-soft 自愈**：启动时的跳过+日志已在 B01（健康注册表）；本批补自愈——刷新回合对 unreachable 上游重试握手，恢复则补进索引、注册表翻转为 live。**同回合自愈**：检测到连接任务死亡（或活连接重拉 list_tools 失败）后，在同一回合内立即 stop+重握手，不等下一轮（对齐验收画面"等一轮刷新即恢复"）。握手成功但 0 工具的上游：不生成索引文件、保留在清单（0 tools，见 architecture 契约）。`settings.handshake_timeout` 适用握手与刷新重拉两阶段（默认值见 architecture 键表；与黄金律 1 的"invoke 无超时"不冲突）。
- **定时刷新**：`settings.refresh_interval`（默认值见 architecture 键表；B01 已解析校验，本批消费）；刷新 = 对每个上游重试握手（活着就复用连接重拉 list_tools；重拉与重握手均受 `handshake_timeout` 上界，超时按失败处理，防半死上游挂住整轮）+ 原子重写 jsonl；启动时挂掉的上游在刷新中恢复则自动补进索引（自愈）；jsonl 是替换不是追加；刷新同步更新健康注册表与 `tools_index/_status.json`。**轮语义**：串行循环，轮与轮不重叠，慢轮顺延（实际间隔 = max(interval, 轮耗时)；实现为睡 `max(0, interval - 上轮耗时)`）。**锁纪律**：刷新对每个上游非阻塞抢 invoke 串行锁——长调用进行中（黄金律 1 无超时）则跳过该上游本轮，该上游维持上一轮快照（旧但自洽），绝不阻塞整轮回合。**健壮性**：刷新循环轮级异常兜底——单轮异常（含轮尾 `_status.json` 写盘失败）只记一行 stderr，循环不死。
- **刷新与既有状态的同步**：`connections` 映射改为 config 镜像——持全部上游连接对象（自愈对同一对象 stop/start，身份不变），invoke 判死退化为单条件 `client is None`（原因从注册表取），消除"启动时死→刷新救活"后 invoke 仍查不到的字典脱节；**不变式：任何进入非 live 态的路径（stop、重握手失败）必须把 `client` 置 None**（否则曾 live 后死亡的服务会落到"工具不存在"错误分支）；`_known_tools`（invoke 的 target 校验表）每轮刷新后无条件重建；`_status.json` 每轮重写（updated_at 兼作"上次探测时刻"）。
- **下游 list_changed**：刷新后若服务清单签名变化（签名 = description 文本的决定因素：可见服务 `sorted{(server, tool_count)}` + live 总数 + 不可达计数；未超上限时可见集合即 live 全集，超上限时被降级隐藏的服务不触发无谓通知），向下游发 MCP list_changed 通知，触发客户端重拉 tools/list。**双线机制**（SDK 2.3.0 源码实证）：旧代协议（initialize 握手——v2.3.0 Client 默认与 langchain-mcp-adapters 等真实下游所在）由 middleware 捕获 `ctx.session` 后调 `send_tool_list_changed()`，并 wrap `create_initialization_options` 强制能力位 `tools.listChanged=true`（SDK 默认 NotificationOptions 全 False，严格按能力过滤的客户端会忽略通知）；新代协议（2026-07-28，subscriptions/listen）构造 MCPServer 时注入自定义 SubscriptionBus，刷新时 publish `ToolsListChanged`（subscriptions 非 False 时 `subscriptions/listen` handler 自动注册，新代能力位自动为 true）。**认识**：list_changed 是尽力而为的增强，宿主不响应时 description 停在启动快照；代理义务 = 启动快照准确 + 变化时尽力通知，陈旧由搜索路标/check_mcp_status 兜底。新代全链路验证归 B03。
- **description 动态注入**：静态触发文案（英文，"何时该搜"的行为触发措辞——这是产品第一 UX 面，起草文案需认真，B04 将消融验证）+ 服务清单（名+工具数）；服务数超 `settings` 上限 → 降级显示部分+总数（字典序取前 N，防跨重启抖动）；fail-soft 的服务不计入清单，有死者加 `(k unavailable)` 计数后缀（D-23：计数+指针，不给详情）；空配置时 description 自洽（`no upstream services configured`）。**更新机制**：仅签名变化时重注册（`remove_tool` + `add_tool` 公共 API；SDK 2.3.0 实证 `list_tools()` 每次请求从 ToolManager 读时求值）。**全挂文案**：`no upstream services currently connected (k unavailable). Run check_mcp_status for details.`（与空配置的 configured 措辞可区分）。
- **假上游 fixture**：hr/itops/audit 已有（B01）；本批补 fleet_gen（Streamable HTTP，程序化生成 42 个 region_*_report 工具）、docs（SSE 型，6 工具，参照 audit.py 新建）、broken（启动即崩）；probe 演示 config 升级为 5 上游 + `refresh_interval: 30`，并补三个演示子命令：`tools`（拉 tools/list 并打印 `search_mcp_tool` 的 description，验证服务清单注入）、`kill <server>` / `start <server>`（终止/重启 probe 自己拉起的 demo 进程，驱动死亡与自愈演示）。
- **生命周期**：退出时序定死——先 cancel 刷新循环，再 `close_all`；防刷新重握手与 teardown 竞态留下孤儿 stdio 进程。
- **测试义务**：刷新回合状态机、roster 文案、签名 diff、bus publish 用假连接写 unit（TDD 沿用 B01）；E2E 全矩阵与新旧双代协议全链路验证归 B03，本批以 probe + 手动验收为主。
- **UTF-8 纪律**：全部文件 IO 显式 `encoding="utf-8"`（Windows cp936 防线）；stderr 日志编码同理。

## 依赖与关联

- 前置批次：B01
- 关联决策：D-08、D-09、D-13、D-17、D-23；架构黄金律 4

## 记录

- 2026-11-04 落盘。
- 2026-11-04 审核修订：握手超时默认值与适用边界、0 工具上游行为、补引 D-09。
- 2026-11-04 修订：上游传输增补 SSE（D-08 修订），验收画面 4→5 假上游（新增 docs SSE 型）。
- 2026-11-04 探索修订：HTTP/SSE 握手提前入 B01（本批首项改为三传输行为面一致性，config.example.json 模板随 B01 落地）；fail-soft 拆分为"启动跳过（B01）+ 刷新自愈（本批）"；description 注入补死服务计数后缀；刷新同步注册表与 `_status.json`；补引 D-23。
- 2026-11-04 审核修订：假上游命名与传输对齐 B01/B03（itops=Streamable HTTP、fleet→fleet_gen）；契约默认值改为引用 architecture 键表（去双写）。
- 2026-11-04 B01 归档回写：`settings.refresh_interval` 已在 B01 解析并通过校验（仅解析不消费），本批直接消费；刷新路径必须沿用 B01 既有索引纪律——0 工具上游删旧 jsonl、写盘失败按 fail-soft 记 unreachable（不得谎报 live）、索引目录按 config 目录解析。
- 2026-11-04 探索定案（B02 开工前）：同回合自愈；非阻塞抢锁、忙则跳过该上游本轮；轮串行不重叠；`connections` 改 config 镜像 + invoke 单条件判死；`_known_tools` 每轮重建；签名 `sorted{(server, tool_count)}` 触发 list_changed；description 仅签名变化时重注册（remove_tool+add_tool 公共 API）；通知双线（旧代 middleware 捕获 session / 新代注入 SubscriptionBus）；全挂 description 文案；退出先 cancel 刷新再 close_all；unit 假连接照写、E2E 归 B03；fixture 定名定数 fleet_gen=42 / docs=6 / broken。
- 2026-11-04 refresh_interval 决策：开发测试期统一 30s（含 probe 演示与验收画面）；`config.example.json` 保持默认 600 不动（交付面无需调整）；probe 内嵌演示值 30s 归档后保留；B03 刷新类 E2E 用例钉 1s 控 60s 总预算。
- 2026-11-04 认知补记：description 注入的生效窗口取决于宿主是否响应 list_changed（多数宿主只在连接时拉一次 tools/list），故启动快照准确是第一义务；变更通知为尽力而为，不做"已送达"假设。
- 2026-11-04 设计审核回写：验收画面不可达计数修正（停 itops 后 1→2，broken 恒占 1）；实施要点补 probe 演示子命令（`tools` / `kill` / `start`）；probe 本批升级为 5 上游，B01 画面以归档版本为准。
- 2026-11-04 change 制品审核回写：刷新重拉补 `handshake_timeout` 上界（适用面扩展至"握手与刷新重拉"，回写 architecture）；签名定义改为 description 文本决定因素（可见集合+总数+计数）；`client=None` 不变式显式化（stop/重握手失败必置 None）；刷新循环轮级异常兜底；旧代能力位 `tools.listChanged=false`（SDK 默认 NotificationOptions 全 False）需 wrap `create_initialization_options` 修正；启动轮由 `run_proxy` 同步 await（守急切契约）。
- 2026-11-04 实施认知：SDK 2.3.0 客户端（直连与 stdio）默认协商 2026-07-28 新代，变更通知只对已开 `subscriptions/listen` 的会话投递（无订阅则零投递）；旧代推送线的受众是 initialize 客户端（langchain 等），新代宿主收通知必须主动 listen。probe 通知演示以 `client.listen(tools_list_changed=True)` 为观察点。
