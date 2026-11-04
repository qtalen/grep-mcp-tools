# B05 release-pypi —— PyPI 公共包发布

> 状态：⬜ 未开工（前置 B03；B04 结果可引述）
> 形态：功能批

## 验收画面

干净环境（无本仓库、无 uv 缓存）：创建一份两行 config 指向包内置 demo 上游 → `uvx grep-mcp-tools@0.1.0 --config config.json` 拉起成功、可搜可调；公司内网主机（外部条件，需实测）`uv tool install grep-mcp-tools==0.1.0` 成功，DeepAgents 以 console script 形态拉起正常；PyPI 页面 README 完整、包体只含运行所需。

## 边界

- 不发内部 PyPI 镜像/私有源（开放项，待公司建）
- 不做版本自动发布流水线之外的多环境发布（只 tag→publish 单通道）
- v0.1.0 即首版占名，不追求功能超出 B01-B03 范围

## 实施要点

- **demo 上游入包**：随包发布 `python -m grep_mcp_tools.demo_server`（单个内置假上游，二十行级 FastMCP，**进 wheel**），README 快速开始与 config 示例指向它——干净环境 uvx 即可跑通。tests/e2e 的全套假上游（hr/itops/fleet_gen/broken 等）仍留仓库不进包。
- **打包**：wheel 瘦身——tests/、docs/ 不进 wheel（留在仓库与 sdist）；console script `grep-mcp-tools`（模块 `grep_mcp_tools`）；`pyproject.toml` 元数据完整（描述、分类、license、Python ≥3.11）。
- **公共包纪律**：包与 README 不得含任何公司内部信息（真实服务名、内网地址、真实 config）；示例一律用内置 demo 上游；README 部署节含 DeepAgents/langchain-mcp-adapters 挂载示例（command/args 形态）与 Claude Code 挂载示例。
- **README**：产品故事（上下文爆炸→按需检索，含价值账数字——以 B04 实测数据为准）；快速开始；config 格式说明；部署节（公司镜像源场景：uv/pip 经环境变量选源）；系统提示建议行（"外部能力都在 search_mcp_tool 后面，先搜后问"——产品控制范围外的最后一道保险）。
- **发布流**：git tag `v0.1.0` → GitHub Actions 构建 → `uv publish`（trusted publishing，无长期 token）；语义化版本；CHANGELOG 起始。
- **发布前检查单**：B03 全绿；`uvx` 干净环境演练（指向内置 demo 上游）；**复检 PyPI 名 `grep-mcp-tools` 仍可用**（2026-11 核实为时点事实，D-19）；**公司内网可达性在内部主机实测**（uv tool install + DeepAgents 拉起，外部条件验证）；包内容审查（无内部信息、无密钥）。

## 依赖与关联

- 前置批次：B03（质量门槛）；B04 结果可引述（非硬依赖）
- 关联决策：D-16、D-19

## 记录

- 2026-11-04 落盘。
- 2026-11-04 审核修订：内置 demo_server 模块入包（修干净环境验收不可演示的矛盾）；发布前复检包名与内网实测入检查单；归档动作具体化。
- 归档动作（发布后执行）：roadmap 登记表 B01-B05 各压缩为一行 `✅ + git tag 指针`，批次明细文件原地保留；里程碑 M3 翻 ✅；当前批次指针清空。
