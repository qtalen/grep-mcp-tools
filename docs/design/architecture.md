# 架构

## 黄金律（5 条，每条防一个死法）

1. **透传**：`invoke_this_tool` 不截断、不设超时、不净化内容。*防：代理变成语义审查者或性能瓶颈。*
2. **索引透明**：索引是明文 JSONL——一服务一文件、一行一工具。*防：黑盒索引；保住"人可直接打开看、可审计"的性质。*
3. **响应是地图不是倾倒**：每次响应只给当下需要的（命中列表或路标），任何死胡同只回路标（服务名方向、收窄提示），永不全量倾倒信息。*防：上下文爆炸从搜索响应的正门复发。*
4. **配置 fail-fast，上游 fail-soft**：配置错误（非法 JSON、缺 mcpServers 键、重复服务名）启动时大声报错退出；上游故障（连不上、启动崩、握手超时）跳过并记日志，绝不阻塞启动。*防：静默错误与启动阻塞。*
5. **纯 Python 零二进制依赖**：搜索引擎、协议、索引全部只用 Python 标准库 + 官方 SDK，不依赖 rg 等任何外部二进制。*防：平台摩擦（Windows 无可用 wheel，实测）与双引擎语义漂移。*

## 结构（三条路径 + 一个健康通道 + 一个注入面 + 连接模型）

```
写路径（启动时 + 定时刷新）:
config.json --fail-fast 校验--> 逐上游握手(stdio/Streamable HTTP/SSE) --fail-soft-->
tools_index/<server>.jsonl（tmp+rename 原子替换, 强制 UTF-8, 一行一工具）
                                ^ 定时刷新(默认600s,可配)顺便自愈
                                  启动时挂掉的上游,恢复后自动补进索引
索引目录基准: 默认 = config 文件同目录下的 tools_index/,
              settings.index_dir 可覆盖(防不同 cwd 产生两套索引)
健康注册表: server -> live N 工具 / live 0 工具 / unreachable+原因
            (握手循环副产品; B01 为启动快照, B02 刷新翻转状态;
             "本轮"=最近一次握手尝试轮: B01 即启动轮, B02 起含每次刷新轮,
             中途死亡在下一轮握手前仍按本轮成功计——仍可搜, invoke 报错)
            驱动: 路标与 description 后缀(计数) / check_mcp_status(详情) /
                  invoke 错误文案 / stderr 启动摘要一行 /
                  tools_index/_status.json(人类侧持久快照)
            _status.json 最小字段: {servers: {<name>: {state, tools,
                  reason, updated_at}}}; 与 jsonl 同纪律:
                  tmp+rename 原子写, UTF-8
            一致性规则: 搜索只加载本轮握手成功服务的索引文件(陈旧索引隔离)

读路径:
search_mcp_tool(keywords) -->
  AND 全命中 1..阈值(默认30) --> 返回完整定义列表(每条含可复制 target;
                                destructiveHint=true 条目带 [destructive] 标记)
  AND=0, OR>0                --> OR 按命中词数排序, 返回样品(默认10条,可配,
                                标注 partial match)
  AND=0, OR=0                --> 服务名路标(仅 live 服务: 名+工具数; 有死者加
                                一行 "(k unavailable -- run check_mcp_status)")
                                + 用法提示; live=0 --> 警告语气 + 强指针
  命中 > 阈值                --> 不返回列表, 提示收窄(报命中数 N, 建议加词
                                或带服务名如 "hr leave")
  空关键词                    --> 用法提示 + 服务方向(live 名单 + 不可达计数行)

健康通道:
check_mcp_status() --> 健康注册表快照(不主动探测重连):
  有死者 --> 逐服务状态+原因 + 强提示(agent 须提醒用户不可用服务及原因)
  全健康 --> 安静清单; 空配置 --> "no upstream services configured"
  惯例: 被动信道(路标/description)只给计数与指针, 主动信道给详情

透传路径:
invoke_this_tool(target, params) --> target 解析(见契约) -->
  索引命中且上游活着 --> 参数原样透传, 返回值/错误原样透传
  target 不存在/格式坏/服务不在 config --> 代理级错误(与上游错误可区分)
  服务在 config 但不可达/上游已挂     --> 代理级错误, 自包含原因(注册表)

注入面:
下游 tools/list 时动态生成 search_mcp_tool 的 description:
  静态触发文案(何时该搜) + 服务清单(名+工具数; 超上限按服务名字典序取前 N
  并加 "(of M)" 后缀——选取定死, 防跨重启抖动触发假 list_changed;
  死服务不计入清单, 有死者加 (k unavailable) 计数后缀);
  定时刷新后清单若有变, 向下游发 MCP list_changed 通知触发重拉:
    旧代协议(initialize 握手) 经 middleware 捕获的 session 调
      send_tool_list_changed(); 新代协议(2026-07-28) 向注入的
      SubscriptionBus publish ToolsListChanged (D-25)。
    通知为尽力而为: 宿主不响应时 description 停在启动快照,
    由搜索路标 / check_mcp_status 兜底
```

### 连接模型

- 每个上游一条常驻 `Client`（stdio 上游即常驻子进程；Streamable HTTP 与 SSE 上游即常驻连接，SSE 为事件流长连接）。**生命周期归属**：SDK 传输基于 anyio TaskGroup，enter/exit 必须发生在同一任务——每个上游由一条专用任务持有自己的 `AsyncExitStack`（任务内完成 enter → 握手 → 等待关闭 → 退出），其他任务只通过会话发请求。违反此约束会触发 `RuntimeError: Attempted to exit cancel scope in a different task`。
- **loopback 上游绕过环境/系统代理**：`127.0.0.1`/`localhost`/`::1` 的 HTTP/SSE 连接显式禁用 httpx2 的 `trust_env`（远程上游保持代理行为）。实现证据（B01 探针）：下游宿主以受限环境拉起代理（MCP stdio 默认 env 白名单不含 `NO_PROXY`），httpx2 回落到 Windows 系统代理，把死上游的"连接失败"变成 `Server returned an error response`。
- **上游握手跨上游并发执行**（总时长 ≈ 单上游 handshake_timeout 上限，不随上游数线性增长）；急切但有限等待——应答自身 initialize 前完成握手与索引，不因单个上游故障中止启动。
- **同一上游的 invoke 调用串行化**（保守默认：上游服务端实现不一定支持并发请求；不分传输统一串行，将来按传输放宽属优化非修正）；跨上游并发执行。
- 定时刷新复用现有连接重拉 `list_tools`；连接已死则重连（即 fail-soft 自愈）。
- `handshake_timeout` 适用握手与刷新重拉两个阶段（刷新重拉超时按失败处理走同回合自愈，防半死上游挂住整轮刷新）；invoke 路径无代理级超时（黄金律 1，不矛盾）。**实现口径**：HTTP/SSE 常驻客户端的读超时置无限（连接/写/池超时保留 30s 防挂死），SDK 默认的 300s 读超时被显式覆盖；stdio 无超时。Streamable HTTP 传输关闭时 `terminate_on_close=False`（不发 DELETE 会话终止；本地/内网场景无收益，且不可达上游会吃满 30s connect 超时拖慢退出，见 D-27）。
- 下游客户端断开时，代理与其 spawn 的全部 stdio 上游子进程按进程组回收，不留孤儿（Windows 专项处理；B03 已验证）。

## 稳定契约

- **工具签名**：`search_mcp_tool(keywords: string[])`；`invoke_this_tool(target: string, params: object)`；`check_mcp_status()`（无参，返回健康注册表快照，见"健康通道"）。params 为自由 JSON 对象（schema 主体为 `object`；缺省/`null` 在代理内部归一为 `{}`），无参工具可传空对象或缺省；一次只调用一个工具。
- **target 命名空间**：`server__tool`（如 `hr__get_employee`）。解析规则：**从首个 `__` 切分**——左侧为服务名，右侧整体为工具名；config 校验强制服务名不得含 `__`（fail-fast），工具名可含 `__` 不受影响。搜索结果每条给出可原样复制的 target 串，agent 永远不手工拼。
- **config 格式**：与 Claude Code 的 mcpServers 同形（stdio 型 `command/args/env`；HTTP 型 `url/headers`，其中 `"type": "sse"` 表示旧式 HTTP+SSE 传输，缺省为 Streamable HTTP）。**fail-fast 校验清单**：非法 JSON / 缺 `mcpServers` 键 / 重复服务名（大小写不敏感——Windows 文件系统下 hr 与 HR 会写同一个索引文件）/ 字段类型错 / 未知 settings 键（拼写保护）；服务名白名单 `^[A-Za-z0-9_-]+$`（路径安全）+ 显式禁止 `__`（白名单字符类允许单个下划线，不覆盖双下划线，实现必须另检）；传输消歧——`command` 与 `url` 并存或全无、`type` 非法值（合法值：缺省 / `http` / `sse` / `stdio`）、`url` 非 `http(s)://`、`"type": "sse"` 配 `command`，均报错；settings 整数键必须 ≥ 1。空配置语义：**缺 `mcpServers` 键 → fail-fast 报错；`mcpServers` 为空对象 `{}` → 合法启动**，description 提示无上游。配置入口为 argv 参数 `--config <path>`；文件按 UTF-8 读取（容忍 BOM）。
- **settings 完整键表**（唯一来源，批次文档只引用不重写）：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `threshold` | int | 30 | 搜索结果返回上限；超过则拒绝返回列表并提示收窄 |
| `refresh_interval` | int | 600 | 定时刷新间隔（秒） |
| `fallback_sample_size` | int | 10 | AND=0 时 OR 兜底返回的样品条数 |
| `description_max_servers` | int | 15 | description 注入服务清单上限，超出降级为部分+总数 |
| `handshake_timeout` | int | 10 | 上游握手超时（秒），适用握手与刷新重拉两阶段（重拉超时按失败走同回合自愈） |
| `index_dir` | str | null | 索引目录；null = config 同目录下 `tools_index/`；相对路径按 config 文件同目录解析（防不同 cwd 产生两套索引） |

- **JSONL 记录字段**：`server`、`name`、`title`、`description`、`inputSchema`、`annotations`（readOnlyHint/destructiveHint 等破坏性提示必须保留，下游靠它判断工具能否随便调）。`server` 字段兼职搜索的作用域关键词：搜 "hr leave" 即在 hr 服务内找 leave 相关工具。
- **匹配域**：索引行**原始 JSON 整行文本**（真·grep；覆盖 server/name/title/description/inputSchema 参数名与参数描述等全部字段值）。服务名关键词只是"恰好命中 server 字段"，不设独立作用域模式。已知噪音：字段名与 schema 结构词（如 "string"、"name"）命中一切——接受（D-22）。
- **匹配语义**：大小写不敏感、子串、非正则（正则元字符按字面处理）；多关键词 AND 优先，AND=0 时 OR 兜底排序（标注 partial match）；排序定死——**OR 主键=命中词数降序，同分并列按（服务文件名, 行号）；AND 下命中词数为常数 N，退化为纯（服务文件名, 行号）位置序**——保证确定性。
- **0 工具上游**：握手成功但工具数为 0 → 不生成索引文件（若存在上一轮同名文件须删除），服务保留在清单中（显示 0 tools）。
- **性能预算**：1000+ 工具索引搜索 <200ms；超量拒绝路径不做全量格式化、快速返回（B03 冒烟阈值断言）。
- **日志**：只走 stderr。stdout 是 stdio 协议通道，零污染是生死线。刷新轮纪律（B02）：正常轮零输出；轮内某上游重握手失败记一行（服务名+原因）；单轮异常（含轮尾 `_status.json` 写盘失败）记一行且刷新循环不死。
- **退出时序**（B02）：先 cancel 刷新循环并等待其结束，再逆序关闭全部上游连接——防刷新重握手与 teardown 竞态留下孤儿 stdio 进程。
- **响应文本语言**：代理生成的所有提示/路标/用法文本用英文（LLM 通用语）；中文关键词照常可搜，子串匹配语言无关。

## 技术选型

| 项 | 选择 | 备注 |
|---|---|---|
| 语言/运行时 | Python 3.11+ + uv | |
| MCP 库 | 官方 `mcp` SDK 2.x（`MCPServer` 服务端 + 高层 `Client` 连上游；钉 2.3.0） | 上游传输三种：stdio、Streamable HTTP、SSE；v2 API 词汇（`MCPServer`=旧 FastMCP、`Client`=旧 ClientSession 上层、协议类型 snake_case、HTTP 栈 httpx2） |
| 搜索引擎 | 纯 Python 行扫描 | 索引仅数 MB，毫秒级；rg 已被否（D-12） |
| 分发 | PyPI 公共包 `grep-mcp-tools`（2026-11 核实未占用，发布前复检） | tag → GitHub Actions → trusted publishing |
| 部署 | 公司内网 `uv tool install grep-mcp-tools==<版本>` | 内网不通 GitHub、通 PyPI（或镜像源，B05 需公司主机实测）；console script 拉起；config.json 留部署机本地 |
| 测试台模型 | deepseek-flash，reasoning_effort=low | 独立 DeepSeek 服务（环境变量配置），与 opencode 无关；模型名开工前核实（D-21） |

## 记录

- 2026-11-04 探索修订：三工具签名（D-01 修订）；健康注册表与健康通道（D-23）；config 校验清单（含大小写不敏感重名、传输消歧、服务名白名单）；匹配域明确为整行扫描（D-22 补）；排序定死（OR 主键=命中词数、并列位置序）；握手跨上游并发；注入清单字典序选取；`_status.json` 最小契约与原子写；"本轮=最近一次握手尝试轮"定义。
- 2026-11-04 实施回写（B01 开工）：SDK 钉 `mcp==2.3.0`（v2 API，词汇替换 FastMCP→MCPServer、ClientSession→Client）；连接模型补"每上游专用任务持有 AsyncExitStack"（anyio cancel scope 同任务约束，实现证据）与"loopback 上游绕过系统代理"（D-24，探针实测证据）；config 白名单措辞修正（`__` 禁令需显式检查，正则不覆盖）。
- 2026-11-04 审核回写：0 工具上游须删除上一轮索引文件（防幽灵工具与 target 校验失效）；HTTP/SSE invoke 读超时显式置无限（对齐 D-06，覆盖 SDK 默认 300s）；索引写盘失败按 fail-soft 记 unreachable（不得谎报 live）；settings 整数键 ≥1、未知键 fail-fast、相对 `index_dir` 按 config 目录解析；config 读取容忍 BOM；空配置路标改为"no upstream services are configured"。
- 2026-11-04 设计审核回写：注入面补 list_changed 双线机制（D-25，旧代 middleware session / 新代注入 SubscriptionBus）；连接模型补 `terminate_on_close=False`（D-27）与进程组回收的"B03 落地"标注；M2 判据口径见 D-26。
- 2026-11-04 B02 change 审核回写：`handshake_timeout` 适用面扩展至刷新重拉（超时按失败走自愈）；补刷新轮日志纪律与退出时序（cancel 刷新先于 close_all）；旧代能力位 `tools.listChanged=false`（SDK 默认 NotificationOptions 全 False）需 wrap `create_initialization_options` 修正——细则见批次文档与 change design。
- 2026-11-05 B03 归档回写：进程组回收标注由"B03 落地"改为"B03 已验证"（E 区孤儿用例实测达标，SDK Windows Job Object）；性能预算由 F 区冒烟锁定（1000+ 工具搜索实测 18~22ms，阈值断言 200ms）。
