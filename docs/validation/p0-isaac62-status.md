# P0 切换到 Isaac Sim 6.2 的验证记录

日期：2026-10-23。用户选择 6.x 后，首期具体锁定本机已安装并与资产配套的 **6.2.0 / Python 3.12 / PhysX**。项目仍为 `Model_Evaluation/backend`；未修改相邻的旧后端项目，也未操作服务器环境。

**本地 Chrome 严格同步可行性已验证；P0 的完整异常矩阵仍未收尾。** 原 5.1 的通过结果保留为历史，不作为 6.2 证据。

## 最新进展：WHEP 与严格配对

- MediaMTX 1.21.2 / Chrome 152 接入成功；编码 SEI 经网关保留，浏览器用图像完整 ID＋CRC 精确选择候选快照。
- `catalog-sync-final` 的 3 个浏览器用例通过，涵盖业务控制、单侧 700ms 延迟、同步模式两轮重连和 60 秒稳定播放。
- 源端 1994/1994 raw 标记验证通过；浏览器累计 1495 次配对呈现，688 次独立画布采样，未检出错配。
- 60 秒窗口内新增 1177 次呈现，约 19.61fps。正常场景含重连的共同延迟中位数 94.7ms、P95 147.6ms、最大 907.2ms。
- 86 项 CPU 测试通过；两项独立审查发现的 request_id 精度及发送失败报告问题已修复并复核。
- 所有本轮监督器创建的 GPU、媒体、HTTP 与浏览器子进程均按运行报告清理；正式 FastAPI/Vue 尚未开始。

方法和边界见 [p0-sync.md](p0-sync.md)、[p0-video.md](p0-video.md)。以下为版本切换时保存的基线历史，不代表最新浏览器状态。

## 基线变化

- 完整 6.x 交付包复制到 `assets/robots/r1pro_2026/isaacsim_6_x/`，保留 5.1 副本。
- 使用 pip 环境 `/home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python`，实际 Python 为 3.12.15。
- 实测 SDK 构建为 `6.2.0-rc.14+release.52805.02189a84.gl`；仅声明此构建的本轮结果，不推及所有 6.x。
- 运行配置、资产默认值、启动器及 TodoList 切换到 6.2；启动前清除子进程继承的旧 Isaac/ROS 导入环境。
- 在 Kit 启动前禁用 Newton 自动切换、指定 PhysX，并关闭持久化设置读取；初始化前后检查实际引擎。
- 检查资产的 `conversion.json` 版本和引用后的实际 articulation 根；不匹配的 5.1 资产不能误用。
- 6.x 包自身依赖闭合，取消 5.1 的外部 OmniPBR 白名单。

## 本轮实测

| 检查 | 结果 |
|---|---|
| 资产副本 | 54 文件，53/53 checksum 通过 |
| 运行时 USD 闭包 | 13 层、1 个纹理依赖；全部位于项目资产包内，无 unresolved、无外部豁免 |
| 机器人 | 28 DOF，按名称映射；根为 `/World/Robot/Geometry/base_link` |
| 物理引擎 | `physx`，运行后核验通过 |
| 姿态 | 零位、转向/车轮、躯干、双臂/夹爪、基座五组检查通过，包含基座四元数误差 |
| CPU 单元测试 | 53/53 通过，包含版本拒绝、环境清理、资产闭包策略与报告指标区分 |
| C 接收探针 | 使用系统 libav 与 libswscale，严格编译零警告；拒绝连接、非法参数和静默 TCP 超时负例通过 |
| RTSP 首次接收 | H.264，267 个有效解码帧，实际尺寸 1440×900；发现输出跟随应用窗口 |
| RTSP 修正后接收 | 锁定窗口尺寸后为 1280×720；6 秒采集窗口内得到 209 个有效解码帧，声明帧率 30fps |
| GPU 编码活动 | 接收期间 `nvidia-smi dmon` 编码单元利用率 1～2%，支持实际使用 GPU 编码的判断 |
| 图像检查 | 保存并检查了从 RTSP 独立解码的机器人图像；不是直接复制本地 Replicator 图像 |

修正后的接收探针首帧等待约 1.85 秒；这是连接与首帧等待时间，**不是端到端显示延迟**。观测接收吞吐约 34.48fps，包含启动缓冲释放，不作为稳定源帧率或浏览器帧率结论。

本轮解码存在 1 次非递增 PTS，因此 PTS 估算帧率报告为 `null`；unregistered SEI 检出帧数为 0。它们不影响“收到并解码了 H.264”的结论，但不支持“编码帧已经与业务日志帧对应”的结论。

服务端 `world.render()` 循环指标与媒体接收指标已分开：当前程序使用 `render_iterations/server_loop_fps/mean_render_iteration_ms` 报告 RTSP 模式，marker 未执行时记为 `null`。原始实测报告生成于这次字段修正前，其 `frames/published_fps` 仅代表服务端循环；结构化摘要已明确注明这一来源。

完整数值与证据来源见 [p0-evidence-62.json](p0-evidence-62.json)。原始数据保存在：

- `outputs/p0/baseline-62/`：基线加载与图像。
- `outputs/p0/rtsp-62-decode/`：第一次 RTSP 接收。
- `outputs/p0/rtsp-62-final/`：1280×720 复测、首帧图像、GPU 编码采样。
- `outputs/p0/unit-tests-62.log`：CPU 测试。

## 早期 RTSP 探针的边界（历史）

P0 当前验证的是 `omni.kit.livestream.app + omni.kit.livestream.rtsp` 应用 framebuffer 路径。IsaacSim 提供 `rtsp://127.0.0.1:8554/stream`，网关应作为客户端拉取；本轮未安装或接通 MediaMTX。

6.2 还提供相机 RTSP writer 与逐帧元数据能力，但默认 simulation time、内部 frame number 不等于我们的 `log_time_s/epoch/render_seq`。浏览器也不能直接播放 RTSP，不能假设 `<video>` 可读取 SEI。下一步必须验证媒体转发是否保留可追踪身份，以及浏览器实际呈现的帧是否与面板一致。

本次再次检查浏览器工具，仍无可用浏览器连接。故不把端口监听、C 解码或 SDK 能力说明算作浏览器验收。

## 尚未完成的关卡

- 补齐实际断流、丢帧/乱序、网关或 worker 异常和刷新等测试；已完成的延迟注入与主动重连不能代替全部故障。
- 当前支持范围仅为已测 Chrome；正式页面加入能力检测，完整长时间稳定性放入 T17。
- 用户确认最终共同延迟上限；当前按原计划 ≥15fps、共同延迟 <2s 记录初始结果。
- 服务器部署 6.2 后，另验其 GPU/驱动、编码、资源与网络条件。

启动与复现方式见 [项目 README](../../README.md)。旧版对照见 [5.1 历史验证](p0-isaac51-status.md)。
