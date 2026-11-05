# Design

## Context

见 `proposal.md`（Why）。现有可复用的地基：

- B01–B03 已归档：代理三工具契约、description 注入与双零路标文案、健康注册表均已冻结并有 75 例确定性 E2E 覆盖。
- `tests/e2e/demo_servers/` 提供 5 个可复用假上游：hr（stdio，8 工具）、itops（Streamable HTTP，12）、audit（SSE，3）、docs（SSE，6）、fleet_gen（HTTP，42），合计 71 工具。
- `tests/e2e/harness.py` 已提供进程拉起、空闲端口、等待等无 pytest 依赖的工具（`free_port` / `pick_ports` / `wait_port_accepts` / `wait_until`）；`conftest.py` 只做 fixture 装配。

约束：产品代码与运行时依赖零改动；Windows 本机运行；模型走独立 DeepSeek 服务（OpenAI 兼容）；API key 只从项目根 `.env` 读取。

## Goals / Non-Goals

**Goals:**

- 四臂实验（a 代理 / b 直挂 / c 关注入 / d 关路标）可在一条命令内跑完并输出两张指标表。
- transcript 是唯一真相源：判分与报告均为其纯函数，规则改了可离线重算，不重跑 API。
- 复用现有假上游，不加新上游、不改其工具定义。

**Non-Goals:**

- 不追求统计显著性；结论只用任务级配对差异（每原型 5 遍）。
- 不接默认 CI、不做多模型矩阵。
- 不改代理行为、配置格式、响应文案或运行时依赖。

## Decisions

### 1. 四臂与直挂臂命名

臂 a 挂代理（3 工具）；臂 b 直连 5 个上游、全量 71 工具扁平化为 `server__tool`；臂 c/d 复用臂 a 的进程编成，仅在 harness 侧改写模型可见内容。

- 备选：直挂臂用宿主自然命名（如 `mcp__server__tool` 或加随机前缀）——会使两臂判分归一化复杂、且引入命名干扰变量。选 `server__tool` 与代理 target 同构，撞名天然分流，判分器两臂共用同一事件流。
- 备选：臂 c/d 重跑完整代理——浪费 60 次调用；直接复用臂 a 的 30 份 transcript 作对照。

### 2. 每臂独立上游编成

每个臂各自拉起一套上游（HTTP/SSE 由 run.py 拉起；hr stdio 在臂 a 由代理经 config 拉起、臂 b 由 harness 拉起），跑完销毁。

- 备选：全实验共享一套上游。HTTP/SSE 服务可多客户端，但共享会引入跨臂状态耦合与生命周期纠缠；独立编成实现简单、失败可归因。

### 3. 消息组装与服务端语义

harness 直接 `append` 服务端返回的 assistant message 对象（保留 `reasoning_content`、`tool_calls`），不做字段手工重组。

- 依据：DeepSeek 官方 Thinking Mode 规则——带 `tools` 的请求必须回传历史 `reasoning_content`，缺省即 400。手工挑选 `role/content/tool_calls` 的常规写法会在第二次请求起失败。
- 备选：每次从 transcript 重建消息——同样可行，但必须显式保留 `reasoning_content`；直接 append 更不易漏字段。

### 4. 判分器是 transcript 的纯函数

输入 = 归一化事件（`server__tool` 调用 + 参数 + 最终回答 + usage）；输出 = 判定结果与指标贡献。判分不依赖运行时进程状态。

- 备选：边跑边判（耦合、规则改一次重跑一次）；LLM 判分（引入元模型与非确定性，违背确定判分承诺）。

### 5. 判定口径

- ①②③⑥：目标任务被调用、参数结构正确、无白名单外的上游工具调用；任务文本给死的值（E1001、2 天、家事）校验值。
- ④：两个 target 按序齐全才算成功（对一步 = 失败），employee_id 均校验。
- ⑤：零 invoke + 最终回答含版本化否定能力词表 + 不含完成断言；幻觉率单列（发起 invoke 或编造 target）。
- 自主发现率：首个工具调用为 `search_mcp_tool`；分母 = 5 原型 × 5 遍 = 25。
- 诊断指标（仅报告、不作 gate）：搜索效率（每任务搜索次数、零命中后是否改用路标/换词恢复）、invoke 正确率（target 是否原样来自搜索结果、参数结构错误率）、`check_mcp_status` 调用率（仅观测）、放弃率。
- 轮次上限：16 次 assistant 轮，触顶记放弃。
- 备选：⑤ 纯行为判（漏掉“含糊其辞”）、LLM 判分（不确定）。词表版本化 + transcript 人工复盘兜底。

### 6. 消融在 harness 侧信道编辑

臂 c：从 `tools/list` 拿到的 `search_mcp_tool` description 中删除 `Connected services...` 注入块；臂 d：从双零命中与空关键词两条响应路径中删除 `Available services:` 路标块（`search.py` 的 signpost 与 empty_keywords 共用同一渲染）。**只删除、不添加、不改写**。`check_mcp_status` 的消融有意不做（排障通道，非任务路径）。

- 备选：产品加实验 config 开关（违反本批“产品代码零改动”边界，且留下永久实验表面）；`description_max_servers=0` 退化（文案变形、路标无对应旋钮）。
- 保真度：剥离后模型看到的即“特性缺席”的反事实，剩余文本全部是代理原话。

### 7. token 度量

常驻 = 每臂隔离测量：同一 system+user 短消息 + 该臂工具清单，读 `usage.prompt_tokens`，减无工具基线；序列化字符数并报。每任务累计 tokens 从真实运行的逐次 usage 聚合，仅报告。

- 备选：tiktoken 计数（DeepSeek tokenizer 不同源，且引依赖）；纯字符数（可复算但不是 vision 的 token 口径）。实测 token 差值作 gate，字符数兜底审计。

### 8. 配置只认 `.env`

`python-dotenv` 的 `dotenv_values()` 直接返回字典，不修改也不读取 `os.environ`；OpenAI 客户端显式传 `api_key`，不依赖 SDK 环境变量兜底。`DEEPSEEK_BASE_URL` 与 `DEEPSEEK_API_KEY` 缺失时 fail-fast；`DEEPSEEK_MODEL`（默认 `deepseek-flash`）与 `DEEPSEEK_REASONING_EFFORT`（默认 `low`）可缺省。

- 依据：用户明确要求不经 shell 设置；`.env` 已在 `.gitignore`。
- 备选：`load_dotenv()` + `os.environ`（会引入环境变量优先级歧义）；自写解析器（引号/转义边界多）。

### 9. 复用与共享模块

假上游直接 import/子进程复用 `tests/e2e/demo_servers/`；把 `free_port`、`pick_ports`、`wait_port_accepts`、`wait_until` 抽到 `tests/shared/proc.py`，`tests/e2e/harness.py` 从它 import 并 re-export（保持现有测试的 import 路径不变），agent 树直接引用；不跨树 import `conftest.py`。

- 备选：复制到两处（漂移）；agent 树 import `conftest.py`（pytest 私有约定，脆弱）。

### 10. 断点续跑与 run-id

run-id 形如 `{arm}-{task}-{rep}`；transcript 落 `tests/agent/transcripts/{run-id}.json`，存在且判分完整则跳过。`--judge-only` / `--report-only` 只读 transcript。

- 依据：120 跑单次成本高、调试判分规则频繁。

## Risks / Trade-offs

- [消融剥离器与文案格式耦合，产品改文案后静默失效] → 剥离器单测钉死样例 + 运行时强断言（臂 c 不得含 `Connected services`、臂 d 不得含 `Available services`），剥不动即失败。
- [30 样本、LLM 无种子，单次翻转即 3.3pp，与判据同量级] → 只用任务级配对差异（≥ −3 个任务），不硬切总体百分比；结论标注噪声口径。
- [⑤ 文本判分仍有边缘模糊] → 词表版本化并随判分器留存，边缘案例读 transcript 人工复盘，不引 LLM 判分。
- [120 跑成本与时长不可控] → 串行 + 429 指数退避 + 断点续跑；先跑小规模冒烟再全量。
- [DeepSeek 模型/参数后续变更] → 模型名、base_url、effort 全部配置化；开工前最小请求复验。
- [`tools` 是否计入 `prompt_tokens` 未获官方明文] → 隔离测量的“有工具/无工具”差值法自证（差值为 0 即异常），字符数并报。
- [消融臂与真实产品并非逐字节同一进程行为] → 只删除不改写，且产品内部行为（索引、注入、刷新）与模型可见信道无关。

## Migration Plan

新增 `tests/agent/`、`tests/shared/proc.py`、`.env.example` 与 dev 依赖；对现有文件仅做 import 调整（`tests/e2e/harness.py` 改为引用 shared 并 re-export）与 `.gitignore` 追加。回滚 = 删除新增文件与 dev 依赖，并还原 `tests/e2e/harness.py` 的 import 与 `uv.lock`；产品代码与发布物不受影响。无数据迁移。

## Open Questions

- `deepseek-flash` 当前 checkpoint 日期：开工复验时读响应 `model` 字段确认，不影响任务拆解。
- `tools` 定义是否计入 `usage.prompt_tokens`：同上，由隔离测量自证。

## 实施记录（2026-11-05）

- 6.1 最小复验：服务端返回 `model=deepseek-flash`，`reasoning_effort=low` 被接受，tools 计入 `prompt_tokens`（288），`reasoning_content` 原样回传第二轮通过（318 prompt tokens）。
- 6.2 冒烟暴露并修正：① 任务文本"张三/E1001"与假上游 `get_employee` 返回值冲突，诱导额外核实被判无关工具 → 改为"帮查一下工号 E1001 的年假余额"（批次文档、tasks、测试同步）。
- 6.3 全量 120 跑（四臂 × 6 任务 × 5 遍）：M2 三项判据均达标——代理 21/30 vs 直挂 24/30（差 −3，边界通过）、自主发现率 100%（25/25）、常驻缩减 87.8%（685 vs 5624 tokens）。消融 a/c/d 通过数与自发现率无差异。
- 实现侧兜底：单 run 异常落盘 gave-up 后继续；`resident.json` 落盘支持离线复算；Windows 控制台显式切换 UTF-8 输出。
- 遗留观察（未在本批改口径）：判分"无其他上游工具调用"对探索性行为偏严（t2/t6 的失败多源于此）；t2 词汇失配任务两臂全失败，作为产品核心赌注的负面样本记录。
