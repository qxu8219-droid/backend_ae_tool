# T09 回放采样与视觉轮角估计

T09 将已归一化的 `ReplayData` 转成共享协议 `TargetFrame`。它提供无状态的时间查询、原始采样点步进和显式启用的视觉轮角估计。HTTP 回放接口、会话调度、时间轴、渲染 worker 和媒体输出属于 T10 及后续工作，本文不表示这些功能已经实现。

## 使用方式与身份

每次加载一份日志时准备一个 `ReplaySampler`，在该日志的后续查询中复用它：

```python
from app.replay.interpolator import ReplaySampler

# data 是已读取并验证的 ReplayData；运行时必须提供实际会话标识。
sampler = ReplaySampler(data, session_id="session-example", max_gap_s=0.25)
target = sampler.sample_at(
    1.5,
    playback_epoch=2,
    control_seq=10,
    target_seq=42,
)
next_time = sampler.step_time(target.log_time_s, "forward")
previous_time = sampler.step_time(target.log_time_s, "backward")
original_time = sampler.frame_time(3)
```

准备阶段验证并隔离源数据、通道和映射快照；之后修改调用方的 `ReplayData` 不会改变已准备的采样结果。查询不依赖上一次请求，反向播放、跳转和乱序重复查询使用同一套规则。输出按共享协议的规范关节名称排序，不能用输入字典的顺序解释关节数组。

`sample_at(data, time_s, ...)` 是一次性离线辅助函数，每次调用都会重新准备采样器，默认 `session_id="offline-sampling"`。实际播放应复用采样器并传入真实会话身份。`log_id` 保留源日志身份；`playback_epoch`、`control_seq`、`target_seq` 由调用方提供并接受共享协议校验，采样器不分配这些序号。

## 时间、步进与通道覆盖

公开播放时钟从零开始：原始通道和 `sample_times_s` 均减去 `time_basis.relative_origin_s`，因此日志具有非零时间原点时也不会重复偏移。`duration_s` 是最后一个原始联合时间戳与首个时间戳之差；不增加重采样网格或虚构时间点。`PreparedChannel` 本身保留输入时钟，只有上层准备过程负责转换到播放时钟。

- `sample_at` 拒绝布尔值、字符串、NaN 和无穷值；有限的越界时间钳制到 `[0, duration_s]`。
- `source_sample_index` 仅在最终查询时间恰好等于原始联合时间戳时存在；插值时为 `None`。它不是渲染帧号或媒体捕获序号。
- `frame_time(index)` 将整数索引钳制到原始联合时间戳范围。
- `step_time(time, "forward" / "backward")` 在恰好命中采样点时选择严格的下一点或上一点；点间查询选择该方向的包围边界，首尾停在边界。

每个通道按自己的时间戳插值。通道覆盖外没有外推；区间插值必须有两个有效、有限端点，且不能跨越无效样本。可选的正数 `max_gap_s` 限制通道相邻源样本之间可插值的跨度；有效的精确端点仍可单独查询。`interval_available(start, end)` 检查区间跨过的每一条源样本边，因此即使两端恰好是有效样本，过长间隔或内部无效样本也不会被误判为可积分。

底层通道可以有不同采样频率，但当前导入器对原始底盘 `x/y/yaw` 三轴仍要求对齐的时间戳网格，以共同执行坐标变换。T09 不新增原始异步底盘三轴的自动对齐导入能力。

## 角度语义与无效数据

角度含义由映射声明，不从数值大小推断：

| 语义 | 适用通道与行为 |
| --- | --- |
| `bounded` | 有限转动关节，直接线性插值，不选择最短角路径，也不在采样阶段钳制关节限位。 |
| `linear` | 夹爪平移及底盘位置，直接线性插值。 |
| `angular_unwrapped` | 连续轮角或显式声明的底盘 yaw，保留多圈数值并线性插值。 |
| `angular_wrapped` | 连续轮角或底盘 yaw，要求显式 `2π` 周期；在每段连续有效源数据内预先按最短角路径展开，再插值。恰好半周期时沿原始差值方向选择分支。 |

映射中的连续轮关节默认 `angular_unwrapped`，底盘 `yaw_semantics` 默认 `angular_wrapped`。逐关节配置位于 `joints.per_joint_overrides.<规范关节名>.angle_semantics`；有限转动关节只接受 `bounded`，平移关节只接受 `linear`。全局 `options.unwrap_continuous` 必须为 `false`，`options.resample` 必须为 `"none"`。如果提供方知道实际圈数，应声明累计多圈角（angular_unwrapped）；最短路径展开不能恢复相邻样本之间未知的整圈转动。

关节目标必须是有限数。当前采样无效时，采样器查找查询时间及之前最后一个有效源样本，并输出该值及 `valid=false`、`forward_filled`、`invalid_hold_previous.v1`。没有历史有效值时输出零及 `origin="missing"`、`valid=false`、`defaulted`。此回退来自源数据，不来自上一次查询；它只是确定性的显示目标，不能当作有效测量。

底盘只有在 `x/y/yaw` 同时有效时才输出 `ChassisPose`，否则为 `None`。底盘源数据已在导入阶段变换到世界坐标，采样或下游消费时不得再应用同一变换。当前协议只含平面 `x/y/yaw`；未来 worker 必须保留自己的初始 `z/roll/pitch`。对于 `None`，后续 worker 的约定是使用初始 `x/y/yaw` 并标明不可用，不能使用依赖播放历史的上一帧位姿。这是后续集成约定，T09 尚未实现 worker 行为。

## 显式启用视觉轮角估计

`virtual_channels.wheel_roll_from_chassis` 默认关闭。启用时必须同时提供正的有限 `wheel_radius_m` 和三个轮关节各自的整数方向 `-1` 或 `1`；不能省略方向、用布尔值代替整数，或推断真实硬件参数。以下仅是合成演示配置，半径和符号不代表真实机器人：

```json
{
  "virtual_channels": {
    "wheel_roll_from_chassis": {
      "enabled": true,
      "wheel_radius_m": 0.10,
      "joint_signs": {
        "wheel_motor_joint1": 1,
        "wheel_motor_joint2": -1,
        "wheel_motor_joint3": 1
      },
      "initial_angle_rad": 0.0,
      "max_lateral_ratio": 0.05,
      "max_segment_s": 0.25
    }
  }
}
```

这是映射片段，应合入现有映射并配置有效底盘来源。`initial_angle_rad` 是日志起点的视觉参考角，不是观测到的绝对轮角。`max_lateral_ratio` 范围为 `[0, 1]`；可选的正数 `max_segment_s` 限制联合时钟上每次积分的时间跨度，和源通道的 `max_gap_s` 分别检查。

只有完全没有有效样本的轮通道才会被替换。只要现有通道包含一个有效样本，无论它来自测量、命令或已有派生结果，都保留整个通道，不用估计值填补其空洞。

模型 `signed_body_forward_distance.v1` 从日志起点累计：将相邻世界位置的位移投影到两端 yaw 的中间朝向，得到带符号的车体前向距离 `ds`，再计算 `initial_angle_rad + joint_sign × 累计距离 / wheel_radius_m`。因此直线倒车可产生负增量，静止可保持角度。输出单位为弧度，语义为 `angular_unwrapped`，来源标记为 `derived`。

该模型是车体中心行程的视觉近似，不包含每个轮子的转向角、安装偏置或独立运动学；弧线运动也受此限制。纯侧移、超过侧向比例阈值的运动，以及 yaw 变化超过 `1e-9` 弧度而平移量不超过 `1e-9` 米的原地旋转不可估计。缺失底盘、无效样本、跨越不可用源区间、超长积分段或数值溢出，也会使累计角未知。

累计轨迹一旦不可用，就不再从后续有效点重新积分或虚构圈数：失败点及之后的派生通道值为 `None`、`valid=false`；从日志起点就没有有效底盘时，整个估计不可用。这里不仅检查积分两端，还使用 `interval_available` 检查跨过的源区间，避免有效端点掩盖中间缺口。最终 `TargetFrame` 仍按上述关节回退规则给出有限显示值，其质量保持无效。

## 来源与归档边界

精确有效样本保留质量信息；发生包角分支变换时改标 `derived` 和 `wrapped_shortest_path.v1`。插值合并端点处理记录、去重来源索引并标记 `interpolated`；混合来源或方法标记 `derived`、`interpolation.composite.v1`。这些操作不会将原本无效的数据变成有效测量。

包角展开依赖连续有效段的前缀，`source_indices` 只标识当前源端点，不展开记录全部历史。视觉轮角的处理记录累计保留先前轨迹的处理步骤；其 `source_indices` 指向 `data.sample_times_s` 中当前积分区间的端点，但数值依赖从起点开始的全部有效轨迹。`sampler.metadata` 说明这些前缀依赖，并给出轮角模型、参数、保留通道、派生通道、状态及首次不可用原因，不能把端点索引误解为全部贡献来源。

T09 只在加载后的准备阶段生成采样和派生结果，不改写原始归档、原始通道值、已存映射快照或其哈希。缺少新增配置字段的旧映射按默认值读取；兼容读取不等于迁移或重写旧归档。
