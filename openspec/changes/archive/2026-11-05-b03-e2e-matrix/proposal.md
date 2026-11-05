# Proposal

## Why

B01/B02 已交付代理全链路与行为面，但确定性质量只靠单元测试与 9 个 in-process 测试（7 个传输级 + 2 个配置解析），没有黑盒 E2E——进程退出码、stderr 文案、stdout 协议纯净度、孤儿进程回收、下游断开退出时序这些"只有真实进程才看得见"的契约无法被自动化守住。B03 是 M1（内部可用）的最后一块：把六区契约矩阵固化成一条命令可跑的确定性套件，并顺带落地两处已裁决但未实施的行为。

## What Changes

- 新建 subprocess 黑盒 E2E 套件 `tests/e2e/`：conftest harness（fixture、裸行 JSON-RPC 客户端、进程清理）+ 六区各一用例文件，覆盖 A 启动与索引 / B 搜索语义 / C invoke 透传 / D description 注入 / E 协议与生命周期 / F 规模冒烟，约 70+ 用例。
- 原 in-process 传输级测试（`tests/e2e/test_demo_servers.py` 7 例）迁到 `tests/integration/`，配置解析测试（2 例）迁到 `tests/unit/`，不占 e2e 预算。
- 假上游家族补齐：`gizmos.py`（50KB schema / slow / 中文描述 / echo_env）、`empty.py`（0 工具）、`blackhole.py`（accept 后不握手）、`mutable.py`（lowlevel Server，list_tools 每次读状态文件）；`fleet_gen` 加 `--count` 参数（默认 42 不变）。
- 行为改动 1：启动摘要措辞 `x/y upstreams indexed` → `x/y upstreams connected`（握手成功即 connected，0 工具上游计入）。
- 行为改动 2：Windows 孤儿回收——下游断开时代理与其 spawn 的 stdio 上游子进程全部退出，不留孤儿（先测后修，必要时上 Job Object）。
- 耗时口径：Windows 本机 `<150s`（设计目标热跑 ~100s）；原 `<60s` 改作将来 Linux CI 的闸门（D-28）。
- **不包含**：CI workflow（本期不建）、agent 效果测试（B04）、真实企业上游。

## Capabilities

### New Capabilities
- `connection-lifecycle`: 代理与上游连接的进程生命周期——下游断开时回收全部 stdio 上游子进程（不留孤儿，Windows 专项）；退出耗时不因不可达的 Streamable HTTP 上游被拖延。

### Modified Capabilities
- `upstream-health`: 启动摘要措辞改为 `x/y upstreams connected`，握手成功即计 connected（含 0 工具上游）。

## Impact

- 代码：`src/grep_mcp_tools/health.py`（摘要措辞）；`index.py`/`server.py`/`__main__.py`（仅当孤儿回收用例暴露缺口时才改）。
- 测试：`tests/e2e/` 重建（conftest + 六区文件 + 4 个新假上游）、`tests/integration/` 新增、`tests/unit/test_startup_summary.py` 更新。
- 依赖/配置：产品零新增运行时依赖；新增 dev 依赖 `pytest-xdist`（验收命令 `-n 4`）与 `windows` marker。
- 无 API、配置格式或索引格式变更；无破坏性变更。
