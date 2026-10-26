# T13：限位、原样本事件与保峰曲线

本模块提供独立 CPU 分析服务，消费 T08 的规范化 `ReplayData`，不修改原件、不改变播放状态。T14 再绑定 HTTP/WS，T15 接入正式媒体界面。所有说明中的事件均指日志观测，不代表机器人安全控制或实际硬件制动。

## 限位来源与同一判定

`scripts/export_joint_metadata.py --check` 从配置指定的 common 文件重建并核对 `app/assets/robots/r1pro_2026_joints.json`，同时核对文件 SHA256。28 关节按 canonical 顺序；保留 vendor URDF 或 simulation assumption 等来源。连续轮关节没有位置上下限，仿真速度/力矩参数不会变成位置限位。

`replay_protocol.limits` 是 worker 和分析共用的纯 Python 判定，不依赖 app/SDK。预警阈值为 `lower + 0.05 × (upper − lower)` 和 `upper − 0.05 × (upper − lower)`，实现用端点加权避免极端有限数相减溢出。边界属于预警带；只有超过硬边界且超过数值容差才算越界。默认容差为旋转 1e-6 rad、直线 1e-8 m；零跨度限位只区分正常/越界。

| 数据质量 | 数值展示 | 位置限位判定 |
|---|---|---|
| 有效 measured / commanded | 保留实测或指令标签 | 判定；插值/单位换算保留处理标签 |
| 无效或 missing | 不当有效观测 | 不设告警位 |
| derived 或 derived/defaulted/forward_filled 处理 | 保留值与推算/填充来源 | 未判定，不当作实测正常或超限 |
| continuous | 保留原值/累计圈数 | 无位置限位 |

worker 的每帧位图来自同次读回姿态与该帧质量。前端继续消费帧快照的位图，只对不具备判定资格的数据显示“未判”，不会自己计算另一套数值限位。

## 原样本事件

`HistoryIndex` 一次性预扫每个关节自己的原始时序，保留单个样本的短峰。相邻的同严重程度、方向、来源和方法合并为事件；不按墙钟节流，不用播放 20Hz/60Hz 采样替代原数据扫描。

事件保留进入观测时刻、最后越界/预警观测时刻、退出观测时刻、结束原因、峰值及其原采样索引/质量，以及原文件、映射和限位来源。正常值或分类切换提供退出观测时刻；缺测、超出显式 `max_gap_s` 或日志结束时，退出时刻为 null，不伪造已恢复正常。

区间是**采样观测区间**，没有计算采样之间的精确阈值穿越时刻。已观测退出采用右开边界；未知退出仅覆盖到最后可判定观测点。曲线的边界插值与历史事件的观测起止可能不同，不能把事件区间当作连续轨迹的精确越界时长。

历史 ID 根据源文件/映射/限位/判定策略和原样本段生成，与 session 或 playback_epoch 无关。索引与输入快照隔离，重复查询、seek、重播不修改历史集合。默认索引容量 200000 事件，单次查询默认最多 10000；超过限制明确报错，不静默截断。

`RenderedEventCursor` 只消费 `RenderedFrame`，输出候选 `AlertData`。同代次前进时查询跨过的全部事件起点，10x 跳过的短事件仍会进入候选；重复/旧采集和旧代次被忽略。新代次在跳转时刻重新定位，不补发跳转跨过的旧事件。候选不携带未来峰值或退出信息。**候选不是浏览器已显示证明**：前端现有 history store 仍等同 session/log/epoch 的配对画面进度到达后才释放即时告警。

## 曲线查询

```python
from app.analysis.service import ReplayAnalysis
from app.analysis.cursor import RenderedEventCursor

analysis = ReplayAnalysis(replay_data, max_gap_s=None)
curve = analysis.curve(['torso_joint1', 'left_arm_joint1'],
                       from_s=0, to_s=None, max_points=2000)
events = analysis.history.overlapping(0, analysis.history.duration_s)
cursor = RenderedEventCursor(analysis.history, session_id=session_id)
alert_candidates = cursor.advance(rendered_frame)
```

查询时间使用相对日志起点的秒；要求 `0 ≤ from_s ≤ to_s ≤ duration`，不向区间外外推。支持 1～128 个已登记通道，单通道预算为 2～10000 点；返回既有 `CurveResponse`，包含时间、单位、质量与相关观测限位区间。

保留区间内原样本，并在需要时按 T09 规则加入查询边界的插值点；包角通道使用与 T09 相同的连续展开及推算来源。没有来源覆盖或数据无效时返回 null。`max_gap_s` 必须与回放采样配置一致；默认不猜测频率或缺口阈值。显式长缺口加入 null 断点，若浮点精度不能表示内部断点则报错，要求分开查询两侧。

降采样按有效段分桶取 min/max，并保留无效段断点。每个有效段至少保留 1～2 个极值点，每个无效段至少一个 null；预算不足返回 `CURVE_BUDGET_TOO_SMALL`，details 包含 required_points。奇数预算可能有空余，不会为凑数制造样本。分桶 min/max 保证桶内极值，不保证低预算保留所有局部波动；完整原样本事件索引另外保留全部可判定超限段。

分析直接查询已登记的源通道，原文件/规范化数据保持不变。准备阶段与查询在大型日志上属于 CPU 工作，T14 调用方应放入 CPU 执行上下文并沿用导入数量/内存限制；本模块不会在导入时启动线程或任务。

## 验证

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/export_joint_metadata.py --check
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/analysis -o addopts= -q
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_analysis.py \
  --output-dir outputs/t13/my-new-probe \
  --isaac-python /home/xq/work/code/projects/r1pro/.venvs/isaacsim62/bin/python
```

省略 `--isaac-python` 只执行 CSV 导入/归档与 CPU 分析。GPU 验证使用明示的合成质量分类 fixture，独立解码 H.264，核对实际像素 ID/CRC、SEI 和四类限位位图；不把合成 measured 分类当真机反馈。详细结果见 [T13 验证记录](validation/t13-status.md)。
