# T11：独立 IsaacSim worker

当前固定 IsaacSim **6.2.0 / Python 3.12 / PhysX**，使用 `configs/robot-asset.json` 指向的完整 6.x 资产。`sim_worker` 只依赖共享 `replay_protocol`、pyzmq 和 SDK；导入模块不会启动仿真，也不导入 `app`。T12 将提供会话管理，T14 绑定 HTTP/WS，T15 接入正式浏览器媒体。

## 进程与通道

| 通道 | worker | 主服务侧 `WorkerClient` | 默认端口 |
|---|---|---|---|
| 可靠 RPC | ROUTER bind | DEALER connect | 19601 |
| 普通目标 | SUB connect | XPUB bind | 19602 |
| 应用/渲染/健康事件 | XPUB bind | SUB connect | 19603 |
| 原生 H.264 媒体 | RTSP server | 网关/独立探针拉流 | 19554 |

三条 ZMQ 通道都使用 `127.0.0.1`；XPUB 保持 PUB 发布语义，并观察真实订阅。客户端先确认目标订阅，再用 `subscribe_ready` 确认 worker 已观察到事件订阅，避免慢订阅者丢掉首条消息。目标与事件使用带 session_id、末尾 NUL 的 topic；RPC 使用 request_id 关联响应。

每个 socket 只归调用线程所有。HWM 为 64，消息上限 1 MiB，每轮最多读取 64 条；普通目标只有一个待执行槽，始终保留合法最新目标。可靠关键帧单独保留，不能被普通目标覆盖。request_id 缓存上限 128；缓存内重复请求返回已有阶段，相同 ID 不同内容拒绝。客户端事件队列上限 128，故事件不能替代可靠 RPC。

常规 RPC 期限默认 10 秒，启动握手默认 120 秒；集成验证允许 150 秒的整个冷启动期限。socket 建立不代表 SDK 或媒体已经就绪。当前 CLI 在场景、机器人、相机与媒体完成初始化后输出 `WORKER_READY`；加载相位与独立加载期限由 T12 接入。

## 目标执行与证明

`TargetFrame.state/speed` 在时间轴产生目标时冻结。AppliedFrame、RenderedFrame 和 FrameSnapshot 继承该目标的上下文，迟到画面不能读取新的播放状态。双方生成类型须同时更新；旧输入缺省值为 paused/1，但旧严格消费者不保证接受新增字段。

普通目标必须匹配当前 session/log/epoch/control、playing 状态和倍速。旧序号、重复序号、占用可靠关键帧身份、跨控制代次消息均拒绝。pause_barrier 清除待执行普通帧与旧可靠目标；随后由新的可靠关键帧确定暂停画面。RPC 的 accepted、applied、rendered 分别表示接收、实际读回、实际原始图像验证，均不表示浏览器已呈现。

启动核验版本、PhysX、53 项资产 checksum、USD 引用闭包和 28 DOF 名称。姿态按名称映射，关节和底盘读回误差门槛为 1e-5；四元数先归一化再比较。连续轮角只在应用时转为等价主值，source_joints 保留累计圈数。底盘 x/y/yaw 使用世界系，保留初始化 z/roll/pitch；底盘缺测时回到初始位姿，不沿用上一个目标的位置。

暂停时继续采集相同日志时刻，每次重新设定并读回姿态，生成新的 capture_id。渲染保持业务姿态，物理步仅用于刷新运行时状态，不作动态仿真。固定第三人称相机上的 64 个实际几何色块编码完整 uint32 capture_id 和 CRC32。RGB 像素身份与同次渲染属性一致后，才产生 RenderedFrame。

H.264 SEI 使用正式 UUID `83249d41b6545e6e8d7779430f88bb0d`，内容为 SnapshotCatalog，最多 16 条且序列化不超过 65536 字节。编码器可能延迟输出，`encoder_poll_capture_id` 仅用于诊断；接收端必须读取实际解码像素 ID/CRC，再在候选目录中选中唯一快照。找不到精确匹配时不得以最新数据代替。P0 旧 UUID/目录与正式协议不通用。

`load_scene(empty)`、`load_robot(galaxea_r1pro_2026)` 验证已初始化的固定目录；其他资产 ID 拒绝。`load_log` 只建立日志身份，文件解析与采样属于主服务。worker 失败后进入终态；重载应创建新会话、重新加载首帧。

## 本机验证

在 backend 目录执行。SDK 环境须已有共享协议及其 Pydantic 依赖，仅补装下列 worker 依赖，不向 SDK 安装主服务：

```bash
export ISAACSIM_PYTHON=/home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
"$ISAACSIM_PYTHON" -m pip install -e proto
"$ISAACSIM_PYTHON" -m pip install -r sim_worker/requirements.txt
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_worker.py \
  --output-dir outputs/t11/my-new-validation \
  --isaac-python "$ISAACSIM_PYTHON" --decoder outputs/t11/rtsp_identity
```

解码器编译需要系统已存在的 C 编译器、pkg-config、libavformat/libavcodec/libavutil/libswscale 开发库；脚本不安装系统软件。探针只产生合成数据，会启动并清理自己创建的 worker，验证可靠控制、最新目标、暂停、seek、旧代次拒绝和多圈轮角。`issued.json` 保存实际发送目标，解码验证按这些目标核对图像、正式快照、实际读回和来源，不从回执反推期望值。省略 `--decoder` 只能证明原始渲染，不能证明编码后配对。

也可用 `scripts/run_worker.sh --session-id <sid> --output-dir <new-dir>` 启动独立 worker；实际控制端入口为 `sim_worker.transport.WorkerClient`。输出目录必须新建。SDK 快速关闭可能直接终止进程，因此错误和资源诊断在关闭前落盘/刷新，并向 `SimulationApp.close(exit_code=...)` 传递失败状态。报告、writer detach 或部分启动失败不会跳过其他资源的释放。

## 原生媒体监听边界

实际检测发现 SDK RTSP 监听 `0.0.0.0`；`ServerConfig.public_ip=127.0.0.1` 不限制绑定地址。worker 默认在启动检查中拒绝非回环监听，关闭资源并以失败码退出；这个检查在原生服务短暂建立监听后发生，在接受日志目标和发布有效快照前完成。

上述 T11 合成验证脚本显式传入 `--allow-native-rtsp-wildcard`，并把开关与实际监听地址记录到证据中。手动使用该参数表示允许原生媒体对外监听，不应直接用于真实日志。T12 会话启动器已实现项目内的受限绑定方案，实际 SDK 仅监听回环地址，见[生命周期说明](session-lifecycle-v1.md)；未修改防火墙或远端环境。

完整证据和未覆盖范围见 [T11 验证记录](validation/t11-status.md)。
