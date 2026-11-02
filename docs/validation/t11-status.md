# T11 验证记录

2026-10-26。固定本机 IsaacSim 6.2.0 / Python 3.12 / PhysX / 6.x USD，1280×720；数据为合成 28 DOF 与世界系底盘轨迹。本轮证明独立 worker 的真实执行、原始渲染和 H.264 编码后身份，不代表正式前端已接通，也未重测浏览器共同延迟。

## 最终结果

| 检查 | 结果 |
|---|---|
| 后端全量测试 | 689 通过，其中 worker 34 项 |
| P0 CPU 回归 | 90 通过 |
| Schema 导出一致性、TypeScript 契约 | 通过 |
| 前端单测、类型检查、生产构建 | 191 通过，构建通过 |
| SDK 环境独立导入 | 未导入 app，未启动 SDK |
| 真实 worker + ZMQ | 首帧、普通目标突发、暂停、seek、旧代次拒绝、多圈轮角与退出通过 |
| 暂停保持采集 | 19 个重采集样本，约 17.65fps |
| 独立 H.264 软件解码 | 170 帧全部匹配，169 个唯一采集 ID |
| 图像与正式快照 | CRC 错误 0，目录匹配错误 0，来源与读回值核验通过 |
| 正常退出 | 退出码 0，四个分配端口均无残留监听 |
| 默认拒绝原生 RTSP 非回环监听 | 真实启动失败码 1，未发布 WORKER_READY，端口清理通过 |

主证据目录为 `outputs/t11/binding-guard-isolated-gpu/`。可提交的数值、范围和文件 SHA256 见 [t11-evidence.json](t11-evidence.json)；大型运行输出不进入 Git。

独立解码记录覆盖 ready/playing/paused 与三个 playback_epoch，按实际发送的 `issued.json` 校验。169 帧的解码图像 ID 不等于编码轮询 ID，说明不能使用当前轮询目标替代编码图像身份。重复的同一采集图像仍精确选择同一快照。已人工检查真实机器人截图和两行像素标记。

## 检查命令

```bash
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -o addopts= -q
python3 -m unittest discover -s tests -v
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_contracts.py --check
npm run check --prefix tools/contracts
npm test --prefix ../frontend
npm run build --prefix ../frontend
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_worker.py \
  --output-dir outputs/t11/new-validation \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python \
  --decoder outputs/t11/rtsp_identity
```

检查日志在 `outputs/t11/checks/`。前端仍有既存的 500 kB chunk 提示，后端仍有 Starlette/httpx 弃用提示，均未导致失败。没有为这次生成协议类型更新重复宣称新的浏览器测试。

## 已修复问题与失败记录

独立代码审查后的回归覆盖：同控制上下文暂停仍执行排队目标、普通目标重复占用可靠关键帧序号、可靠回复发送失败后继续执行、单个清理失败跳过后续资源、RTSP start 返回 false 被忽略、验证器只验证可解码而不强制核验身份。还修复了诊断回调异常、关闭后继续接受请求与调试 JSON 导入缺失。

真实运行发现并修复四元数未归一化导致的误差误判、渲染计时重复等待、暂停时 Fabric 时间未推进，以及预热时丢弃编码器初始关键包。失败运行分别保留在 `first-gpu/`、`encoded-gpu/`。随后实际流验证通过。

原生监听检查还发现 SDK 快速退出将启动异常结束为 0。现先保存/输出诊断，再传递 `exit_code=1` 给 SDK，已用真实启动验证。失败与修复后记录在 `default-binding-rejection/` 和 `default-binding-rejection-fixed/`。

`binding-guard-final-gpu/` 在后端测试及前端构建同时执行期间测得 14.57fps，未通过门槛。代码不变、其他检查结束后，`binding-guard-isolated-gpu/` 测得 17.65fps 并通过完整解码核验。不能据此宣称负载竞争下稳定达标；持续运行、调度与资源压力由 T12/T17 继续验证。

## 验收边界

- 17.65fps 是短窗口的原始采集频率。独立解码接收吞吐约 21.08fps，含缓冲释放，不能冒充实际渲染或浏览器帧率。
- 解码器出现一次非单调 PTS，SDK 声明帧率为 60；实际配对使用像素 ID/CRC 和正式目录，不依赖 PTS 或声明帧率。首帧等待约 2.11s 是拉流启动耗时，不是健康播放共同延迟。
- 原生媒体实际绑定 `0.0.0.0`；本次合成验证显式允许。默认 worker 拒绝该绑定。真实日志接入前必须落实隔离部署，未修改防火墙、真机或服务器。
- 本地 12 个来源、27 个真机原文件已校验归档，未推断夹爪标定/底盘世界位姿，未用于此次媒体测试。
- 正式会话进程管理、HTTP/WS 业务绑定、WHEP/浏览器正式配对、真实日志转换和长期压力仍分别属于 T12～T18。
