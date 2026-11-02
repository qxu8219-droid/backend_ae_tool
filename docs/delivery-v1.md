# 首期本地交付与运行手册

本版本提供本机单会话、空场景、R1Pro 2026 的运动学日志回放。正式网页使用真实 IsaacSim 画面，与关节、底盘、进度和曲线游标共同呈现。支持规范 CSV/JSON 和准备后的 B041 包；不包含完整 MCAP 直接导入、多用户、远程认证、动力学控制评测或精确实物标定。

两个独立仓库应保持相邻目录：`Model_Evaluation/backend` 与 `Model_Evaluation/frontend`。除特别说明，以下后端命令均在 `backend/` 执行。推荐先使用合成日志验证安装，再使用有权限的真实日志。交付验收范围和版本见 [T18 记录](validation/t18-status.md)。

## 1. 安装前准备

| 组件 | 本机已验证版本/要求 | 是否在源码仓库中 |
| --- | --- | --- |
| 操作系统与显卡 | Ubuntu 22.04 系列 x86_64，支持实际 SDK 的 NVIDIA 驱动；T17 使用 RTX 5070 Ti Laptop 12GB | 外部环境 |
| API Python | Python 3.12.15；独立 venv，依赖按 `requirements.lock` | lock 在仓库，venv 不在 |
| SDK Python | IsaacSim 6.2.0（pip 元数据 6.2.0.0）、Python 3.12、PhysX | 外部独立 SDK 环境 |
| worker 附加依赖 | `pyzmq==27.1.0`、`pydantic==2.14.1`、本仓库 `proto` 0.1.0 | 配方在仓库 |
| 前端/契约工具 | Node 26.8.1、npm 11.19.0；各自 `package-lock.json` | lock 在仓库 |
| 浏览器 | T17为Chrome152，本次T18使用Chrome156；需WebCodecs、OffscreenCanvas、WebRTC encoded transform | 外部环境 |
| 媒体网关 | MediaMTX v1.21.2 linux_amd64，固定二进制 SHA256 | 来源/校验在仓库，二进制不在 |
| 机器人资产 | 完整 IsaacSim 6.x 资产包，转换版本必须为 6.2.0 | 不在 Git；需另行复制 |
| 本地编译 | `cc`、Linux libc/动态链接接口，编译 RTSP 回环绑定库 | C 源码在仓库 |

这是已验证组合，不代表其他版本都不兼容，也不代表只替换 USD 即可使用 IsaacSim 5.1。SDK 的完整安装包、许可接受和显卡驱动由目标机器环境准备；本项目的 `sim_worker/requirements.txt` **不是 SDK 完整安装配方**。不在 HTTP venv 中安装 IsaacSim，也不向 SDK 环境安装整个后端/FastAPI。

SDK 的可编辑 `proto` 安装会绑定某个 checkout。若另建 checkout，应使用它自己的 SDK 环境，或明确重新安装该环境的协议路径；不能把仍指向旧 checkout 的协议称作新版本独立安装。更新 SDK 环境前先停止使用它的 worker。

本机性能前提：接通电源，使用系统平衡模式或等效的非省电档位。2026-11-02 同数据对照中，省电模式约14fps，临时平衡模式恢复约19fps；没有改变720p画质或同步门槛。可先用 `powerprofilesctl get` 检查（仅适用于安装了该工具的Linux桌面）；平台启动脚本不会改电源设置，验收临时调整后恢复了原档位。服务器须按自己的硬件/功耗策略重新测量。

## 2. 安装后端与前端

在尚无 `.venv` 的新 checkout，指定基础 Python 3.12：

```bash
env -u PYTHONPATH -u PYTHONHOME /absolute/path/to/python3.12 -m venv .venv
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m pip install -r requirements.lock
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m pip install --no-deps -e proto -e .
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m pip check
npm ci --prefix tools/contracts
```

不要对已有 venv 重复创建或改指向。`PYTHONPATH/PYTHONHOME` 可能来自 ROS，需要在检查和安装时清除。`run_backend.sh`、`run_worker.sh` 会清理各自不适用的继承环境。

为已准备好的 6.2 SDK 安装 worker 依赖（路径指向 **venv 的 bin/python**）：

```bash
export ISAACSIM_PYTHON=/absolute/path/to/isaacsim62/bin/python
env -u PYTHONPATH -u PYTHONHOME "$ISAACSIM_PYTHON" -m pip install -r sim_worker/requirements.txt
env -u PYTHONPATH -u PYTHONHOME "$ISAACSIM_PYTHON" -m pip install --no-deps -e proto
env -u PYTHONPATH -u PYTHONHOME "$ISAACSIM_PYTHON" -c \
  'import sys, importlib.metadata as m, replay_protocol; print(sys.prefix); print(m.version("isaacsim")); print(replay_protocol.__file__)'
```

在前端目录安装：

```bash
cd ../frontend
npm ci
npm run typecheck
```

## 3. 资产与媒体网关

资产目标为 `backend/assets/robots/r1pro_2026/isaacsim_6_x/`。本工作区已有完整副本；新机器从批准的交付包复制整个目录，保留 `reproduction/`、材质、网格和相对引用。当前来源记录在 `configs/robot-asset.json` 的 `source_directory`，它是追溯字段，不是 worker 实际读取路径。

```bash
# 仅用于目标目录尚不存在的新安装；替换源包绝对路径。
export R1PRO_ASSET_BUNDLE=/absolute/path/to/r1pro_2026_usd_isaacsim_6_x
if [ ! -e assets/robots/r1pro_2026/isaacsim_6_x ]; then
  mkdir -p assets/robots/r1pro_2026
  cp -a "$R1PRO_ASSET_BUNDLE" assets/robots/r1pro_2026/isaacsim_6_x
fi
(cd assets/robots/r1pro_2026/isaacsim_6_x && sha256sum --check CHECKSUMS.sha256)
```

配置字段 `asset_directory`、`entrypoint`、`common_config` 决定实际资产路径；它们必须指向同一已验证包。API 元数据检查目前仍使用默认 common 路径；本版交付保留上述默认目录。整体迁移需同步元数据引用、审计和回归，不能只改 JSON 路径绕过限位校验。修改 common 内容后还需重新生成关节元数据并检查 hash。当前场景 ID 固定为 `empty`、机器人 ID 固定为 `galaxea_r1pro_2026`；目录 API 不是自定义场景上传入口。

MediaMTX 来源、固定下载地址、校验值和重新安装命令在 [SOURCE.txt](../tools/mediamtx/SOURCE.txt)。只需安装其中的二进制，不必启动 P0 网关脚本。正式 API 会自动托管自己的网关：

```bash
(cd tools/mediamtx && sha256sum --check binary.sha256)
bash scripts/build_rtsp_loopback.sh
```

`outputs/tools/rtsp_loopback.so` 是本机生成物，不入 Git。正式启动器通过它把原生 RTSP 限制为回环监听，worker 还会核对实际监听地址。缺库或校验失败应修复后重试。历史合成探针的 `--allow-native-rtsp-wildcard` 不属于本交付启动步骤。

## 4. Mock 与真实模式

Mock 不需要 GPU：在 `frontend/` 执行 `VITE_USE_MOCK=true npm run dev`，打开 `http://127.0.0.1:5173`，创建会话、载入示例、播放。Mock 上传流程不会解析真实日志，显示值来自合成轨迹。

真实模式先启动后端。仅在本地文件尚不存在时复制模板，编辑 SDK 路径：

```bash
# backend/
cp -n .env.example .env.local
# 编辑 .env.local 中 ISAACSIM_PYTHON；其他默认值可用于本机。
set -a
source .env.local
set +a
bash scripts/run_backend.sh
```

`.env.local` **不会被后端自动读取**。它是本地可信 shell 配置，使用前检查内容。脚本启动 `app.runtime:create_runtime_app --factory`；默认 `app.main:app` 是契约测试入口，业务返回 `503 BACKEND_NOT_CONFIGURED`。不要增加 uvicorn worker 数量，也不要对 SDK Python 路径执行 `readlink -f` 后用于启动。

另开终端，在前端目录执行：

```bash
VITE_USE_MOCK=false API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

打开 `http://127.0.0.1:5173`，创建会话并等到就绪，上传日志，检查统计/诊断/映射，再点“激活此日志”。随后播放、暂停、倍速、定位、单步和停止。没有匹配媒体帧时，同步区域保持原画面；独立系统健康信息仍可更新。

本地构建检查使用 `VITE_USE_MOCK=false npm run build`；模式在构建时确定，不能通过只更改服务端环境切换已构建产物。Vite dev/preview 都是本地验证入口，不是已验收的公开服务器部署。本版实际验收使用 dev 服务，远程 HTTPS、代理、认证和媒体网络仍需独立交付。

### 合成日志与 B041

从 `backend/` 生成一份明确用于演示的 12 秒合成 CSV；已有文件不会被覆盖：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python - <<'PY'
from pathlib import Path
from scripts.validate_frontend import synthetic
path = Path('outputs/demo/synthetic.csv')
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('x') as stream:
    stream.write(synthetic())
print(path.absolute())
PY
```

在 UI 先展开“解析器与映射配置”，在“映射 JSON”粘贴以下配置，再选择该 CSV；选择文件会触发上传，成功后再激活。它把合成值标为指令，并启用文件中的底盘 x/y/yaw；默认映射会把关节标为实测且不导入底盘，不能用于这个演示：

```json
{"joints":{"origin":"commanded"},"chassis":{"available":true,"origin":"commanded","yaw_semantics":"angular_unwrapped"}}
```

真实 B041 数据先将 `data/real_logs/derived/<source>/<profile>/mapping.json` 内容粘贴到映射 JSON，再选择同目录 `recording.b041` 上传。原始 Parquet/runtime 目录不能直接当作这个包上传，准备步骤和标定边界见 [B041 说明](b041-replay-v1.md)。

### 关闭与重建

点击“关闭会话”并等待完成；停止播放只把日志归零，不会释放 GPU。worker 故障后关闭旧会话、创建新 sid、重新上传并激活，位置从零开始。刷新会清理本标签页已经拿到 sid 的会话；创建响应尚未返回便强制关闭页面的情况尚无服务端租约恢复。关闭后再对 API、Vite 的前台终端各按 Ctrl+C。不要用按进程名称批量 kill 的方法清理其他任务。

## 5. 配置、端口与保留策略

正式环境变量及默认值见 [运行配置表](runtime-api-v1.md#启动)和 [`.env.example`](../.env.example)。`REPLAY_STORAGE_ROOT` 相对路径以 backend 为基准；在交付验收中使用全新目录。前端 API 代理改变时通过 shell 显式设置 `API_PROXY_TARGET`，并保持后端允许的页面来源一致。

| 用途 | 正式默认绑定 |
| --- | --- |
| API/WS | TCP 127.0.0.1:8000 |
| Vite 页面 | TCP 127.0.0.1:5173 |
| MediaMTX 管理 API | TCP 127.0.0.1:6197 |
| WHEP | TCP 127.0.0.1:6189 |
| ICE 媒体 | UDP 127.0.0.1:6188 |
| worker RPC/目标/事件/RTSP | 从 6100–6199 租用四个可用 TCP 端口，均回环 |

worker 的独立 CLI 默认端口与以上会话管理器端口不同；正式操作以会话记录为准。正式 runtime 始终创建 managed 网关；Python `GatewayConfig(mode='external')` 属于集成接口，当前没有对应 `REPLAY_*` 环境开关。单会话、端口池和各类超时在 `app/core/config.py` 定义，不能杜撰环境变量来覆盖。

上传默认 64MiB，另限 20,000 个采样时刻、128 通道、600,000 点、单次导入一个解析进程。`REPLAY_MAX_UPLOAD_BYTES` 仅改变文件字节限制，不改变其他预算；前端显示上限使用 `VITE_MAX_UPLOAD_BYTES`，服务端仍有最终校验权。大 MCAP 不应靠放大此数值导入。

`REPLAY_STORAGE_ROOT/logs/sessions/<sha256(sid)>/logs/<lid>/` 存会话上传副本。正常 DELETE 或服务优雅关闭会清理当前拥有会话的这些副本。`state/<sid>/` 的 worker 日志、逐帧追踪、截图和生命周期记录，以及网关配置/日志会保留；没有磁盘 TTL 或自动轮转。内存会话历史上限 16 不等于磁盘只留 16 份。崩溃遗留 staging/旧目录不会在下次启动自动恢复或清扫。

独立保存的 `data/real_logs/raw/`、catalog、`derived/` 和验收 `outputs/t*/` 不由会话清理管理。源资料与需要留档的映射/证据应另行备份。仅在对应进程完全退出、确认所属目录且已备份之后手工清理本次会话记录；不要清空整个 outputs，里面还有运行必需的 `.so`。

## 6. 网络隔离核验

本版浏览器和媒体端点都在同一台机器。API、ZMQ、原生 RTSP、网关 API/WHEP/ICE 仅绑定回环，不配置 STUN/TURN，也不向外部接口发布 ICE 候选。无需修改系统防火墙。

会话 ready 后可以只读检查 `ss -lntup`，按自有 worker 的命令行、PID 与监听信息核对分配端口（退出后另有 `state/<sid>/lifecycle-final.json`）；原生 RTSP 必须显示 `127.0.0.1`，不能是 `0.0.0.0` 或 `[::]`。网关的生成配置位于 `state/gateway/`，不是 `configs/mediamtx-p0.yml`。关闭后检查同一组自有端口已释放。T18 的实际核验记录保存在验收证据中。

HTTP 端口转发本身不能使远端浏览器连通本版回环 UDP ICE。远程交付必须重新设计和验证页面安全上下文、COOP/COEP、CORS、WHEP/ICE 路由及访问控制；不能将所有监听改成通配地址就宣称完成。

## 7. 数据契约、定位和验收

- [日志格式与适配器扩展](log-import-v1.md)：CSV/JSON 映射、单位、独立通道、质量与预算。
- [采样语义](replay-sampling-v1.md)：包角、多圈角、缺测、插值与有条件的轮角推算。
- [B041 映射](b041-replay-v1.md)：state/action、原始纳秒时钟、夹爪参数、积分轨迹与标注边界。
- [契约与生成工具](contracts-v1.md)：REST/WS/IPC 版本；修改 Python 契约后同步生成前端文件，不直接编辑生成物。
- [故障排查与错误码](troubleshooting-v1.md)：按实际症状和日志定位。
- [证据目录](validation/t18-status.md)：本次交付版本、检查命令、原始证据和未完成范围。

不依赖私有日志的完整真实 UI 闭环，从 backend 执行；输出目录必须不存在：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_frontend.py \
  --isaac-python "$ISAACSIM_PYTHON" \
  --output-dir outputs/t18/my-new-synthetic
```

该工具拥有自己的 API/Vite/Chrome/worker，自动生成合成 CSV，验证实际 UI 创建、上传、播放控制、故障、新会话归零和清理。它使用系统 `/usr/bin/google-chrome`，分配 6700–6799 测试服务端口；不要在同机同时运行其他 GPU 验收或负载测试。

真实 B041 和完整故障/录屏矩阵复用 [韧性验证命令](resilience-validation-v1.md)，将输出改为新的 `outputs/t18/...`。该矩阵额外需要本地 raw/catalog/派生包以及系统 Python 的 OpenCV/NumPy；不为此向 SDK 或 HTTP 环境安装图像分析依赖。T18 的新进程、新 storage 验证不等于清空 shader cache，也不等于从零重装操作系统或 SDK。


## 8. 依赖公告与交付边界

2026-11-02 的锁定依赖安装成功，但 npm audit 仍报告5个受影响包（其中开发依赖2个critical）；仅生产依赖为2个moderate。本轮没有执行 `npm audit fix --force` 或跨主版本迁移。当前AJV未启用公告涉及的 `$data`，ECharts使用 `line` 且未注册tooltip而非公告中的 `lines` 路径，未配置mocker开发服务器插件。Tinypool测试进程的原型污染前置链未验证排除，不能因其属于dev依赖而声称无风险。

本地功能交付不等于依赖安全验收。运行项目自己的可信测试代码；在使用不可信测试代码、共享CI或公开服务之前，需完成相关依赖升级与兼容性复验。公告、原始审计摘要和适用范围见 [T18记录](validation/t18-status.md)。
