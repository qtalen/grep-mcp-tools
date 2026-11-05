# B03 e2e-matrix —— 确定性 E2E 全矩阵

> 状态：✅ 已归档 2026-11-05（OpenSpec change：`openspec/changes/archive/2026-11-05-b03-e2e-matrix/`；实施与审核记录见 change design.md）
> 形态：地基批

## 验收画面

`uv run pytest tests/e2e -n 4` 全绿，Windows 本机热跑 <150s（实测 ~130s；串行约 ~300s）；覆盖下述六区矩阵全部用例（collect-only 75 例）；`uv run python tests/e2e/probe.py` 仍是随时可用的手动演示入口。本期不建 CI workflow；`<60s` 判据改作将来 Linux CI 的闸门（D-28）。

## 边界

- 只测确定性契约（代理对不对）；agent 用得好不好归 B04
- 不测真实企业上游（全部用笼中假上游）
- 性能只做冒烟阈值断言，不做基准报告
- 本期不建 CI workflow（本地 Windows 全量绿即验收）

## 执行结构

**黑盒形态**：测试以子进程拉起代理（`python -m grep_mcp_tools --config <tmp>/config.json`），下游用 SDK `Client`（stdio 传输）或裸行 JSON-RPC 客户端驱动——代理、上游全是真实进程，stdout/stderr/退出码都可断言。in-process 传输级测试（原 `test_demo_servers.py` 7 例）迁到 `tests/integration/`，不占 e2e 预算。

```
pytest 进程
   |  spawn: python -m grep_mcp_tools --config X
   |  stdio (JSON-RPC)
下游 Client（SDK / 裸行客户端）
   |
   +-- hr.py（代理 spawn，stdio）
   +-- itops/audit（测试 spawn，HTTP/SSE，显式绑 127.0.0.1）
   +-- dead_http / dead_sse（config 指向无人监听端口）
```

**fixture 编成**：

- **session 级标准代理**：hr（stdio）+ itops（HTTP）+ audit（SSE）+ dead_http；多数 A/B/C/D 用例复用这一次拉起。
- **function 级变体**（各自独享进程）：fail-fast 族（进程即退，不握手）、broken、empty（0 工具）、空配置、全健康（无死条目）、全挂（全部条目指向无人监听端口，live=0）、dead_sse、错路径 404、blackhole（握手超时钉 5s——2s 实测会把健康上游也拖超时）、重启 jsonl 替换、cwd/相对路径、env 注入（gizmos）、description_max_servers=2、阈值 T/T+1 两态、fleet_gen 1000+。
- **刷新类用例的进程编成（实施定案）**：各自独享进程、隔离优先——roster 增删（hr + mutable，interval=1）、自愈+搜索并发重写（hr + mutable + itops，interval=1）、新旧双代通知（`sdk_client` 形态无法共享进程，各起一个）、"中途死亡"两阶段（interval=5，翻转前窗口约 5s）。原设想的"共享刷新簇"未采用：共享进程会引入测试间状态耦合，实测预算在独享形态下仍达标。

**等待纪律（防抖）**：

- 一律 `wait_until(条件, deadline)` 轮询到位即返回（deadline 给足预期 5~10 倍余量），禁止 `sleep(固定秒)` 后断言。
- 刷新轮失败会向 stderr 记一行（服务名+原因）——把它当免费事件源："等 itops 翻 unreachable" = 等该行出现，比数秒数准。
- 计时型断言在 Windows 上统一放宽余量（宁可慢、不可抖）；`windows` marker 用例用更宽的界。

**预算记账**：Windows 本机 150s 验收线（D-28），验收命令 `uv run pytest tests/e2e -n 4`（pytest-xdist 并行；`-n 6` 实测因并发争抢导致健康上游握手超时，不可用）。实测：81 例串行 337s → 合并变体后 75 例串行 ~300s、`-n 4` 129~133s。风险源是进程拉起的边际成本（实测每变体 5~7s，冷跑再贵 30~50%，冷跑值仅观察不设闸门）。超支收敛顺序：合并变体进程 → 放宽计时余量 → 永不砍用例、不把余量压到会抖。

**产物组织**：`tests/e2e/conftest.py`（harness：fixture、裸行客户端、进程清理、端口/等待工具）+ 六区各一文件；`windows` marker 标记孤儿回收/cp936 等平台专项；验收计数以 `pytest --collect-only` 为准。

## 假上游全家

`tests/e2e/demo_servers/`：hr（stdio，8 工具）、itops（Streamable HTTP，12 工具，含 firehose/boom/search/region）、audit（SSE，3 工具）、docs（SSE，6 工具，B02 探针用）、fleet_gen（Streamable HTTP，42 个 region_*_report，加 `--count` 参数供 F 区放大）、broken（启动即崩）；死条目 dead_http/dead_sse 只存在于 config（指向无人监听端口）。

B03 补齐：

- `gizmos.py`（stdio）：机关工具集——巨型 schema 50KB、慢工具 `slow(seconds=5)`（C 区传 2 秒断言不被掐断，默认 5s 留给 probe）、中文描述工具、`echo_env`（A 区 env 注入）、`B03_PID_FILE` 启动时写自身 PID（E 区孤儿判活用）。
- `empty.py`（stdio）：握手成功但 0 工具（A 区 0 工具 + D-23 connected 措辞）。
- `blackhole.py`：裸 socket accept 后挂住、不完成 MCP 握手（A 区 HTTP 握手超时变体）。
- `mutable.py`（Streamable HTTP，lowlevel Server）：`list_tools` 每次调用读一个 JSON 状态文件——测试改文件驱动 roster 变化，与"上游死亡"变量解耦；自愈用例仍用 kill/restart itops。

现有服务器的工具数量（hr=8、itops=12、audit=3、docs=6、fleet_gen 默认 42）被多处断言钉死，机关工具一律不进共享 fixture；docs 仅随旧测试迁移保留，不参与 B03 矩阵 fixture。

## 六区矩阵

**A 启动与索引（~28 例）**：多 stdio 握手行数正确；Streamable HTTP 单独/混合；SSE 单独/混合；broken fail-soft；握手超时不阻塞；HTTP/SSE 不可达族（连接拒绝 / 路径 404 / SSE 端点不可达）fail-soft 且 stderr 摘要含原因；HTTP 握手超时变体（测试 config 钉 5s，blackhole）；check_mcp_status 三例——全健康清单（无死条目变体）/ 有死者强提示+原因在场（共享 fixture）/ 空配置自答；0 工具上游（empty：不生成索引文件、清单保留 0 tools）；description 含换行/引号/中文仍单行（gizmos 中文描述工具）；两服务同名工具互不覆盖；config 不存在/非法 JSON/缺 `mcpServers` 键/未知类型 各有明确错误不静默（fail-fast）；`mcpServers` 空对象为合法启动且 description 提示无上游；重复服务名/服务名含 `__` 拒绝启动；重启后 jsonl 替换不追加+原子性；env 注入上游生效（gizmos `echo_env`）；相对路径/不同工作目录一致（索引目录基准=config 同目录）；上游启动成功后中途死亡——下轮握手前仍可搜（本轮=最近握手轮）+ invoke 报代理级错误，刷新后翻 unreachable 并索引隔离（interval=5 独享进程，两阶段各自确定性断言）；运行中给某假上游增/删一个工具 → 下一轮刷新后 jsonl 行数变化、搜索可见性随之变化、invoke 已删工具得代理级错误（D-17 主路径；mutable 状态文件驱动，不掺死亡变量）；startup summary 文案断言 `x/y upstreams connected`（live 0 工具上游计入 connected，见 D-23/开放项）。

**B 搜索语义（20 例）**：单词/多词 AND/OR 兜底排序/双零路标；双零路标含不可达计数行+指针（死服务名与原因不在搜索响应）；全部上游不可达→警告+强指针；陈旧索引隔离（预植入假 jsonl、该上游从启动起不可达→搜不到）；命中数=阈值 T 返回 vs T+1 拒绝（off-by-one 边界，阈值由变体 config 指定，不依赖默认值）；阈值调高后原拒绝用例可返回；大小写三态；子串非词边界；关键词=服务名的作用域搜索；关键词只在参数描述里也命中；空关键词用法提示；正则元字符按字面；中文/emoji/超长关键词；返回格式含可复制 target+参数类型+`[destructive]` 标记（target 独占首段、整串可复制，标记不拼进 target，放行尾）；同分排序确定；搜 "string" 命中一切（已知特性锁定，D-22/已接受风险）。

**C invoke 透传（14 例）**：正常调用；同名工具命名空间分流（hr__search vs itops__search）；上游错误透传；target 不存在/格式坏（无 `__`、空、首段非已知服务）代理级错误；target 服务在 config 但启动即不可达→代理级错误自包含原因（与"首段非已知服务"文案可区分）；target 在索引但上游挂 → 代理级错误+状态检查提示（`Run check_mcp_status...`；复用 interval=5 独享进程的中途死亡窗口）；100KB 原样不截断（itops firehose）；空参数/多余参数/深嵌套结构交上游校验不拦截；中文参数返回无损（gizmos）；慢工具不提前掐断（gizmos `slow(seconds=2)`，断言耗时 ≥2s 且结果完整）；并发 invoke 不同上游互不阻塞（同上游串行，见连接模型）；同上游连续调用连接复用不串话。

**D description 注入（4 例）**：tools/list 含服务清单；超上限降级（用测试配置 `description_max_servers=2` 触发，不造 16 个假服务）；fail-soft 服务不计入清单、有死者出现 `(k unavailable)` 计数后缀；空配置自洽。

**E 协议与生命周期（5 例 + 6 另加）**：全程 stdout 零污染（裸行 JSON-RPC 客户端：全程 stdout 每行可解析为 JSON-RPC；stderr 才有日志）；initialize→tools/list→call_tool 全链路；长会话几十次调用无状态泄漏（行为稳定口径：50+ 混合调用全绿、搜索前后一致；不做内存剖析）；客户端断开 → 代理与 stdio 上游子进程全部退出无孤儿（Windows 专项；PID 文件 + ctypes 判活——本机 PowerShell 无输出、wmic 缺失，不用进程表扫描）；搜索进行时索引重写读不到半行。另加：刷新自愈用例（启动挂→刷新后可搜；`refresh_interval: 1` 控预算，`wait_until` 轮询 stderr 失败行/搜索可见性，deadline 给足）、新代协议用例（`Client` 默认 `mode=auto` 经 `server/discover` 探测协商 2026-07-28，`listen(tools_list_changed=True)` 订阅 → 触发清单变化后收到 `ToolsListChanged`、重拉 tools/list 见新 roster，验证 B02 注入的 SubscriptionBus；roster 变化由 mutable 状态文件触发）、旧代协议通知用例（`Client(mode=legacy)` 强制 initialize 握手，`message_handler` 观察 `notifications/tools/list_changed` → 重拉 tools/list 见新 roster，覆盖真实下游所在路径）、退出耗时用例（不可达 Streamable HTTP 上游在 config，下游断开后代理进程应在阈值内退出，验证 `terminate_on_close=False` 不拖 30s，见 D-27；阈值给足 Windows 余量）、DeepAgents 拉起形态用例（按 langchain-mcp-adapters 的 command/args 形态 spawn）、`_status.json` 断言用例（字段齐全、与注册表一致，tmp+rename 原子性）。

**F 规模冒烟（3 例）**：1000+ 工具索引搜索满足性能预算（fleet_gen `--count 1000`，预算值见 architecture 契约）；单工具 50KB 巨型 schema 不崩（gizmos）；大索引下拒绝路径快速返回（不做全量格式化）。

## B03 内嵌的产品改动

测试矩阵会逼出实现改动，以下两项已确定在 B03 内落地（tasks 须包含实现任务，不只写测试）：

1. **startup summary 措辞**：`x/y upstreams indexed` → `x/y upstreams connected`（D-23；握手成功即 connected，0 工具上游也计入）——A 区断言新文案，须先改 `health.emit_startup_summary` 并同步单测。
2. **Windows 孤儿回收**：架构"连接模型"要求下游断开时代理与 stdio 上游子进程全部退出无孤儿（"B03 落地"）。按"先测后修"执行：E 区用例先跑，优雅退出路径若已达标则只留用例；若不达标再上 Windows Job Object（ctypes）等实现手段。

矩阵运行中被逼出的其他 bug 修复随机入册。

## 依赖与关联

- 前置批次：B02
- 关联决策：D-01、D-06、D-09、D-10、D-12、D-13、D-17、D-20、D-22、D-23、D-25、D-27、D-28（测试逼出的三条裁决：D-06 超时不设、D-09 重复名 fail-fast、D-22 噪音接受；D-25 双代通知与 D-27 退出不拖 30s 的全链路验证归本批；孤儿进程清理为架构"连接模型"实现要点）

## 记录

- 2026-11-04 落盘。矩阵源自探索阶段逐区枚举（含测试反哺产品设计的裁决）。
- 2026-11-04 审核修订：空配置语义按 architecture 契约拆分（缺键 fail-fast / 空对象合法）；target 解析按"首个 `__` 切分"重写用例表述；引用裁决改为点名编号。
- 2026-11-04 修订：上游增补 SSE（D-08 修订），A 区加 SSE 用例、假上游全家加 sse_variant。
- 2026-11-04 探索修订（B01 开工前定案）：三传输提前入 B01（假上游对齐 hr/stdio、itops/HTTP、audit/SSE + dead 死条目）；A 区增 HTTP/SSE 不可达族、HTTP 握手超时变体、check_mcp_status 三例（16→23）；B 区增路标计数行/全挂警告指针/陈旧索引隔离、`[destructive]` 标记（17→20）；C 区增 config-但-不可达文案（13→14）；D 区按"死服务不计入+计数后缀"改写；"无网络"放宽为本地 loopback。合计 ~70+ 例（以 collect-only 为准）。
- 2026-11-04 审核修订：错引 D-10 改 D-22；执行结构逐项点名共享/独享 fixture（fleet_gen 不进共享）；A 区补中途死亡用例（23→24）、E 区补 `_status.json` 断言；关联决策补 D-01/D-13/D-23；契约数值改引用 architecture（去双写）；各区标注用例数为参考值，验收计数以 `pytest --collect-only` 为准；sse_variant（见前条记录）已并入 audit.py。
- 2026-11-04 B01 归档回写（承接开放项）：E 区生命周期用例顺带覆盖 `terminate_on_close` 对不可达 HTTP 上游的多等 30s 并收紧；计时型用例在 Windows 上的抖动余量统一评估；F 区大索引复验 OR 样品数默认 10 是否仍合理（23 工具演示规模未受压）；A 区补 startup summary 对 live 0 工具的措辞断言。
- 2026-11-04 B02 探索回写：E 区"另加"补新代协议用例（`discover()` 开线 + `subscriptions/listen`，验证 B02 注入的 SubscriptionBus）；刷新类用例 config 钉 `refresh_interval: 1`，不真等 30s（控 60s 总预算）。
- 2026-11-04 设计审核回写：A 区补运行中增/删工具用例（D-17 主路径）与 startup summary `connected` 措辞断言；D 区超上限用例改为测试配置 `description_max_servers=2`；E 区另加补旧代协议通知用例与退出耗时用例（D-27），计数改 5+6；B 区锁定 `[destructive]` 标记不拼进 target；Windows 专项打 `windows` marker；术语 FastMCP→MCPServer；总用例数改 ~70+。
- 2026-11-05 落盘定案（B03 开工前）：e2e 改 subprocess 黑盒（旧 7 例迁 `tests/integration/`）；补 gizmos/empty/blackhole/mutable 四类假上游与 fleet_gen `--count`；刷新簇拆 interval=1 共享 + interval=5 独享；等待统一 `wait_until` + stderr 事件源；预算定 Windows 本机 150s、设计目标热跑 ~100s（D-28），CI 本期不建；startup summary 措辞与 Windows 孤儿回收列入本批产品改动；超支收敛顺序与中途校准点入档。
- 2026-11-05 change 审核回写：E 区双代通知用例的客户端 API 更正——SDK 2.3.0 无 `Client.discover()`；新代 = 默认 `mode=auto` 经 `server/discover` 探测协商，旧代 = `Client(mode=legacy)` 强制 initialize 握手。
- 2026-11-05 实施回写：collect-only 75 例（A24/B21/C11/D4/E10/F3 + harness 冒烟 2）；串行 337s，按"合并变体进程"收敛到 75 例串行 ~300s，验收改 `-n 4` 并行实测 129~133s；新增 dev 依赖 `pytest-xdist`（产品零运行时依赖不变）。孤儿回收实测现状已达标（SDK Windows Job Object），无需改产品代码；判活改用 gizmos 自写 PID 文件 + ctypes（本机 PowerShell 无输出、wmic 缺失）。
