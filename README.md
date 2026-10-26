# 机器人日志回放平台后端

首期基线现为 **Isaac Sim 6.2.0 / Python 3.12 / PhysX**，使用对应的 6.x USD。服务器后续需要部署同一运行环境；只替换 USD 不能替代运行时升级。当前开发到 T12：已具备前端 Mock 工作台、独立导入/采样/时间轴、真实 worker/ZMQ 以及会话和媒体网关生命周期。HTTP/WS 业务绑定和前端实际媒体接入仍待 T14/T15。

当前结果见 [6.2 验证记录](docs/validation/p0-isaac62-status.md)。[5.1 验证记录](docs/validation/p0-isaac51-status.md)与旧资产保留供追溯，不作为当前版本的验收证据。

已接通真实 Chrome 的严格同步原型：1280×720、60 秒稳定播放约 19.6fps；图像 ID＋CRC 精确匹配快照后，在同一 canvas 展示机器人、28 关节、底盘和进度。暂停、seek、单步、0.1x/10x、双向额外延迟 700ms 及两轮重连通过。详细方法、统计口径和未完成项见 [媒体路线](docs/validation/p0-video.md)与[同步报告](docs/validation/p0-sync.md)。

用户已确认沿用 ≥15fps、共同延迟 <2s 的健康播放目标；故障等待时同步区域共同保持并提示状态。新增运动中的消息丢弃/乱序/重复、真实网关停启、浏览器刷新和 worker SIGKILL/重载验证见[故障报告](docs/validation/p0-faults.md)。

## P1 接口契约

`app/` 提供 18 个 REST 路由声明与 WS 模型，`proto/` 是 worker 可独立导入的共享协议，`contracts/` 保存生成 Schema、49 份有效样例和 TypeScript 类型。完整说明、安装与验证命令见 [contracts-v1.md](docs/contracts-v1.md)。

合法业务调用目前返回 `503 BACKEND_NOT_CONFIGURED`，WS 关闭 1013；独立服务模块已实现，正式 HTTP/WS 绑定仍待 T14。当前可独立运行的前端 Mock 工作台见 [frontend README](../frontend/README.md)。

## T08 实际日志导入

`app/adapters/` 提供 CSV/JSON 参考 reader、映射与规范化；`app/services/log_store.py` 提供原文件/映射快照/规范化结果的原子归档与查询，解析在有界独立 CPU 进程执行。它不会创建 IsaacSim 会话或改变当前播放状态。完整格式、异常表、资源界限及插件说明见 [导入说明](docs/log-import-v1.md)，本轮证据见 [T08 验证](docs/validation/t08-status.md)。

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports --session offline-analysis import /path/to/source.csv

env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -o addopts= -q

env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_import.py \
  --output-dir outputs/t08/my-new-probe
```

最后一个命令生成明确标注的合成 CSV，并实际解析/归档、核对哈希与夹爪方向及记录事件循环观测；输出目录必须不存在。真机样本已另行归档，夹爪标定和底盘语义仍待团队确认。HTTP 上传入口保持 503，等待 T14 接入，不以离线导入成功冒充端到端回放成功。

## T09 确定性采样

`app/replay/` 提供按原采样时钟定位的 `ReplaySampler`：每段日志准备一次，之后生成带真实调用方身份的 `TargetFrame`，支持独立频率通道、包角展开、保留多圈值、缺测回退和前后原采样点定位。虚拟轮角默认关闭，需要显式半径及三个关节方向，并保留来源与模型限制。

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_sampling.py \
  --output-dir outputs/t09/my-new-probe
```

该探针实际导入并归档合成 CSV，然后验证顺序/随机/反向查询的一致性和原数据不变，记录 CPU 耗时；不启动仿真。接口与缺测/角度语义见 [采样说明](docs/replay-sampling-v1.md)，本轮证据见 [T09 验证](docs/validation/t09-status.md)。T10 时间轴核心现已完成，见下一节。

## T10 回放时间轴核心

`Timeline` 提供完整播放控制与单调时钟推进；`DeliveryTracker` 分开管理可靠关键目标和只保留最新一帧的播放队列，并区分接受、应用和渲染证据。核心由调用方显式 `tick()`，不在导入模块时启动后台任务。接口、状态矩阵与后续传输要求见 [时间轴说明](docs/replay-timeline-v1.md)，验证见 [T10 记录](docs/validation/t10-status.md)。

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_timeline.py \
  --output-dir outputs/t10/my-new-probe
```

该 T10 探针使用真实导入和采样模块，但 worker 是测试替身，合成回执不代表实际渲染或浏览器呈现。真实 worker 见下一节。

## T11 独立 worker 与本地数据

`sim_worker/` 已接入真实 IsaacSim 6.2：通过 ZMQ 接收目标，应用并读回 28 DOF/底盘，在实际图像处生成帧身份和正式快照。独立 H.264 解码探针核验像素 ID/CRC、SEI 快照与发送目标。启动、协议、安装和复现命令见 [worker 说明](docs/worker-v1.md)，证据见 [T11 验证](docs/validation/t11-status.md)。正式浏览器联调仍待 T15；T12 会话生命周期见下一节。

原生 SDK RTSP 默认监听所有 IPv4 接口，worker 默认拒绝该行为。T11 历史合成探针曾显式允许；T12 会话启动器已用项目内绑定适配库限制为回环监听，并再次检查实际地址。

已下载的真机样本已归档到 `data/real_logs/raw/`，12 个来源条目、27 个文件，SHA256 校验并只读保存；夹爪与底盘待确认字段尚未转换。目录、溯源和后续数据放置约定见 [本地数据说明](docs/local-data-storage-v1.md)。

## T12 会话生命周期

异步 SessionManager 管理单会话、真实启动相位、独立 RPC/心跳期限、故障状态及资源清理；MediaMTX 支持托管和外部模式，网关恢复不会重建 worker。应用 lifespan 可显式托管该管理器，默认契约应用仍不启动 GPU。详细接口、端口、所有权与 Linux 运行边界见 [生命周期说明](docs/session-lifecycle-v1.md)，实际故障/重载证据见 [T12 验证](docs/validation/t12-status.md)。

```bash
bash scripts/build_rtsp_loopback.sh
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_sessions.py \
  --output-dir outputs/t12/my-new-validation \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

此命令验证真实 SDK 与网关的完整资源流程，使用合成轨迹，退出时清理本次进程。下一项 T13 将实现限位元数据、历史事件与保峰曲线。

## 当前浏览器实验入口

在本目录安装测试依赖；使用系统 Chrome，临时 profile 只访问 localhost。MediaMTX 官方二进制放在项目 `tools/mediamtx/`，来源、SHA256 与重装命令见 [SOURCE.txt](tools/mediamtx/SOURCE.txt)。第三方二进制与 node_modules 均不进入 Git。

```bash
npm ci --prefix scripts/spike/browser_tests
export ISAACSIM_PYTHON=/home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
python3 scripts/run_p0_browser_test.py --run-dir outputs/p0/my-browser-sync \
  --test sync --delay-modes metadata,video
```

监督器自动启动源、MediaMTX、页面和浏览器，保存报告后清理本次子进程。端口已被其他服务占用时拒绝启动。每次使用新的或空的目录，不能复用旧结果目录。

增加同步重连与持续运行测试：

```bash
P0_SYNC_RESILIENCE=1 P0_SYNC_SOAK_MS=60000 \
python3 scripts/run_p0_browser_test.py --run-dir outputs/p0/my-sync-soak \
  --test sync --delay-modes metadata,video
```

如需手动查看，分别运行下列三个命令，在 `P0_READY` 后打开 `http://127.0.0.1:8768/?autoconnect=1&sync=1`：

```bash
bash scripts/run_p0.sh --mode rtsp_sync --width 1280 --height 720 \
  --rt-subframes 1 --seconds 300 --output-dir outputs/p0/my-sync-live
bash scripts/run_mediamtx_p0.sh
python3 scripts/spike/whep_preview.py --output-dir outputs/p0/my-sync-live --port 8768
```

这三个手动进程需分别退出。页面中的色块是图像身份诊断标记；该页面是 P0 验证工具。源端传输正常不等于浏览器验证通过，浏览器结果位于 `browser/<scenario>/sync-presentation.json`。

可选 `P0_SYNC_RECORD_VIDEO=1` 保存 WebM，需要先完成与 Playwright 匹配的 FFmpeg 安装：在测试目录执行 `npx playwright install ffmpeg`，等待成功退出后再运行录屏。使用 `PLAYWRIGHT_BROWSERS_PATH` 时，安装与测试需指向同一目录。本机 Node 26 的一次解压停滞已保留失败记录，当前录屏工具使用该已下载、ZIP CRC 校验通过的官方包重新解压；未改系统 FFmpeg。

## 资产与运行环境

完整资产包已复制到 `assets/robots/r1pro_2026/isaacsim_6_x/`：

- USD 入口：`r1pro_2026.usd`。
- 关节配置：`reproduction/configs/r1pro_2026_common.json`。
- 引用到 `/World/Robot` 后的 Articulation 根：`/World/Robot/Geometry/base_link`。
- 配置单一入口：[configs/robot-asset.json](configs/robot-asset.json)。

部署时复制整个资产目录。资产不进入源码 Git；源码保留来源、版本与 checksum 配置。53 项文件校验、转换版本和实际 articulation 根都要匹配。6.x 资产没有外部 MDL 白名单，不再沿用 5.1 的 OmniPBR 例外。

启动入口改为明确的 pip Python；本地设置如下，服务器换成其自己的 6.2 venv 路径：

```bash
export ISAACSIM_PYTHON=/home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

`scripts/run_p0.sh` 清除本次子进程继承的旧 Isaac/ROS Python 与动态库路径；`SimulationApp` 启动前锁定 PhysX、关闭 Newton 自动切换与持久化设置读取。运行时再次核对版本和实际物理引擎。EULA 环境变量使用 `OMNI_KIT_ACCEPT_EULA`；已有值会保留。

## 基线检查

在本目录执行。CPU 测试需要 Python 标准库和 Node，HTTP/JPEG 探针另需 Pillow。Isaac 探针使用选定的 SDK 环境依赖。

```bash
python3 -m unittest discover -s tests -v
python3 scripts/spike/asset_audit.py --output outputs/p0/asset-audit-62.json
bash scripts/run_p0.sh --mode baseline --output-dir outputs/p0/my-62-baseline
```

每次仿真必须使用新的或空的输出目录。检查包含 28 DOF 名称映射、五组姿态、底盘位置与四元数、超限直设、运行时依赖闭包和图像保存。禁止将 5.1 资产覆盖到 6.x 目录；传入不匹配的转换版本会在启动 SDK 前被拒绝。

## 6.2 RTSP 视频探针

终端一：

```bash
bash scripts/run_p0.sh --mode rtsp --width 1280 --height 720 \
  --seconds 120 --output-dir outputs/p0/my-62-rtsp
```

该路线启用 `omni.kit.livestream.rtsp` 与 `omni.kit.livestream.app`，捕获应用 framebuffer；RTSP 地址为 `rtsp://127.0.0.1:8554/stream`。`--rtsp-port` 可调整服务端口。源是 IsaacSim 的 RTSP 服务端，后续网关应拉流，不能把它当作向 MediaMTX 主动推流。

终端二，在看到 `P0_READY` 后：

```bash
bash scripts/spike/build_rtsp_probe.sh
outputs/p0/tools/rtsp_decode rtsp://127.0.0.1:8554/stream 8 \
  outputs/p0/my-62-rtsp/decoder.json outputs/p0/my-62-rtsp/first-frame.ppm
```

解码探针使用系统已安装的 GCC、pkg-config、libavformat/libavcodec/libavutil/libswscale 开发库；构建输出到 gitignore 的 `outputs/`。使用 CPU 解码，不安装或替换系统媒体包。无这些开发库时会明确构建失败。

报告分别记录声明帧率、PTS 单调性、PTS 估算帧率、实际接收帧率和首帧等待时间。接收吞吐可能包含启动缓冲释放，不能当作浏览器显示帧率；首帧等待时间也不等于端到端延迟。无流、静默服务或卡住会超时退出并输出失败 JSON。

**此默认 framebuffer RTSP 模式不提供业务帧配对。** 严格同步使用上文的 `rtsp_sync` 独立相机路线；浏览器通过编码变换显式解析 SEI，再根据实际图像 ID 选择快照。

## 成对图像实验与历史入口

原有 HTTP/JPEG 配对页仍可用于独立同步实验，但 5.1 的性能、画质与通过结果不会继承给 6.2。需要另做真实运行验证：

```bash
bash scripts/run_p0.sh --mode paired --renderer PathTracing \
  --width 640 --height 480 --samples 1 --rt-subframes 1 \
  --seconds 300 --output-dir outputs/p0/my-62-paired
python3 scripts/spike/paired_preview.py --output-dir outputs/p0/my-62-paired --port 8765
```

采集与页面服务在两个终端运行；看到 `latest.frame` 后打开 `http://127.0.0.1:8765/`。同一 canvas 共同呈现图像、关节、底盘和游标，并核对真实像素标记。`joints` 是运行时读回值，`source_joints` 保留原始累计轮角；夹爪单位为 m，其他关节为 rad。该探针仍使用临时控制邮箱，正式控制屏障与快照契约在后续阶段实现。

`scripts/spike/native_webrtc/` 是 5.1/旧 SDK 的归档候选，**不是 6.2 当前浏览器入口**。当前启动器不提供旧 `native` 模式；需要复现时使用 `feature/p0-isaac51` 的独立工作区与 5.1 环境。

## 输出与退出

`results.json` 保存版本、物理引擎、运行参数、姿态检查及执行循环统计；`trace.jsonl` 在 RTSP 模式中只是服务端执行记录，不能当作编码帧时间戳。`decoder.json` 与首个解码图像提供独立媒体接收证据。

仿真在 `--seconds` 后退出，也可 Ctrl+C；手动页面服务独立退出。故障后使用新输出目录重新加载，从头播放。同步可行性已有真实浏览器证据；完整异常注入、正式前端、真实 log 与服务器验证仍未完成。
