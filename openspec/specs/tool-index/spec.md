# tool-index Specification

## Purpose
定义上游握手与明文 JSONL 索引的落地行为。索引是系统的信任基础与人可审计面：一服务一文件、一行一工具、原子替换。

## Requirements
### Requirement: 三传输握手并发急切执行

代理 SHALL 在应答下游 initialize 之前完成对全部上游的握手与索引落盘（急切），且握手跨上游并发执行——总等待时长近似单上游 `settings.handshake_timeout` 上限，不随上游数线性增长；单个上游故障不得中止启动（有限等待）。

#### Scenario: 三传输混合握手

- **WHEN** config 含 stdio、Streamable HTTP、SSE 三种传输的上游且全部可达
- **THEN** 三者全部握手成功并各自落成索引文件

#### Scenario: 急切——initialize 前索引就绪

- **WHEN** 下游客户端完成与代理的 initialize 握手
- **THEN** 全部上游索引已落盘，首个搜索调用即可命中

#### Scenario: 并发上限度

- **WHEN** 多个上游中有一个握手会超时（`settings.handshake_timeout` 内无响应）
- **THEN** 代理整体启动耗时不超过该超时上限的可观余量，未按上游数线性叠加

### Requirement: JSONL 索引落地

每个握手成功且工具数 >0 的上游 SHALL 生成索引文件 `<index_dir>/<server>.jsonl`：一行一工具，字段为 `server`/`name`/`title`/`description`/`inputSchema`/`annotations`；上游缺 `title` 或 `annotations` 时对应字段写 `null` 保形；工具描述含换行时该行仍为合法单行 JSON；全部文件 IO 强制 UTF-8；文件以 tmp+rename 原子替换（重启重写而非追加）。索引目录默认为 config 文件同目录下 `tools_index/`，`settings.index_dir` 可覆盖。

#### Scenario: 字段齐全写入

- **WHEN** 某上游工具定义含全部字段
- **THEN** 索引行含六个字段且值与上游定义一致

#### Scenario: 缺 title 与 annotations 写 null

- **WHEN** 上游工具定义无 `title` 与 `annotations`
- **THEN** 索引行对应字段值为 `null`，行结构不变

#### Scenario: 多行描述单行化

- **WHEN** 某工具 description 含换行符
- **THEN** 其索引行仍为单行合法 JSON，换行被转义

#### Scenario: 重启替换不追加

- **WHEN** 代理对同一 config 连续启动两次
- **THEN** 索引文件内容为单次快照，无重复行

#### Scenario: index_dir 覆盖

- **WHEN** `settings.index_dir` 指定绝对路径
- **THEN** 索引文件写入该目录而非默认目录

### Requirement: 握手失败 fail-soft

上游握手失败（连接拒绝、超时、进程秒退）SHALL 跳过该上游并向 stderr 记日志，不生成其索引文件，不阻塞其他上游握手与代理启动；失败事实记入健康注册表（见 upstream-health）。

#### Scenario: 死条目跳过

- **WHEN** 某上游 url 指向无人监听的端口
- **THEN** 该上游无索引文件、代理正常启动、其余上游索引不受影响

#### Scenario: 握手超时不阻塞

- **WHEN** 某上游握手耗时超过 `settings.handshake_timeout`
- **THEN** 该上游被放弃并记日志，代理启动不被无限拖延

### Requirement: 0 工具上游

握手成功但 `list_tools` 返回空的上游 SHALL 不生成索引文件（若上一轮存在同名索引文件，SHALL 删除），且保留在服务清单中显示 0 tools（健康注册表记 `live 0 tools`）。

#### Scenario: 0 工具上游

- **WHEN** 某上游握手成功且无任何工具
- **THEN** 无该服务的索引文件，健康清单含该服务且状态为 live 0 tools

#### Scenario: 上一轮索引残留被清除

- **WHEN** 索引目录已存在该服务上一轮的索引文件，而本轮握手成功但工具数为 0
- **THEN** 旧索引文件被删除，搜索不会命中其中的幽灵工具

### Requirement: 同名工具互不覆盖

不同服务的同名工具 SHALL 各占各自索引文件中的一行，以 `server` 字段区分，互不覆盖。

#### Scenario: 两服务同名工具

- **WHEN** hr 与 itops 都定义名为 `search` 的工具
- **THEN** 两份索引各含一行，target 分别为 `hr__search` 与 `itops__search`

### Requirement: 定时刷新轮

代理 SHALL 按 `settings.refresh_interval` 秒间隔周期执行刷新轮：对每个上游——连接仍活着则复用连接重拉 `list_tools`，连接已死或不可用则重试握手（自愈）；重拉与重握手均 SHALL 受 `settings.handshake_timeout` 上界约束（超时按失败处理，防半死上游挂住整轮）；随后按既有索引纪律原子重写该上游的 jsonl（替换不追加、0 工具删除残留文件、写盘失败按 fail-soft 记 unreachable 且不得谎报 live）。启动时不可达的上游在刷新轮中恢复时 SHALL 自动补进索引并翻转为 live。

#### Scenario: 活连接重拉刷新

- **WHEN** 某上游连接存活且期间新增了一个工具，一轮刷新完成
- **THEN** 该上游 jsonl 行数与新工具集一致，搜索可见新工具

#### Scenario: 启动时死刷新中复活

- **WHEN** 某上游启动握手失败（无索引文件），随后服务恢复且一轮刷新完成
- **THEN** 该上游生成索引文件、注册表翻转为 live，搜索与 invoke 均可达

#### Scenario: 刷新写盘失败不谎报

- **WHEN** 一轮刷新中某上游索引写盘失败（如目录只读）
- **THEN** 该上游记 unreachable 且原因自包含，不出现"注册表 live 但索引缺失"的谎报态

### Requirement: 轮串行不重叠

刷新轮 SHALL 串行执行：同一时刻至多一轮在进行，轮与轮的实际间隔为 `max(refresh_interval, 单轮耗时)`——慢轮顺延下一轮，永不并发重叠。

#### Scenario: 慢轮顺延

- **WHEN** 某轮刷新因一个上游接近 `handshake_timeout` 而耗时超过 `refresh_interval`
- **THEN** 下一轮在该轮结束后才开始，任一时刻不存在两轮刷新并发进行

### Requirement: 刷新锁纪律

刷新轮对每个上游的操作 SHALL 非阻塞抢占该上游的 invoke 串行锁：锁被长调用占用（invoke 路径无代理级超时）时跳过该上游本轮刷新——该上游维持上一轮快照（旧但自洽：清单仍可见、invoke 可达性按注册表），SHALL NOT 等待锁、SHALL NOT 中断进行中的调用、SHALL NOT 因此阻塞同轮其他上游的刷新。

#### Scenario: 长调用进行中跳过该上游

- **WHEN** 上游 A 的一个慢 invoke（长于本轮刷新时刻）进行中，刷新轮到达
- **THEN** A 被跳过本轮（索引与注册表维持上一轮状态），同轮其他上游正常刷新，慢调用不被中断

#### Scenario: 下一轮恢复刷新

- **WHEN** 上一轮因锁占用跳过了上游 A，慢调用结束后下一轮刷新到达
- **THEN** A 正常参与刷新并更新索引与注册表

### Requirement: 同回合自愈

刷新轮检测到上游连接任务死亡或活连接重拉 `list_tools` 失败时，SHALL 在同一回合内立即停止旧连接并重试握手，不等下一轮；重试成功则按正常刷新落地（索引、注册表翻转），重试失败则记 unreachable 且原因自包含。三传输（stdio / Streamable HTTP / SSE）SHALL 走同一自愈路径，不分传输特判。

#### Scenario: 中途死亡同回合重握手

- **WHEN** 某上游服务进程在两轮刷新之间被终止，下一轮刷新检测到连接已死
- **THEN** 该轮内先停止旧连接再重试握手：服务已重启则恢复 live 并重建索引，服务仍死则记 unreachable 及原因

#### Scenario: 活连接重拉失败同回合处理

- **WHEN** 某上游连接任务仍存活但重拉 `list_tools` 抛错（如服务半死）或拉取超过 `handshake_timeout`
- **THEN** 同回合内停止该连接并重试握手，不把半死连接留到下一轮

#### Scenario: stdio 上游同样自愈

- **WHEN** stdio 型上游子进程退出，下一轮刷新到达
- **THEN** 自愈路径与 HTTP/SSE 一致：stop + 重握手（代理重新 spawn 子进程），无传输特判分支
