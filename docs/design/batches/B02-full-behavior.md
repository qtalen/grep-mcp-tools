# B02 full-behavior —— 行为面补全

> 状态：⬜ 未开工（B01 已归档 2026-11-04）
> 形态：功能批

## 验收画面

config 挂 5 个假上游（hr stdio、itops Streamable HTTP、fleet_gen Streamable HTTP 型、docs SSE 型、broken 启动即崩——命名与传输对齐 B01/B03 假上游全家）：broken 被跳过、stderr 日志有记录、其余四个正常索引；tools/list 中 `search_mcp_tool` 的 description 含服务清单 "hr (8 tools), itops (12 tools), fleet (42 tools), docs (6 tools)... (1 unavailable)"；把刷新间隔调到 30s 演示：杀掉 hr 进程后等一轮刷新 → hr 自动回到索引、可搜可调，注册表与 `tools_index/_status.json` 同步翻转；服务清单变化时下游收到 list_changed 通知、重拉 tools/list 看到新清单。

## 边界

- 不做治理/白名单、不做代理 HTTP 形态（Non-Goals）
- 不做上游 list_changed 事件驱动的即时重索引（定时为准，D-17）
- E2E 全面自动化留 B03，本批以 probe + 手动验收为主

## 实施要点

- **三传输行为面**（握手与调用已入 B01，本批补时间维度行为）：Streamable HTTP 与 SSE 上游参与定时刷新、自愈、list_changed 的路径与 stdio 完全一致（同一抽象下，不得分传输特判）。
- **fail-soft 自愈**：启动时的跳过+日志已在 B01（健康注册表）；本批补自愈——定时刷新对 unreachable 上游重试握手，恢复则补进索引、注册表翻转为 live。握手成功但 0 工具的上游：不生成索引文件、保留在清单（0 tools，见 architecture 契约）。`settings.handshake_timeout` 仅握手阶段（默认值见 architecture 键表；与黄金律 1 的"invoke 无超时"不冲突）。
- **定时刷新**：`settings.refresh_interval`（默认值见 architecture 键表）；刷新 = 对每个上游重试握手（活着就复用连接重拉 list_tools）+ 原子重写 jsonl；启动时挂掉的上游在刷新中恢复则自动补进索引（自愈）；jsonl 是替换不是追加；刷新同步更新健康注册表与 `tools_index/_status.json`。
- **下游 list_changed**：刷新后若服务清单（哪些服务在线+工具数）发生变化，向下游发 MCP list_changed 通知，触发客户端重拉 tools/list。
- **description 动态注入**：静态触发文案（英文，"何时该搜"的行为触发措辞——这是产品第一 UX 面，起草文案需认真，B04 将消融验证）+ 服务清单（名+工具数）；服务数超 `settings` 上限 → 降级显示部分+总数；fail-soft 的服务不计入清单，有死者加 `(k unavailable)` 计数后缀（D-23：计数+指针，不给详情）；空配置时 description 自洽（提示无上游）。
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
