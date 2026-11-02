# T12：会话生命周期

`app/core/SessionManager` 管理一个活动会话，提供异步 `start/create/rpc/publish/delete/close` 和同步 `get/drain_events/drain_worker_events`。实现位于 `app/core/session_manager.py`；正式 REST/WS 已由 T14 绑定，T15 已接入浏览器呈现。只有无配置的默认契约入口仍返回503；操作入口见 [交付手册](delivery-v1.md)。

## 使用方式

先准备已固定的 IsaacSim 6.2.0 / Python 3.12 / PhysX 环境、共享协议和 pyzmq，安装说明见 [worker 说明](worker-v1.md)。MediaMTX 使用 `tools/mediamtx/` 已记录 checksum 的官方 v1.21.2 二进制。

```bash
bash scripts/build_rtsp_loopback.sh
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_sessions.py \
  --output-dir outputs/t12/my-new-validation \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

输出目录必须不存在。探针只生成合成姿态，自动启动和清理自己的 SDK、网关及解码器，验证完整启动相位、实际首帧、暂停采集、网关恢复、worker kill、新 sid 首帧重载、创建中删除和应用关闭。不会连接或修改真机/数据服务器。Python 参数必须保留 venv 的 `bin/python` 入口，不能展开其符号链接后改用基础解释器。

代码调用示例：

```python
from pathlib import Path
from app.core.config import Settings
from app.core.session_manager import SessionManager
from app.schemas.rest import CreateSessionRequest

async def example():
    settings = Settings(isaac_python=Path('/absolute/isaacsim62/bin/python'))
    async with SessionManager(settings) as manager:
        created = await manager.create(CreateSessionRequest(
            scene_id='empty', robot_id='galaxea_r1pro_2026'))
        status = manager.get(created.session_id)  # 初始 starting，不等待 GPU 冷启动
        await manager.delete(created.session_id)  # 创建期间也可删除
```

在 ASGI 服务中可显式使用 `create_app(session_manager=manager)`；lifespan 负责启动与关闭。导入默认 `app.main:app` 不创建 GPU 或网关进程。会话管理器与 ZMQ socket 使用同一 asyncio 事件循环，部署使用一个服务进程；同一 state_dir 的文件锁拒绝第二个服务所有者。

## 状态和期限

| 设置 | 默认值 | 含义 |
|---|---|---|
| max_sessions | 1 | starting、ready、stopping、未删除的 error 都占用槽 |
| port_first / port_last | 6100 / 6199 | 四个 worker 端口，按租约分配，绑定前移交 |
| startup_timeout_s | 150s | 整个 worker 初始化期限，支持 60～120s 冷启动 |
| load_timeout_s | 120s | 加载相位及目录确认 RPC 的独立期限 |
| rpc_timeout_s | 10s | 正式回放控制请求期限 |
| heartbeat_interval_s / timeout | 1s / 3s | ready 后独立探测；启动期间不按心跳超时误杀 |
| shutdown_grace_s | 10s | SIGTERM 后最多等待，再 SIGKILL 自有进程组 |
| history_limit | 16 | 只保留有限的已结束会话状态，帧缓存在清理时释放 |

worker 实际输出 booting → loading_scene → loading_robot → rtsp_publishing，管理器在 SDK 就绪、ZMQ 双向订阅和固定场景/机器人确认后进入 ready。rtsp_publishing 表示媒体组件准备阶段；**ready 不等于已有视频**。尚未加载日志时 `video.publishing=false`，只有 MediaMTX 实际源路径 ready 且存在 H.264 track 才为 true。

RPC 使用独立响应泵，按 session/request_id/control_seq/playback_epoch/确认阶段关联。正常 RPC 的等待不阻塞进程退出、心跳和事件观察。待请求上限 64，socket HWM 64、单消息 1 MiB；worker 事件和生命周期事件各保留 128 条，丢弃数可供后续传输层诊断。其余可靠关键帧与图像配对规则仍由 T10/T11 保证。

运行中失败先记录 error，再执行清理。错误会话不会原地恢复，需删除旧会话并创建新 sid，重新加载日志首帧；worker 的采集 ID 可在新 sid 中从 1 开始。`delete` 对未知或已结束 sid 幂等，删除期间不允许新建。删除由会话自身任务完成，调用方取消等待不会中断资源清理和名额释放；清理失败仍保留名额并允许重试。旧帧缓存、socket 和自有媒体路径在清理时释放。

## 媒体绑定与网关所有权

SDK 原生 RTSP 的通配监听由项目内 `rtsp_loopback.so` 限制：只把指定 RTSP 端口的 IPv4/IPv6 wildcard bind 改为回环地址，其他 bind 不变。库只注入本次 worker 子进程；不修改 SDK 安装目录、系统动态库或防火墙。worker 仍检查实际 `/proc/net/tcp*` 监听地址，不符合回环条件则拒绝就绪。这是已验证的 Linux/glibc、固定 SDK 方案，不代表任意平台兼容。

托管 MediaMTX 默认监听本机 API 6197、WHEP 6189、ICE UDP 6188；原生 RTSP 与 ZMQ 也限本机。网关关闭 RTSP/RTMP/HLS/SRT/MoQ 对外服务，只通过 TCP 拉取 worker 的 RTSP 源；WHEP 使用 sid 作为路径。支持配置前端 CORS origins；当前配置用于本机浏览器，不作为远程访问部署。

网关失联或自有路径不可用时改变媒体状态并产生 gateway_unreachable，worker 保持运行。托管模式最多自动重建三次，生成新配置恢复当前自有路径；外部模式每次健康检查同时核对自有路径，即使没有观察到 API 中断也能补回丢失路径。外部进程不被停止，冲突路径不被覆盖。只有 API 可用且自有配置核对成功才产生 gateway_recovered；实际 H.264 publishing 再独立确认。

外部模式通过 `GatewayConfig(mode='external', api_url='http://127.0.0.1:9997', whep_origin='http://127.0.0.1:8889')` 指定本机 API 和 WHEP origin，服务须提供 v1.21.2 API 权限与相应 WHEP/CORS 配置。路径的新增、查询、删除依据[该固定版本的官方 API](https://raw.githubusercontent.com/bluenviron/mediamtx/v1.21.2/api/openapi.yaml)。

外部路径所有权写入 `state_dir/gateway/owned-paths.json`。服务重启先清理自己先前登记的路径，再允许复用源端口。路径被其他人修改时拒绝删除；API 不可达时保留待清理记录并报告失败，不能把它当作完整删除成功。托管模式可结束自己的网关进程完成隔离清理。

## 退出与证据

子进程使用独立进程组；关闭先尝试可靠 shutdown，再 TERM/KILL。即使 leader 已退出，也检查同组剩余子进程。Linux parent-death signal 防止 supervisor 被结束后留下其直接 worker/网关进程；进程清理由应用 lifespan 和所有权记录共同负责，不扫描并终止其他用户服务。

`state_dir/<sid>/worker.log` 保存 SDK 输出，`worker/` 保存真实渲染证据，`lifecycle-final.json` 保存状态、退出码、分配端口与清理错误。网关每次启动保存独立配置与日志；原始日志仍在 [data/real_logs](local-data-storage-v1.md) 中，不会被本模块改写或自动播放。

验证事实与未覆盖范围见 [T12 验证记录](validation/t12-status.md)。
