# Spec Delta

## ADDED Requirements

### Requirement: 路由不因启动定格

invoke 的路由表 SHALL 恒含全部配置服务（不因启动握手失败而缺席）；每次调用按该服务当前连接状态判定可达性——不可达时报代理级错误，判定与错误文案不变（原因取自健康注册表）。启动时不可达而经刷新自愈恢复的服务，其 invoke SHALL 正常透传成功。

#### Scenario: 启动时死刷新后可调

- **WHEN** 上游 itops 启动握手失败，一轮刷新后自愈恢复，agent 以 `itops__search` 调用
- **THEN** 调用正常透传并返回上游结果，不因启动时不可达而被拒绝

#### Scenario: 不可达错误文案不变

- **WHEN** 上游 hr 当前不可达，agent 以 `hr__get_employee` 调用
- **THEN** 返回代理级错误，自包含注册表中的不可达原因，与"服务不在 config"文案可区分

### Requirement: target 校验随刷新保鲜

工具存在性校验 SHALL 以最近一轮刷新的索引为准，每轮刷新后无条件重建：上游运行中新增的工具在下一轮刷新后 SHALL 可通过校验并透传；上游已删除的工具在下一轮刷新后 SHALL 得"工具不存在"代理级错误（提示重新搜索）。

#### Scenario: 刷新后新工具可调

- **WHEN** 上游新增工具 `region_cn_report` 且一轮刷新完成，agent 以该 target 调用
- **THEN** 校验通过并透传至上游

#### Scenario: 已删工具刷新后拒绝

- **WHEN** 上游删除工具 `legacy_export` 且一轮刷新完成，agent 仍以旧 target 调用
- **THEN** 返回代理级错误提示工具不存在并建议搜索，不透传给上游

#### Scenario: 刷新前维持旧校验

- **WHEN** 上游已删除某工具但刷新轮尚未到达，agent 以该 target 调用
- **THEN** 校验仍按上一轮索引通过，调用透传后由上游返回其错误（代理不预判）
