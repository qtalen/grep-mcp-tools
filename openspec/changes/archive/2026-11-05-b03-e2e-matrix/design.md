# Design

## Context

见 `proposal.md`（Why）。矩阵细目、假上游全家与预算口径的唯一来源是 `docs/design/batches/B03-e2e-matrix.md`（下称"批次文档"），本文件只记录实现层面的技术决定。

现状事实：

- 测试面：`tests/e2e/` 仅有 9 个用例——7 个 in-process 传输级测试（直接调 `handshake_all`/`ProxyServer`，不起代理进程）+ 2 个 config 示例解析；`tests/unit/` 已有 20+ 个逻辑层测试；`tests/spikes/` 为选型证据。
- 假上游：hr（stdio，8 工具）、itops（HTTP，12）、audit（SSE，3）、docs（SSE，6）、fleet_gen（HTTP，42）、broken（秒退）。
- 代码事实：入口 `python -m grep_mcp_tools --config <path>`（`__main__.py`）；摘要打印在 `health.emit_startup_summary`（当前文案 `x/y upstreams indexed`）；退出时序 `server._shutdown`（先 cancel 刷新 → `close_all`）；`terminate_on_close=False` 已在 `index.py` 落地；SDK 2.3.0 的 `Client` 无独立 `discover()` API——新代由默认 `mode="auto"` 经 `server/discover` 探测协商（`client.py:335-342`），旧代用 `mode="legacy"` 强制 initialize；`listen()` 已由 B02/probe 实证。
- 契约：`docs/design/architecture.md`（连接模型、日志纪律、性能预算）+ D-28（Windows 150s / Linux CI 60s）。

## Goals / Non-Goals

**Goals:**

- 六区矩阵落为一条命令可跑的 subprocess 黑盒套件，Windows 本机 <150s（热跑目标 ~100s）。
- 两处行为改动落地：启动摘要 `connected` 措辞；Windows 孤儿回收（先测后修）。
- 矩阵作为回归网直接锁住 B01/B02 契约；probe 保持可用。

**Non-Goals:**

- 不建 CI workflow；不接真实企业上游；不做性能基准报告与内存剖析；不引入新的运行时依赖。

## Decisions

- **e2e 唯一形态 = subprocess 黑盒**：测试用 `subprocess` 拉起 `python -m grep_mcp_tools --config <tmp>/config.json`，下游用 SDK `Client`（stdio）或裸行 JSON-RPC 客户端驱动。理由：A 区 fail-fast（退出码 2）、E 区 stdout 零污染/孤儿回收/退出耗时、startup summary 文案，这些只有真进程可见；in-process 层已有 20+ 单测，不缺。旧 `test_demo_servers.py` 整体迁 `tests/integration/`（内容不改，仅 `DEMO_DIR` 一行改为指向 `tests/e2e/demo_servers/`——demo_servers 不随迁）；`test_config_example.py` 是纯解析，迁 `tests/unit/`（`parents[2]` 深度不变，无需改动）。
- **harness 集中在 `tests/e2e/conftest.py`**（可拆 `harness.py`）：提供
  - `write_config(tmp_path, servers, settings)`：写临时 config（含三方传输、死条目、env）；
  - `ProxyProcess`：start/stop/force_kill、`client()`（SDK Client 上下文）、`stderr` 行缓冲读取（后台线程）、`wait_stderr(pattern, deadline)`、`wait_exit(timeout) -> returncode`；
  - `spawn_demo(script, port=None, *args)` 上下文管理器（测试自己拉 itops/audit/mutable，显式绑 127.0.0.1）；
  - `raw_client`：手写 JSON-RPC 行客户端（stdout 零污染与协议层断言用）；
  - `wait_until(pred, deadline)` 同步/异步两版；
  - `free_port()` / `wait_port_accepts()`（从现测试抽出复用）。
- **fixture 编成**：session 级标准代理（hr + itops + audit + dead_http）承载多数 A/B/C/D 用例；function 级变体各自独享进程（fail-fast 族、broken、empty、空配置、全健康、全挂、dead_sse、404、blackhole、重启、cwd、env、description_max_servers=2、阈值 T/T+1、fleet_gen 1000+）；刷新簇两进程——`refresh_interval=1` 共享（hr + mutable + itops）服务 roster 增删/双代通知/搜索并发重写，`refresh_interval=5` 独享服务中途死亡两阶段（翻转前窗口 ~5s）。理由：把墙钟等待压到预算内，同时让"窗口型"用例有确定窗口。
- **等待纪律**：一切"等刷新生效"的断言走 `wait_until`（deadline 为预期 5~10 倍）；刷新失败 stderr 行作为翻转事件源；禁止 `sleep(固定秒)` 后断言。计时型断言在 Windows 上统一放宽；`windows` marker 用例用更宽界。理由：慢机器/冷启动下固定 sleep 是抖动的唯一来源。
- **假上游四件套**：`gizmos.py`（stdio；50KB schema、`slow(seconds=5)`、中文描述工具、`echo_env`）、`empty.py`（0 工具）、`blackhole.py`（accept 后不握手）、`mutable.py`（HTTP lowlevel Server；`list_tools` 每次读状态文件）。现有服务器工具数（hr=8/itops=12/audit=3/docs=6/fleet_gen 默认 42）被断言钉死，机关工具一律不进共享 fixture；`fleet_gen` 加 `--count`（默认 42 不变）。
- **startup summary**：改 `health.emit_startup_summary` 文案为 `x/y upstreams connected`，connected 判据 = 握手成功（含 0 工具）；同步 `tests/unit/test_startup_summary.py`。A 区新断言即规格。
- **Windows 孤儿回收（先测后修）**：结论是优雅退出路径已达标（SDK stdio 传输在 Windows 用 Job Object 托管子进程），只留用例、不改产品代码。判活不用进程表扫描（本机 PowerShell 无输出、wmic 缺失）：假上游经 config env 注入 `B03_PID_FILE` 自写 PID，测试用 ctypes `OpenProcess`+`GetExitCodeProcess` 判活。
- **预算记账与并行**：验收线 Windows 150s；实测串行 75 例 ~300s（81 例时 337s），按"合并变体进程"收敛后仍超线，故验收命令采用 pytest-xdist `-n 4`（dev 依赖）实测 129~133s；`-n 6` 实测因并发争抢导致健康上游握手超时，不可用。超支收敛顺序——合并变体进程 → 放宽计时余量 → 永不砍用例、不压余量到会抖。

## Risks / Trade-offs

- [冷跑超 150s] → 记录热/冷两值与差值；冷跑只作观察、不设独立闸门；结构上仍保热跑 ~100s。
- [Windows 计时抖动] → `wait_until` + 宽 deadline + `windows` marker；宁可慢不可抖。
- [进程表扫描在部分 Windows 环境不可用] → 实测本机 PowerShell 无输出、wmic 缺失；改用假上游自写 PID 文件 + ctypes 判活（无外部命令依赖）。
- [并发争抢导致握手超时] → `-n 6` 实测健康上游也超时；验收固定 `-n 4`，计时断言保留宽余量。
- [mutable.py 用 lowlevel Server 有实现成本] → 已确认 SDK 有 `mcp.server.lowlevel.Server`；只做"list_tools 读文件"一件事，不涉其余协议面。
- [共享刷新簇状态污染] → 每用例 arrange 自足（自设状态文件/自 kill-restart），断言不依赖前序用例遗留。
- [迁移旧测试引入路径/导入问题] → 只移动文件、不改内容；迁移后跑 `pytest tests/integration tests/unit` 确认。
- [stderr 中文编码] → harness 以 bytes 缓冲、`errors="replace"` 解码；需要精确断言的文案均为 ASCII。

## Migration Plan

无数据/接口迁移。回滚 = revert 本 change 提交；`tests/integration/` 与 `tests/unit/` 的迁移可单独撤回而不影响产品代码。

## Open Questions

- 无。孤儿回收是否需要 Job Object 由 E 区用例结果决定，属于任务内条件分支，不改变规格与任务拆解。
