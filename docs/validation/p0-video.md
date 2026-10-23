# P0 浏览器媒体路线

2026-10-23。版本固定为 IsaacSim 6.2.0-rc.14、MediaMTX 1.21.2、Playwright 1.58.2、系统 Chrome 152.0.7977.82。运行范围为本机 Linux，浏览器使用临时 profile 和 headless 模式；未使用用户的浏览器 profile，也未部署服务器。

## 实际接通的路线

```text
独立 Replicator 相机产品 → IsaacSim RTSP writer / NVENC H.264
  → rtsp://127.0.0.1:8554/stream
  → MediaMTX 拉流 → WHEP :8889/stream/whep / ICE UDP :8189
  → Chrome RTCRtpScriptTransform → WebCodecs VideoDecoder
  → 按图像身份选择数据 → 同一 canvas 呈现
```

HTTP 实验页面监听 `127.0.0.1:8768`，MediaMTX 管理 API 为 `127.0.0.1:9997`。网关的 RTSP、RTMP、HLS、SRT 等其他监听关闭。IsaacSim 是 RTSP 服务端；网关是拉流客户端。

普通 `rtsp` 模式仍使用应用 framebuffer；严格同步的 `rtsp_sync` 使用 `isaacsim.streaming.rtsp` 的独立相机 writer。不能将这两条采集路径的性能或身份结论混用。

## 对比与决定

| 候选 | 已验证能力 | 边界 |
|---|---|---|
| 6.2 RTSP → 本地 libav | 收流、H.264 解码、1280×720 | 只证明本地媒体接收 |
| 6.2 相机 RTSP → MediaMTX → WHEP | 真实 Chrome 解码、SEI 保留、像素身份配对 | 当前用于后续正式集成的候选基线 |
| 5.1 HTTP/JPEG 配对 | 历史实验 | 不继承为 6.2 性能证据 |
| 旧 native Web SDK 页面 | 旧环境构建与启动探针 | 未在 6.2 完成验证，不作为当前路线 |

选择 WHEP 候选的依据包含严格同帧配对，而不只是浏览器有画面。具体身份方案见 [同步记录](p0-sync.md)。目前仍是实验脚本，没有正式 REST/WS 会话和日志上传服务。

## 生命周期与复现

`outputs/p0/whep-lifecycle-release/` 的真实浏览器用例通过两轮断开/重连：每轮新建 PC 并实际解码帧，检查 WHEP Location、DELETE 2xx、所有旧 PC 关闭、video 解绑和网关会话归零。修复了“先关闭 PC 使网关先删除资源，DELETE 随后返回 404”的释放顺序。

测试监督器负责启动源、网关、页面和浏览器，先检查端口，退出时只清理自身后代（包括独立进程组的 Chrome）。TCP 预检允许 TIME_WAIT，仍拒绝活动监听；现有外部进程不会被停止。

```bash
export ISAACSIM_PYTHON=/absolute/path/to/isaacsim62/bin/python
python3 scripts/run_p0_browser_test.py --run-dir outputs/p0/new-lifecycle --test lifecycle
python3 scripts/run_p0_browser_test.py --run-dir outputs/p0/new-sync \
  --test sync --delay-modes metadata,video
```

每次使用新目录；`worker/`、`browser/`、`logs/` 和 `supervisor.json` 分开保存。依赖安装见 [README](../../README.md)，网关来源与校验见 [SOURCE.txt](../../tools/mediamtx/SOURCE.txt)。

性能应读取浏览器呈现记录；源循环率、接收吞吐与包含控制等待的平均呈现率是不同指标。长时间稳定性、真实网络丢包、网关/worker 异常重建和远程网络条件仍需后续测试。
