# tool-invoke Specification

## Purpose
定义 `invoke_this_tool` 的透传语义：target 命名空间解析、参数与返回值原样透传、错误两分（代理级 vs 上游级）、同一上游串行化。代理不是语义审查者，也不是性能瓶颈。

## Requirements
### Requirement: target 命名空间与解析

target SHALL 为 `server__tool` 形态（如 `hr__get_employee`），解析规则为从首个 `__` 切分——左侧为服务名，右侧整体为工具名（工具名自身可含 `__`）；服务名不得含 `__` 由 config 校验（显式 `__` 禁令）保证。搜索结果每条给出可原样复制的 target，agent 无需也不应手工拼接。

#### Scenario: 正常解析

- **WHEN** 以 target `hr__get_employee` 调用且 hr 在 config
- **THEN** 解析为服务 hr、工具 get_employee 并路由

#### Scenario: 工具名含双下划线

- **WHEN** 以 target `itops__run__deploy` 调用
- **THEN** 从首个 `__` 切分：服务 itops、工具 `run__deploy`

#### Scenario: 无分隔符报错

- **WHEN** 以不含 `__` 的 target 调用
- **THEN** 返回代理级错误，提示 target 格式与从搜索结果复制

### Requirement: 透传黄金律

参数 SHALL 原样透传给上游（空对象、多余键、深嵌套结构均交上游自行校验，代理不拦截）；上游返回值与错误 SHALL 原样透传给下游——不截断、不设代理级超时、不净化内容。

#### Scenario: 正常调用透传

- **WHEN** 以合法 target 与参数调用且上游正常返回
- **THEN** 下游收到上游返回值原样

#### Scenario: 上游错误原样

- **WHEN** 上游工具抛错（如 boom 工具）
- **THEN** 下游收到的错误为上游错误原样，与代理级错误可区分

#### Scenario: 100KB 大响应不截断

- **WHEN** 上游返回约 100KB 内容（firehose 型工具）
- **THEN** 下游收到完整内容，无截断或净化

#### Scenario: 慢工具不提前掐断

- **WHEN** 上游工具耗时较长（如 sleep 5s）
- **THEN** 代理不因超时提前返回错误

#### Scenario: 多余参数交上游校验

- **WHEN** params 含上游 schema 未定义的键
- **THEN** 代理原样转发，由上游接受或拒绝

### Requirement: 代理级错误两分

两类代理级错误 SHALL 与上游错误可区分：其一，target 格式坏、服务不在 config、工具不在该服务索引——提示从搜索结果复制 target；其二，服务在 config 但本轮不可达——错误信息自包含原因（来自健康注册表，如连接拒绝原文），不得要求 agent 另行查询状态。

#### Scenario: 未知服务

- **WHEN** 以 target `ghost__tool` 调用且 ghost 不在 config
- **THEN** 返回代理级错误，说明服务未配置

#### Scenario: 未知工具

- **WHEN** 以 target `hr__no_such_tool` 调用
- **THEN** 返回代理级错误，说明工具不存在并建议搜索

#### Scenario: config 内但不可达

- **WHEN** 以死条目服务的 target 调用
- **THEN** 返回代理级错误，自包含该服务不可达的原因，与"服务不在 config"文案可区分

### Requirement: 同上游串行化与跨上游并发

同一上游的 invoke 调用 SHALL 串行执行（不分传输统一串行）；不同上游的 invoke 调用 SHALL 可并发执行。

#### Scenario: 同上游串行

- **WHEN** 对同一上游同时发起两个 invoke
- **THEN** 两次调用按序完成，无并发交错

#### Scenario: 跨上游并发

- **WHEN** 对两个不同上游各发起一个慢 invoke
- **THEN** 总耗时近似单个调用而非两者之和

### Requirement: 路由不因启动定格

invoke 的路由表 SHALL 恒含全部配置服务（不因启动握手失败而缺席）；每次调用按该服务当前连接状态判定可达性——不可达时报代理级错误，判定与错误文案不变（原因取自健康注册表）。启动时不可达而经刷新自愈恢复的服务，其 invoke SHALL 正常透传成功。

#### Scenario: 启动时死刷新后可调

- **WHEN** 上游 itops 启动握手失败，一轮刷新后自愈恢复，agent 以 `itops__search` 调用
- **THEN** 调用正常透传并返回上游结果，不因启动时不可达而被拒绝

#### Scenario: 不可达错误文案不变

- **WHEN** 上游 hr 当前不可达，agent 以 `hr__get_employee` 调用
- **THEN** 返回代理级错误，自包含注册表中的不可达原因，与"服务不在 config"文案可区分

### Requirement: target 校验随刷新保鲜

工具存在性校验 SHALL 以最近一轮刷新的索引为准，每轮刷新后无条件重建：上游运行中新增的工具在下一轮刷新后 SHALL 可通过校验并透传；上游已删除的工具在下一轮刷新后 SHALL 得"工具不存在"代理级错误（提示重新搜索）。

#### Scenario: 刷新后新工具可调

- **WHEN** 上游新增工具 `region_cn_report` 且一轮刷新完成，agent 以该 target 调用
- **THEN** 校验通过并透传至上游

#### Scenario: 已删工具刷新后拒绝

- **WHEN** 上游删除工具 `legacy_export` 且一轮刷新完成，agent 仍以旧 target 调用
- **THEN** 返回代理级错误提示工具不存在并建议搜索，不透传给上游

#### Scenario: 刷新前维持旧校验

- **WHEN** 上游已删除某工具但刷新轮尚未到达，agent 以该 target 调用
- **THEN** 校验仍按上一轮索引通过，调用透传后由上游返回其错误（代理不预判）
