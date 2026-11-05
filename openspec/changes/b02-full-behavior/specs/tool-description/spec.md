# Spec Delta

## Purpose

定义下游注入面：`search_mcp_tool` 的 description 随上游健康与工具集动态生成，并在服务清单变化时经新旧两代协议通知下游重拉。让 agent 无需盲搜即可看见可用能力边界。

## ADDED Requirements

### Requirement: description 动态生成

`search_mcp_tool` 的 description SHALL 由静态触发文案（英文，"何时该搜"的行为触发措辞）与服务清单动态拼接：清单列出每个 live 服务的名字与工具数（如 `hr (8 tools)`），按服务名字典序排列；unreachable 服务 SHALL NOT 出现在清单中。

#### Scenario: tools/list 含服务清单

- **WHEN** 代理以 4 个 live 上游（hr 8 工具、itops 12 工具、fleet_gen 42 工具、docs 6 工具）启动，下游拉取 tools/list
- **THEN** `search_mcp_tool` 的 description 含按字典序排列的全部 4 个服务名与各自工具数

#### Scenario: 刷新后清单随之变化

- **WHEN** 某上游中途死亡且一轮刷新将其翻为 unreachable 后，下游重新拉取 tools/list
- **THEN** description 的服务清单不再含该服务，且出现不可达计数后缀

### Requirement: 清单上限降级

live 服务数超过 `settings.description_max_servers` 时，清单 SHALL 只显示字典序前 N 个服务并以 `(of M)` 形态标明 live 服务总数 M；N 的选取 SHALL 确定（字典序），不得跨轮抖动。

#### Scenario: 超上限部分显示

- **WHEN** live 服务为 3 个而 `description_max_servers` 为 2
- **THEN** description 只含字典序前 2 个服务，且清单头部含 `(of 3)` 形态的 live 总数标注

#### Scenario: 选取跨轮不抖动

- **WHEN** 连续两轮刷新 live 服务集与工具数均未变化
- **THEN** 降级清单展示的服务集合保持不变

### Requirement: 死者计数后缀与全挂文案

清单存在不可达服务时，description SHALL 追加 `(k unavailable)` 形态的计数后缀并给出 check_mcp_status 指针；死服务名与原因 SHALL NOT 出现在 description 中。全部配置服务不可达时 description SHALL 含 `no upstream services currently connected (k unavailable). Run check_mcp_status for details.`；空配置时 SHALL 含 `no upstream services configured`（两文案可区分"没配"与"配了但全挂"）。

#### Scenario: 有死者加计数后缀

- **WHEN** 4 个 live、1 个 unreachable，下游拉取 tools/list
- **THEN** description 清单后含 `(1 unavailable)` 计数后缀与 check_mcp_status 指针，不含死服务名与原因

#### Scenario: 全挂文案

- **WHEN** 全部 5 个配置上游握手失败，下游拉取 tools/list
- **THEN** description 含 `no upstream services currently connected (5 unavailable). Run check_mcp_status for details.`

#### Scenario: 空配置文案

- **WHEN** mcpServers 为空启动，下游拉取 tools/list
- **THEN** description 含 `no upstream services configured`，无服务清单与计数后缀

### Requirement: 仅签名变化时重注册

代理 SHALL 以服务清单签名判断 description 是否变化，签名为决定 description 文本的可见集合：可见服务的 `sorted{(server, tool_count)}`、live 服务总数、不可达计数三者的组合（未超上限时可见集合即 live 全集）。签名不变时 SHALL NOT 重注册工具、SHALL NOT 发 list_changed 通知；签名变化时 SHALL 以更新后的 description 重注册 `search_mcp_tool`。

#### Scenario: 同签名两轮安静

- **WHEN** 一轮刷新后服务清单签名与上一轮相同
- **THEN** 工具注册不变化，不向下游发 list_changed 通知

#### Scenario: 签名变化重注册

- **WHEN** 某上游工具数从 8 变为 9 且刷新完成
- **THEN** `search_mcp_tool` 以新 description 重注册，此后 tools/list 返回新清单

#### Scenario: 被降级隐藏的服务不触发

- **WHEN** live 服务数超过 `description_max_servers`，且某被隐藏服务的工具数发生变化（可见清单、总数、不可达计数均未变）
- **THEN** 不重注册、不发 list_changed 通知（description 文本未变）

### Requirement: list_changed 双线通知

服务清单签名变化时，代理 SHALL 同时经两线尽力通知下游：旧代协议（initialize 握手）向已建立会话推送 `notifications/tools/list_changed`；新代协议（2026-07-28 subscriptions/listen）发布 `ToolsListChanged` 事件。通知为尽力而为增强：下游不响应或无可达会话时，代理行为 SHALL 不受影响（不重试、不报错）。

#### Scenario: 旧代客户端收通知

- **WHEN** 下游以 initialize 握手连接，某上游死亡使清单签名变化且刷新完成
- **THEN** 该会话收到 `notifications/tools/list_changed`，客户端重拉 tools/list 可见新清单

#### Scenario: 新代客户端收事件

- **WHEN** 下游以新代协议开线并订阅 subscriptions/listen，清单签名变化且刷新完成
- **THEN** 订阅流收到 `ToolsListChanged` 事件，客户端重拉 tools/list 可见新清单

#### Scenario: 通知失败不影响代理

- **WHEN** 清单签名变化时无任何已建立会话（或推送失败）
- **THEN** 代理不重试、不报错，刷新与索引行为照常完成
