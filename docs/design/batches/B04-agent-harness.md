# B04 agent-harness —— 用真 LLM 量化效果

> 状态：✅ 已归档 2026-11-05
> 形态：地基批

## 验收画面

`uv run python tests/agent/run.py` 跑完一轮套件，终端出两张表：

1. **A/B 主表**（M2 判据来源）：6 任务原型 × 5 遍 × 双臂（代理 / 直挂 71 工具），每臂 30 样本，逐任务通过率与配对差异、零提示自主发现率、常驻与搜索响应的 token 实测——满足 M2 判据：代理臂通过任务数 ≥ 直挂臂 −3（30 样本下 ≈10 个百分点，与 roadmap/vision 同口径）、自主发现率 ≥80%（分母不含负样本）、常驻上下文缩减 ≥80%。
2. **消融对照表**（文案决策依据，不作 gate）：完整代理臂 vs 关 description 注入臂 vs 关双零路标臂的自主发现率/通过率。

`tests/agent/transcripts/` 落盘全量对话记录（每次 search 的关键词、每次 invoke、每次响应、每轮 `reasoning_content` 与 usage），可人工复盘 agent 的每一步；判分器与报告是 transcript 的纯函数，`--judge-only` / `--report-only` 可离线重算。

## 边界

- 不进默认 CI（要 API key、要花钱、结果不确定）
- 不做多模型矩阵（开放项，待 M2 后按需）
- 不驱动产品改文案以外的代码改动——消融结论只反哺 description 静态文案与路标措辞；消融的"关闭"由 harness 侧实现（见"消融实现"），产品代码零改动

## 上游编成与双臂

**假上游编成**（复用 `tests/e2e/demo_servers/`，共 71 工具）：

| 上游 | 传输 | 工具数 | 任务角色 |
|---|---|---|---|
| hr | stdio | 8 | ①④（年假/请假）；② 的潜在干扰源（`hr__search` 描述含 record） |
| itops | Streamable HTTP | 12 | ⑥（运维知识库）、③ 干扰项 |
| audit | SSE | 3 | ②（词汇失配） |
| docs | SSE | 6 | ⑥ 干扰项（`search_docs`） |
| fleet_gen | Streamable HTTP | 42 | ③（超量 42 region 报表） |

- **臂 a（代理）**：run.py 拉起 HTTP/SSE 上游，代理子进程经 config 拉起 hr（stdio）；harness 作为 MCP 客户端挂代理，工具列表 = 3。
- **臂 b（直挂）**：run.py 同时拉起全部上游，harness 作为 5 个 MCP 客户端直连，全量 71 工具扁平化为 `server__tool`（与代理 target 同构；撞名的 `hr__search` / `itops__search` 因此天然分流，匹配真实宿主如 Claude Code `mcp__server__tool` 的行为）。

两臂上游代码、进程编成一致，唯一变量为"3 工具 + 搜索中介 vs 71 工具全量常驻"。

## 任务套件与判分（定稿）

任务提示一律光秃秃的自然语言，零工具暗示——提示里出现"搜索工具"即实验作废（L0/L1 被替 agent 解答）。判分作用于**归一化上游动作流**：臂 a 的 `invoke_this_tool(target, params)` 与臂 b 的直呼工具名归一为同一 `server__tool` 事件；代理三工具是臂 a 的机制，永不判为"无关工具"。

| # | 原型 | 任务文本（定稿） | 期望上游动作 | 判分规则 |
|---|---|---|---|---|
| ① | 显式工具型 | 帮查一下工号 E1001 的年假余额 | `hr__get_leave_balance` | target 正确；employee_id = "E1001"；无其他上游工具调用 |
| ② | 词汇失配型 | 这条配置记录是谁改的？我想查变更历史 | `audit__audit_trail_retention` | target 正确；无参数；无其他上游工具调用 |
| ③ | 超量型 | 查一下 east_1 区的报表 | `fleet_gen__region_east_1_report` | target 正确；无其他上游工具调用（"report" 全库 44 命中 > 阈值 30：fleet_gen 42 + `hr__region_hr_report` + `itops__region_capacity`，逼出收窄回路） |
| ④ | 链式型 | 查工号 E1001 还剩几天年假；若剩 5 天以上，帮他提交 2 天年假申请，理由写"家事" | `hr__get_leave_balance` → `hr__apply_leave` | 两步全对才算成功（对一步 = 失败）：两个 target 按序、employee_id 均为 "E1001"、days = 2、reason 含"家事"、无其他上游工具调用 |
| ⑤ | 负样本 | 在 GitHub 上给 grep-mcp-tools 仓库点个 star | 无 | 成功 = 零 invoke **且** 最终回答含否定能力词 **且** 不含完成断言；幻觉率单列 = 发起 invoke 或编造 target 的比例 |
| ⑥ | 同名分流型 | 在运维知识库搜一下部署文档，再在人事档案里查张三的记录 | `itops__search` + `hr__search` | 两个 target 都中；query 键在场且为字符串；碰 `docs__search_docs`（刻意干扰项）判失败 |

- **参数值口径**：任务文本给死的值校验（E1001、2 天、家事）；自由文本（query）只校验键在场与类型。
- **⑤ 的"明确承认"口径**：零 invoke 是硬条件；"承认"用版本化否定能力词表（无法/不能/没有/不支持/cannot/unable…）判定，并断言回答不含完成标记；边缘案例由 transcript 人工复盘兜底，不引入 LLM 判分。
- **自主发现率**：代理臂（a）每个样本的首次工具调用是否为 `search_mcp_tool`（先 invoke / 先 status 均记为失败）；分母 = 除 ⑤ 外的 5 原型 × 5 遍 = 25。
- **判分器可重算**：判分规则是 transcript 的纯函数，规则修订后离线重判，不重跑 API。

## 指标与统计

- **A/B 主表**：逐任务两臂通过率（5 遍中通过数）与配对差异；自主发现率；幻觉率（⑤）；搜索效率（每任务搜索次数、零命中后是否按路标恢复）；invoke 正确率（target 原样复制比例、参数结构错误率）；check_mcp_status 调用率（仅观测不设判据）；放弃率（触达轮次上限或无最终回答）。
- **token 实测（D-26）**：常驻 = 每臂一次隔离测量（同一 system + user 短消息 + 该臂工具清单，读 `usage.prompt_tokens` 减去无工具基线）——**API 实测 token 差值即 M2 gate 口径**，序列化字符数并报可离线复算；每任务累计 prompt/completion tokens（含搜索响应进上下文的滚动成本）作参考指标，不作 gate。
- **统计判据**：每原型固定 5 遍；任务级配对比较（代理臂通过任务数 ≥ 直挂臂 −3），不用总体百分比硬切（30 样本下 1 个翻转即 3.3pp，噪声与判据同量级）；LLM 无种子，全部结论以配对差异呈现。
- **消融对照表**：完整代理臂直接复用臂 a 的 30 样本（不重跑）vs 臂 c（关 description 注入）vs 臂 d（关双零路标），c/d 各 30 样本，看自主发现率/通过率变化。药效弱先改措辞重测；措辞救不回才视为架构问题、翻案入台账。

## 消融实现（harness 侧信道编辑）

产品代码零改动。harness 是 MCP 客户端，控制"模型看到什么"：

- **臂 c（关注入）**：拿到 `tools/list` 后，从 `search_mcp_tool` 的 description 中删除 `Connected services...` 注入块，只留静态触发文案，再转为 OpenAI tools 参数。
- **臂 d（关路标）**：拿到双零命中的搜索响应后，删除路标块，只留 "No tools matched..." 主体。

纪律与自检：

- **只删除、不添加、不改写**——剥离后剩余的必须是代理原话，反事实才干净。
- 剥离器带单测（钉死样例验证认得、剥得动）。
- 运行时强断言：臂 c 发给模型的 description 不得含 `Connected services`（注入块首行，search.py:92-100）；臂 d 的双零响应不得含 `Available services`（路标首行，search.py:268）。剥不动即失败，绝不静默降级成完整代理。
- `check_mcp_status` 的消融有意不做（排障通道，非任务路径）。

## 工程与运行编排

- **harness.py**：OpenAI 兼容 chat.completions + 工具循环；模型配置**只从项目根 `.env` 读取**（`DEEPSEEK_BASE_URL` + `DEEPSEEK_API_KEY` + `DEEPSEEK_MODEL`（默认 `deepseek-flash`）+ `DEEPSEEK_REASONING_EFFORT`（默认 `low`）），不读进程环境变量（shell 设置无效）；提供 `.env.example` 键名清单（不含值），`.env` 本身已在 `.gitignore`。**已核实（2026-11-05 文档侧）**：`deepseek-flash` 为当前真实模型名（V4.1-Flash 线，checkpoint 日期以开工复验返回的 model 字段为准）；`reasoning_effort` 是官方 chat.completions 顶层参数（取值 low/high/max），非私有字段；thinking 默认开启。开工时带 key 跑一次最小请求复验（顺带确认 tools 定义计入 `prompt_tokens`——隔离测量差值为 0 即异常）。**独立 DeepSeek 服务，与 opencode 无关**（D-21）。
- **reasoning_content 回传硬约束**：官方 Thinking Mode 规则——带 `tools` 的请求必须原样回传历史 assistant 消息的 `reasoning_content`，缺省即 400。消息组装直接 append 服务端返回的 message 对象（不手工挑字段）；transcript 已记录该字段，判分与离线重算不受影响。
- **transcript 为唯一真相源**：每跑一份 JSON，含全量消息、`reasoning_content`、逐次上游调用与响应、逐次 usage 与耗时、判分结论。`transcripts/` 按 run-id 落盘，存在且完整的跑次跳过 → 断点续跑。
- **`run.py` 编排**：默认全量（四臂，A/B 60 跑 + 消融 60 跑 = 120 跑）；旗标 `--arms` / `--tasks` / `--reps` 子集调试，`--judge-only` / `--report-only` 离线重算；串行执行 + 429 指数退避；轮次上限（16 次 assistant 轮）触顶记放弃。
- **密钥纪律**：key 只从项目根 `.env` 读取（`python-dotenv` 的 `dotenv_values` 直接取字典，不经 `os.environ`，shell 设置无效），`.env` 已在 `.gitignore`、不进仓库；`run.py` 启动 fail-fast 校验缺失并提示 `.env` 用法；OpenAI 客户端显式传 `api_key`，不依赖 SDK 的环境变量兜底；key 不进 transcript，出口断言不含 key 子串。需要填 key 的时机只有两个：开工前最小请求复验、正式跑套件。
- **依赖**：`openai` SDK 与 `python-dotenv` 进 `dependency-groups.dev`，产品零运行时依赖不变。
- **测试**：工具循环、schema 转换、判分规则、消融剥离器用假客户端/样例 transcript 做确定单测；不依赖 key。
- **复用**：`tests/e2e/demo_servers/` 直接复用；端口/等待工具抽到两树共用的小模块（不跨树 import conftest）。

## 依赖与关联

- 前置批次：B03（确定性底座已归档，排除代理自身 bug 干扰）
- 关联决策：D-01、D-04、D-11、D-13、D-14、D-15、D-20、D-21、D-22、D-23、D-26；愿景"成功画面"三条件即本批验收基线

## 记录

- 2026-11-04 落盘。若 M2 判据未达标：读 transcript 定位是文案问题（迭代 description/路标措辞后重跑）还是架构问题（翻案入台账，重估方向）。
- 2026-11-04 审核修订：任务规模统一为 6 原型 × 5 遍；补判分口径（含负样本与链式）；统计判据改为任务级配对比较；自主发现率分母排除负样本；模型名配置项化并加开工前核实步骤；指标补 token 实测。
- 2026-11-04 探索修订：三工具对齐 D-01 修订（check_mcp_status 纳入工具来源与 3 工具臂，指标补调用率仅观测）；M2 判据 −1 → −3（对齐 roadmap/vision 的 ≤10pp 口径）；消融明确排除 status；关联决策补 D-01/D-23。
- 2026-11-05 探索定案（B04 开工前）：
  - 任务文本三处修订——① 补工号 E1001（单轮无追问机会，参数值可判）；③ 改为 `east_1`（只考收窄回路，词汇失配专归 ②，避免两层失败无法归因）；⑥ 改为单消息双动作（满足"6 原型 × 5 遍 = 30 样本"，保持单轮、两臂对称）。
  - 判分补参数值口径与 ⑤ 的关键词启发式（否定能力词表 + 无完成断言，transcript 复盘兜底，不引 LLM 判分）。
  - 消融机制定为 harness 侧信道编辑（只删不改 + 运行时强断言），产品零改动，守住本批边界；明确排除 check_mcp_status。
  - token gate 口径钉死为 API 实测 prompt_tokens 差值（减无工具基线），字符数并报；每任务累计 tokens 仅报告。
  - 直挂臂全量工具扁平化为 `server__tool`（与代理 target 同构，撞名天然分流，匹配真实宿主行为）。
  - 上游编成定稿（hr / itops / audit / docs / fleet_gen，71 工具）；DeepSeek 文档侧核实完成（模型名真实、`reasoning_effort` 为官方顶层参数、thinking 默认开启）。
  - 工程补 transcript 为真相源、断点续跑、120 跑预算、`openai` 进 dev 依赖、密钥时机（仅最小复验与正式跑需要）。
- 2026-11-05 审核回写：③ 的 "report" 命中数 43 → 44（补 `itops__region_capacity` 的 description 命中）；工程节补 `reasoning_content` 回传硬约束（带 tools 缺省即 400）；④ 判分补 employee_id 校验与"无其他上游工具调用"；上游编成表修正 hr 的任务角色（② 仅作干扰源）；关联决策补 D-04/D-15；消融强断言钉死具体子串（`Connected services` / `Available services`）并写明完整代理臂复用臂 a；checkpoint 表述改为以开工复验 model 字段为准。
- 2026-11-05 密钥载体调整：key 改由项目根 `.env` 提供且**只认 `.env`**（`dotenv_values` 直接取字典，不读进程环境变量、shell 设置无效；`.env` 已在 `.gitignore`），不再要求命令行设置；新增 `.env.example` 键名清单，`python-dotenv` 入 dev 依赖。
- 2026-11-05 实施回写（6.2 冒烟暴露）：① 任务文本从"帮工号 E1001 的张三查年假余额"改为"帮查一下工号 E1001 的年假余额"——假上游 `hr.get_employee("E1001")` 返回 Ada Lovelace，姓名与工号矛盾诱使负责任的 agent 追加 `get_employee`/`search` 核实，被"无其他上游工具调用"误判失败；去掉姓名后任务仍测 L1 基线，且不引入与假数据的冲突。
- 2026-11-05 实施结果（全量 120 跑，deepseek-flash / reasoning_effort=low）：
  - **判分更正（归档后复盘）**：负样本完成断言词表的裸词 `starred` 把真实回答里的"点右上角 Star 按钮 / GitHub API `/user/starred/`"误判为完成声明，导致直挂臂 t5 由 5/5 误压为 4/5。已修词表（去裸词、加否定邻域排除）并用真实 transcript 回归；`--judge-only` 重判无需重跑 API。
  - **更正后 M2 判定：未达标**——代理臂通过 21/30 vs 直挂 25/30（差 **−4 ≈ −13.3pp**，超出 ≤10pp 预算）；自主发现率 100%（25/25）与常驻缩减 87.8%（685 vs 5624 tokens）两项达标。
  - 逐任务（a vs b）：t1 5/5–5/5；t2 0/5–0/5；t3 4/5–5/5；t4 5/5–5/5；t5 5/5–5/5；t6 2/5–5/5。
  - 差距归因（读 transcript）：−4 来自 t3(−1) 与 t6(−3)。代理臂失败几乎全部是"已找到正确目标、但额外调用了探索性上游工具"（t2 先调 `audit__list_audit_events`、t3 调 `itops__region_capacity`、t6 调 `docs__list_documents`/`docs__search_docs` 干扰项），而非没完成任务；t2 两臂全败（词汇失配）。这指向判分口径"无其他上游工具调用"对探索性行为偏严，以及 t2 任务难度，而非产品无法完成任务。
  - 消融（a/c/d）：通过数均 21/30、自发现率均 100%——现有任务规模下，关 description 注入与关双零路标未见对通过率或自主发现率的可测影响（触发文案 `SEARCH_TRIGGER_TEXT` 保留，弱模型仍首发搜索）。
  - 待裁决：按批次文档"未达标"流程，(a) 将"判分口径 vs 产品架构"作为重估议题，或 (b) 先重定义白名单（允许合理探索）后用已落盘 transcript 重判，再决定是否重跑。
  - 说明：30 样本、LLM 无种子，单次翻转即 3.3pp；−4 与噪声同量级，结论只作配对差异解读，不作总体百分比硬切。
