# AGENTS.md — grep-mcp-tools

路标文件：这里只有指针，内容都在设计圣经。两处不重复，避免打架。

## 设计圣经（涉及产品决策时必读）

- 愿景与边界：`docs/design/vision.md`
- 架构与黄金律：`docs/design/architecture.md`
- 决策台账（ADR，翻案须走台账纪律）：`docs/design/decisions.md`
- 迭代规划与真实进度：`docs/design/roadmap.md`
- 批次计划（实施某批次前必读该文件）：`docs/design/batches/`

## 开发流程纪律

### @reviewer 审核纪律

以下情形必须调用 `@reviewer` 子智能体审核相应产出物，并对审核结论逐条验证：确认属实的问题及时修复；仅当把握不准、需要用户裁决时才抛给用户：

1. 变更 `docs/design/` 下的设计圣经后——以变更前内容为基线审核本次变更，同时对照用户与 AI 讨论的内容，检查设计圣经是否正确反映了讨论确定的产品实施计划；已被否定的提案不作为审核依据。
2. `/opsx-propose` 的 change 制品生成完毕后——对照设计圣经审核 change 制品。
3. `/opsx-apply` 阶段代码生成并自检完毕后——对照 change 制品审核代码。

### TDD 纪律

`/opsx-apply` 阶段实际写代码之前，必须先写单元测试，覆盖正常情况、异常情况、边缘情况；再按 TDD 规范编写实际业务代码，使测试通过。

### 批次落盘工作流

对已经讨论完毕的批次实施计划，用户要求落盘设计圣经时，按以下顺序执行，不跳步：

1. 落盘设计圣经。
2. 对设计圣经进行审核并修复（见 @reviewer 审核纪律第 1 条）。
3. 调用 `/opsx-propose` 开始写 change 制品。
4. 对照设计圣经对 change 制品进行审核并修复（见第 2 条）。
5. 调用 `/opsx-apply` 开始实现代码。
6. 对照 change 制品审核代码并修复（见第 3 条）。
7. 调用 `/opsx-archive` 归档。
8. 提交并推送代码。

审核与修复的过程遵循 @reviewer 审核纪律章节：逐条验证审核结论，确认属实的问题及时修复，把握不准、需要用户裁决时才抛给用户。

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
