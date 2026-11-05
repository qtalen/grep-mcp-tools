# upstream-health Specification

## Purpose
定义上游健康注册表三态与其四通道可见性行为：死上游对 agent 可诊断（check_mcp_status 详情）、对人类可观察（stderr 与 _status.json），被动信道只给计数与指针。

## Requirements
### Requirement: 健康注册表三态

代理 SHALL 维护健康注册表，每配置服务一态：`live N tools`（握手成功且 N>0）、`live 0 tools`（握手成功无工具）、`unreachable + 原因`（握手失败，原因自包含）。注册表为握手循环的副产品；"本轮"定义为最近一次握手尝试轮——服务中途死亡在下一轮握手前仍按本轮成功计（仍可搜，invoke 报代理级错误）。

#### Scenario: 三态并记

- **WHEN** 4 个配置上游中 3 个握手成功（其一为 0 工具）、1 个连接拒绝
- **THEN** 注册表含 2 个 live N、1 个 live 0、1 个 unreachable 含原因

### Requirement: check_mcp_status 工具

代理 SHALL 暴露无参工具 `check_mcp_status`，返回健康注册表快照且不主动探测重连：有不可达服务时逐服务列出状态与原因，并强提示 agent 提醒用户不可用服务及原因；全部健康时返回安静清单；空配置时返回 "no upstream services configured"。

#### Scenario: 有死者详情与强提示

- **WHEN** 存在不可达上游时调用
- **THEN** 响应逐服务列出状态、失败原因原文与提醒用户的强提示

#### Scenario: 全健康安静清单

- **WHEN** 全部上游握手成功时调用
- **THEN** 响应为简洁服务清单，无警告噪音

#### Scenario: 空配置自答

- **WHEN** 空配置（mcpServers 为 {}）时调用
- **THEN** 返回 "no upstream services configured"

### Requirement: 被动信道只给计数与指针

被动信道（搜索路标、空关键词方向、description 注入）SHALL 只包含不可达服务的计数与指向 check_mcp_status 的指针；不可达服务的名字与原因 SHALL NOT 出现在被动信道中，详情只在 check_mcp_status 提供。

#### Scenario: 路标只含计数与指针

- **WHEN** 搜索双零触发服务名路标且存在不可达上游
- **THEN** 路标含不可达计数行与 check_mcp_status 指针，不含死服务名与原因

### Requirement: stderr 启动摘要与 stdout 零污染

代理 SHALL 在启动完成时向 stderr 输出一行摘要（如 `x/y upstreams connected (server FAILED: reason)`）；`connected` 定义为握手成功（含 0 工具上游），失败者附原因；全部日志 SHALL 只走 stderr，stdout 作为 stdio 协议通道 SHALL 零污染。

#### Scenario: 摘要行

- **WHEN** 4 个上游中 3 个握手成功（其一为 0 工具）、1 个失败
- **THEN** stderr 含一行 `3/4 upstreams connected` 与失败者及原因

#### Scenario: stdout 零污染

- **WHEN** 代理经历启动、搜索、invoke 全流程
- **THEN** stdout 只含 MCP 协议消息，无任何日志混入

### Requirement: _status.json 持久快照

代理 SHALL 在索引目录写入 `_status.json` 人类侧快照，最小字段为 `{servers: {<name>: {state, tools, reason, updated_at}}}`；与索引文件同纪律：tmp+rename 原子写、UTF-8；启动时与每轮刷新后各写入一次，内容与内存注册表一致（`updated_at` 兼作该服务上次探测时刻）。

#### Scenario: 字段齐全且一致

- **WHEN** 启动完成存在失败上游
- **THEN** `_status.json` 每服务含 state/tools/reason/updated_at，state 与 check_mcp_status 一致

#### Scenario: 刷新后重写

- **WHEN** 一轮刷新使某上游从 unreachable 翻转为 live
- **THEN** `_status.json` 被重写，该服务 state 为 live、reason 为 null、updated_at 不早于翻转前的值，其余字段与内存注册表一致

#### Scenario: 原子写

- **WHEN** `_status.json` 被写入或更新
- **THEN** 观察者任一时刻读到的文件为完整快照，不出现半写状态

### Requirement: 注册表随刷新轮翻转

健康注册表 SHALL 随每轮刷新翻转状态：unreachable 上游重握手成功翻转为 live（含工具数）；live 上游检测死亡（重握手失败）翻转为 unreachable 且原因自包含。既有"本轮"语义保持——服务中途死亡在下一轮刷新前仍按本轮成功计（仍可搜，invoke 报代理级错误）。

#### Scenario: 恢复翻转

- **WHEN** unreachable 上游在刷新轮中重握手成功且带 12 个工具
- **THEN** 注册表该服务翻转为 live 12 tools，check_mcp_status 与 `_status.json` 同步体现

#### Scenario: 死亡翻转

- **WHEN** live 上游服务进程被终止，下一轮刷新重握手失败
- **THEN** 注册表该服务翻转为 unreachable 且原因自包含（如进程退出、连接拒绝原文），服务清单不再计入该服务

### Requirement: 刷新轮日志纪律

刷新轮正常完成时 SHALL 向 stderr 零输出（不打扰）；轮内某上游重握手失败 SHALL 向 stderr 记一行失败日志（沿用握手失败文案形态，含服务名与原因）；stdout 零污染纪律对刷新路径同样生效。

#### Scenario: 正常轮安静

- **WHEN** 一轮刷新全部上游成功
- **THEN** stderr 无新增输出

#### Scenario: 失败轮出声

- **WHEN** 一轮刷新中某上游重握手失败
- **THEN** stderr 含一行该服务握手失败日志（服务名+原因），代理继续服务
