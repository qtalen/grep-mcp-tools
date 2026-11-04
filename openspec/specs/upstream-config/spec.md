# upstream-config Specification

## Purpose
定义代理启动配置的格式、fail-fast 校验规则与空配置语义。配置是代理的唯一输入面：配置错误必须在启动时大声失败，绝不静默或半启动。

## Requirements
### Requirement: config 入口与条目格式

代理 SHALL 通过 argv 参数 `--config <path>` 读取 JSON 配置文件。文件按 UTF-8 读取并容忍 BOM（Windows 编辑器常见）。格式与 Claude Code 的 mcpServers 同形：`mcpServers` 映射表 + 可选 `settings` 块。stdio 型条目含 `command`/`args`/`env`；HTTP 型条目含 `url`/`headers`，其中 `"type": "sse"` 表示旧式 HTTP+SSE 传输，缺省为 Streamable HTTP。

#### Scenario: stdio 条目解析

- **WHEN** config 中某服务条目含 `command` 与 `args`
- **THEN** 该服务按 stdio 传输解析，启动时以子进程形态握手

#### Scenario: HTTP 条目缺省为 Streamable HTTP

- **WHEN** 某服务条目含 `url` 且无 `type` 键
- **THEN** 该服务按 Streamable HTTP 传输解析

#### Scenario: SSE 条目解析

- **WHEN** 某服务条目含 `url` 与 `"type": "sse"`
- **THEN** 该服务按旧式 HTTP+SSE 传输解析

#### Scenario: 带 BOM 的 config 可读

- **WHEN** config 文件以 UTF-8 BOM 开头
- **THEN** 代理正常解析并启动

### Requirement: 结构错误 fail-fast

以下结构错误 SHALL 在启动时报错并以非零退出码终止，错误信息明确指认问题条目：非法 JSON、config 文件不存在、缺 `mcpServers` 键、重复服务名（大小写不敏感——同一文件系统下 `hr` 与 `HR` 会写同一个索引文件）、字段类型错（如 `mcpServers` 非对象、`command` 非字符串）。

#### Scenario: 非法 JSON

- **WHEN** config 文件内容不是合法 JSON
- **THEN** 启动报错退出，信息含解析失败原因

#### Scenario: 缺 mcpServers 键

- **WHEN** config 顶层无 `mcpServers` 键
- **THEN** 启动报错退出，明确指出缺键

#### Scenario: 重复服务名大小写不敏感

- **WHEN** config 同时定义 `hr` 与 `HR` 两个服务
- **THEN** 启动报错退出，指认重名

#### Scenario: 字段类型错

- **WHEN** 某服务条目的 `command` 值为数字
- **THEN** 启动报错退出，指认条目与字段

### Requirement: 服务名白名单

服务名 MUST 匹配 `^[A-Za-z0-9_-]+$`，且 MUST NOT 含 `__`（target 命名空间切分符；白名单字符类允许单个下划线，不覆盖双下划线，必须显式检查）。白名单同时保证索引文件路径安全；不匹配者 SHALL 在启动时报错退出。

#### Scenario: 服务名含双下划线被拒

- **WHEN** 服务名写作 `hr__legacy`
- **THEN** 启动报错退出，指认非法服务名

#### Scenario: 服务名含路径字符被拒

- **WHEN** 服务名写作 `../evil`
- **THEN** 启动报错退出，指认非法服务名

### Requirement: 传输消歧

传输字段冲突 SHALL 在启动时报错退出：`command` 与 `url` 并存或全无；`type` 取非法值（合法值：缺省、`http`、`sse`、`stdio`，且必须与 command/url 形态相容）；`url` 非 `http(s)://` 协议；`"type": "sse"` 条目同时含 `command`。

#### Scenario: command 与 url 并存

- **WHEN** 某条目同时含 `command` 与 `url`
- **THEN** 启动报错退出，指认传输字段冲突

#### Scenario: 传输字段全无

- **WHEN** 某条目既无 `command` 也无 `url`
- **THEN** 启动报错退出，指认传输字段缺失

#### Scenario: url 协议非法

- **WHEN** 某条目 `url` 为 `ftp://example.com`
- **THEN** 启动报错退出，指认 url 协议

#### Scenario: SSE 条目配 command

- **WHEN** 某条目含 `"type": "sse"` 与 `command`
- **THEN** 启动报错退出，指认冲突

### Requirement: 空配置语义

`mcpServers` 为空对象 `{}` 时 SHALL 合法启动：无上游握手、三工具仍暴露、无上游相关行为自洽（搜索空关键词提示无上游）。缺 `mcpServers` 键则为结构错误（见上）。

#### Scenario: 空对象合法启动

- **WHEN** config 为 `{"mcpServers": {}}`
- **THEN** 代理正常启动，下游可见三工具，check_mcp_status 返回无上游提示

### Requirement: settings 键表

代理 SHALL 接受可选 `settings` 块，键与默认值以 `docs/design/architecture.md` settings 键表为唯一来源（`threshold`/`refresh_interval`/`fallback_sample_size`/`description_max_servers`/`handshake_timeout`/`index_dir`）。缺省键取默认值；值类型错误按结构错误 fail-fast；未知键与小于 1 的整数值同样 fail-fast（拼写保护与防静默怪异行为）；`index_dir` 相对路径按 config 文件同目录解析。

#### Scenario: settings 全缺省

- **WHEN** config 无 `settings` 块
- **THEN** 代理以键表默认值启动

#### Scenario: threshold 覆盖生效

- **WHEN** `settings.threshold` 设为 5，搜索命中 6 条 AND 结果
- **THEN** 返回超量收窄提示而非列表

#### Scenario: 未知 settings 键 fail-fast

- **WHEN** `settings` 含拼写错误的键（如 `thresold`）
- **THEN** 启动报错退出，指认未知键

#### Scenario: 非正整数值 fail-fast

- **WHEN** `settings.threshold` 为 0 或 -1
- **THEN** 启动报错退出

#### Scenario: 相对 index_dir 按 config 目录解析

- **WHEN** `settings.index_dir` 为相对路径且进程 cwd 与 config 目录不同
- **THEN** 索引目录解析为 config 同目录下的该路径
