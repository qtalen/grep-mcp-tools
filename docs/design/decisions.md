# 决策台账（ADR）

格式：编号 | 决策 | 理由 | 被否备选。修订须引用实现证据并保留 superseded 链条，禁止空口翻案。

| # | 决策 | 理由 | 被否备选 |
|---|---|---|---|
| D-01 | 双工具接口：`search_mcp_tool` / `invoke_this_tool`，无第三个工具 | 极简接口；撞名保险（避免与他方 `search_tools`/`invoke_tool` 重名） | 原名 search_tools/invoke_tools；精简列表+二次详情查询工具 |
| D-02 | target 命名空间 `server__tool`；搜索结果直接给出可复制 target | 跨上游同名工具不撞车；agent 永不手工拼 target，消灭幻觉拼串一类错误 | 让 agent 自己按 server+tool 拼 |
| D-03 | 多关键词 AND 优先（大小写不敏感子串） | 收窄语义自然：超量→加词，零命中→换词，回路自洽 | 纯 OR：超量时 agent 不知道扔哪个词，回路打转 |
| D-04 | AND=0 时 OR 兜底：按命中词数排序，返回前 10 条样品（可配） | 盲人 agent 首搜友好；死胡同变导航。样品默认 10 条，落盘时拟定，实施可调 | 纯 AND（对不知词汇的 agent 太苛刻）；双零直接死胡同 |
| D-05 | 返回完整工具定义（name+description+参数及类型） | agent 拿到即可直接构造 invoke，无需二次查询 | 精简列表+describe 工具（破坏两工具极简） |
| D-06 | invoke 纯透传：不截断、不净化、不设代理级超时 | 职责边界：上游返回多少是上游的事；治的是"定义爆炸"不是"结果爆炸" | 截断保险丝（可配置截断）；短超时 |
| D-07 | invoke 的 params 为 JSON 对象（schema `{"type":"object"}`） | 模型 function call 协议层原生吐 JSON 对象，客户端直通；无字符串转义易错步 | params 为 JSON 字符串 |
| D-08 | 上游传输 stdio + Streamable HTTP + SSE 全支持（2026-11-04 修订：增补 SSE——企业存量 MCP 服务大量使用旧式 HTTP+SSE 传输） | 企业 MCP 多态并存；官方 SDK 三种客户端全覆盖，增量成本低 | 只 stdio（砍需求）；只 stdio+Streamable HTTP（丢存量 SSE 服务） |
| D-09 | config 沿用 Claude Code mcpServers 同形；配置 fail-fast、上游 fail-soft | 零学习成本；配置错是用户的错必须吵，上游挂是环境的错必须忍 | 自创配置格式；上游错误也 fail-fast |
| D-10 | 索引：一服务一 JSONL、一行一工具、含 server/title/annotations 字段；原子写（tmp+rename）、强制 UTF-8 | 明文可审计；server 字段兼职作用域关键词；annotations 保住破坏性提示 | 数据库/内存索引（丢透明性） |
| D-11 | 不做语义检索 | **实测效果不好**（用户经验证据）；将来有人提议加 embedding 时引用本条 | embedding/向量检索 |
| D-12 | 纯 Python 引擎，不依赖 rg | 实证：PyPI ripgrep 15.x 无 Windows wheel，裸装现场 Rust 编译卡死；AND 语义在 rg 里别扭（管道法有文件名前缀污染 bug，单正则需 PCRE2 而 wheel 构建恰好没有）；rg 默认尊重 .gitignore 会跳过被 ignore 的索引目录；索引仅数 MB 用不到 rg 的速度 | rg 优先+Python 兜底（双引擎语义漂移风险）；`ripgrep==14.1.0` 钉版本依赖（0 star 个人重打包） |
| D-13 | description 动态注入服务清单（名+工具数，超上限降级） | 治 L0/L1（agent 不知道入口存在）：description 是代理与下游唯一常驻信道，按服务数增长而非工具数，约 300~500 token | 无注入；注入全量工具清单 |
| D-14 | 撤销浏览模式（空关键词返回全量目录） | 目录价值随规模反向变化——产品存在的理由是大目录，目录最大时它最没法用；1~2k token 成本换 dubious 收益 | 空关键词→全量工具名目录 |
| D-15 | 双零命中→响应内回声服务名路标；空关键词→用法提示+服务方向 | 撞墙才给路标，按需几十 token，永不预拉 | 预拉目录（见 D-14） |
| D-16 | 代理自身 v1 只做 stdio | 本地单用户是产品初心；HTTP 形态引入多客户端并发/鉴权一堆新问题 | 代理同时服务 HTTP（团队共享，留 v2） |
| D-17 | 定时刷新索引（默认 600s 可配）+ fail-soft 自愈 + 清单变化时向下游发 list_changed | 上游工具会变；定时刷新顺带自愈启动时挂掉的上游；list_changed 让 description 注入的清单跟着活 | 仅重启刷新（清单滞后）；响应上游 list_changed 事件即时重索引（v1 从简） |
| D-18 | 响应文本用英文 | LLM 通用语、跨模型最稳；中文关键词匹配不受影响（子串语言无关） | 中文响应文本 |
| D-19 | 分发走 PyPI 公共包（`grep-mcp-tools`，2026-11 核实未占用——时点事实，B05 发布前复检）；tag→GitHub Actions→trusted publishing；尽早发 0.1.0 占名 | 公司内网不通 GitHub、通 PyPI；开发留在 GitHub；公司侧 `uv tool install` 钉版本升级；内网可达性属外部条件，B05 验收须在公司主机实测 | git clone + uv sync 冻结 venv（内网不可达 GitHub，作废）；公司内部 PyPI 镜像（若将来建立可切换） |
| D-20 | 测试三层：确定性 E2E（六区 50+ 用例）/ agent 测试台（deepseek-flash low，独立服务）/ A/B 直挂基线+消融 | 代理对不对用确定性测试；agent 用得好不好必须真 LLM 量化；A/B 隔离"模型不行还是产品不行" | 仅确定性测试（测不到 L0/L1/L2 发现行为） |
| D-21 | agent 测试台模型：deepseek-flash，reasoning_effort=low，独立 DeepSeek 服务（env 配置，与 opencode 无关） | 弱模型低推理力度是最严格测试公民——路标只有真的好它才走得通；成本可控。模型名与力度均做成配置项不硬编码；开工前经 /models 或最小请求核实该服务确有此模型名 | 强模型（可能靠智力硬闯掩盖产品缺陷）；opencode 网关免费档（用户明确独立服务） |
| D-22 | 搜索噪音接受：类型词（如 "string"）命中几乎所有工具；E2E 锁定现状 | agent 极少拿类型词当关键词；这是"匹配域=索引行全部字段"的必然代价 | 匹配域排除 schema 键名（将来实测若痛再做） |

## 已接受风险

| 风险 | 决策 |
|---|---|
| 提示注入：上游工具描述（不可信文本）经搜索结果流入下游上下文 | 接受：与直挂上游同信任模型，代理不加重不缓解，不做净化 |
| 搜索噪音：搜 "string" 等 schema 高频词命中一切 | 接受（编号决策见 D-22）：E2E 锁定现状，将来若痛再排除 schema 键名匹配域 |
| 词汇失配（产品核心赌注） | 缓解：路标回路 + description 注入；B04 A/B 实验量化验证，证伪则重估方向 |
