# Proposal

## Why

B01 交付了最小全链路，但代理是"启动定格"的：`settings.refresh_interval` 已解析未消费，上游中途死亡、工具集变化、服务恢复，代理到重启前都一无所知；`search_mcp_tool` 的 description 也是静态文案，agent 看不到上游服务清单，只能盲搜或背口诀。本 change 交付 B02 full-behavior——补全时间维度行为：定时刷新、fail-soft 自愈、description 动态注入、下游 list_changed 通知。

## What Changes

- **定时刷新轮**：消费 `settings.refresh_interval`（默认 600s，键表既有）；每轮对每个上游活着复用连接重拉 `list_tools`、死了重握手（自愈），原子重写 jsonl、同步翻转健康注册表并重写 `_status.json`。轮语义：串行循环不重叠、慢轮顺延；对每上游 invoke 锁非阻塞抢占，忙则跳过该上游本轮。
- **同回合自愈**：检测到连接任务死亡或活连接重拉失败，同回合内立即 stop+重握手，不等下一轮；启动时挂掉的 upstream 在刷新中恢复即自动补进索引。
- **invoke 状态面与刷新对齐**：`connections` 改为 config 镜像（持全部上游连接对象，死活长在对象上），invoke 判死退化为单条件 `client is None`；`_known_tools`（target 校验表）每轮刷新后无条件重建。
- **description 动态注入**（新能力面）：`search_mcp_tool` 的 description 由静态触发文案 + 服务清单（名+工具数）组成；超 `description_max_servers` 上限降级为字典序前 N+总数；死者不计入清单、以 `(k unavailable)` 计数后缀呈现；空配置与全挂各有自洽文案；仅清单签名变化时重注册工具。
- **下游 list_changed 双线通知**：签名 `sorted{(server, tool_count)}` 变化即通知——旧代协议经 middleware 捕获 session 调 `send_tool_list_changed()`，新代协议向注入的 SubscriptionBus publish `ToolsListChanged`（D-25）；尽力而为，宿主不响应时由搜索路标/check_mcp_status 兜底。
- **连接关闭**：Streamable HTTP 上游 `terminate_on_close=False`（D-27）；退出时序定死先 cancel 刷新循环再 `close_all`。
- **fixture 与探针**：补 fleet_gen（Streamable HTTP，42 工具）、docs（SSE，6 工具）、broken（启动即崩）三个假上游；probe 演示 config 升级 5 上游 + `refresh_interval: 30`，新增 `tools` / `kill` / `start` 三个演示子命令。
- 不做（留后续批次）：E2E 全矩阵自动化（B03）；上游 list_changed 事件驱动即时重索引（D-17，v2）；治理/白名单、代理 HTTP 形态（Non-Goals）。

## Capabilities

### New Capabilities

- `tool-description`: 下游注入面——`search_mcp_tool` description 的动态生成契约（触发文案+服务清单、上限降级、死者计数后缀、空配置/全挂文案、签名变化才重注册）与 list_changed 双线通知（旧代 session 推送 / 新代 SubscriptionBus publish，D-25）。

### Modified Capabilities

- `tool-index`: 新增"定时刷新轮"行为——轮语义（串行不重叠、慢轮顺延）、每上游锁纪律（非阻塞抢 invoke 锁，忙则跳过该上游本轮）、同回合自愈（死亡即 stop+重握手，不等下轮）、刷新沿用 B01 索引纪律（原子替换、0 工具删残留、写盘失败记 unreachable 不谎报 live）。
- `upstream-health`: 注册表语义从"启动快照"扩展为"随刷新轮保鲜"——每轮翻转状态、`_status.json` 每轮重写（updated_at 兼作上次探测时刻）；"本轮"定义保持"最近一次握手尝试轮"，由刷新轮驱动。
- `tool-invoke`: invoke 状态面与刷新对齐——`connections` 为 config 镜像（全上游在场），判死单条件 `client is None`（原因取自注册表）；`_known_tools` 每轮无条件重建，invoke 中上游死亡时传输层错误仍走代理级错误自包含原因。

## Impact

- **代码**：`src/grep_mcp_tools/index.py`（刷新循环、`UpstreamConnection` 重握手、`terminate_on_close=False`）、`server.py`（connections 镜像、`_known_tools` 重建、description 动态注册、middleware/bus 双线）、`health.py`（注册表翻转沿用、`_status.json` 每轮重写）、`__main__.py`/`run_proxy`（刷新任务编排与退出时序）；`tests/unit/` 新增刷新状态机/roster 文案/签名 diff/通知双线（假连接 TDD）；`tests/e2e/` 补 fleet_gen/docs/broken 与 probe 子命令。
- **依赖**：无新增运行时依赖（SDK 2.3.0 既有能力：`remove_tool`/`add_tool`、`ctx.session.send_tool_list_changed()`、`subscriptions=` 构造注入）。
- **语义契约唯一来源**：`docs/design/architecture.md`（settings 键表、注入面、连接模型）；关联 `docs/design/batches/B02-full-behavior.md`（验收画面与实施要点）与 `docs/design/decisions.md`（D-08/D-09/D-13/D-17/D-23/D-25/D-27）。
- **交付面**：`config.example.json` 不动（`refresh_interval: 600` 默认即生产值）；开发/测试期统一 30s，probe 内嵌 30s 归档后保留。
