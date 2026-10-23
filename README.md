# 机器人日志回放平台 P0

首期基线现为 **Isaac Sim 6.2.0 / Python 3.12 / PhysX**，使用对应的 6.x USD。服务器后续需要部署同一运行环境；只替换 USD 不能替代运行时升级。当前仍是 P0 探针，尚未进入正式 FastAPI/Vue 开发。

当前结果见 [6.2 验证记录](docs/validation/p0-isaac62-status.md)。[5.1 验证记录](docs/validation/p0-isaac51-status.md)与旧资产保留供追溯，不作为当前版本的验收证据。

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

**RTSP 解码通过不代表浏览器或严格同步通过。** 浏览器仍需要 WebRTC/WHEP 或其他可验证的接入。当前默认 framebuffer 流没有建立 `epoch/log_time_s/render_seq` 与显示帧的映射；不能因为 SDK 提供 metadata API 就假定浏览器能读取 SEI。

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

仿真在 `--seconds` 后退出，也可 Ctrl+C；页面服务独立退出。故障后使用新输出目录重新加载，从头播放。实际浏览器呈现、共同显示延迟和最终画质仍需 P0 验收。
