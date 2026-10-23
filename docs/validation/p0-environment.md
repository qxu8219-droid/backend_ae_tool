# P0 运行环境与资源测量

2026-10-23，本机 Linux，RTX 5070 Ti Laptop 12GB，驱动 580.178.04。IsaacSim 实际构建为 6.2.0-rc.14+release.52805.02189a84.gl，Python 3.12.15，PhysX。资产为项目内 6.x 完整交付包，54 文件、53 项 checksum，28 DOF；依赖闭包见 [6.2 记录](p0-isaac62-status.md)。

`catalog-sync-final` 新进程启动到 P0_READY 为 6.81 秒。已有 shader/cache 目录保留，因此这不是清空缓存后的首次机器冷启动。源端执行循环 19.49fps；独立浏览器 60 秒窗口为 19.61fps，不将两者视为同一指标。

`outputs/p0/catalog-sync-resource/` 另做 30 秒持续播放与 5 秒资源采样：真实浏览器通过，531 次新呈现，17.70fps，标记错配 0。

| 指标 | 本轮观察值 | 口径 |
|---|---|---|
| GPU 显存 | 活跃样本约 2388～2455MiB，退出后约 608MiB | 整个 GPU，包含桌面等其他使用者 |
| GPU encoder | 主要为 7～9% | nvidia-smi 采样，支持 NVENC 实际活动判断 |
| GPU utilization | 多数活跃样本 65～90% | 不是长期上界，存在瞬间低值 |
| 受管进程 CPU | 稳定阶段约 149～207% | 100% 表示一个逻辑核，取 /proc ticks 增量；不是整机百分比 |
| 受管进程 RSS 合计 | 稳定阶段约 8049～8107MiB | 含共享映射的重复计算，不能解释为独占物理内存 |
| 退出 | 自有进程归零、相关监听关闭 | supervisor 清理记录与端口复核 |

采样包含 Isaac、MediaMTX、测试 Node 和 Chrome；不能用于宣称正式 FastAPI CPU<5% 已达标。资源探针与录屏探针分开，不能把其中一个的性能当作另一个的结果。未执行 30 分钟泄漏验收。

原始采样、配置、路径与录屏摘要见 [p0-whep-sync-evidence.json](p0-whep-sync-evidence.json)。本机性能会随 GPU 功率、后台负载和缓存状态变化；服务器安装匹配 6.2 环境后需要单独验证。
