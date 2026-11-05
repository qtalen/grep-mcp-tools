# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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
