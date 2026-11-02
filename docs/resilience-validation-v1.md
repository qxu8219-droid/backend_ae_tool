# T17 本地韧性与持续回放验证

本工具使用正式 API/WS、IsaacSim 6.2、WHEP/WebCodecs 和 B041 日志。它只启动、干预和清理本次拥有的本地进程，不连接机器人或远端服务器，也不更改防火墙或操作系统网络策略。原始日志、catalog 与输入映射在运行前后校验 SHA256。

## 运行

在 backend 目录中，使用新的输出目录。SDK Python 必须指向实际 6.2 venv 的入口；不要把符号链接解析成基础 Python。

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_resilience.py \
  --mode soak --seconds 1800 \
  --offline-bundle data/real_logs/derived/RBA60725B041_20261020114048516_RAW/b041_ee2_body_velocity_v1/recording.b041 \
  --profile configs/mappings/b041_provisional_v1.json \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python \
  --output-dir outputs/t17/my-new-soak
```

`--check` 只读取输入与 hash，不启动 SDK；`--seconds 10`～`1799` 是短探针，成功状态仍为 `smoke_only`，不能作为 30 分钟验收。`--mode matrix` 执行控制/故障、专门的短录屏测试及离线逐帧分析；活动区间分析失败也会使整个 matrix 失败。两种模式顺序执行；请勿与其他 SDK 进程测试、构建或全量测试并发测性能。

若只需重跑录屏及分析，使用 `--mode recording`，结果标为 recording，不冒充完整故障矩阵。当前正式录屏由 Chrome CDP 直接保存无损 viewport PNG 帧、原始时间戳与 SHA256，不需要 FFmpeg。实际输入不是从应用 canvas 导出的图像，也不是从遥测生成的模拟画面。

运行中可查看输出目录的 `progress.json`、`resource-latest.json`、`browser.log`。完成或失败后保存 `validation-evidence.json`；每 5 秒的资源样本保存为 `resources.jsonl`，实际配对/健康窗口/控制事件追加到 `observations.jsonl`。失败不会被改写为通过，重试使用新目录。

## 同步、故障与录屏口径

独立浏览器 oracle 检查实际 canvas 的图像 ID/CRC、数值文字、进度和源数据，不从“最新遥测”推断当前画面。正式快照位于同一 H264 access unit 的 SEI 内，WS 的 frame_snapshot 不驱动呈现。因此 WS 延迟是控制/遥测延迟，不能描述成独立延迟了帧内 SEI 数据。

矩阵包括 0.1x/1x/4x/10x、播放/暂停/单步/连续定位/停止/末尾重播，原生 decoded Worker 消息延迟、丢弃、重复和重排，WS 消息异常和重连，以及拥有的 worker/gateway 中断、创建取消、页面刷新与多轮新会话。消息注入不等同于 IP/RTP 丢包；没有未经授权的系统网络修改。

独立录屏测试另存 `recording/browser-recording-evidence.json`，其中给出 `screen-recording.json`、实际屏幕 PNG、独立文字模板及 active-window 路径。运行：

```bash
/usr/bin/python3 scripts/validate_recording.py /absolute/path/to/recording/screen-recording.json \
  /absolute/path/to/recording/recording-observations.jsonl \
  --window /absolute/path/to/recording/active-window.json \
  --output /absolute/path/to/recording/recording-report.json \
  --frames /absolute/path/to/recording/recording-frames.jsonl
```

该离线工具使用系统已有的 OpenCV/NumPy，HTTP 和 SDK 环境无需安装。每个收到并保存的屏幕帧均验证文件 hash、尺寸和时间戳，ID/CRC 与面板文字由独立模板核对。预先声明的起止身份范围确定至少 8 秒的活动区间；区间内不能跳过不可读帧或错误面板来获得通过。录制边界外的区间仍单列；`active_window.passed` 不等于整个记录所有帧都完成配对，只有 `strict_full_frame_passed=true` 才表示本次全部已录屏幕帧通过。缺失边界、区间过短、错误文字、文件缺失/篡改或区间内不可读都会失败。无损录屏保留每个已接收帧，不宣称捕获了浏览器所有绘制事件；检查覆盖身份和声明文字区域，不是每个机器人像素的视觉模型核验。

## 性能与资源口径

- 长测按单调墙钟持续至少 1800 秒，每分钟执行暂停、seek、恢复；不能用日志时间、倍速或多个短测试相加替代。
- 逐次实际呈现统计捕获延迟、同步缓冲和解码占用；完整健康窗口至少 15fps、每次呈现共同延迟小于 2 秒。明确记录控制/稳定等待区间，末尾不足 2 秒的窗口只排除帧率判断，仍检查延迟与缓存。
- 浏览器检查点每 5 秒独立读像素和源值；呈现次数与延迟由每次真实呈现回调计数，两个采样口径不混用。
- 资源采样使用 /proc 的 PID+启动时间跟踪本次子进程，包括 worker、API、浏览器和媒体网关。CPU 同时报告单核百分比与整机百分比；100% 单核表示占满一个逻辑核。原计划“主服务 CPU<5%”只针对 API，不应将全部组件总和或不同分母替代它。
- 自动化 Node 进程单列 `browser_driver`，不当作浏览器产品占用；纯源值校验使用普通断言，健康长测直接连接原生 WebSocket，避免每条遥测在测试框架中产生保留到测试结束的调用记录。故障矩阵仍通过明确的消息代理注入异常。
- nvidia-smi 显示整卡利用率作为环境信息；只有本次拥有 PID 的显存计入平台占用。显存归属不可读取时显示未知，不当作 0。
- RSS 汇总可能重复计算共享页；首尾稳定窗口和斜率是观测量，不自动证明不存在泄漏。持续回放的资源摘要依据实际 soak 起止时刻，排除会话创建和退出后的内存释放，避免掩盖增长。
- 自有进程先 TERM，最多 10 秒开始 KILL 收尾。报告包含进程身份、生命周期和端口释放情况；清理或 hash 检查失败会使整体运行失败。

单相机 worker 明确关闭 SDK 的 `rtx.hydra.supportMultiTickRate`，避免用于多传感器异频采集的额外 flush 更新；分辨率、画质、zero-delay、渲染完成等待和原始像素身份屏障保留。新目标的已验证物理读回仅复用一次，暂停后的后续 capture 仍重新推进/还原/读回。

真实执行结果与未覆盖条件记录在本阶段验证报告中；工具本身存在不等于验收已经通过。

## 全量 worker 数值追踪

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_replay_trace.py \
  --bundle data/real_logs/derived/RBA60725B041_20261020114048516_RAW/b041_ee2_body_velocity_v1/recording.b041 \
  --profile configs/mappings/b041_provisional_v1.json \
  --trace-root outputs/t17/my-new-soak/service/state \
  --output outputs/t17/my-new-soak/runtime-trace-validation.json
```

写入进程停止后，该工具流式核对每条采集追踪的 28 个源值/读回、来源质量、SE(2) 位姿、进度与帧身份，不调用生产 adapter/sampler。它明确限制为当前连续、有效、来源时间唯一的 offline state 输入；不是浏览器像素证明。创建中取消且未加载日志的空追踪应单列原因，不能悄悄计为通过。

早期 WebM 探针经过 JPEG→VP8 两次有损编码，细小青色文字未通过固定38的比较阈值。其失败证据保留，正式路径改为无损屏幕采集；没有调松阈值。独立青色单数字0/8替换仍必须被检出。用于诊断的 Playwright FFmpeg1011 仅保存在 `outputs/t17/playwright-tools`，不属于当前默认录屏依赖，也未装入系统或 SDK。
