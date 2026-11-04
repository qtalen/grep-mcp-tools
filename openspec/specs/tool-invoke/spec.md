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
