# Tasks

## 1. 基座与测试迁移

- [x] 1.1 把 `tests/e2e/test_demo_servers.py` 整体移到 `tests/integration/`、`tests/e2e/test_config_example.py` 移到 `tests/unit/`（内容不改；仅把 `test_demo_servers.py` 的 `DEMO_DIR` 一行改为指向 `tests/e2e/demo_servers/`——demo_servers 不随迁；`test_config_example.py` 的 `parents[2]` 深度不变无需改）；验证 `uv run pytest tests/integration tests/unit -q` 全绿。
- [x] 1.2 建 `tests/e2e/conftest.py`（可拆 `harness.py`）：`write_config`、`ProxyProcess`（start/stop/client/stderr 缓冲/wait_stderr/wait_exit）、`spawn_demo`、`raw_client`、`wait_until`、`free_port`、`wait_port_accepts`；验证：一个最小冒烟用例（hr-only 代理经 stdio initialize → tools/list 见三工具）通过。
- [x] 1.3 `pyproject.toml` 注册 `windows` marker；验证 `uv run pytest --markers` 列出 windows，`uv run pytest tests/e2e -m windows --collect-only -q` 不报未知 marker。

## 2. 假上游家族补齐

- [x] 2.1 新建 `demo_servers/gizmos.py`（stdio：50KB schema 工具、`slow(seconds=5)`、描述覆盖换行/引号/中文三种形态的工具、`echo_env`）；验证：经代理握手后 jsonl 行数与工具名齐全，含换行/引号/中文的描述行仍为合法单行 JSON，`slow` 参数默认 5。
- [x] 2.2 新建 `demo_servers/empty.py`（握手成功、0 工具）；验证：经代理握手成功、不生成索引文件、注册表 `live 0 tools`。
- [x] 2.3 新建 `demo_servers/blackhole.py`（accept 后不完成握手）；验证：harness 直连可建立 TCP、`initialize` 无应答（短超时内挂起）。
- [x] 2.4 新建 `demo_servers/mutable.py`（HTTP，lowlevel Server，`list_tools` 每次读状态文件）；验证：改写状态文件后重拉 `list_tools` 见工具增减。
- [x] 2.5 `demo_servers/fleet_gen.py` 加 `--count`（默认 42 不变）；验证：默认索引 42 行，`--count 1000` 索引 1000 行，既有 fleet_gen 用例仍绿。

## 3. 产品改动（TDD）

- [x] 3.1 先改/加 `tests/unit/test_startup_summary.py` 断言 `x/y upstreams connected`（含 0 工具上游计入 connected），再改 `health.emit_startup_summary` 使测试通过；验证 `uv run pytest tests/unit/test_startup_summary.py -q` 绿。
- [x] 3.2 先写 E 区孤儿回收用例（`windows` marker，见 8.x）跑一次现状；若优雅退出路径已达标则只留用例，若不达标实现 Job Object（ctypes，KILL_ON_JOB_CLOSE）或退出钩子枚举 terminate；验证：该用例在 Windows 上绿。

## 4. A 区：启动与索引（~28 例）

- [x] 4.1 session 级标准 fixture（hr + itops + audit + dead_http）落地；验证：一次拉起后 `check_mcp_status` 显示 3 live + 1 unreachable。
- [x] 4.2 共享 fixture 上的启动/索引用例：多 stdio 握手行数、HTTP/SSE 单独与混合、两服务同名工具互不覆盖、startup summary `connected` 文案、status 全健康（无死变体）/有死者强提示；验证：对应子集绿。
- [x] 4.3 fail-fast 族：config 不存在 / 非法 JSON / 缺 `mcpServers` / 未知类型 / 重复服务名 / 服务名含 `__`——进程退出码 2 + stderr 指认原因；验证：参数化子集绿。
- [x] 4.4 不可达族：dead_http（共享）、dead_sse、错路径 404、blackhole（handshake_timeout=2）、broken（stdio 秒退）——fail-soft、不生成索引文件且 stderr 摘要含原因；每个变体 config 至少含一个健康上游，以断言启动与其余上游握手不被拖住；验证：对应子集绿。
- [x] 4.5 边界与重现：0 工具上游（empty）、空配置合法启动、空配置下 `check_mcp_status` 自答 "no upstream services configured"、全健康（无死条目）、全挂（live=0）、重启 jsonl 替换不追加+无 tmp 残留、cwd/相对路径一致、gizmos 变体（env 注入 `echo_env` + description 换行/引号/中文仍单行）；验证：对应子集绿。
- [x] 4.6 刷新相关：middle-death 两阶段（interval=5：翻转前仍可搜+invoke 代理级错误 → 翻转后 unreachable+索引隔离）、mutable 增删工具（D-17 主路径）；验证：两用例绿且不互相依赖。

## 5. B 区：搜索语义（20 例）

- [x] 5.1 共享 fixture 上的匹配/排序/格式用例：单词/多词 AND/OR 兜底、双零路标（计数行+指针、无死名）、大小写三态、子串非词边界、服务名作用域、参数描述命中、空关键词、正则元字符字面、中文/emoji/超长、target 可复制+`[destructive]` 放行尾、同分排序确定、搜 "string" 锁定现状；验证：对应子集绿。
- [x] 5.2 变体用例：陈旧索引隔离（预植入 jsonl）、阈值 T/T+1 两态（变体 config）、阈值调高后原拒返回、全部上游不可达警告+强指针；验证：对应子集绿。

## 6. C 区：invoke 透传（14 例）

- [x] 6.1 共享 fixture 上的透传用例：正常调用、同名分流（hr__search vs itops__search）、上游错误透传、target 三类坏形态、config-但-不可达（原因自包含、与"非已知服务"可区分）、100KB 原样、空/多余/深嵌套参数、并发跨上游不阻塞、同上游串行复用；验证：对应子集绿。
- [x] 6.2 gizmos 用例：中文参数无损、多行中文返回、`slow(seconds=2)` 不被掐断（耗时 ≥2s 且结果完整）；验证：对应子集绿。

## 7. D 区：description 注入（4 例）

- [x] 7.1 共享 fixture + `description_max_servers=2` 变体 + 空配置变体：清单在场、超限降级（含 of M）、死服务不计入且 `(k unavailable)` 后缀、空配置自洽；验证：对应子集绿。

## 8. E 区：协议与生命周期（5 + 6 例）

- [x] 8.1 stdout 零污染（raw_client 全程逐行可解析 JSON-RPC）、initialize→tools/list→call_tool 全链路；验证：两用例绿。
- [x] 8.2 长会话行为稳定（50+ 混合调用全绿、搜索前后一致，不做内存剖析）；验证：用例绿。
- [x] 8.3 孤儿回收：下游断开后代理退出且 stdio 子进程无残留（PID 文件 + ctypes 判活；本机 PowerShell 无输出、wmic 缺失，故不用进程表扫描）；验证：`windows` marker 用例绿。
- [x] 8.4 退出耗时：不可达 Streamable HTTP 上游在 config，下游断开后代理在 10 秒内退出（D-27；Windows 余量已含在 10s 内）；验证：用例绿。
- [x] 8.5 刷新自愈（启动挂→刷新后可搜，interval=1 + wait_until）、搜索进行时索引重写读不到半行；验证：两用例绿。
- [x] 8.6 新旧双代通知：新代用默认 `Client(mode="auto")`（经 `server/discover` 协商 2026-07-28）+ `listen(tools_list_changed=True)`，收 ToolsListChanged 后重拉见新 roster；旧代用 `Client(mode="legacy")` 强制 initialize 握手，message_handler 观察 `notifications/tools/list_changed`；roster 变化由 mutable 状态文件触发；验证：两用例绿。
- [x] 8.7 DeepAgents 拉起形态（command/args 的 stdio spawn）、`_status.json` 字段齐全+与注册表一致+无 tmp 残留；验证：两用例绿。

## 9. F 区：规模冒烟（3 例）

- [x] 9.1 fleet_gen `--count 1000`：搜索 <200ms 预算断言；50KB schema（gizmos）不崩；大索引拒绝路径快速返回；验证：三用例绿（性能断言用宽阈值防抖）。

## 10. 集成收口

- [x] 10.1 `uv run pytest tests/e2e --collect-only -q` 计数与六区覆盖对账（以实际收集为准，实际 75 例：A24/B21/C11/D4/E10/F3 + harness 冒烟 2）；验证：计数与批次文档六区逐项可对应。
- [x] 10.2 全量 `uv run pytest tests -q --durations=10`：记录热跑耗时（实际：全量串行 260 passed / 399.66s；验收命令 `tests/e2e -n 4` 132.77s）；超支按"合并变体 → 放宽计时余量 → 不砍用例"收敛（81→75 例、新增 xdist）；验证：`uv run pytest tests/e2e -n 4` 全绿且 <150s。
- [x] 10.3 记录冷跑/热跑实测值与差值（重启后首跑 A 区 150.4s vs 热跑 154.8s，未见显著冷跑惩罚；全量热跑串行 399.7s）；验证：数据已写入批次文档记录。
- [x] 10.4 `uv run python tests/e2e/probe.py` 管道冒烟（搜索/status/tools/kill/start 各一次）仍可用；验证：输出符合预期、退出正常。
- [x] 10.5 按实际实施结果回写批次文档（预算实测值、孤儿回收实现方式、用例计数）与 roadmap 开放项状态；验证：文档与代码/测试一致。
- [x] 10.6 矩阵运行中暴露的其他缺陷：逐个登记并修复（实测：PowerShell 进程表不可用→改 PID 文件判活；原子写 .tmp 瞬态断言→改 wait_until；blackhole 2s 超时过紧→5s；均测试侧修复，无产品缺陷），验证全量套件持续全绿。

## Workflow follow-up

- 对照本 change 制品审核代码并修复（@reviewer），逐条验证审核结论。
- 归档 change（`/opsx-archive`），确认 specs 同步（connection-lifecycle 新建、upstream-health 更新）。
- 提交并推送代码。
