# T12 验证记录

2026-10-26。固定本机 IsaacSim 6.2.0 / PhysX / 6.x USD，真实 MediaMTX v1.21.2，1280×720 合成轨迹。验证对象为会话资源与实际媒体源；正式 HTTP/WS 绑定及浏览器呈现仍在后续任务。

## 当前验证证据

- 后端全量 710 项通过，包含 21 项生命周期测试；Schema 导出一致性通过。最终审查和补充回归结果将在提交前更新。
- 真实运行 `outputs/t12/real-lifecycle-venv/`：三个不同 sid，两个完整 SDK 加载与一个创建中删除。
- 实际相位顺序为 booting、loading_scene、loading_robot、rtsp_publishing、ready；初始无日志时视频不冒充 publishing。
- 原生 RTSP 实际只监听 `127.0.0.1`，未允许通配绑定；网关 API/WHEP/ICE 均使用回环配置。
- 169 个真实 H.264 解码帧全部与正式 SEI 和发送目标匹配；168 个唯一采集 ID，CRC 与目录错误 0，覆盖 ready/playing/paused 和三个代次。
- 原始采集短窗口约 18.56fps；该值不是浏览器帧率或持续性能保证。
- 实际 kill -9 worker 后约 0.65 秒进入 error；删除后新 sid 的日志时刻为 0、capture_id 为 1。
- 实际结束并重建托管网关后恢复拉流，worker PID 保持不变。
- 应用关闭清理 ready 会话；创建期间删除和重复删除通过；本轮所有 6 个自有进程结束，分配端口与媒体路径清理通过。

CPU 测试也使用真实 ZMQ、真实子进程及真实本地 MediaMTX，只在会话控制测试中以明确标记的 CPU runtime 替代 GPU。覆盖并发 RPC、超时/身份错误、进程组残留子进程、拒绝 TERM 的进程、父进程被结束、单会话冲突、慢启动、启动异常、心跳失联、外部网关所有权和跨服务重启的路径清理。

## 可重复检查

```bash
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -o addopts= -q
bash scripts/build_rtsp_loopback.sh
bash scripts/worker_validation/build_decoder.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_sessions.py \
  --output-dir outputs/t12/new-validation \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

独立解码器强制核对图像 ID/CRC、正式 UUID、唯一候选快照以及 `issued.json` 保存的实际发送目标；只看视频可解码不能通过。真实 GPU 性能验证应在构建/CPU 重负载检查结束后执行。

## 保留的失败与边界

`phase-red/` 记录原 worker 缺少中间启动相位的失败断言，已在真实 SDK 加载处补充相位。`real-lifecycle/` 记录验证脚本展开 venv Python 符号链接后误用基础解释器的启动失败；改为保留虚拟环境入口后重跑通过，未为此安装或替换 SDK 依赖。

一次端口测试遇到正常 TCP TIME_WAIT，接管测试 socket 补齐与实际服务器一致的 SO_REUSEADDR；一次 PID 观察遇到退出期间 `/proc` 的 ESRCH，已按进程结束处理。故障记录与后续通过日志均保留。

当前 3 秒目标验证到管理器状态/事件，尚未测到浏览器错误徽章。外部网关模式已用真实本机服务验证路径所有权，不涉及用户提供的远端真机/数据服务器。长期负载、完整网络故障矩阵、私有日志转换、正式浏览器延迟属于 T14～T18；本轮没有修改远端数据或执行真实机器人动作。
