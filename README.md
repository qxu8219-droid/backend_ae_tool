# 机器人日志回放平台后端

首期本地版本：**IsaacSim 6.2.0 / Python 3.12 / PhysX / R1Pro 2026**。提供正式 REST/WS、CSV/JSON/B041 导入、完整播放控制、真实仿真与网页同帧显示、限位曲线和标注回看。单机单会话；夹爪和部分底盘轨迹保留推算标识。

**首次安装和交付请从 [本地运行手册](docs/delivery-v1.md) 开始。** 它包括 SDK/资产/MediaMTX 准备、环境模板、端口、网络隔离、保留策略、合成演示和验收命令。排错见 [错误码与定位说明](docs/troubleshooting-v1.md)。T17 的 30 分钟性能记录与本次 T18 交付证据分别保存，不能互相代替。

## 已准备环境后的快速启动

在 backend 目录，仅在本地配置文件不存在时复制模板，编辑其中的 SDK Python 路径：

```bash
cp -n .env.example .env.local
# 编辑 ISAACSIM_PYTHON，保留 venv/bin/python 路径。
set -a
source .env.local
set +a
bash scripts/build_rtsp_loopback.sh
bash scripts/run_backend.sh
```

另开终端，在 frontend 目录：

```bash
VITE_USE_MOCK=false API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

浏览器打开 `http://127.0.0.1:5173`：创建会话 → 等待就绪 → 上传日志和映射 → 检查诊断 → 激活 → 播放。用“关闭会话”释放 GPU，再退出前后台终端。停止播放不释放会话。worker 故障后重建新会话、重新上传、从零加载。

后端不自动加载 `.env`。正式入口是 `app.runtime:create_runtime_app --factory`；默认 `app.main:app` 只用于契约测试，业务返回503。当前正式模式没有外部网关/远程监听环境开关，也不要启动多个 uvicorn worker。

## 数据与能力

| 入口 | 当前支持 |
| --- | --- |
| CSV/JSON | 约定格式、显式字段映射、单位/时间/角度转换、独立通道、质量诊断 |
| B041 | 两份离线Parquet及live/Rim记录经本地准备工具形成 `.b041`；上传时选择保存的mapping |
| 真实原频率MCAP/相机录像 | 尚未直接接入；不能把原文件改后缀当作 `.b041` |
| 回放与诊断 | 播放/暂停/停止、0.1–10倍速、时间/帧定位、单步、28关节面板、底盘、保峰曲线、限位与阶段/历史事件 |

B041 目前是18个臂/躯干反馈通道加4个推算手指，6个底盘关节缺失。离线底盘为暂定速度积分，在线底盘位姿不可用。映射可版本化调整；原始文件保存在 Git 忽略的 `data/real_logs/raw/`，不改写原件。详见 [B041使用说明](docs/b041-replay-v1.md)和[本地数据保存](docs/local-data-storage-v1.md)。

会话上传副本在正常关闭时删除；源资料、派生包和state诊断需自行保留与备份。没有跨会话日志库、诊断TTL或自动断点恢复。

## 开发检查

以下使用已安装环境；CPU检查结束后再运行真实GPU验收：

```bash
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -o addopts= -q
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_contracts.py --check
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_joint_metadata.py --check
npm run check --prefix tools/contracts
```

前端在其目录运行 `npm test`、`VITE_USE_MOCK=false npm run build`、`npm run test:e2e`。契约变更后用 `scripts/export_contracts.py` 和 `tools/contracts` 生成双方类型，不编辑生成文件。CPU旧探针需要系统已有NumPy，不应为了运行它们把NumPy装入HTTP环境。

不依赖私有日志的真实UI创建/上传/回放/故障/重建检查：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_frontend.py \
  --isaac-python "$ISAACSIM_PYTHON" \
  --output-dir outputs/t18/my-new-synthetic
```

输出目录必须新建；脚本只管理自己的进程。真实B041故障矩阵与持续验证见 [韧性工具](docs/resilience-validation-v1.md)。

## 文档与证据

- [T18交付记录](docs/validation/t18-status.md)：本次版本、验证结果、来源和后续边界。
- [T17验收](docs/validation/t17-status.md)：本地30分钟、消息/进程故障、录屏与资源口径；单核CPU低于5%的目标未达到。
- [T16真机适配](docs/validation/t16-status.md)、[T15正式媒体](../frontend/docs/validation/t15-status.md)：来源数值、质量与同帧证据。
- [REST/WS运行](docs/runtime-api-v1.md)、[契约](docs/contracts-v1.md)、[日志映射与扩展](docs/log-import-v1.md)、[采样](docs/replay-sampling-v1.md)、[分析](docs/analysis-v1.md)。
- [P0媒体选型](docs/validation/p0-video.md)、[同步原型](docs/validation/p0-sync.md)、[6.2基线](docs/validation/p0-isaac62-status.md)：保留历史证明范围。5.1/旧WebRTC和spike入口不作为当前操作入口。

本地性能与网络隔离已验证的范围不自动扩展到远端服务器。精确实物标定、完整MCAP、远程部署/认证、多用户、动力学评测和相机视角增强属于后续工作。
