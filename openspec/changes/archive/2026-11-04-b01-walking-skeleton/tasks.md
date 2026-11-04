# Tasks

## 1. 工程骨架

- [x] 1.1 建 `pyproject.toml`（uv、src 布局、模块 `grep_mcp_tools`、console script `grep-mcp-tools`、钉版官方 `mcp` SDK）与空包结构；验证 `uv sync` 成功且 `uv run python -c "import grep_mcp_tools"` 无错
- [x] 1.2 配置 pytest + pytest-asyncio（`tests/unit/`、`tests/e2e/` 目录）；验证 `uv run pytest` 可执行（0 收集、退出码 0）

## 2. 开工首验（选型实证，结论回写 design.md 记录节）

- [x] 2.1 首验 A：写最小 spike——FastMCP 服务端返回 content 列表 + `isError=true`，经 ClientSession 往返验证原样转发（含 100KB 载荷）；结论二选一：FastMCP 可用 / 需降级低层 `Server` API；验证 spike 测试通过且 `server.py` 服务端选型定案
- [x] 2.2 首验 B：验证官方 SDK 弃用的 SSE 服务端 transport 仍可被 `sse_client` 握手（最小 SSE 服务 + 握手脚本/测试）；不可用则实现 stdlib 手写最小 SSE fixture（事件流 + 消息 POST 端点）并定案；验证 audit 假上游的 SSE 形态可用

## 3. config 解析与校验（TDD 纯逻辑）

- [x] 3.1 先写失败单测覆盖 spec 场景：结构错误族（非法 JSON / 文件不存在 / 缺 `mcpServers` 键 / 重复服务名大小写不敏感 / 字段类型错）、服务名白名单 `^[A-Za-z0-9_-]+$`、传输消歧五条（`command`+`url` 并存 / 全无 / `type` 非法 / `url` 非 http(s) / sse 配 `command`）；验证单测在实现前置红
- [x] 3.2 实现 `config.py`（解析 + 校验 + 报错文案指认条目）至单测全绿；验证 `uv run pytest tests/unit/ -k config` 通过
- [x] 3.3 先写失败单测再实现 settings 合并：全缺省取键表默认值、`threshold` 覆盖生效、值类型错 fail-fast、`mcpServers: {}` 合法；验证单测绿

## 4. 搜索引擎（TDD 纯逻辑，无 IO）

- [x] 4.1 先写失败单测：匹配语义族（大小写三态 / 子串 / 正则元字符字面 / 中文 / 仅参数描述命中 / 服务名作用域 / 结构词 "string" 命中一切）；验证置红
- [x] 4.2 先写失败单测：AND/OR 优先级、阈值 off-by-one（T 返回 / T+1 拒绝）、阈值可配、OR 样品数与 partial 标注、排序确定性（OR 命中词数降序+并列位置序；AND 纯位置序）、超量拒绝不做全量格式化；验证置红
- [x] 4.3 先写失败单测：双零路标（live 名单 + 不可达计数行 + check_mcp_status 指针、死服务名与原因不在场）、live=0 警告强指针、空关键词方向、结果格式（可复制 target / `[destructive]` 标记 / 英文文案）、陈旧索引隔离（只喂本轮 live 行）；验证置红
- [x] 4.4 实现 `search.py` 至单测全绿；验证 `uv run pytest tests/unit/ -k search` 通过
- [x] 4.5 性能单测：1000+ 索引行多关键词搜索 <200ms、拒绝路径快速返回；验证性能测试通过且不抖动（留余量断言）

## 5. 健康注册表（TDD）

- [x] 5.1 先写失败单测再实现 `health.py` 内存注册表：三态（live N / live 0 / unreachable+原因）、本轮=启动握手轮快照；验证单测绿
- [x] 5.2 先写失败单测再实现 `_status.json` 写盘：最小字段（servers.{name}.{state,tools,reason,updated_at}）、与注册表一致、tmp+rename 原子写、UTF-8；验证单测绿

## 6. 握手与索引（三传输）

- [x] 6.1 先写失败单测再实现 JSONL 写盘器：一行一工具六字段、缺 `title`/`annotations` 写 `null`、description 多行仍单行（`ensure_ascii=False` 中文明文）、原子替换不追加、`index_dir` 覆盖与默认 config 同目录；验证单测绿
- [x] 6.2 实现握手循环：跨上游 `asyncio.gather` 并发 + 每上游 `handshake_timeout` 有限等待、失败跳过+stderr 日志、0 工具上游不落文件、活连接 `AsyncExitStack` 持有；以本地假服务集成验证三传输全部成功、死条目跳过不阻塞（对应 spec 场景）；验证集成单测绿
- [x] 6.3 stderr 启动摘要一行 `x/y upstreams indexed (server FAILED: reason)` 与 stdout 零污染断言；验证单测绿

## 7. 下游服务与三工具

- [x] 7.1 `server.py` + `__main__.py`：按首验 A 选型落服务端，注册三工具（静态英文 description）、`--config` argv、config fail-fast 报错退出；验证最小 ClientSession 握手 + tools/list 见三工具
- [x] 7.2 invoke 路由：target 从首个 `__` 切分、params 原样透传、返回值/错误原样透传、代理级错误两分文案（不在 config vs 在 config 但不可达自包含原因）、每上游锁串行 + 跨上游并发；先写失败单测（含 firehose 100KB、boom 错误、慢工具、并发行为）再实现；验证单测绿
- [x] 7.3 接线 `check_mcp_status`：快照不探测、有死者强提示、全健康安静、空配置自答；先写失败单测再实现；验证单测绿

## 8. 假上游与 config 模板

- [x] 8.1 `tests/e2e/demo_servers/hr.py`：stdio、8 工具（员工/请假/薪酬），含与 itops 同名的 `search`、一个 `destructiveHint=true` 工具、"region" 植入；验证可被代理握手并列出 8 行索引
- [x] 8.2 `tests/e2e/demo_servers/itops.py`：Streamable HTTP、12 工具（工单/部署/日志），含 `firehose`（100KB）、`boom`（抛错）、同名 `search`、"region" 植入；显式绑 127.0.0.1 + 随机端口入参；验证握手成功
- [x] 8.3 `tests/e2e/demo_servers/audit.py`：SSE、2~3 工具，含描述只写 "audit trail retention" 的工具（按首验 B 定案的形态）；验证经 SSE 握手成功
- [x] 8.4 `config.example.json` 入仓库：三传输条目 + settings 全键示例；验证以 config.py 解析通过

## 9. 探针与全链路验收

- [x] 9.1 `tests/e2e/probe.py`：subprocess 拉起 console script + `--config`、临时目录生成 demo config（绝对路径/随机空闲端口/绑 127.0.0.1/dead_http 死条目）、交互循环（关键词→结果、`invoke <target> <json>`、`status`、EOF 退出、可管道驱动）、每次搜索打印上下文账单（chars/4 vs 索引行总字符数/4）与索引目录；验证管道驱动跑通
- [x] 9.2 按 B01 验收画面逐项走查：`leave` AND 命中+账单、`invoke` 透传、`region` 超量收窄（演示 threshold=5）、乱词双零路标、`status` 三活+死条目、stderr 摘要 `3/4 upstreams indexed`；验证逐项符合并记录
- [x] 9.3 核对拟定值与收尾：OR 样品数 10 探针实测是否合适（不合适则调整并回写 decisions/B01 台账）、全量 `uv run pytest` 绿、设计文档无漂移；验证全绿+核对结论入记录
