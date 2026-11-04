# B03 e2e-matrix —— 确定性 E2E 全矩阵

## 验收画面

`pytest tests/e2e` 全绿，耗时 <60s，可直接进 CI；覆盖下述六区矩阵全部用例；`uv run python tests/e2e/probe.py` 仍是随时可用的手动演示入口。

## 边界

- 只测确定性契约（代理对不对）；agent 用得好不好归 B04
- 不测真实企业上游（全部用笼中假上游）
- 性能只做冒烟阈值断言，不做基准报告

## 实施要点（六区矩阵）

**假上游全家**（tests/e2e/demo_servers/，FastMCP 各约二十行）：hr、itops、fleet_gen（程序化生成 40+ 个 region_*_report 工具，专测超量）、broken（启动即崩）、http_variant（Streamable HTTP 型）、sse_variant（SSE 型）、以及机关工具：巨型 schema 50KB、慢工具 sleep 5s、中文描述工具、firehose 100KB、boom 抛错。

- **A 启动与索引（16 例）**：多 stdio 握手行数正确；Streamable HTTP 单独/混合；SSE 单独/混合；broken fail-soft；握手超时不阻塞；0 工具上游（不生成索引文件、清单保留 0 tools）；description 含换行/引号/中文仍单行；两服务同名工具互不覆盖；config 不存在/非法 JSON/缺 `mcpServers` 键/未知类型 各有明确错误不静默（fail-fast）；`mcpServers` 空对象为合法启动且 description 提示无上游；重复服务名/服务名含 `__` 拒绝启动；重启后 jsonl 替换不追加+原子性；env 注入上游生效；相对路径/不同工作目录一致（索引目录基准=config 同目录）。
- **B 搜索语义（17 例）**：单词/多词 AND/OR 兜底排序/双零路标；命中 30 返回 vs 31 拒绝（off-by-one 边界）；阈值改 50 后 31 可返回；大小写三态；子串非词边界；关键词=服务名的作用域搜索；关键词只在参数描述里也命中；空关键词用法提示；正则元字符按字面；中文/emoji/超长关键词；返回格式含可复制 target+参数类型；同分排序确定；搜 "string" 命中一切（已知特性锁定，D-10/已接受风险）。
- **C invoke 透传（13 例）**：正常调用；同名工具命名空间分流（hr__search vs itops__search）；上游错误透传；target 不存在/格式坏（无 `__`、空、首段非已知服务）代理级错误；target 在索引但上游挂 → 代理级错误+重启提示；100KB 原样不截断；空参数/多余参数/深嵌套结构交上游校验不拦截；中文参数返回无损；慢工具不提前掐断；并发 invoke 不同上游互不阻塞（同上游串行，见连接模型）；同上游连续调用连接复用不串话。
- **D description 注入（4 例）**：tools/list 含服务清单；超上限降级；fail-soft 服务不出现；空配置自洽。
- **E 协议与生命周期（5 例）**：全程 stdout 零污染（stderr 才有日志）；initialize→tools/list→call_tool 全链路；长会话几十次调用无状态泄漏；客户端断开 → 代理与 stdio 上游子进程全部退出无孤儿（Windows 专项）；搜索进行时索引重写读不到半行。另加：刷新自愈用例（启动挂→刷新后可搜）、DeepAgents 拉起形态用例（按 langchain-mcp-adapters 的 command/args 形态 spawn）。
- **F 规模冒烟（3 例)**：1000+ 工具索引搜索 <200ms；单工具 50KB 巨型 schema 不崩；大索引下拒绝路径快速返回（不做全量格式化）。

**执行结构**：session 级 fixture（标准代理+四假上游，多数用例复用一次拉起）+ function 级 fixture（变体 config 各自拉起）；全部本地 subprocess 无网络。

## 依赖与关联

- 前置批次：B02
- 关联决策：D-06、D-09、D-10、D-12、D-17、D-20、D-22（测试逼出的三条裁决：D-06 超时不设、D-09 重复名 fail-fast、D-22 噪音接受；孤儿进程清理为架构"连接模型"实现要点）

## 记录

- 2026-11-04 落盘。矩阵源自探索阶段逐区枚举（含测试反哺产品设计的裁决）。
- 2026-11-04 审核修订：空配置语义按 architecture 契约拆分（缺键 fail-fast / 空对象合法）；target 解析按"首个 `__` 切分"重写用例表述；引用裁决改为点名编号。
- 2026-11-04 修订：上游增补 SSE（D-08 修订），A 区加 SSE 用例、假上游全家加 sse_variant。
