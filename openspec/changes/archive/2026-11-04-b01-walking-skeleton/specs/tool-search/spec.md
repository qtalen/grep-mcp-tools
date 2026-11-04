# Spec Delta

## Purpose

定义 `search_mcp_tool` 的读路径语义机：匹配域与匹配语义、AND/OR 优先级、阈值与样品、排序定死、路标与空关键词方向、陈旧索引隔离。响应是地图不是倾倒。

## ADDED Requirements

### Requirement: 工具签名

代理 SHALL 对下游暴露 `search_mcp_tool(keywords: string[])` 工具；keywords 为关键词数组。

#### Scenario: 签名可见

- **WHEN** 下游客户端调用 tools/list
- **THEN** 返回的工具定义中含 `search_mcp_tool`，参数为 `keywords: string[]`

### Requirement: 匹配域为索引行原始 JSON 整行

匹配域 SHALL 为索引行的原始 JSON 整行文本（真·grep），覆盖 `server`/`name`/`title`/`description`/`inputSchema` 全部字段值。服务名关键词只是恰好命中 `server` 字段，不设独立作用域模式。已知接受噪音：字段名与 schema 结构词（如 "string"、"name"）命中一切。

#### Scenario: 只在参数描述里命中

- **WHEN** 关键词仅出现在某工具 inputSchema 的参数描述文本中
- **THEN** 该工具被命中

#### Scenario: 服务名作为关键词

- **WHEN** 关键词为某服务名（如 "hr"）
- **THEN** 该服务全部工具被命中（命中 server 字段）

#### Scenario: 结构词命中一切

- **WHEN** 搜索 "string"
- **THEN** 索引中所有工具均命中（已知特性锁定，非缺陷）

### Requirement: 匹配语义

匹配 SHALL 大小写不敏感、子串、非正则——正则元字符按字面处理；中文等非 ASCII 关键词照常可搜（语言无关）。

#### Scenario: 大小写三态

- **WHEN** 工具描述含 "Leave"，分别搜索 "leave"、"LEAVE"、"Leave"
- **THEN** 三次均命中

#### Scenario: 子串非词边界

- **WHEN** 搜索 "ploy" 且某工具名含 "employee"
- **THEN** 命中

#### Scenario: 正则元字符按字面

- **WHEN** 搜索 "region.*"（字面点号与星号）
- **THEN** 仅命中字面含该串的工具，不做正则展开

#### Scenario: 中文关键词

- **WHEN** 某工具描述含 "请假" 且搜索 "请假"
- **THEN** 命中

### Requirement: AND 优先与超量拒绝

多关键词 SHALL AND 优先：全部关键词命中才计入 AND 集。AND 集大小在 1..`settings.threshold`（默认 30）内 SHALL 返回完整结果列表；超过阈值 SHALL 不返回列表，报命中数 N 并提示收窄（建议加词或带服务名）。

#### Scenario: AND 命中返回

- **WHEN** 搜索 ["hr", "leave"] 且两词同时命中的工具有 2 个
- **THEN** 返回 2 条完整结果

#### Scenario: off-by-one 边界

- **WHEN** AND 命中数恰为阈值 T
- **THEN** 返回完整列表
- **WHEN** AND 命中数为 T+1
- **THEN** 拒绝返回列表并提示收窄

#### Scenario: 阈值可配

- **WHEN** 测试 config 设 `settings.threshold` 高于命中数
- **THEN** 原本被拒的搜索返回列表

### Requirement: OR 兜底与排序定死

AND=0 且 OR>0（至少一个关键词命中）时 SHALL 按 OR 兜底：按命中词数降序返回 `settings.fallback_sample_size`（默认 10）条样品并标注 partial match。排序 SHALL 定死：OR 主键=命中词数降序、同分并列按（服务文件名, 行号）；AND 集命中词数为常数，退化为纯（服务文件名, 行号）位置序。保证任意两次相同搜索结果顺序一致。

#### Scenario: OR 兜底样品与标注

- **WHEN** 搜索 ["kubernetes", "deploy"]，无工具同时含两词，含 "deploy" 的工具有 15 个
- **THEN** 返回按命中词数排序的样品（默认 10 条），响应含 partial match 标注

#### Scenario: OR 排序确定性

- **WHEN** 同一 OR 搜索执行两次
- **THEN** 两次结果顺序完全一致（命中词数降序，同分按服务文件名与行号）

#### Scenario: AND 排序确定性

- **WHEN** 同一 AND 搜索执行两次
- **THEN** 两次结果顺序完全一致（纯位置序）

### Requirement: 双零路标

AND=0 且 OR=0 时 SHALL 返回服务名路标：仅 live 服务的名+工具数名单、一行不可达计数与指针（如 `(k unavailable -- run check_mcp_status)`）加用法提示；死服务名与原因 SHALL NOT 出现在搜索响应中。全部上游不可达（live=0）时路标 SHALL 升为警告语气并给出 check_mcp_status 强指针；完全未配置任何服务（空配置）时 SHALL 明确提示无上游服务已配置，不输出"0 个不可达"的自相矛盾计数。

#### Scenario: 双零路标含计数行与指针

- **WHEN** 搜索乱词且 4 个配置上游中 1 个不可达
- **THEN** 响应含 3 个 live 服务名+工具数、一行 `(1 unavailable -- run check_mcp_status)`，不含死服务名与原因

#### Scenario: 全部不可达警告

- **WHEN** 全部配置上游不可达时搜索任意词
- **THEN** 响应为警告语气，指向 check_mcp_status，不含服务详情

#### Scenario: 空配置路标不自相矛盾

- **WHEN** config 未配置任何上游服务时搜索任意词
- **THEN** 响应明确提示无上游服务已配置，不出现"0 个不可达"计数

### Requirement: 空关键词方向

keywords 为空数组（或全空白）时 SHALL 返回用法提示 + live 服务名单与不可达计数行，不执行搜索。

#### Scenario: 空关键词

- **WHEN** 以 `keywords: []` 调用
- **THEN** 返回用法提示与 live 服务方向，不报错

### Requirement: 结果格式

结果列表每条 SHALL 含可原样复制调用的 target（`server__tool`）、工具描述、参数及类型描述；`annotations.destructiveHint=true` 的条目 SHALL 带 `[destructive]` 标记。代理生成的全部提示/路标/用法文本 SHALL 为英文。

#### Scenario: target 可原样复制

- **WHEN** 从搜索结果复制某条 target 直接调用 invoke_this_tool
- **THEN** 透传成功，无需手工加工

#### Scenario: destructive 标记

- **WHEN** 某命中工具 `annotations.destructiveHint=true`
- **THEN** 该条目带 `[destructive]` 标记

### Requirement: 陈旧索引隔离

搜索 SHALL 只加载本轮（最近一次握手尝试轮）握手成功服务的索引文件；上轮残留的索引文件 SHALL NOT 参与搜索。

#### Scenario: 残留索引不搜出

- **WHEN** 索引目录残留上轮某服务的索引文件，且该服务本轮握手失败
- **THEN** 搜索不返回该服务的任何工具

### Requirement: 性能预算

1000+ 工具规模下搜索 SHALL 在 200ms 内完成；超量拒绝路径 SHALL 快速返回，不做全量格式化。

#### Scenario: 千工具规模

- **WHEN** 索引含 1000+ 工具且执行一次多关键词搜索
- **THEN** 200ms 内返回

#### Scenario: 拒绝路径快速

- **WHEN** 超量命中触发收窄提示
- **THEN** 不格式化任何工具条目即返回
