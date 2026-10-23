# 机器人日志回放平台 P0

首期使用 **Isaac Sim 5.1.0 / Python 3.11 / PhysX** 和对应 5.1 USD。当前代码是 P0 探针，尚未进入正式 FastAPI/Vue 开发。实施状态与版本影响见 [P0 验证记录](docs/validation/p0-isaac51-status.md)。

机器人资产已完整复制到：

```text
assets/robots/r1pro_2026/isaacsim_5_1/
```

主入口是 `r1pro_2026.usd`，关节配置在包内 `reproduction/configs/r1pro_2026_common.json`。整个目录必须一起部署，不能只复制入口 USD。资产不进入源码 Git；来源和校验方式见 `configs/robot-asset.json`。未修改相邻的 `/home/xq/work/code/projects/backend` 项目。

## 本地验证

在本目录执行。CPU 测试使用标准库 unittest，其中页面脚本测试还需要 Node。HTTP/JPEG 验证脚本需要 Pillow；Isaac 自带 Python 已提供其运行依赖。

```bash
python3 -m unittest discover -s tests -v
python3 scripts/spike/asset_audit.py \
  --asset-dir assets/robots/r1pro_2026/isaacsim_5_1 \
  --output outputs/p0/asset-audit.json

export ISAACSIM_ROOT=/home/xq/isaacsim
bash scripts/run_p0.sh --mode baseline --output-dir outputs/p0/my-baseline
```

`baseline` 检查版本、USD 依赖闭包、28 DOF 名称、五组姿态、底盘朝向、超限直设并保存图像。每次仿真必须使用新的或空的输出目录；程序拒绝覆盖旧帧、旧控制和旧结果。

## 成对图像与数据实验

这是 HTTP/JPEG 实验，**不是正式 NVENC/WebRTC 推流方案**。目前只有低分辨率、低采样 PathTracing 组合通过连续像素标记核验；图像噪声明显，正式画质与浏览器呈现尚未验收。

终端一，启动采集；输出目录使用新的名称：

```bash
export ISAACSIM_ROOT=/home/xq/isaacsim
bash scripts/run_p0.sh --mode paired --renderer PathTracing \
  --width 640 --height 480 --samples 1 --rt-subframes 1 \
  --seconds 300 --output-dir outputs/p0/my-live
```

看到 `P0_READY` 并生成 `latest.frame` 后，在终端二启动页面服务：

```bash
python3 scripts/spike/paired_preview.py --output-dir outputs/p0/my-live --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。页面在一张 canvas 中共同呈现图片、28 关节、底盘和时间游标；对 JPEG 中实际渲染的 12 位红绿方块独立解码。缺帧、错配和解码尚未完成时不提交部分画面。夹爪单位为 m，其他关节为 rad。

可加 `?image_delay_ms=700` 或 `?metadata_delay_ms=700` 测试单侧迟到。`window.p0Stats` 提供呈现帧数、旧帧丢弃、像素核验和错配计数。它们用于后续实际浏览器验收，不能仅凭单元测试宣称浏览器已通过。

在采集仍运行时，可执行独立 HTTP 检查：

```bash
python3 scripts/spike/http_validate.py \
  --report outputs/p0/my-live/http-validation.json
```

它验证 JPEG 像素帧标记及 19 次真实 worker 控制回执；它不验证浏览器解码、合成、屏幕刷新或实际显示延迟。脚本中的控制邮箱只保留最新命令，属于 P0 实验限制；正式控制屏障与队列在 P2 实现。

本探针将车轮角在仿真边界换算为 `[-π,π)` 的等价旋转，`joints` 是运行时读回值，`source_joints` 保留合成日志累计角。正式面板对原始角与运行时等价角的展示契约仍需在 P1 固化。

## 官方旧版 WebRTC 候选

在终端一执行以下命令，保持实例运行：

```bash
export ISAACSIM_ROOT=/home/xq/isaacsim
bash scripts/run_p0.sh --mode native --seconds 300 \
  --width 1280 --height 720 --output-dir outputs/p0/my-native
```

终端二：

```bash
cd scripts/spike/native_webrtc
npm ci --no-audit --no-fund
npm run build
npm run preview
```

打开 `http://127.0.0.1:8766/`，手动连接 localhost:49100。具体 SDK 版本、接口和验证边界见 [native_webrtc/README.md](scripts/spike/native_webrtc/README.md)。当前已验证扩展启动和 TCP 49100 监听；未验证浏览器收到媒体，也未解决这条原生链路的业务帧身份关联。

## 输出与退出

- `results.json`：所选探针范围、运行参数、姿态检查、版本、依赖和性能。
- `trace.jsonl`：成对实验中的帧快照；native 模式只有服务端渲染记录。
- `first-frame.png` / `baseline.png`：采集图像，不能代替浏览器呈现证据。
- `marker-failure.png`：独立帧标记检查失败时的图像。
- `latest.frame` 与 `control.json`：仅用于当前运行。

仿真到达 `--seconds` 后自动退出；也可 Ctrl+C。页面服务单独 Ctrl+C。仿真结束后页面可能仍显示最后一帧，此时不会再处理控制。首期故障后用新输出目录重建并从头加载，不恢复断点。
