# T14 正式 REST/WS 运行方式

配置运行入口已把会话、日志、时间轴、分析和实际 worker 接到 19 条 REST 路由及 `/ws/sessions/{sid}`。当前只支持本机单会话、空场景、R1Pro 6.x 资产、IsaacSim **6.2.0 / PhysX**；正式前端媒体已由 T15 接入，B041 标注查询由 T16 增加。首次运行见 [本地交付手册](delivery-v1.md)。

## 启动

先按 [契约安装说明](contracts-v1.md)安装后端隔离环境、按 [worker 说明](worker-v1.md)准备 SDK 和资产，并构建项目内 RTSP 回环绑定库。新增依赖 `websockets==15.0.1` 仅安装在后端 `.venv`，不改变 SDK 环境。

```bash
bash scripts/build_rtsp_loopback.sh
ISAACSIM_PYTHON=/home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python \
  bash scripts/run_backend.sh
```

入口是 `app.runtime:create_runtime_app --factory`；脚本绑定 `127.0.0.1:8000`、单进程，并限制 WS 单消息 64KiB、传输队列 16。不要给它增加多个 uvicorn worker。`ISAACSIM_PYTHON` 必须保留 venv 的可执行文件路径，不能将符号链接解析成基础解释器。

`app.main:app` 仍是无配置的契约应用：合法业务请求返回 `503 BACKEND_NOT_CONFIGURED`、WS 关闭 1013。导入该模块不启动 GPU；它用于既有契约和前端失败边界测试。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ISAACSIM_PYTHON` | 必填 | 6.2 venv Python |
| `REPLAY_API_PORT` | 8000 | 本机 HTTP/WS |
| `REPLAY_STORAGE_ROOT` | `backend/outputs/service` | 本次服务拥有的上传归档、会话和诊断 |
| `REPLAY_MTX_API_PORT` | 6197 | 本机 MediaMTX 管理接口 |
| `REPLAY_WHEP_PORT` | 6189 | 本机浏览器媒体接口 |
| `REPLAY_ICE_PORT` | 6188 | 本机 UDP ICE |
| `REPLAY_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8768` | 逗号分隔的开发页面来源，HTTP/WS/网关使用相同配置 |
| `REPLAY_MAX_UPLOAD_BYTES` | 67108864 | 上传文件上限；HTTP multipart 另有 512KiB 表单开销上限 |
| `REPLAY_MAX_GAP_S` | 未设定 | 可选正数，采样器与曲线分析共用；更改后需重启并重新加载 |

正常优雅关闭会清理当前服务拥有的 worker、网关和上传会话副本；state 诊断保留，没有磁盘轮转/TTL，崩溃遗留文件不会自动恢复或清扫。`data/real_logs/raw/` 是另外保存的只读原始资料，不属于该清理范围。运行入口限本机开发，不包含远程部署认证；服务器部署在后续交付中处理。

## 操作顺序

1. `GET /api/v1/scenes`、`/robots`、`/robots/{robot_id}/joints` 获取目录与 28 关节元数据。
2. `POST /api/v1/sessions`，请求 `{"scene_id":"empty","robot_id":"galaxea_r1pro_2026"}`；保存返回的 `session_id` 和 `ws_url`。
3. 轮询 `GET /api/v1/sessions/{sid}` 或观察 WS 启动事件，等待 `ready`。启动时不可上传或播放。
4. `POST /api/v1/sessions/{sid}/logs` 上传 multipart `file`；可附 `parser` 和 JSON 字符串 `mapping_config`。原件、映射快照、规范化数据和哈希一起归档。上传成功不激活回放。
5. 检查返回的通道、缺测和质量，以及 `GET .../logs/{lid}/channels`、`/curve?channels=steer_motor_joint1&max_points=2000`。曲线只查询归档源通道；虚拟轮角不伪装成源值。
6. `POST .../playback/load`，请求 `{"log_id":"..."}`，再调用 `play`、`pause`、`speed`、`seek`、`step`、`stop`。`play` 可带 `speed`；`speed` 必须带 `speed`；`seek` 只接受 `time_s` 或 `frame_index` 二选一；`step` 使用 `forward`/`backward`。所有请求可带不参与排序的 `request_id`。
7. 从 `GET .../video` 或完整 WS 快照读取 WHEP 地址与正式 SEI UUID。正常停止回放仍持续采集当前姿态；`DELETE /api/v1/sessions/{sid}` 才释放会话。

所有控制 HTTP 响应仅表示 `ack_stage=accepted`。`control_seq` 和 `playback_epoch` 用于丢弃旧操作，实际应用/渲染由 worker 回执证明。worker 故障后删除旧会话、创建新 sid、重新上传并从首帧加载；不会自动恢复旧 worker 或继续旧进度。

## WS 与严格同步

每次连接有新的 `connection_id`，第一包是完整 `connection_snapshot`，含关节元数据、播放状态、worker、video、sync。重新同步时也发送完整快照。连接内 `seq` 在实际出队时递增；健康合并或清除旧上下文不制造假缺号。

客户端只发送严格信封的 `ping` 和 `set_telemetry_rate`（10/20/30Hz）。必须使用当前 sid/connection_id 与递增 seq；错误来源/身份/格式会关闭本连接。健康数据只含资源信息，不包含“最新姿态”，因此不能拿来填视频面板。

同步快照保留实际 `RenderedFrame` 的冻结上下文、28 关节读回、源值、质量与限位位图，不受健康遥测限频。缓存最多 16 帧、TTL 2 秒；每会话最多 8 个 WS 客户端，每连接队列 64 条。溢出或发送超过 1 秒关闭 1013 `SLOW_CONSUMER`，客户端需重新连接并等待配对。

控制切换会清除旧帧；只保留当前 log/epoch 的候选告警。worker/网关生命周期事件按顺序保留，不能被重同步清除。事件丢失、媒体恢复、缺帧等明确标记 `resync_required` 或 `stalled`。网关中断期间即使 SDK 仍产生快照，也保持 stalled。

服务端只知道已渲染，不知道浏览器是否呈现，因此不报告 `paired`。浏览器应解析每个 H.264 access unit 的正式快照目录，以实际解码图像的 capture ID＋CRC 找到唯一同帧快照，再在一个 canvas 同时绘制图像与面板。WS `frame_snapshot` 是辅助遥测；当前正式前端使用同一编码帧 SEI 内的快照目录驱动呈现，WS 最新值不作为替代。

## 复现实际验证

```bash
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_api.py \
  --output-dir outputs/t14/my-new-validation \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

输出目录必须不存在。浏览器探针需要 `npm ci --prefix scripts/spike/browser_tests` 与系统 Chrome。脚本实际启动本机 HTTP/WS 服务，用合成 command CSV 执行全控制流程；独立软件解码 H.264、核对 WS 和原 CSV 数值，再注入 worker 退出并验证新会话从头加载，最后清理本次资源。

浏览器复用 P0 的 **video-only** 页面观察实际 WHEP 图像，以独立像素 CRC 核对 WS 同帧记录。P0 的旧同步目录 UUID 不兼容正式目录，不能给这个验证页面加 `sync=1`；不使用页面上的旧控制按钮。该探针不是 T15 正式工作台严格同步验收。实测结果和限制见 [T14 验证记录](validation/t14-status.md)。

## T14 阶段实施裁定（历史范围，后续 T15/T17 已有独立验收）

以下细化沿用已批准计划，不涉及远端部署或尚未确认的真机标定。

- 由每会话单事件循环 actor 统一拥有 Timeline，路由只做验证和委派；CPU 准备放线程，缓存 2 份准备结果并保留当前活动对象。若后续日志规模超过这个容量，需要调整内存和并发预算。
- load 准备与后续控制按锁顺序处理，等待上限 32；新控制取消旧 RPC 等待并用控制序号隔离迟到回执。代价是大日志首次准备期间后续控制也等待。
- WS 队列 64、会话最多 8 客户端，健康限频独立于帧；慢消费者明确断开，重置时保留同代次告警与生命周期事件。代价是处理慢的浏览器必须重连，不能补显示旧队列。
- 服务端从实际 RenderedFrame 构造冻结快照，不宣布浏览器 paired；正式呈现必须由 T15 验证。若直接把 WS 最新值画到原生视频旁边，仍不满足严格同步要求。
- 采样与分析共用 gap，历史曲线只来自归档源通道；虚拟轮角只作为明确标记的运行时推算。代价是本轮没有虚拟轮角的独立历史曲线。
- multipart 在解析前限制总接收字节，LogStore 再限制文件；依赖锁定版本 Starlette 的异常清理，FastAPI 包装的 body 错误按 cause 转成 413。升级这些依赖时须重跑流式上传与清理测试。
- 删除由服务拥有并屏蔽请求取消，停止 actor、清理 worker/日志、等待已启动的准备任务后释放上下文；上传副本可回收，原始资料另存只读目录。代价是 DELETE 可能等待清理完成，不代表立即返回。
- 默认契约应用保持无配置；真实业务只通过显式 factory 启动。代价是启动错入口仍得到 503；运行说明明确给出正确命令。
- T14 用 P0 原生视频观察与独立正式 SEI/WS 核对提供证据；P0 旧 UUID 不改为正式工作台。代价是完整前端同步呈现、网络韧性和长期性能仍需后续 T15/T17 验收。
