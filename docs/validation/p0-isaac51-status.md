# P0 验证记录与 Isaac Sim 5.1 影响

> 历史记录：用户随后选择 6.x，当前首期固定为 6.2。下文是切换前的 5.1 证据，不代表 6.2 已通过相同验收。当前状态见 [6.2 验证记录](p0-isaac62-status.md)。

日期：2026-10-23。首期基线按用户要求切换为 **Isaac Sim 5.1 + 5.1 USD**，项目位置为 `Model_Evaluation/backend`。服务器未安装或升级任何软件，也未进行服务器实跑。

**P0 尚未整体验收通过。** 资产、运动学读回、原生直播服务启动，以及一条低画质的成对图像/数据传输实验已经有实测证据；实际浏览器呈现、正式视频路线和最终画质仍待验收。

## 已完成的检查

| 项目 | 本轮结果 | 证据 |
|---|---|---|
| 项目内资产副本 | 49 文件，48/48 manifest 校验通过，源包未修改 | `outputs/p0/asset-audit.json` |
| USD 依赖 | 8 层、2 个资产依赖；机器人文件全部位于副本内，额外使用运行时内置 OmniPBR.mdl，无 unresolved | `outputs/p0/baseline-51-final/results.json` |
| 机器人映射 | 28 DOF，21 revolute / 3 continuous / 4 prismatic；按名称映射实际乱序 DOF | 同上 |
| 姿态与基座 | 零位、转向/车轮、躯干、双臂/夹爪、基座五组检查通过，包含位置及四元数角误差 | 同上 |
| 原生 WebRTC 服务端 | 5.1 自带 nvcf/webrtc 扩展启动，TCP 49100 监听，正常退出 | `outputs/p0/native-51-final/listener-validation.json` |
| 原生 WebRTC 页面 | 固定 SDK 5.6.0，构建、JS 检查与 HTTP 静态资源检查通过 | `scripts/spike/native_webrtc/` |
| CPU 与页面脚本测试 | 46/46 通过；页面脚本使用 Node VM，不是实际浏览器 | `outputs/p0/unit-tests-final.log` |
| 成对实验 A | 640×480，PathTracing，1 spp，无降噪；90 秒、1810 帧、整轮平均 20.11fps，采集端像素标记错配 0 | `outputs/p0/live-51-final/results.json` |
| 成对实验 B | 相同参数；40 秒、1076 帧、整轮平均 26.89fps，采集端像素标记错配 0 | `outputs/p0/control-51-final/results.json` |
| HTTP 与控制 | 独立检查 81 个不同 JPEG 的实际像素标记；19 次控制回执通过，错配 0 | `outputs/p0/control-51-final/http-validation.json` |

两轮成对测试包含播放、到末尾后的保持或暂停；表中是整轮平均吞吐，不是全程运动场景的最低 FPS。HTTP 客户端收到帧并完成 JPEG 核对时，帧龄中位约 30.14ms、最大约 71.82ms；**此指标不包含浏览器解码、合成与屏幕显示，不能称为端到端显示延迟**。

19 次控制覆盖暂停保持、0.1x、10x、seek、前后单步、停止归零、到末尾和重新播放；控制按实际 worker 回执串行验证。快速命令洪泛、网络重连、长时间稳定性仍不在本轮通过范围。

本地 GPU 为 RTX 5070 Ti Laptop 12GB、驱动 580.178.04；worker 为 5.1.0-rc.19 / Python 3.11.13 / Kit 107.3.3。运行中的一次 GPU 内存观测约 2992MiB，它不是峰值显存或服务器性能结论。完整结构化结果保存在 [p0-evidence.json](p0-evidence.json)，原始日志、图像和逐帧数据位于 gitignore 的 `outputs/p0/`。

## 5.1 对后续开发的实际影响

1. **运行环境需隔离。** 5.1 worker 使用其内置 Python 3.11，不能直接使用原计划的 6.2/Python 3.12 worker 路径。主服务与 worker 的进程隔离仍适用，共享协议包需兼容两端。
2. **原媒体选型需要重做，但不能断言 5.1 无法接浏览器。** 已找到匹配旧 Kit 的官方 WebRTC 样例候选并准备验证页；尚未在真实浏览器收到媒体。原文“没有官方 JS 客户端”“5.1 无法浏览器回放”的绝对结论不继续采用。[官方样例](https://github.com/NVIDIA-Omniverse/web-viewer-sample)、[5.1 直播文档](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html)
3. **新版逐帧 metadata API 不能照搬。** 本机 livestream core 是 7.5.0，官方变更记录显示 Python IServer 和逐帧 metadata 是后续 10.x 能力；需要自行建立显示帧与日志状态的关联。[core 变更记录](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.core/latest/CHANGELOG.html)
4. **严格同步仍有渲染和画质成本。** 本轮 RaytracedLighting 探针出现诊断块旧色逐渐衰减，显式采图、zero-delay、关闭 AA 和几何可见性标记均未使所有实验通过。PathTracing 无降噪可通过像素核对，但低采样图像明显有噪声。960×720/8 spp 的早期实验只有约 4.51fps，640×480/1 spp 才达到表中吞吐。没有把这条临时 HTTP/JPEG 路线定为正式方案。
5. **连续轮角需要运行时适配。** 直接设置超过 ±2π 的累计车轮角触发 5.1 底层驱动目标错误。当前探针只在运行时转为等价的 `[-π,π)` 旋转，读回值放在 `joints`，原始累计角保留在 `source_joints`；正式数据契约与界面必须保留这种区别。
6. **部署结论需在服务器复验。** 本轮只使用本机 5.1，服务器的 GPU、驱动、编码与浏览器网络路径尚未验证。没有证明必须升级 6.2，也没有证明所有首期指标已经在 5.1 上满足。

本轮曾尝试以动态材质颜色编码帧号，失败后改为固定材质的几何可见性标记。验证始终读取真实渲染像素，并进一步读取 HTTP JPEG 像素；没有通过只比较同一个 JSON 字段来证明图像同步。材质历史混合的具体 RTX 内部机制尚未定位，不将其直接定性为全部 5.1 渲染路径的缺陷。

## 未完成的 P0 关卡

- 实际浏览器的解码、共同绘制、屏幕呈现与逐帧录像核对。本次 `cua.getState()` 没有可用浏览器，Chrome 和内置浏览器入口均返回不可用。
- 原生 WebRTC 页面实际收到 5.1 视频，以及此路径中的业务帧身份关联。
- 在可接受画质下达到目标帧率，并测量真正的显示延迟；与用户确定共同延迟上限和同步验收口径。
- 视频/数据单侧迟到及重连的真实浏览器测试。当前只有页面逻辑测试和 HTTP 层验证。
- 渲染模式选型与高质量帧同步路径收敛。P1 正式媒体组件不先锁死这条 JPEG 实验路线。

## 已采取的工程修正

- 原资产包完整复制且按 checksum 核验；运行时依赖检查允许唯一明确的 Isaac 内置 MDL，不允许继续引用原项目目录。
- 拒绝复用非空运行目录，避免旧帧、旧 epoch、旧控制和旧结果污染新运行。
- 底盘校验同时检查位置与四元数，帧快照使用实际读回的 x/y/yaw。
- HTTP 页面夹爪标为 m；其余旋转关节标为 rad。
- 独立代码审查的两项重要问题已修复，对应失败用例转为通过；本轮没有宣称浏览器已验收或 P0 全部完成。

复现命令见 [项目 README](../../README.md)。
