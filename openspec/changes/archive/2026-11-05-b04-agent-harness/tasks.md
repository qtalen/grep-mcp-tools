# Tasks

## 1. 基座与配置

- [x] 1.1 `pyproject.toml` 的 `dependency-groups.dev` 增加 `openai`、`python-dotenv`；验证 `uv sync` 成功且 `uv run python -c "import openai, dotenv"` 通过，`uv run pytest tests/unit -q` 仍绿。
- [x] 1.2 新建 `.env.example`（`DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_REASONING_EFFORT`，不含真实值）与 `tests/agent/README.md`（.env 用法、四臂命令、产物位置）；验证 `git check-ignore .env` 命中、`.env.example` 未被忽略。
- [x] 1.3 抽 `tests/shared/proc.py`（`free_port`、`pick_ports`、`wait_port_accepts`、`wait_until`、`wait_until_async`），`tests/e2e/harness.py` 改为从它 import 并 re-export（现有测试 import 路径不变）；验证 `uv run pytest tests/e2e -n 4 -q` 全绿（行为不变）。
- [x] 1.4 建 `tests/agent/` 包骨架（`__init__.py`、`transcripts/` 并在 `.gitignore` 忽略该目录）；验证目录结构存在、`uv run python -c "import tests.agent"` 通过。

## 2. harness（TDD）

- [x] 2.1 先写 `tests/agent/test_config.py`：只从 `.env` 取值（`dotenv_values` 字典），进程环境变量被忽略；缺键 fail-fast；model/effort 有默认值。再实现 `tests/agent/config.py`；验证单测绿。
- [x] 2.2 先写 `tests/agent/test_tools.py`：MCP `tools/list` → OpenAI tools 参数（name/description/parameters 原样、required 保留、无 inputSchema 时给空对象）。再实现转换；验证单测绿。
- [x] 2.3 先写 `tests/agent/test_loop.py`：假 chat 客户端——无 `tool_calls` 即终止；有则执行并 append；轮次上限 16 记放弃；消息序列保留服务端 assistant message 对象（含 `reasoning_content`）。再实现工具循环；验证单测绿。
- [x] 2.4 先写 `tests/agent/test_arms.py`（臂 b 扁平化命名 `server__tool`、撞名分流、工具清单合并；臂 a 工具来源），再实现两臂 MCP 客户端（臂 a 经 stdio 挂代理子进程，臂 b 直连 5 个 demo 上游）；验证单测绿，并用真实 demo_servers 做无 key 连接冒烟（臂 a 见 3 工具、臂 b 见 71 工具）。
- [x] 2.5 先写 `tests/agent/test_transcript.py`（序列化完整、出口断言无 key 子串、run-id 命名），再实现 transcript 记录器；验证单测绿且样例落盘可被读回。

## 3. 任务套件与判分（TDD）

- [x] 3.1 按 B04 批次文档「任务套件与判分（定稿）」表（唯一来源）定义 6 任务（提示文本、白名单、参数校验规则、⑤ 词表）于 `tests/agent/tasks/`；验证 6 条齐全、提示不含 `search_mcp_tool`/工具暗示词（断言）。
- [x] 3.2 先写 `tests/agent/test_judge.py`：逐原型覆盖正常/失败/边界（无关工具、④ 仅一步、⑤ 幻觉 invoke、⑥ 碰干扰项、参数值错、超轮放弃），再实现 `tests/agent/judge.py`；验证单测绿。
- [x] 3.3 先写指标派生单测（自主发现率分母 25、幻觉率、每任务搜索次数、零命中后按路标恢复、invoke 正确率与参数结构错误率、check_mcp_status 调用率、放弃率、token 聚合），再实现指标计算；验证单测绿。

## 4. 消融剥离器（TDD）

- [x] 4.1 先写 `tests/agent/test_ablate.py`（注入块剥离后不含 `Connected services`；路标块剥离后不含 `Available services`；剩余文本为原话切片），再实现 `tests/agent/ablate.py`；验证单测绿。
- [x] 4.2 臂 c/d 接入 tools 转换与响应回传路径，运行期强断言剥离生效（臂 d 同时覆盖双零与空关键词两条路径）；验证无 key 冒烟中臂 c description 不含注入块、臂 d 两类响应均不含路标。

## 5. 编排与报告（TDD）

- [x] 5.1 先写 `tests/agent/test_report.py`（样例 transcripts → A/B 主表与消融对照表数值、配对差异、M2 判据计算），再实现 `tests/agent/report.py`；验证单测绿。
- [x] 5.2 先写 `tests/agent/test_run.py`（run-id 生成、已完成 run-id 跳过判定、429 退避序列、旗标解析、缺 key fail-fast），再实现 `tests/agent/run.py`（四臂编成、断点续跑、`--arms/--tasks/--reps`、`--judge-only/--report-only`）；验证单测绿且 `--help` 可用、缺 key 时给出 `.env` 提示。
- [x] 5.3 实现常驻 token 隔离测量（有/无工具 `prompt_tokens` 差值 + 序列化字符数）；验证用假 client 单测差值与字符计数正确。

## 6. 集成与验收

- [x] 6.1 最小请求复验（需用户在 `.env` 填 key）：确认 `deepseek-flash` + `reasoning_effort=low` + `tools` 可用、第二轮带 `reasoning_content` 不报 400、记录返回 `model` 与 usage；验证命令输出符合预期（2026-11-05 实测：model=deepseek-flash、reasoning_content=yes、第二轮 318 prompt tokens 通过）。
- [x] 6.2 小规模冒烟（1 任务 × 1 遍 × 双臂，需 key）：验证 transcript 落盘、判分与报告可完整跑通（2026-11-05 实测：a/b 各 1/1、自发现 100%、常驻缩减 87.8%；期间修正 ① 任务文本的姓名/工号矛盾）。
- [x] 6.3 全量 120 跑（需 key）：验证 A/B 主表与消融对照表输出、transcripts 完整、断点续跑可跳过已完成跑次（2026-11-05 实测：121 份 JSON 含 resident.json，四臂 × 6 任务 × 5 遍完整）。
- [x] 6.4 结果对照 M2：逐条记录三项判据数值与结论；未通过时按批次文档流程读 transcript 定位（文案 vs 架构）。（2026-11-05：21/30 vs 24/30 差 −3 达标、自发现 100%、常驻缩减 87.8%；逐任务与消融观察已写入批次文档「实施结果」与 change design「实施记录」）
- [x] 6.5 产品零改动核对：`src/` 零改动；`git diff`/未跟踪文件限于 `tests/agent/`、`tests/shared/`、`tests/e2e/harness.py`（import 调整）、`.gitignore`、`pyproject.toml`（dev 组）、`uv.lock`、`.env.example`、`tests/agent/README.md` 与规划产物（`docs/design/`、`openspec/changes/`）；`uv run pytest tests/unit tests/integration -q` 绿（185 passed）。

## Workflow follow-up

- 按 AGENTS.md 用 @reviewer 对照 change 制品审核代码并修复。
- `/opsx-archive` 归档本 change。
- 翻台账：roadmap 批次登记行、M2 里程碑状态、当前批次指针、批次文档状态头、architecture 记录条目，对照实际结果回写。
- 提交并推送。
