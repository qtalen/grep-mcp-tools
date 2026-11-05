# 路线图（三个账本）

## 里程碑表

| 里程碑 | 定义 | 判据 | 状态 |
|---|---|---|---|
| M1 内部可用 | 代理全行为面 + 确定性质量全绿 | B01-B03 完成；`pytest tests/e2e` 全绿 <60s；probe 可演示 | 🔨 进行中（B01 ✅ 2026-11-04；待 B02-B03） |
| M2 效果已证明 | 三条成功标准由 A/B 实验达标 | B04 完成：成功率差 ≤10pp、上下文省 ≥80%、自主发现率 ≥80% | ⬜ |
| M3 公开发布 | PyPI 0.1.0 可安装 | B05 完成：干净环境 `uvx grep-mcp-tools@0.1.0` 拉起可用 | ⬜ |

> **当前批次指针**：下一个开工批次 = **B02**（登记表顺序即执行顺序；此指针随归档更新）。

## 批次登记表（顺序即执行顺序，可重排）

| 批次 | 一句话意图 | 形态 | 状态 | 依赖 |
|---|---|---|---|---|
| [B01 行走骨架](batches/B01-walking-skeleton.md) | 最小代理全链路 + 交互探针 | 功能批 | ✅ 已归档 2026-11-04 | — |
| [B02 行为面补全](batches/B02-full-behavior.md) | 定时刷新、description 注入、fail-soft 自愈全套 | 功能批 | ⬜ 未开工 | B01 |
| [B03 E2E 全矩阵](batches/B03-e2e-matrix.md) | 六区 50+ 用例确定性自动化 | 地基批 | ⬜ 未开工 | B02 |
| [B04 agent 测试台](batches/B04-agent-harness.md) | 真 LLM 量化效果：A/B 基线 + 消融 | 地基批 | ⬜ 未开工 | B03 |
| [B05 发布](batches/B05-release-pypi.md) | PyPI 公共包 0.1.0 | 功能批 | ⬜ 未开工 | B03（B04 结果可引述） |

## 开放项与遗留总账（已立项想法 + 待修遗留）

| 项 | 说明 | 状态 |
|---|---|---|
| 公司内部 PyPI 镜像切换 | 若公司建 devpi/nexus 镜像，分发从公共 PyPI 切内部源，一行配置 | 💤 待外部条件 |
| 多模型矩阵 | 强/弱模型各跑一轮 agent 套件，摸清产品赌注的模型边界 | 💤 待 M2 |
| 代理 HTTP 团队共享形态 | 多客户端并发、索引共享、鉴权设计（见 D-16） | 💤 v2 候选 |
| 治理/白名单 | 按下游客户端过滤可搜可调的上游工具（企业管控诉求） | 💤 v2 候选 |
| 上游 list_changed 精细化 | 从定时刷新升级为事件驱动即时重索引（见 D-17） | 💤 v2 候选 |
| B01 遗留：startup summary 把 live 0 工具计作 indexed | 期望文案改为 `x/y upstreams connected`（握手成功即 connected，0 工具上游也计入）；B03 A 区加断言 | 💤 待 B03 |
| B01 遗留：`terminate_on_close` 对不可达 HTTP 上游可能多等 30s | 已裁决 D-27：`terminate_on_close=False`（不发 DELETE）；B03 生命周期用例断言退出耗时 | ✅ 裁决 D-27（B03 落地） |
| 同一 JSON 文件内字面重复键无法检测 | `json.loads` 后写覆盖语义（stdlib 限制）；大小写不敏感重名已 fail-fast 拦截 | 💤 接受现状 |

## 记录

- 2026-11-04 探索修订：B02 意图移除"HTTP 上游"（三传输握手提前入 B01）。
- 2026-11-04 B01 归档：登记行翻 ✅ 压索引；M1 里程碑标为进行中（1/3 批次）；当前批次指针推进到 B02；B01 审核遗留（startup summary 措辞、`terminate_on_close` 多等 30s、json 文件内重复键检测）转入开放项总账。B01 契约已回写 architecture（0 工具删旧索引、读超时无限、BOM、settings 校验、相对 index_dir），新裁决 D-24 已入台账。
- 2026-11-04 设计审核回写：开放项表改名（并入 B01 遗留）；startup summary 期望文案定为 `x/y upstreams connected`；`terminate_on_close` 裁决 D-27（`terminate_on_close=False`）；同一 JSON 文件内字面重复键维持"接受现状"，不进 E2E 正文。
