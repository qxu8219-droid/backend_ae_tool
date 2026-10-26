# T12 会话生命周期实现计划

> 执行方式：沿用已批准的开发清单，使用 executing-plans 内联实施、TDD 和一次最终独立审查。当前用户要求继续现有项目，因此保留现有 feature/p1-contracts checkout。

目标：提供单会话异步生命周期、独立 worker/网关健康、可靠退出和新会话重载基础。依据：`../../../.trae/documents/robot-log-replay-platform-todolist.md` 的 T12 及原项目计划。HTTP/WS 业务绑定属于 T14，正式浏览器回放属于 T15。

全局约束：IsaacSim 6.2.0/PhysX；SDK 不导入 app；所有新增监听限本机；真机/服务器严格只读，本轮无需远端操作；只用合成轨迹验证。普通 RPC 10s、冷启动允许 120s、运行中故障目标 3s 内进入错误，SIGTERM 最多等 10s 再 SIGKILL。错误后创建新 sid 并从首帧重载。

## Task 1: 进程与端口资源、原生媒体绑定

文件：`app/core/config.py`、`ports.py`、`processes.py`、`process_child.py`；`sim_worker/native/rtsp_loopback.c`、构建脚本、worker 启动器；`tests/core/test_resources.py`。

- [x] RED：真实 socket 验证独占分配、耗尽与释放；真实子进程验证 TERM/KILL、父进程退出后子进程清理；原生 bind 拦截测试验证指定 RTSP 端口从通配地址转为回环而其他端口不受影响。
- [x] GREEN：只管理自己创建的进程组；端口租约明确转交 bind 所有者。项目内共享库只拦截指定端口的 wildcard bind，其他 bind 原样传递；只注入 worker 环境。原生监听二次核验保持默认拒绝，未成功隔离则启动失败。
- [x] 验证：`pytest tests/core/test_resources.py`；真实 SDK 通过受限绑定启动且无通配 RTSP 监听。

## Task 2: 并发 RPC 与媒体网关管理

文件：`app/core/worker_client.py`、`gateway.py`；`tests/core/test_worker_client.py`、`test_gateway.py`。

- [x] RED：真实 ZMQ 验证握手、并发 request_id/阶段关联、错误身份、超时与关闭时清空 pending；真实本地 HTTP/MediaMTX 验证受限配置、注册/删除自有路径、已有路径冲突、网关失联/恢复。
- [x] GREEN：asyncio 单事件循环持有 socket，RPC 等待不阻塞心跳/事件/退出监听；HWM/消息/事件/待请求有界。MediaMTX 固定 v1.21.2 校验二进制；托管模式只管理自己的进程，外部模式只操作自己的唯一 sid 路径，恢复后重新确认拉流配置。
- [x] 验证：核心子集测试和官方二进制配置验证。worker ready 与媒体 publishing 分开；没有源帧时不宣称已发布。

## Task 3: 会话状态机与应用退出

文件：`app/core/session_manager.py`、`session.py`；`sim_worker/main.py`、`runtime_62.py` 的真实启动相位；`app/main.py` 的可注入 lifespan；`tests/core/test_session_manager.py`。

- [x] RED：并发创建/容量冲突、唯一 sid、相位与独立期限、慢启动、启动失败/超时、运行中退出/失去心跳、删除幂等/创建中删除、重复重建、网关故障不改变 worker 状态、应用关闭清理。
- [x] GREEN：async create/get/delete/close 与 bounded 事件；create 即占用单会话槽，失败保留诊断并清理资源，新建需先完成删除。删除期间阻止重新创建。初始化期限与 ready 后 3s 心跳期限分开；流程始终在 finally 清理，仅在完成资源回收后释放槽。
- [x] 验证：`pytest tests/core/test_session_manager.py`，真实 CPU 子进程/真实 ZMQ fixture 只替代昂贵 SDK；生产没有假 worker 开关。

## Task 4: 实际集成、文档与最终审查

文件：`scripts/validate_sessions.py`、`docs/session-lifecycle-v1.md`、T12 验证报告、README/开发清单。

- [x] 实际 SDK + 托管 MediaMTX：创建、等待就绪、发送合成首帧、确认网关源就绪；暂停保持；kill -9 worker 后小于 3s 错误；删除后新 sid 从首帧重载；连续重建/创建中删除/关闭管理器，检查本轮端口和进程无残留。
- [x] 实际网关停启与外部模式：worker 继续健康，源就绪恢复；删除后自有路径消失，外部进程不被终止。
- [x] 全量后端、P0 CPU、Schema/TS 检查；若生成物改变，前端单测/构建。GPU性能验收单独执行，不与构建争抢资源。
- [x] 一次最终独立审查；问题按影响分级，重要项 RED→GREEN 修复并验证；记录未覆盖项，再提交和更新清单。

审查重点：取消与 spawn 竞态导致孤儿进程；已退出 leader 的存活子进程；RPC 超时后迟到回复污染新请求；外部网关路径冲突/不可达时的删除所有权；未就绪和恢复状态冒充媒体已发布。这些路径分别由对应任务测试覆盖。
