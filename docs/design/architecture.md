# 架构

## 黄金律（5 条，每条防一个死法）

1. **透传**：`invoke_this_tool` 不截断、不设超时、不净化内容。*防：代理变成语义审查者或性能瓶颈。*
2. **索引透明**：索引是明文 JSONL——一服务一文件、一行一工具。*防：黑盒索引；保住"人可直接打开看、可审计"的性质。*
3. **响应是地图不是倾倒**：每次响应只给当下需要的（命中列表或路标），任何死胡同只回路标（服务名方向、收窄提示），永不全量倾倒信息。*防：上下文爆炸从搜索响应的正门复发。*
4. **配置 fail-fast，上游 fail-soft**：配置错误（非法 JSON、缺 mcpServers 键、重复服务名）启动时大声报错退出；上游故障（连不上、启动崩、握手超时）跳过并记日志，绝不阻塞启动。*防：静默错误与启动阻塞。*
5. **纯 Python 零二进制依赖**：搜索引擎、协议、索引全部只用 Python 标准库 + 官方 SDK，不依赖 rg 等任何外部二进制。*防：平台摩擦（Windows 无可用 wheel，实测）与双引擎语义漂移。*

## 结构（三条路径 + 一个注入面 + 连接模型）

```
写路径（启动时 + 定时刷新）:
config.json --fail-fast 校验--> 逐上游握手(stdio/Streamable HTTP/SSE) --fail-soft-->
tools_index/<server>.jsonl（tmp+rename 原子替换, 强制 UTF-8, 一行一工具）
                                ^ 定时刷新(默认600s,可配)顺便自愈
                                  启动时挂掉的上游,恢复后自动补进索引
索引目录基准: 默认 = config 文件同目录下的 tools_index/,
              settings.index_dir 可覆盖(防不同 cwd 产生两套索引)

读路径:
search_mcp_tool(keywords) -->
  AND 全命中 1..阈值(默认30) --> 返回完整定义列表(每条含可复制 target)
  AND=0, OR>0                --> OR 按命中词数排序, 返回样品(默认10条,可配,
                                标注 partial match)
  AND=0, OR=0                --> 服务名路标(名+工具数) + 用法提示
  命中 > 阈值                --> 不返回列表, 提示收窄(报命中数 N, 建议加词
                                或带服务名如 "hr leave")
  空关键词                    --> 用法提示 + 服务名方向

透传路径:
invoke_this_tool(target, params) --> target 解析(见契约) -->
  索引命中且上游活着 --> 参数原样透传, 返回值/错误原样透传
  target 不存在/格式坏/上游已挂 --> 代理级错误(与上游错误可区分)

注入面:
下游 tools/list 时动态生成 search_mcp_tool 的 description:
  静态触发文案(何时该搜) + 服务清单(名+工具数, 超上限降级为部分+总数);
  定时刷新后清单若有变, 向下游发 MCP list_changed 通知触发重拉
```

### 连接模型

- 每个上游常驻一条 `ClientSession`（stdio 上游即常驻子进程；Streamable HTTP 与 SSE 上游即常驻连接，SSE 为事件流长连接）。
- **同一上游的 invoke 调用串行化**（保守默认：stdio 上游的服务端实现不一定支持并发请求）；跨上游并发执行。
- 定时刷新复用现有连接重拉 `list_tools`；连接已死则重连（即 fail-soft 自愈）。
- `handshake_timeout` 只适用握手阶段；invoke 路径无代理级超时（黄金律 1，不矛盾）。
- 下游客户端断开时，代理与其 spawn 的全部 stdio 上游子进程按进程组回收，不留孤儿（Windows 专项处理）。

## 稳定契约

- **工具签名**：`search_mcp_tool(keywords: string[])`；`invoke_this_tool(target: string, params: object)`。params 为自由 JSON 对象（schema 即 `{"type":"object"}`），无参工具可传空对象或缺省；一次只调用一个工具。
- **target 命名空间**：`server__tool`（如 `hr__get_employee`）。解析规则：**从首个 `__` 切分**——左侧为服务名，右侧整体为工具名；config 校验强制服务名不得含 `__`（fail-fast），工具名可含 `__` 不受影响。搜索结果每条给出可原样复制的 target 串，agent 永远不手工拼。
- **config 格式**：与 Claude Code 的 mcpServers 同形（stdio 型 `command/args/env`；HTTP 型 `url/headers`，其中 `"type": "sse"` 表示旧式 HTTP+SSE 传输，缺省为 Streamable HTTP）。空配置语义：**缺 `mcpServers` 键 → fail-fast 报错；`mcpServers` 为空对象 `{}` → 合法启动**，description 提示无上游。配置入口为 argv 参数 `--config <path>`。
- **settings 完整键表**（唯一来源，批次文档只引用不重写）：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `threshold` | int | 30 | 搜索结果返回上限；超过则拒绝返回列表并提示收窄 |
| `refresh_interval` | int | 600 | 定时刷新间隔（秒） |
| `fallback_sample_size` | int | 10 | AND=0 时 OR 兜底返回的样品条数 |
| `description_max_servers` | int | 15 | description 注入服务清单上限，超出降级为部分+总数 |
| `handshake_timeout` | int | 10 | 上游握手超时（秒），仅握手阶段 |
| `index_dir` | str | null | 索引目录；null = config 同目录下 `tools_index/` |

- **JSONL 记录字段**：`server`、`name`、`title`、`description`、`inputSchema`、`annotations`（readOnlyHint/destructiveHint 等破坏性提示必须保留，下游靠它判断工具能否随便调）。`server` 字段兼职搜索的作用域关键词：搜 "hr leave" 即在 hr 服务内找 leave 相关工具。
- **匹配域**：索引行**全部字段**（server/name/title/description/inputSchema 的参数名与参数描述）。服务名关键词只是"恰好命中 server 字段"，不设独立作用域模式。已知噪音：类型词（如 "string"）命中一切——接受（D-22）。
- **匹配语义**：大小写不敏感、子串、非正则（正则元字符按字面处理）；多关键词 AND 优先，AND=0 时 OR 兜底排序（按命中词数降序，标注 partial match）；同分并列按（服务文件名, 行号）定死，保证确定性。
- **0 工具上游**：握手成功但工具数为 0 → 不生成索引文件，服务保留在清单中（显示 0 tools）。
- **性能预算**：1000+ 工具索引搜索 <200ms；超量拒绝路径不做全量格式化、快速返回（B03 冒烟阈值断言）。
- **日志**：只走 stderr。stdout 是 stdio 协议通道，零污染是生死线。
- **响应文本语言**：代理生成的所有提示/路标/用法文本用英文（LLM 通用语）；中文关键词照常可搜，子串匹配语言无关。

## 技术选型

| 项 | 选择 | 备注 |
|---|---|---|
| 语言/运行时 | Python 3.11+ + uv | |
| MCP 库 | 官方 `mcp` SDK（FastMCP 服务端 + ClientSession 连上游） | 上游传输三种：stdio、Streamable HTTP、SSE |
| 搜索引擎 | 纯 Python 行扫描 | 索引仅数 MB，毫秒级；rg 已被否（D-12） |
| 分发 | PyPI 公共包 `grep-mcp-tools`（2026-11 核实未占用，发布前复检） | tag → GitHub Actions → trusted publishing |
| 部署 | 公司内网 `uv tool install grep-mcp-tools==<版本>` | 内网不通 GitHub、通 PyPI（或镜像源，B05 需公司主机实测）；console script 拉起；config.json 留部署机本地 |
| 测试台模型 | deepseek-flash，reasoning_effort=low | 独立 DeepSeek 服务（环境变量配置），与 opencode 无关；模型名开工前核实（D-21） |
