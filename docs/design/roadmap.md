# 路线图（三个账本）

## 里程碑表

| 里程碑 | 定义 | 判据 | 状态 |
|---|---|---|---|
| M1 内部可用 | 代理全行为面 + 确定性质量全绿 | B01-B03 完成；`uv run pytest tests/e2e -n 4` 全绿（Windows 本机热跑 <150s，见 D-28）；probe 可演示 | ✅ 已达成 2026-11-05（B01-B03 全归档；e2e 75 例、`-n 4` 112.9~132.8s；probe 管道冒烟可用） |
| M2 效果已证明 | 三条成功标准由 A/B 实验达标 | B04 完成：成功率差 ≤10pp、上下文省 ≥80%、自主发现率 ≥80% | ✅ 已达成 2026-11-05（D-29 主口径：代理 25/30 vs 直挂 26/30 = 差 −1；自发现率 100%、常驻缩减 87.8%。严格口径对照：21/30 vs 25/30，差 −4） |
| M3 公开发布 | PyPI 0.1.0 可安装 | B05 完成：干净环境 `uvx grep-mcp-tools@0.1.0` 拉起可用 | ⬜ |

> **当前批次指针**：下一个开工批次 = **B05**（登记表顺序即执行顺序；此指针随归档更新）。

## 批次登记表（顺序即执行顺序，可重排）

| 批次 | 一句话意图 | 形态 | 状态 | 依赖 |
|---|---|---|---|---|
| [B01 行走骨架](batches/B01-walking-skeleton.md) | 最小代理全链路 + 交互探针 | 功能批 | ✅ 已归档 2026-11-04 | — |
| [B02 行为面补全](batches/B02-full-behavior.md) | 定时刷新、description 注入、fail-soft 自愈全套 | 功能批 | ✅ 已归档 2026-11-05 | B01 |
| [B03 E2E 全矩阵](batches/B03-e2e-matrix.md) | 六区 75 例确定性自动化 | 地基批 | ✅ 已归档 2026-11-05 | B02 |
| [B04 agent 测试台](batches/B04-agent-harness.md) | 真 LLM 量化效果：A/B 基线 + 消融 | 地基批 | ✅ 已归档 2026-11-05 | B03 |
| [B05 发布](batches/B05-release-pypi.md) | PyPI 公共包 0.1.0 | 功能批 | ⬜ 未开工 | B03（B04 结果可引述） |

## 开放项与遗留总账（已立项想法 + 待修遗留）

| 项 | 说明 | 状态 |
|---|---|---|
| 公司内部 PyPI 镜像切换 | 若公司建 devpi/nexus 镜像，分发从公共 PyPI 切内部源，一行配置 | 💤 待外部条件 |
| 多模型矩阵 | 强/弱模型各跑一轮 agent 套件，摸清产品赌注的模型边界；B04 已给出弱模型基线数据，M2 达成后可启动 | 💤 待启动（M2 已达成） |
| B04 遗留：探索性调用判分偏严 | 判分"无其他上游工具调用"把代理臂的合理核实/探索（t2 先调 `audit__list_audit_events`、t3 调 `itops__region_capacity`、t6 调 docs 干扰项）判为失败；严格口径的 −4 差距主要源于此 | ✅ 已裁决 D-29（2026-11-05；主口径重判后 M2 达标，严格口径保留对照） |
| B04 观察：t2 词汇失配两臂全败 | 工具描述仅 "audit trail retention"，代理与直挂均 0/5；核心赌注的负面样本，需更大样本/多任务验证后再下结论 | 💤 待观察 |
| B04 判分误判（已修） | 负样本完成断言裸词 `starred` 误伤"点 Star 按钮 / GitHub API 路径"，曾使直挂臂 t5 5/5→4/5 并假象达成 M2；已修词表 + 否定邻域排除 + 真实 transcript 回归，重判不需重跑 API | ✅ 已修复（2026-11-05） |
| 代理 HTTP 团队共享形态 | 多客户端并发、索引共享、鉴权设计（见 D-16） | 💤 v2 候选 |
| 治理/白名单 | 按下游客户端过滤可搜可调的上游工具（企业管控诉求） | 💤 v2 候选 |
| 上游 list_changed 精细化 | 从定时刷新升级为事件驱动即时重索引（见 D-17） | 💤 v2 候选 |
| B01 遗留：startup summary 把 live 0 工具计作 indexed | 期望文案改为 `x/y upstreams connected`（握手成功即 connected，0 工具上游也计入）；B03 A 区加断言 | ✅ B03 落地（2026-11-05；`health.startup_summary` + A 区断言） |
| B01 遗留：`terminate_on_close` 对不可达 HTTP 上游可能多等 30s | 已裁决 D-27：`terminate_on_close=False`（不发 DELETE）；B03 生命周期用例断言退出耗时 | ✅ 裁决 D-27（B03 落地） |
| 同一 JSON 文件内字面重复键无法检测 | `json.loads` 后写覆盖语义（stdlib 限制）；大小写不敏感重名已 fail-fast 拦截 | 💤 接受现状 |

## 记录

- 2026-11-04 探索修订：B02 意图移除"HTTP 上游"（三传输握手提前入 B01）。
- 2026-11-04 B01 归档：登记行翻 ✅ 压索引；M1 里程碑标为进行中（1/3 批次）；当前批次指针推进到 B02；B01 审核遗留（startup summary 措辞、`terminate_on_close` 多等 30s、json 文件内重复键检测）转入开放项总账。B01 契约已回写 architecture（0 工具删旧索引、读超时无限、BOM、settings 校验、相对 index_dir），新裁决 D-24 已入台账。
- 2026-11-04 设计审核回写：开放项表改名（并入 B01 遗留）；startup summary 期望文案定为 `x/y upstreams connected`；`terminate_on_close` 裁决 D-27（`terminate_on_close=False`）；同一 JSON 文件内字面重复键维持"接受现状"，不进 E2E 正文。
- 2026-11-05 B02 归档：登记行翻 ✅ 压索引；M1 里程碑标为进行中（2/3 批次）；当前批次指针推进到 B03。B02 契约已同步主 specs（新建 tool-description capability，tool-index/tool-invoke/upstream-health 合入刷新与通知 requirements）；实施记录（含 SDK 2.3.0 新代协议通知投递认知）见归档 change 的 design.md。
- 2026-11-05 B03 落盘：M1 判据补耗时口径（Windows 本机 150s、设计目标热跑 ~100s；`<60s` 改作将来 Linux CI 闸门，D-28）；B03 批次文档补执行结构（subprocess 黑盒、共享/独享 fixture、刷新簇、`wait_until` + stderr 事件源）、假上游补齐（gizmos/empty/blackhole/mutable、fleet_gen `--count`）与产品改动面（startup summary 措辞、Windows 孤儿回收先测后修）。
- 2026-11-05 B03 实施回写：M1 判据改 `-n 4` 并行（D-28 回写：75 例串行 ~300s、`-n 4` 129~133s；`-n 6` 会因并发争抢握手超时）；孤儿回收实测已达标、未改产品代码；新增 dev 依赖 pytest-xdist；B01 遗留 startup summary 措辞翻 ✅。
- 2026-11-05 B03 归档：登记行翻 ✅ 压索引；M1 里程碑达成（3/3 批次）；当前批次指针推进到 B04。B03 契约已同步主 specs（新建 connection-lifecycle；upstream-health 启动摘要改 connected）；实施与审核记录见归档 change 的 design.md。
- 2026-11-05 B04 归档：登记行翻 ✅ 压索引；当前批次指针推进到 B05。本批为纯测试设施（`skip_specs`，主 specs 无变化）；新增 `tests/agent/` 四臂测试台与 `tests/shared/proc.py`，dev 依赖新增 `openai`/`python-dotenv`。实施结果：代理 21/30 vs 直挂 24/30、自发现 100%、常驻缩减 87.8%；消融 a/c/d 无差异。观察项（探索性调用判分偏严、t2 词汇失配两臂全败）转入开放项总账；实施与审核记录见归档 change 的 design.md。
- 2026-11-05 B04 判分更正（归档后复盘）：负样本完成断言词表裸词 `starred` 误伤真实回答（"点 Star 按钮" / GitHub API `/user/starred/`），直挂臂 t5 4/5→5/5，**M2 由"边界达标"更正为"未达标"**（代理 21/30 vs 直挂 25/30，差 −4 ≈ −13.3pp）。已修词表 + 否定邻域排除 + 真实 transcript 回归测试；`--judge-only` 重判，未重跑 API。
- 2026-11-05 B04 口径裁决（D-29）：用户裁决将任务成功率改为"任务完成"口径（同服务只读探索允许、跨服务/写操作仍失败），严格口径保留仅作对照。重判后 **M2 达成**：主口径代理 25/30 vs 直挂 26/30（差 −1）、自发现 100%、常驻缩减 87.8%；严格口径 21/30 vs 25/30（差 −4）。批次文档判分表、任务定义与报告口径同步；代码修复旧 verdict 兼容、写工具重复调用、严格口径单源统计。
