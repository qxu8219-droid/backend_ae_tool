# 本地回放故障排查与错误码

先记录失败操作、HTTP `error.code/message/details`、session_id、log_id 和发生时刻。`ack_stage=accepted` 只表示控制请求接受，实际呈现需等待配对帧。日常启动以 [交付手册](delivery-v1.md) 为准。

## 常见症状

| 症状 | 检查与处理 |
| --- | --- |
| 合法请求一律503 | 用 `bash scripts/run_backend.sh` 启动正式factory；`app.main:app` 是契约入口 |
| API监听前退出 | 查看终端traceback：SDK路径、元数据hash、MediaMTX文件/hash、端口占用、storage权限；启动异常不保证有HTTP错误响应 |
| SERVICE_ALREADY_RUNNING | 同一storage的manager文件锁被占用；正常关闭原拥有进程。不要删除锁文件绕过活跃服务 |
| 创建后无画面 | 看worker.phase；启动总限150秒、加载阶段120秒。ready后还需上传并激活日志 |
| 浏览器能力不足/无法解码 | 检查Chrome、WebCodecs、OffscreenCanvas、WebRTC encoded transform、安全上下文、COOP/COEP与控制台错误 |
| 一直显示Mock | 重启Vite时显式 `VITE_USE_MOCK=false`；已有产物需重新构建，不能仅刷新切换模式 |
| API成功而WHEP/ICE失败 | 检查实际 `/video` URL、网关日志、Origin；本版浏览器须在同一主机，API的TCP转发不等于UDP ICE转发 |
| 画面保持但健康仍更新 | 等待同帧配对或控制屏障时的预期行为；查看sync/worker/video状态，不用最新WS姿态补画面 |
| worker error | 保存日志，关闭旧会话，新建sid、重新上传并从零加载；不支持断点续播 |
| 上传后机器人未变化 | 检查导入诊断并激活该日志；上传不会替换当前活动日志 |
| B041推算标签或轮组缺失 | 当前来源/标定边界；确认参数后用新profile生成，不删除质量标签 |
| 刷新后需要重新上传 | 本标签页已拿到sid时会清理旧会话；创建响应前强制关页的回收尚未覆盖 |
| 关闭失败 | 保存lifecycle-final报告，修复权限/磁盘等原因后重试；不能按进程名批量kill其他任务 |

## HTTP 与 WS 错误速查

以下是常用错误，不替代完整 [OpenAPI](../contracts/openapi.json)和[消息清单](../contracts/manifest.json)。导入的行/列诊断在 `error.details.issues`；同一状态码可能有不同原因。

| 状态/位置 | 典型代码 | 下一步 |
| --- | --- | --- |
| HTTP 400 | INVALID_MAPPING、UNKNOWN_PARSER、INVALID_PARSER、INVALID_CONTROL | 检查parser、inline JSON对象、字段和控制参数；B041使用专用映射参数 |
| HTTP 404 | SESSION_NOT_FOUND、LOG_NOT_FOUND、ROBOT_NOT_FOUND、UNKNOWN_ASSET | 核对所属sid/lid、固定场景与机器人ID |
| HTTP 409 | SESSION_CAPACITY、SESSION_ERROR、SESSION_STOPPING | 单会话占用或状态不允许；关闭旧会话后重建 |
| HTTP 409 | IMPORT_BUSY、CONTROL_BUSY | 导入/控制/连接达到预算；减少并发并等待 |
| HTTP 408 | IMPORT_TIMEOUT | 上传或隔离解析超时；检查体量/格式，保留诊断 |
| HTTP 413 | REQUEST_TOO_LARGE、IMPORT_LIMIT_EXCEEDED、IMPORT_MEMORY_LIMIT | 区分HTTP正文、文件、点数和解析内存限制；单增上传大小无效 |
| HTTP 415 | UNSUPPORTED_LOG_FORMAT | 使用CSV、JSON、准备后的.b041；不能直接上传原始Parquet/MCAP |
| HTTP 422 | EMPTY_LOG、NO_TIMESTAMP、NO_JOINT_CHANNELS、IMPORT_WORKER_FAILED、INVALID_LOG_ANNOTATIONS | 检查源格式、时间、有效关节或标注边界；不用预测补成反馈 |
| HTTP 500 | LOG_ARCHIVE_CORRUPT、LOG_DELETE_FAILED | 保存证据；从另存原件重导，或修复资源清理问题 |
| HTTP 503 | BACKEND_NOT_CONFIGURED、WORKER_NOT_READY、SERVICE_CLOSED、WORKER_RPC_TIMEOUT、SESSION_CLEANUP_FAILED | 检查入口、启动阶段、服务状态和worker期限 |
| worker.error.code | WORKER_START_FAILED、WORKER_EXITED、WORKER_TRANSPORT_FAILED、各类TIMEOUT | 查看worker日志与生命周期报告；这些不全是HTTP错误 |
| WS 1008 | ORIGIN_NOT_ALLOWED、消息/身份校验失败 | 检查Origin、sid/connection_id/seq和消息结构 |
| WS 1013 | SLOW_CONSUMER、BACKEND_NOT_CONFIGURED、WS_CAPACITY | 分别检查慢消费者、入口、连接容量；WS_CAPACITY在握手接受前拒绝，浏览器可能显示握手失败而非REST错误信封 |

## 定位日志

下面变量应改为实际storage与sid；端口不是默认时同步替换URL。没有 `/health` 路由，可先读取 `/api/v1/scenes` 判断HTTP就绪。

```bash
task_store="$PWD/outputs/service"
task_sid='替换为实际session_id'
curl --fail-with-body "http://127.0.0.1:8000/api/v1/sessions/$task_sid"
curl --fail-with-body "http://127.0.0.1:8000/api/v1/sessions/$task_sid/video"
rg -n 'WORKER_PHASE|WORKER_READY|WORKER_STARTUP_ERROR|WORKER_ERROR|Traceback' \
  "$task_store/state/$task_sid/worker.log"
ss -lntup
df -h "$task_store"
```

- API日志在启动终端或操作者重定向的位置；只有验证启动器约定自己的 `api.log`。
- `state/<sid>/worker.log`：SDK启动、相位和错误。
- `state/<sid>/worker/runtime.json`、`runtime-final.json`、`rendered.jsonl`、前几张 `capture-*.png`：运行信息、原始像素身份和逐帧追踪。
- `worker/readback-failure.json`：仅对应读回错误发生时生成。
- `state/<sid>/lifecycle-final.json`：收尾状态、端口、退出码、清理错误；运行期间可能尚未生成。此时用自有PID命令行与监听信息核对端口。
- `state/gateway/gateway-*.json` / `.log`、`owned-paths.json`：实际网关配置、启动代次和路径归属。
- `logs/sessions/<sha256(sid)>/logs/<lid>/`：上传原件、映射、规范化结果和manifest；正常会话关闭会删除此会话上传副本。

解析进程stdout/stderr不保存为永久 `import.log`，应保存HTTP响应中的导入诊断。state诊断没有磁盘TTL/自动轮转；不要清空整个outputs，里面还有必需的回环绑定库。

## 独立验证入口

按源数据/映射→规范化质量→Timeline目标→worker读回/原始像素→编码后身份→浏览器呈现的顺序检查。各工具的证明范围不同：

| 工具 | 范围 |
| --- | --- |
| `scripts/import_log.py` | CPU导入/归档，不启动仿真 |
| `scripts/validate_b041.py` | 四个登记来源的独立数值/质量核验，需本地数据 |
| `scripts/validate_frontend.py` | 合成日志正式UI/SDK、worker故障、重建与清理 |
| `scripts/validate_resilience.py --mode matrix` | B041消息/进程故障、刷新/复制页、录屏核对 |
| `scripts/validate_resilience.py --mode soak --seconds 1800` | 同会话30分钟；短运行只能称smoke |
| `scripts/validate_recording.py` | 已录PNG的身份/声明文字；不宣称捕获全部浏览器绘制 |

故障验证只向工具自己创建且核验身份的进程发信号，不用于真机或其他业务进程。
