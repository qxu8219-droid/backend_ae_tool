# T14 正式 REST/WS 业务绑定计划

沿用已批准 T14 清单（`../../../.trae/documents/robot-log-replay-platform-todolist.md`），executing-plans 内联实施、TDD、一次最终独立审查。当前 checkout/feature/p1-contracts 保留。远端只读，本轮无需访问远端或转换未确认标定。

## Global Constraints

- 实际会话使用 T12 SessionManager，原始日志用 T08 LogStore，采样/时间轴/分析复用 T09/T10/T13；单事件循环拥有所有业务状态，CPU 准备/查询放执行线程，不堵心跳。
- REST 控制只返回 accepted；实际 applied/rendered 才更新对应证据。快照取 RenderedFrame 冻结上下文，数值/质量/位图不得补写最新目标状态。
- 可靠 RPC 与普通最新目标分开；覆盖旧控制时取消旧 RPC 等待，迟到响应不使新控制失败。
- 同一 gap 配置用于采样与曲线，历史曲线明确查询归档源通道；可选虚拟轮角仍由采样器作为推算展示，不冒充原数据。
- WS 每次连接先发完整 connection_snapshot，新 connection_id/独立seq；服务端不声称浏览器 paired。数据丢失、媒体重连、日志/代次更换必须显式要求等待/重同步。
- 帧缓存16、客户端队列64、每会话最多8个WS；健康遥测10/20/30Hz独立限频，关键快照不降频。溢出/慢消费者关闭本连接并要求重连，不能无声配最新遥测。
- 已配置模式绑定真实服务，未配置默认app继续明确503/WS1013，保留契约测试；新增独立正式启动入口。SDK6.2/PhysX/回环端口；无真实机器人动作。

## Review Focus

旧控制RPC超时/迟到是否毒化新控制；load准备与delete/上传取消竞态；WS重连/重同步是否带旧帧或丢掉有效同代次告警；multipart限额是否在临时磁盘膨胀前执行并正确关闭文件；actor错误/缓存溢出/网关故障是否静默或造成错误的已呈现声明。

## Task 1: 业务协调与快照交付

文件：`app/services/replay_service.py`、`replay_session.py`、`prepared.py`、`app/sync/`；core必要的失败/丢失诊断接口；`tests/api/test_service.py`、`tests/sync/`。

- [ ] RED：真实CPU worker/ZMQ下load/控制、完整readback/rendered关联、快速覆盖控制、并行准备/删除、缓存有界、候选告警与错误终态。
- [ ] GREEN：每会话actor定时tick，转发可靠RPC/普通目标并接收真实回执；有界日志准备缓存；相同映射/gap；冻结FrameSnapshot；取消安全清理。
- [ ] 验证：协调服务/缓存测试通过，无生产假worker模式。

## Task 2: REST、上传限额与启动入口

文件：`app/api/routes.py`、`app/main.py`、`app/runtime.py`、上传限额中间件、启动脚本；`tests/api/test_runtime_api.py`。

- [ ] RED：18条路由、错误信封、未就绪/未知资源/无日志/冲突、实际CSV导入与原件保存、流式/声明超限、并发/删除、CORS、应用退出。
- [ ] GREEN：显式配置服务注入，默认未配置仍503；受限multipart读取，清理文件；生成契约并同步前端生成物。
- [ ] 验证：REST测试和契约/类型检查通过。

## Task 3: WS 状态、同步和背压

文件：`app/api/websocket.py`、同步broker/outbox、最小系统事件枚举扩展；`tests/sync/test_websocket.py`。

- [ ] RED：首包完整元数据、新连接seq、严格客户端信封、健康限频/帧不降频、快照身份、缺帧/媒体恢复/控制切换、慢消费者、删除/断开。
- [ ] GREEN：有界队列、发送期限、每连接计数/遥测率；当前上下文过滤；显式awaiting_pair/stalled/resync_required；保留同代次待呈现告警，旧代次不污染。
- [ ] 验证：真实WebSocket与多客户端测试通过。

## Task 4: 实际 HTTP/WS/GPU 全流程及审查

文件：`scripts/validate_api.py`、T14使用/证据、README/开发清单；P0页面适配验证路径或等效正式SEI诊断页。

- [ ] 实际启动配置服务，通过HTTP上传合成CSV、load/play/speed/seek/step/pause/stop/delete；WS核对状态和实际快照，独立H264核对同帧身份；覆盖worker退出/重新创建重载。
- [ ] 观察真实仿真图像，明确P0旧目录UUID不能直接消费正式目录；只增加验证适配，不冒称T15正式工作台完成。
- [ ] 后端全量、P0 CPU、Schema/TS、前端单测/构建与契约边界浏览器回归；一轮最终审查与必要RED→GREEN修复；提交并更新进度。
