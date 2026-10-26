# T04：首期接口与共享协议 v1

2026-10-23。用户确认沿用当前方案和初始门槛：至少 15fps、共同延迟小于 2 秒，同帧对应要求不变。本文件配合 [OpenAPI](../contracts/openapi.json)、[独立消息清单](../contracts/manifest.json)和[有效样例](../contracts/examples.json)使用。

## 交付范围

已提供 18 个 REST 路由声明、8 类服务端 WS 消息、2 类客户端 WS 消息，以及 worker RPC、目标、事件和媒体候选目录模型。类型以 Python 为单一来源，自动生成 JSON Schema 和 TypeScript。

**正式 HTTP/WS 业务尚未接入。** 合法 REST 调用返回 `503 BACKEND_NOT_CONFIGURED`，WS 关闭码为 1013；OpenAPI 中的成功响应描述后续实现。T08～T11 已实现独立导入、采样、时间轴与真实 worker，尚待 T12/T14 绑定正式会话和 API。

依赖方向为 `app → replay_protocol ← sim_worker`。`proto/` 是独立可安装包，只依赖 Pydantic 与标准库；在 IsaacSim 的 Python 中已验证可以独立导入，不加载 app、FastAPI、NumPy 或 IsaacSim。API 使用独立的 backend `.venv`。

## 身份与确认阶段

| 模型 | 含义 |
|---|---|
| PlaybackSnapshot | 服务端目标位置、已应用位置、已渲染身份；不包含服务端声称的已显示位置 |
| TargetFrame | 目标日志时刻、28 个目标值、质量、底盘目标及冻结的 state/speed |
| AppliedFrame | 实际读回值，另保留 source_joints 与应用时间 |
| RenderedFrame | 已应用状态的采集身份；必须显式提供 raw_identity_verified=true，不能默认生成验证证据 |
| FrameSnapshot | 可用于统一呈现的冻结状态：姿态、日志时间、进度、限位与质量 |
| PresentedFrame | 浏览器本地确认：对应快照、连接代次、实际呈现时间；不能作为 worker RPC 确认阶段 |

`request_id` 是关联字符串；操作顺序使用 `control_seq`，目标顺序使用 `target_seq`。`playback_epoch` 隔离 load/seek/stop/重播等跳变。普通序列为 JavaScript 安全整数；`capture_id` 为完整正 uint32，不回绕，当前 v1 明确 `render_seq == capture_id`。它们不等于原采样点索引。

控制 HTTP 响应只确认 `ack_stage=accepted`。RPC 可确认 accepted、applied、rendered；浏览器呈现由客户端自行证明。T10/T11 已实现暂停顺序屏障、关键帧可靠执行与跨通道乱序处理，模型校验不能替代接收端状态机。

T11 补充 TargetFrame 的 `state=ready|playing|paused|ended`（缺省 paused）和 `speed`（缺省 1）。它们在目标创建时冻结，并贯穿实际应用/渲染与快照；禁止使用最新服务端状态补写迟到帧。旧输入可以使用缺省值，旧严格消费者可能拒绝新输出字段，因此后端、worker 协议和前端生成物必须同步部署。

## 媒体与 WS

`SnapshotCatalog` 包含同 session 的 1～16 份快照、64 个唯一标记像素坐标、完整 ID＋CRC32 布局与诊断性的 encoder_poll_capture_id。浏览器必须从实际解码图像读取身份，再精确选择快照；未知、缺失或过期配对保持整个显示区域。

`VideoInfo` 声明 WHEP 地址、SEI UUID、目录版本、WebCodecs H.264 与编码接收变换能力。发布状态为 true 时必须提供 UUID 和端点。正式 v1 样例 UUID 为 `83249d41b6545e6e8d7779430f88bb0d`；它与 P0 的 `p0.snapshot_catalog.v1`/旧 UUID 分开，当前 P0 页面继续使用原格式。T11/T15 正式集成时需要显式迁移，不能直接宣称两种格式兼容。

WS 信封包含 session_id、connection_id、seq、wall_time、type、data。type 在运行时和导出 Schema 中都必填；seq 只在同一连接内递增。重连先发送包含关节元数据、回放、worker、video 与同步状态的 connection_snapshot。10/20/30Hz 设置仅影响健康遥测，同步必需的快照不受该降频规则约束。

帧快照和日志告警携带日志与播放代次；日志告警定位必须同时具有 log_id、playback_epoch、log_time_s。系统故障可以没有日志位置并即时显示。当前回放区域只消费 PresentedFrame；服务端状态与历史事件查询不能提前推动当前游标。

## REST 和数据边界

路由完整保留计划的目录 3 个、会话 3 个、日志 3 个、播放 8 个、视频 1 个。请求参数错误为 400，资源不存在 404、状态冲突 409、上传超限 413、日志内容致命解析错误 422、worker 未就绪 503。当前仅参数校验与未接入服务的 503 已实现，其他业务结果属于后续实现。

- seek 必须恰好选择 time_s 或 frame_index，二者不可同时出现，显式 null 不能冒充缺省；负值允许进入后续 clamp。导出类型也表达互斥。
- speed 范围 `[0.1,10]`；step 方向为 forward/backward。单步索引指规范化原采样时间集合，不是采集或编码序号。
- curve 的 channels 使用逗号分隔，去除空白、按首次出现去重，拒绝空项，最多 128 个；max_points 为每通道 2～10000 点；缺测值不能标为有效实测。
- 上传返回 log_id、stats、issues、mapping_snapshot_id、映射快照、文件引用和 hash；上传不等于激活日志。
- 28 关节严格使用共享 canonical 顺序；旋转 rad，夹爪 m；continuous 无位置限位。限位元数据保留来源，不能用仿真速度上限冒充位置限位。
- JSON 数值必须有限，数值字段拒绝布尔值；未知字段、未知版本、越界序号及跨会话/日志/代次的嵌套身份被拒绝。任意错误 details 和映射 JSON 同样拒绝 NaN/Infinity。

ReplayData 保留原文件引用/hash、映射快照、规范化原样本时间集合和每通道独立时序，不用回放网格覆盖原样本。规范化时间单位为秒；原始时间单位与转换保留在映射快照。来源区分 measured/commanded/derived/missing，处理标记区分转换、填充、插值等。累计轮角样例保留超过 2π 的值；包角通道必须声明周期。

具体首值、长缺口、单位映射、插值与轮角派生算法属于 T08/T09，当前不执行清洗。样例中的数值是合成数据，不是对真机 log 字段含义的推断。

## IPC 拓扑与期限

T11 实际拓扑：worker 绑定 RPC ROUTER 和事件 XPUB，目标 SUB 连接主服务 XPUB；主服务 DEALER/SUB 连接 worker。XPUB 仍使用 PUB 发布语义，同时观察真实订阅，以完成双方握手；目标端口由主服务侧占用。端点均为回环地址。独立 CLI 默认端口 19601/19602/19603，后续 T12 管理器负责动态分配。普通 RPC 默认 10 秒，启动握手默认 120 秒；不能把慢启动按普通 RPC 判死。

subscribe_ready 在双方实际观察到目标和事件订阅后才确认。普通目标有界取最新，首末帧、seek/stop/step 和暂停屏障走可确认路径。具体启动、队列、媒体与退出边界见 [worker 说明](worker-v1.md)。

## 安装、导出和检查

在 backend 下执行；Python 3.12 的路径可换成目标机器的基础解释器，不使用系统 3.10：

```bash
/path/to/python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e proto -e .
npm ci --prefix tools/contracts
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/contracts -o addopts='' -q
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_contracts.py
npm run generate --prefix tools/contracts -- --frontend-dir ../frontend/src/api
npm run check --prefix tools/contracts
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_contracts.py --check
```

清除 PYTHONPATH/PYTHONHOME 是为避免本机 ROS 的 Python3.10 包注入；主服务与 worker 环境保持分离。前端生成目录可由 `--frontend-dir` 调整，禁止手工编辑 `.gen.ts`。

查看契约页面可运行 `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，访问 `/docs`。这只启动契约 ASGI 应用，不启动 IsaacSim。

T05 接入后补充两项生成规则：JSON Schema 2020-12 的 prefixItems 仅在传给 TS 编译器时转换成数值元组，保存的原始 Schema 不变；REST 类型保留 Schema 默认字段的可选性，与 WS 类型一致。导出同时复制运行时 Schema 和 canonical 关节 fixture，供前端 AJV 校验。

TypeScript 固定 5.9.3，满足 openapi-typescript 7.13.0 的 peer 范围；未强制绕过不兼容依赖。递归 JSON 类型也从 Schema 生成独立别名，避免 TS2502，没有手抄另一套字段。Pydantic 与 OpenAPI 依据分别见[严格模式说明](https://docs.pydantic.dev/latest/concepts/strict_mode/)和[OpenAPI 导出说明](https://fastapi.tiangolo.com/how-to/extending-openapi/)。
