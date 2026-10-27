# B041 portable recording v1（T16 实施接口）

用户于2026-10-27允许先用明确的临时假设进行夹爪/底盘适配，精确资料到达后修订。原始文件保留只读、SHA256追踪；此包是可上传的选定数值/标注副本，不冒称原Parquet/JSONL原件。

文件后缀 `.b041`，UTF-8 JSON，parser=`b041_bundle_v1`。生产HTTP解析只用现有Python依赖；Parquet读取放本地准备工具，可使用既有检查工具环境中的pyarrow19.0.1，不放SDK/HTTP解析进程。只选明确B041源，不混其他硬件或不同次采集。

```json
{
  "schema": "b041.recording.v1",
  "source": {
    "id": "recording-id", "format": "full23_parquet", "time_unit": "ns",
    "clock": "source_log_time_ns", "sha256": "64-hex", "name": "episode.parquet",
    "episode_index": 8, "base_locked": false,
    "files": [{"name":"episode.parquet","sha256":"64-hex","role":"numeric_source"}]
  },
  "samples": [{
    "time": "1790000000000000000", "row": 1, "frame_index": 0, "event": "frame",
    "state": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,80,80,0,0,0,0,0,0,0],
    "action": null,
    "state_quality": [{"valid":true,"source_time":"1790000000000000000","age_s":0,"timing":"source_log_time_ns"}],
    "action_quality": null,
    "context": {}
  }],
  "annotations": {"alignment":"none","source_id":null,"sha256":null,"segments":[],"reference":{}},
  "events": [], "diagnostics": {}
}
```

上述quality数组仅简写示意，真实每个有向量的state/action均须**6组**：left_arm、right_arm、left_gripper、right_gripper、base、torso，分别对应full23切片0:7、7:14、14:15、15:16、16:19、19:23。所有向量须23个finite number或null；不可序列化NaN/Infinity。原无效数值仍可保留finite值，但valid不得改true。source_time为字符串或null，age_s为秒或null；保留源valid、age、来源时间，禁止将已有15Hz因果保持冒充新传感器采集。

source.format=`full23_parquet|runtime_events`；time_unit=`ns|s`。绝对ns始终字符串，先作精确差再转相对秒。runtime的time使用该行monotonic（字符串），clock=`run_monotonic_event_time`；不是capture_monotonic/model_time_s。row为1基原JSONL行号/Parquet行号；frame_index为原Parquet整数或null。context保留chunk/step/reference_step/kind/clamped等已有字段。

runtime samples只取实际存在的`feedback23`为state，记录的`target23`为action（包含准备姿态目标）；没有的一侧为null。质量timing=`event_monotonic_only`、source_time/age_s=null，不虚构每组freshness。原始raw_response的request_state23/action23等保留在events/context诊断，绝不摊开成已执行轨迹。home无运动序列应明确不可回放；hold聚合不能扩增成连续帧。SUMMARY/profile/selection文件作为hash来源引用；base_locked必须来自已记录设置，未知用null。

events元素为`{time: string|null, row: int, kind: string, details: object}`，保存有界的原事件内容（去除samples已单列的state/action不是必须）；时间仍为source.time_unit。只作为诊断/历史来源，不冒充传感器采样或自动变成硬件执行证明。

annotations.alignment=`source_frames|reference_only|none`。full23按精确source_id选择侧车源并核对episode_index/实际帧数；segments保留原对象字段（id若源名不同在producer中增加id），至少含`start_frame`/`end_frame`半开区间、label、review_status、boundary_status；原字段保留在每段`details`。end_frame可以等于帧数N，不能寻址第N样本。任一边界不匹配应拒绝准备或显式诊断禁用overlay，不猜测对齐。runtime selection只是旧来源参考，写reference_only，不把历史segment挂在runtime时间轴。

准备工具输出目录必须全新，包名`recording.b041`；同时输出manifest/QC与source hashes，不改原件。支持`prepare_b041.py parquet <file> --source-id <id> --annotations <json> --output-dir <new-dir>`及`runtime <run-dir> --output-dir <new-dir>`；PyArrow只在parquet分支惰性导入。没有同次MCAP，不补造raw wheel/steering/odometry。

后端映射计划：`MappingConfig.b041`为可选专用配置，basis=state(default)|action；profile_id、status=provisional、左右独立native_closed/open/zero1/2/travel1/2；默认0/100、0/0、+0.05/-0.05m，不静默clamp。gripper结果derived，原native scalar作为自定义通道保留。chassis.mode=auto|integrate_body|unavailable；auto仅离线state轨迹积分；body vx/vy/wz、初值0、最大源age0.2s和积分gap0.2s，遇无效/过期/缺口累计valid永久中断，不重锚拼接。六个底盘关节仍missing，虚拟轮角默认关闭。运行时最大插值gap默认0.25s，同采样与分析使用。
