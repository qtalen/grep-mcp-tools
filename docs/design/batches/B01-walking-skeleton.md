# B01 walking-skeleton —— 最小代理全链路 + 交互探针

## 验收画面

`uv run python tests/e2e/probe.py` 一条命令：代理以 stdio 拉起、连上 2 个 stdio 假上游（hr / itops）、完成握手建索引。在探针里输入 `leave` → 看到 AND 命中列表（每条含可复制 target、description、参数及类型）与上下文账单（本次结果 ~X token vs 直挂 2 上游 ~Y token 常驻）；输入 `invoke hr__xxx ...` → 透传成功返回；输入 `region` 或乱词 → 看到超量/零命中路标提示（演示用 config 设 `settings.threshold=5`，20 个工具即可触发超量路径；此为演示值，契约默认仍为 30）。

## 边界（本批不做什么）

- 只支持 stdio 上游（Streamable HTTP 与 SSE 留 B02）
- 无定时刷新、无 fail-soft 自愈（留 B02）
- 无 description 动态注入（留 B02）
- 错误处理覆盖主路径即可，长尾细节（超时、孤儿进程等）留 B03 用例驱动
- 不发包、不写 README 部署节（留 B05）

## 实施要点

- **工程骨架**：`pyproject.toml`（uv，模块名 `grep_mcp_tools`，console script `grep-mcp-tools`，依赖官方 `mcp` SDK）；`--config <path>` argv 参数。
- **config 解析与 fail-fast 校验**：Claude Code mcpServers 同形 + `settings` 块（键表见 architecture）；非法 JSON / 缺 `mcpServers` 键 / 重复服务名 / 服务名含 `__` / 字段类型错 → 启动时报错退出，信息明确；`mcpServers: {}` 为合法启动。
- **握手建索引**：逐上游 stdio 握手 → `tools_index/<server>.jsonl`，一行一工具：`server/name/title/description/inputSchema/annotations`；`json.dumps` 保证单行（description 含换行也安全）；强制 `encoding="utf-8"`；tmp+rename 原子替换。
- **搜索引擎**：完整实现 architecture『读路径』语义机及其全部契约（匹配域、AND/OR 优先级、阈值与样品数、并列排序定死、partial 标注、性能预算——均以 architecture 为唯一来源，本文件不重复数值）。
- **结果格式化**：每条含 target（可原样复制）、description、参数及类型描述；响应文本英文。
- **invoke_this_tool**：target 解析 `server__tool`（恰一个 `__` 分隔）；params 为自由 JSON 对象原样透传；上游错误原样透传；target 不存在/格式坏 → 代理级错误（文案与上游错误可区分，提示从搜索结果复制 target）。
- **日志**：全部走 stderr（stdout 是协议通道）。
- **probe.py**：subprocess+stdio 拉起代理（真实启动路径）；交互循环（输关键词→显示结果；`invoke <target> <json>`→透传）；每次搜索后打印上下文账单（结果体积 chars/4 估算 vs 直挂常驻估算）+ 索引文件位置。
- **假上游 ×2**：`tests/e2e/demo_servers/hr.py`（8 个工具：员工/请假/薪酬）、`itops.py`（12 个工具：工单/部署/日志）。机关：两服务各有一个同名 `search` 工具（验证命名空间）；itops 有一个描述只写 "audit trail retention" 的工具（验证 OR 兜底/路标）；一个 `firehose` 工具返回 100KB（透传演示）；一个 `boom` 工具抛错（错误透传）。
- `config.example.json` 入仓库当模板（真实 config 不入库）。

## 依赖与关联

- 前置批次：无
- 关联决策：D-01~D-07、D-09、D-10、D-12、D-14、D-15、D-18

## 记录

- 2026-11-04 落盘。OR 兜底样品数默认 10 为落盘时拟定值（D-04），实施时若探针实测不合适可调，调整需回写台账。
- 2026-11-04 审核修订：超量演示阈值说明（threshold=5 演示值）；读路径语义机去双写（唯一来源=architecture）；config 校验补服务名 `__` 禁令；补引 D-14。
