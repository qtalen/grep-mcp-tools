# Proposal

## Why

产品的全部核心赌注——关键词检索治 L2、description 注入治 L0/L1、路标回路——目前只有确定性 E2E 证明"代理行为对不对"，没有任何真 LLM 数据证明"agent 用得好不好"。M2 里程碑要求三条成功标准（成功率差 ≤10pp、上下文省 ≥80%、自主发现率 ≥80%）量化达标；若假设被证伪，需要证据驱动地重估方向（vision「最难场景」）。因此需要一座可复现的 agent 测试台：同模型、同任务，代理臂对直挂臂，并单独消融两个被动信道。

## What Changes

- 新增 `tests/agent/` 测试台：`harness.py`（OpenAI 兼容 API + 工具循环的迷你 agent）、`tasks/`（6 任务原型）、`judge.py`（transcript 纯函数判分）、`report.py`（指标表）、`run.py`（编排）。
- 四臂实验：臂 a 挂代理（3 工具）、臂 b 直挂全部 71 上游工具、臂 c 关注入、臂 d 关双零路标；A/B 60 跑 + 消融 60 跑。
- 判分与指标：逐任务通过率与任务级配对差异、自主发现率（≥80%）、幻觉率（负样本）、搜索效率、invoke 正确率、check_mcp_status 调用率（仅观测）、放弃率；常驻 token 用 API 实测 `prompt_tokens` 差值作 M2 gate，字符数并报。
- transcript 全量落盘（含 `reasoning_content`、逐次 usage），作唯一真相源；判分/报告可离线重算，支持断点续跑。
- 配置只认项目根 `.env`（`python-dotenv` 的 `dotenv_values`，不读进程环境变量）；dev 依赖新增 `openai`、`python-dotenv`。
- 复用 `tests/e2e/demo_servers/` 假上游；端口/等待工具抽为两树共用模块。
- 明确不做：默认 CI 接入、多模型矩阵、任何产品行为改动（消融在 harness 侧信道实现）。

## Capabilities

### New Capabilities

无。本变更为纯测试设施（tooling），产品对外行为与契约不变，`.openspec.yaml` 已声明 `skip_specs: true`。

### Modified Capabilities

无。

## Impact

- 新增目录 `tests/agent/`（含 `transcripts/` 输出，gitignored）、`tests/shared/proc.py`、`.env.example`。
- `pyproject.toml`：`dependency-groups.dev` 增加 `openai`、`python-dotenv`；产品运行时依赖不变；`uv.lock` 随之更新。
- 复用 `tests/e2e/demo_servers/`（hr / itops / audit / docs / fleet_gen）；`tests/e2e/harness.py` 改为从 `tests/shared/proc.py` 引用并 re-export（行为不变，仅 import 调整）；`.gitignore` 追加 `tests/agent/transcripts/`。
- 外部依赖：DeepSeek OpenAI 兼容 API（独立服务，仅跑实验时需要 key）；不改变发布形态（B05 不受影响）。
