# Spec Delta

## ADDED Requirements

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
