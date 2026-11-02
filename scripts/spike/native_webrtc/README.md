# P0 native stream — Isaac Sim 5.1 浏览器验证页

> 历史归档：首期基线已改为 6.2。本页与 SDK 5.6.0 不作为当前 6.2 浏览器入口；复现使用 `feature/p0-isaac51` 的独立工作区。

这是可丢弃的原生 WebRTC 连接 spike，不是正式前端。目标是把 5.1 的流呈现在自研浏览器页面，便于记录连接事件。**构建通过不等于已确认 Isaac 5.1 兼容；本页未实现视频/关节/底盘/log 游标帧同步。**

固定依赖：`@nvidia/omniverse-webrtc-streaming-library` **5.6.0**，按官方 `web-viewer-sample` **1.5.2** 的 `StreamType.DIRECT` / `AppStreamer.connect` 调用形状接入。该样例标明面向 Kit 107.3.1 及以后；本机 Isaac 5.1 使用 Kit 107.3.3。具体能否连接此安装仍须真实实例验证。

## 本地启动

在本目录执行（已带 package-lock.json）：

```bash
npm ci --no-audit --no-fund
npm run build
npm run preview
```

打开 Chromium/Chrome：

- 手动连接：`http://127.0.0.1:8766/`
- 自动连接：`http://127.0.0.1:8766/?autoconnect=1`
- 指定目标：`http://127.0.0.1:8766/?host=127.0.0.1&port=49100&autoconnect=1`

开发模式为 `npm run dev`，同样只监听 127.0.0.1:8766，端口占用时退出，不偷偷换端口。默认主机 `127.0.0.1`、信令 TCP 49100，媒体端口遵循官方样例默认值而不显式传入。请求分辨率为 1280×720、30 fps。画面与日志留在这个页面，不写入正式 frontend。

本机安装验证环境：Node 26.8.1 / npm 11.19.0；SDK 自身声明 Node >=19、npm >=10（其 package.json 比旧样例的 Node 18 声明更严格）。node_modules 与 dist 均由本目录 .gitignore 排除。

## Isaac 5.1 端：给负责启动实例的操作者

本页不启动 Isaac。以下为已核对安装文件的配置方法，**本 spike 尚未用它完成真实连接**。

5.1 官方 standalone 样例在 `SimulationApp` 创建后启用：

```python
from isaacsim.core.utils.extensions import enable_extension
enable_extension("omni.services.livestream.nvcf")
```

该扩展会依赖并启用 `omni.kit.livestream.webrtc`，后者依赖 core 和 streamsdk。本机 5.1 的对应版本为 nvcf 7.2.0、webrtc 7.0.0、core 7.5.0、streamsdk.plugins 7.6.3。仅想验证直接流时也可启用 `omni.kit.livestream.webrtc`；优先保持官方 standalone 样例的 nvcf 路径以减少变量。

由操作者在其专用启动代码中设定、检查这些 **5.1** settings：

```python
kit.set_setting("/app/livestream/enabled", True)
kit.set_setting("/app/livestream/port", 49100)
kit.set_setting("/app/livestream/proto", "websocket")
kit.set_setting("/app/livestream/ipversion", "ipv4")
```

这些值也与本机 webrtc 扩展默认设置一致；core 默认 enabled=true。应在启用直播扩展之前确定端口。使用 nvcf 时安装自带的媒体端口范围为 47998–48020，HTTP 服务默认 8011；5.1 官方基础网络指南列出 TCP 49100 和 UDP 47998。localhost 初测优先；远程网络/NAT 下需另验媒体可达性，单独转发 TCP 49100 不能证明视频可达。

官方 Python 样例使用 `headless=True`、持续 `kit.update()`，这样 headless 也持续渲染；纯物理 tick 或没有 render/update 的循环不能作为本页的画面验收。若已有自研 SimulationApp worker，应在其现有主循环集成，不另起第二个 Isaac。

参考本机文件：

- `/home/xq/isaacsim/VERSION`
- `/home/xq/isaacsim/kit/launcher.toml`
- `/home/xq/isaacsim/standalone_examples/api/isaacsim.simulation_app/livestream.py`
- `/home/xq/isaacsim/extscache/omni.kit.livestream.webrtc-7.0.0+107.1.0.lx64.r.cp311/config/extension.toml`
- `/home/xq/isaacsim/extscache/omni.services.livestream.nvcf-7.2.0+107.0.3/config/extension.toml`

若后续需传自定义消息，再启用 `omni.kit.livestream.messaging` 并注册自己的事件。本页只记录收到的 onCustomEvent；消息到达时间不代表显示帧的 log 时间。

## 验证边界

1. `page ready` 表示页面/SDK 已加载；`connect result` 只表示连接请求结果。
2. `onStart` success 表示流启动事件；`video loadeddata` / `video playing` 才表示浏览器收到/播放媒体数据。仍需人工核对画面内容及真实 Isaac 5.1 实例来源。
3. 5.1 官方规定一个实例同时一个客户端，且需 GPU NVENC。请关闭竞争的桌面 streaming client；已有离线 MP4 正常不证明原生 WebRTC 正常。
4. 断开使用 SDK 公开 `terminate(false)`，不请求关闭 Kit 进程；重连采用重新加载页面，不依赖 SDK 私有字段。
5. 未创建逐帧 metadata、未将 onCustomEvent 对应到视频帧、未实现 seek。不可把这个页面作为“同步验收通过”。

## 官方来源（核对日期 2026-10-23）

- [Isaac 5.1 livestream 指南](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/manual_livestream_clients.html)
- [Isaac 5.1 release notes：Kit 107.3.3](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/overview/release_notes.html)
- [官方 web-viewer-sample 版本选择说明](https://github.com/NVIDIA-Omniverse/web-viewer-sample)
- [sample 1.5.2 的 package.json：SDK 5.6.0](https://github.com/NVIDIA-Omniverse/web-viewer-sample/blob/1.5.2/package.json)
- [sample 1.5.2 的 AppStream 调用](https://github.com/NVIDIA-Omniverse/web-viewer-sample/blob/1.5.2/src/AppStream.tsx)
- [sample 1.5.2 的 NVIDIA registry](https://github.com/NVIDIA-Omniverse/web-viewer-sample/blob/1.5.2/.npmrc)
- [sample 1.5.2 的 Vite GFN external 设置](https://github.com/NVIDIA-Omniverse/web-viewer-sample/blob/1.5.2/vite.config.ts)

此处使用独立的原生 JS 页面和 SDK 的公开 API，没有复制整个 React 样例。仅直接连接，无 GDN/GFN 云端脚本。官方样例现已归档、5.1 官方也已停止支持，因此固定依赖，不用 latest 或 6.2 的 `primaryStream` 配置替代本验证基线。

## 本次本地验证

2026-10-23：`npm run build` 通过；构建包含 SDK 的约 620 kB JS，Vite 给出 chunk 超过 500 kB 的体积提示。`node --check src/main.js` 和本地 HTTP 页面/静态资源读取通过。当前工具未提供可用浏览器，未做浏览器内点击或真实 WebRTC 连接验收；主任务随后已启动 Isaac 5.1 原生直播扩展并验证 49100 监听，详见项目 P0 验证记录；尚无实际浏览器媒体接收证据。
