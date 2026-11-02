# T17 韧性、同步与持续性能 Implementation Plan

> **For agentic workers:** 使用 writing-plans/TDD，独立验证工具以 dispatching-parallel-agents 并行实现，主代理集成并执行；结束时一次独立审查。用户已批准原 T17 计划并要求继续执行，无需重新审批相同范围。

**Goal:** 用正式 UI、已适配真实日志和真实 SDK 6.2 完成故障/控制矩阵、录屏逐帧对照及至少 30 分钟持续回放，留下可复查的同步、性能和资源证据。

**Architecture:** 复用 T16 的自有进程启动/清理与独立像素 oracle，新增矩阵/soak 两种浏览器模式和只读资源监测；短 smoke 排除工具错误后运行完整 1800 秒 soak，再执行故障矩阵和录屏分析。工具只在本地运行，媒体仍走正式 API/WS/SEI/WHEP/WebCodecs。

**Tech Stack:** 既有 Python3.12、Vue/TypeScript、Playwright/Chrome、SDK6.2/PhysX；系统 Python 的既有 OpenCV/Pillow 用于离线视频分析；不安装新 SDK/系统依赖。

**Spec:** `../../../.trae/documents/robot-log-replay-platform-todolist.md` T17；用户已确认同帧严格对应、健康至少15fps/共同延迟小于2秒、故障新sid从0秒重加载。

## Global Constraints

- 复用两仓 `feature/p1-contracts`；后端基线a9ed2c7，前端1fc17d3。远端严格只读且本轮不访问；本地raw27+catalog前后SHA一致。
- 自有新输出目录、回环监听、独立端口；只给本轮拥有且PID身份/命令已核验的worker/gateway注入故障，不改变系统网络、防火墙或其他服务。
- SDK Python入口保留venv symlink，环境清除PYTHONPATH/PYTHONHOME/LD_PRELOAD/LD_LIBRARY_PATH。一个GPU会话序列；不并行运行GPU或后端进程测试。
- B041仍用T16provisional profile；数据语义不在T17重猜或提升为精确标定。
- 运行时每个实际呈现统计捕获延迟和有界缓存；约每5秒独立核对图像ID/CRC/同帧文字与源值，并离线核对完整worker采集追踪的数值/质量。录屏分析独立读取实际Chrome无损PNG帧，预声明短活动区间逐帧核对，列出可读/不可读/未覆盖；不把30分钟抽样冒称30分钟全帧像素覆盖。
- 正式快照在同一H264 access unit的SEI内；WS帧快照不驱动显示。WS延迟应如实记为控制/遥测延迟，不能宣称独立延迟了帧内SEI。包级网络故障与消息级注入分别标记。
- 30分钟按真实单调墙钟计，不以日志时间/倍速替代；快速smoke不得给出正式soak通过。
- CPU同时列单核百分比和整机百分比，包含worker/gateway/API/browser；GPU利用率注明整卡，显存只将拥有PID计入。原CPU<5%不偷换统计口径，按实际结果重新评估。
- 验证器失败/取消也写失败证据并收尾，自有进程/端口未释放不得报pass。

## Review Focus

1. 乱序/重复/迟到帧跨seek、日志和会话代次不得覆盖新状态；故障关闭必须释放VideoFrame。
2. 监测漏掉短命进程、PID复用、将其他进程GPU内存计入或CPU分母错误会伪造资源结论。
3. 页面刷新和连续创建/取消中有晚到异步结果，不得留下worker/gateway或旧媒体。
4. 短smoke、暂停保持、负载窗口不能冒充30分钟健康性能；逐帧录屏有无法读取时必须单列覆盖率。
5. 验证过程被中断、工具报错、没有源hash或清理结果时不能生成成功报告。

## Task 1：资源监测（并行）

Files: `scripts/validation_resources.py`, `tests/core/test_validation_resources.py`。
Interface: `ResourceSampler(roots, roles=None).sample() -> dict`；`summarize_resources(samples, stable_after_s=60, window_s=60) -> dict`。

- [x] RED/GREEN固定CPU ticks/单核与整机分母、PID身份/消失、无GPU/超时、稳定窗口汇总。
- [x] /proc只读采样、拥有子进程及角色、RSS/FD/线程、bounded nvidia-smi；只报告有依据的增长趋势。

## Task 2：正式浏览器矩阵与soak（并行）

Files: `frontend/e2e/resilience-real.spec.ts`及focused helpers，`playwright.resilience.config.ts`。
Inputs: `T17_MODE=matrix|soak`, `T17_SOAK_SECONDS=1800`, `REAL_*`, `B041_OFFLINE_BUNDLE`, `B041_PROFILE`, `T17_OWNERSHIP_DIR`, `T17_API_PID`。
Outputs: `browser-evidence.json`, `observations.jsonl`, `progress.json`；有界缓冲，不保存30分钟全部快照到RAM。

- [x] 控制.1/1/4/10x、播放/暂停/单步/seek/结束/停止，真实数据独立数值与像素核对。
- [x] 原生Worker消息延迟/重排/丢弃/重复、WS延迟/断开；worker SIGKILL、owned gateway中断、创建中取消、刷新、至少多轮新sid重载0。
- [x] 连续1800秒回放/重复seek、健康窗口fps/捕获延迟/缓冲占用与源值检查；smoke标签独立。
- [x] 仅有生产缺陷才修改产品代码，并先复现RED、修复GREEN。

## Task 3：逐帧录屏核验（并行）

Files: `scripts/validate_recording.py`, `tests/core/test_recording_validation.py`, `frontend/e2e/resilience-video.ts`。

- [x] 独立模板/CRC算法覆盖正确画面及故意错误面板，确定损失编码容差；不可读不记作匹配。
- [x] 对实际短矩阵无损录屏的全部已保存帧逐一检查图像身份、对应文字ROI，输出覆盖/错配/未知及逐帧JSONL。

## Task 4：启动器与最终运行（主代理）

Files: `scripts/validate_resilience.py`, `tests/core/test_resilience_runner.py`, docs/validation/t17-*。

- [x] 完成参数/正式soak时长/资源及清理验收RED/GREEN，复用ManagedProcess、T16输入hash；失败也写结果。
- [x] 创建独立输出并采样每5秒resource JSONL；短smoke后，顺序运行完整30分钟与matrix，不改变全局进程/网络。
- [x] 收集worker故障<3秒、清理10秒开始强制收尾证据；检查新sid0起点和全部拥有进程/端口释放。
- [x] 核验录屏、CPU/GPU/显存、内存与缓存增长，记录已测条件和未覆盖包级网络条件。
- [x] 适当回归、一次独立审查和必要修复；更新原todo/README/进度，保存源码提交与完整验收报告。

## 执行结论（2026-10-27）

本地矩阵、1800.057秒同会话28次seek、全量worker追踪和无损屏幕序列通过。完整证据见 [T17记录](../validation/t17-status.md)。CPU口径明确：主服务12.406%单核/0.517%整机，不将原单核5%标为通过。暂定实物语义、远程网络/服务器、未知SID的文档销毁恢复边界单列；下一阶段T18。
