# Spec Delta

## Purpose

定义代理与上游连接的进程生命周期：下游断开时整棵进程树按进程组回收，不留孤儿子进程（Windows 专项保障）；退出耗时不因不可达上游被拖延。

## ADDED Requirements

### Requirement: 下游断开时的子进程回收

下游 stdio 客户端断开（stdin EOF 或连接关闭）时，代理 SHALL 在有限时间内退出，并回收其 spawn 的全部 stdio 上游子进程——按进程组终止，不留孤儿。Windows 平台适用同一保证（实现可借助 Job Object 等手段）。

#### Scenario: 断开后无孤儿

- **WHEN** 下游客户端在与代理的会话中主动断开
- **THEN** 代理进程退出，且其 spawn 的全部 stdio 上游子进程在有限时间内全部终止，无残留进程

### Requirement: 退出耗时不因不可达上游拖延

代理 SHALL 在关闭上游连接时不因目标不可达而等待连接级超时：配置中存在不可达的 Streamable HTTP 上游时，下游断开后代理进程 SHALL 在 10 秒内退出（不出现 30s 级拖延）。

#### Scenario: 不可达 HTTP 上游不拖退出

- **WHEN** config 含一个不可达的 Streamable HTTP 上游，下游客户端断开
- **THEN** 代理进程在 10 秒内退出，stderr 无异常堆积
