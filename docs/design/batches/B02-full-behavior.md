# B02 full-behavior —— 行为面补全

## 验收画面

config 挂 5 个假上游（hr stdio、itops stdio、fleet Streamable HTTP 型、docs SSE 型、broken 启动即崩）：broken 被跳过、stderr 日志有记录、其余四个正常索引；tools/list 中 `search_mcp_tool` 的 description 含服务清单 "hr (8 tools), itops (12 tools), fleet (42 tools), docs (6 tools)..."；把刷新间隔调到 30s 演示：杀掉 hr 进程后等一轮刷新 → hr 自动回到索引、可搜可调；服务清单变化时下游收到 list_changed 通知、重拉 tools/list 看到新清单。

## 边界

- 不做治理/白名单、不做代理 HTTP 形态（Non-Goals）
- 不做上游 list_changed 事件驱动的即时重索引（定时为准，D-17）
- E2E 全面自动化留 B03，本批以 probe + 手动验收为主

## 实施要点

- **HTTP 上游两型**：Streamable HTTP 与 SSE（config 中 `"type": "sse"`，旧式传输：事件流 + 消息 POST 端点，官方 SDK `sse_client` 覆盖）握手与调用，与 stdio 同一抽象下；连接模型、定时刷新、fail-soft 对三种传输一致。
- **fail-soft 全套**：上游启动崩/连不上/握手超时（`settings.handshake_timeout` 默认 10s，仅握手阶段，与黄金律 1 的"invoke 无超时"不冲突）→ 跳过+日志；不阻塞其他上游。握手成功但 0 工具的上游：不生成索引文件、保留在清单（0 tools，见 architecture 契约）。
- **定时刷新**：`settings.refresh_interval`（默认 600s）；刷新 = 对每个上游重试握手（活着就复用连接重拉 list_tools）+ 原子重写 jsonl；启动时挂掉的 上游在刷新中恢复则自动补进索引（自愈）；jsonl 是替换不是追加。
- **下游 list_changed**：刷新后若服务清单（哪些服务在线+工具数）发生变化，向下游发 MCP list_changed 通知，触发客户端重拉 tools/list。
- **description 动态注入**：静态触发文案（英文，"何时该搜"的行为触发措辞——这是产品第一 UX 面，起草文案需认真，B04 将消融验证）+ 服务清单（名+工具数）；服务数超 `settings` 上限 → 降级显示部分+总数；fail-soft 的服务不出现；空配置时 description 自洽（提示无上游）。
- **UTF-8 纪律**：全部文件 IO 显式 `encoding="utf-8"`（Windows cp936 防线）；stderr 日志编码同理。
- `config.example.json` 更新为含 HTTP 型与 settings 的完整模板。

## 依赖与关联

- 前置批次：B01
- 关联决策：D-08、D-09、D-13、D-17；架构黄金律 4

## 记录

- 2026-11-04 落盘。
- 2026-11-04 审核修订：握手超时默认值与适用边界、0 工具上游行为、补引 D-09。
- 2026-11-04 修订：上游传输增补 SSE（D-08 修订），验收画面 4→5 假上游（新增 docs SSE 型）。
