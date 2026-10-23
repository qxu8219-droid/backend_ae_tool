# T08 参考日志导入与归档

当前已实现实际 CPU CSV/JSON 导入服务，输出 T04 的 `ReplayData`。这不是 Mock parser，但它只理解下述参考格式和显式映射，不代表真机日志字段已经确认。没有启动 IsaacSim，也不会创建/激活回放会话；现有 HTTP 应用继续返回契约阶段的 503，待 T12/T14 接入真实会话和服务。

## 命令行与服务入口

在 backend 目录，使用已有 Python 3.12 环境：

```bash
# 导入，标准输出是 UploadResult JSON；失败向 stderr 输出结构化 error 并退出1。
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports --session offline-analysis import /path/to/source.csv \
  --mapping configs/mappings/r1pro_default.json

# 用导入结果中的 log_id 查询；session 在此只是离线归档命名空间。
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports --session offline-analysis list
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports --session offline-analysis channels log-<32位hex>
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports --session offline-analysis show log-<32位hex>
```

程序入口 `LogStore(root, limits).import_file(session_id, async_byte_chunks, filename, mapping_config=..., parser=...)`。另有 `get/channels/list_logs/delete_session`。会话授权由未来会话管理器负责，不从用户路径创建会话；同一个应用应持有一个 LogStore 实例。上传只新增候选日志，不操作播放状态。

存储目录使用 session ID 的 SHA256 和随机 log ID；用户文件名仅作元数据与受限格式后缀提示。归档含原始字节、`mapping.json`、`replay.json`、`result.json`、校验 manifest。原文件与完成文件设为只读，完成后原子重命名；查询不暴露未完成 staging。查询验证身份、文件哈希和结果一致性；取消、失败及删除会清理当前任务，会话删除阻止导入结果再次出现。跨进程多实例协调、进程崩溃后的 staging 清扫与目录 fsync 级断电持久性不在本轮保证范围。

## 支持的布局

CSV：UTF-8（允许 BOM），首行唯一非空字段名；严格列数，支持标准 CSV 引号和字段内换行。采用有界字节分块与物理行缓冲读取。

JSON：UTF-8（允许 BOM），重复键、混合歧义布局、长度不一致会明确拒绝。源日志允许 NaN/Infinity 数值标记作为无效样本处理；映射配置与所有输出始终要求有限 JSON 数值。

```json
[{"timestamp":0,"torso_joint1":0.1},{"timestamp":0.1,"torso_joint1":0.2}]
```

也支持 `{ "records": [上述记录] }`、裸列字典 `{ "timestamp":[0,0.1], "torso_joint1":[0.1,0.2] }`、`{ "columns": {上述列} }`。

独立频率通道使用：

```json
{
  "channels": {
    "timestamp": [1000, 1002],
    "torso_joint1": {"timestamps":[1000,1000.5,1002],"values":[0.1,0.2,0.3]}
  }
}
```

参考 timestamp 数组定义时间域；通道自己的 timestamps 使用同一声明的时间单位与时钟。独立通道保留原频率，超出参考域的样本按 issue 丢弃。底盘三个轴若网格不同，本轮拒绝，要求在后续采样层显式对齐。

选择顺序：显式 `parser` → 映射中的 `adapter` → 已注册后缀 → probe 最高置信度；并列或不识别时明确失败，不猜一个成功。内置 `csv_v1` 与 `json_v1`。

## 映射与来源含义

完整默认配置见 `configs/mappings/r1pro_default.json`。默认约定为 timestamp 秒、canonical 旋转关节 rad、四个夹爪 m、来源 measured、底盘不可用；这是参考格式的声明约定，不能用于推断陌生文件是实际反馈还是指令。拿到真机 log 后必须按实际字段、单位、坐标系和来源覆盖。

局部配置可只填写需要覆盖的层级；若提供 `joints.map`，该字典就是完整映射，未列出的关节视为缺失。支持：

- `timestamp.field/unit/start_offset`：单位 s/ms/us/ns。使用 Decimal 在绝对时间上先减首个有效时间，再转换浮点秒并加非负 start_offset，避免 Unix 纳秒精度被提前丢失。
- `joints.unit/origin/map/per_joint_overrides`：origin 为 measured 或 commanded；单关节可覆盖 unit/scale/offset/origin。旋转 rad/deg，移动 m/mm/cm，先单位转换，再 scale，再以输出单位表示的 offset。全局角度单位不会污染夹爪单位；不夹紧超限值，不改变四夹爪正负方向。
- `chassis.available/frame/map/position_unit/yaw_unit/origin/transform`：非 world 必须显式提供 transform。`rotation_rad` 先旋转 xy，再加 `translation_x_m/translation_y_m`；yaw 加相同旋转。非单位变换标记 derived，保留各输入字段与填充值来源。
- `custom_channels.<name>.field/unit/scale/offset/origin`：自定义标量保留声明单位，包括 mm/cm/deg，只应用显式 scale/offset；不会偷偷变成 SI 后仍标旧单位。不能占用 canonical 或 chassis 通道名。
- `options.max_forward_fill_s` 默认 0.25；`resample` 当前只允许 none，`unwrap_continuous` 只允许 false。统一重采样、角度展开、虚拟轮角属于 T09，相关未支持请求明确拒绝。

规范化时间轴保留原采样集合，`sample_times_s` 是参考时刻与各独立通道时刻的有序并集。`TimeBasis.source_origin` 保存秒单位的原始起点，精确十进制形式另存 metadata；`relative_origin_s` 等于 start_offset。UploadStats.duration 是首末样本的跨度。T09 把播放位置映射到源时间域时，需要加上 relative_origin_s；不能把非零源起点直接当成播放进度。没有导入阶段重采样或插值。

## 异常处理表

| 输入 | 行为 | 质量与诊断 |
|---|---|---|
| 参考时间列缺失/无有效时刻 | 拒绝导入 | NO_TIMESTAMP |
| 重复/倒退时间 | 按输入顺序丢弃，不重排 | 行号与 TIMESTAMP_NOT_MONOTONIC |
| 无效/NaN/Inf 时间 | 丢弃；全部无效则失败 | INVALID_TIMESTAMP |
| 独立通道域外时刻 | 丢弃 | TIMESTAMP_OUTSIDE_REFERENCE |
| 缺少关节字段 | 生成零默认值 | missing、valid=false、defaulted |
| 已有列首值为空/非有限 | 保留 null | valid=false；不从未来倒填 |
| 距最近真实有效值 ≤ 前填上限 | 前填最近值，不链式延长预算 | forward_filled；指向被复用的源索引 |
| 超过前填上限的缺口 | 保留 null | valid=false，不造连续曲线 |
| 整列都无效 | 列保留 null，计入 missing | ALL_VALUES_INVALID |
| 所有关节都无有效值 | 拒绝导入；自定义电压等不能救回 | NO_JOINT_CHANNELS |
| 数字列出现普通非数字文本、bool、对象/数组 | 拒绝导入 | FIELD_NOT_NUMERIC + 行/通道 |
| 单位/比例/坐标变换后溢出 | 拒绝导入 | NUMERIC_OVERFLOW |
| 有限但超出限位 | 保留原值 | descriptor 提供版本化限位；区间预扫描在 T13 |
| 任一底盘轴在某样本无效 | 同样本三个输出轴均 null | 不生成部分有效 world pose |
| 非法/尚未支持的映射 | 拒绝请求 | INVALID_MAPPING，status400 |
| 字节/点数/内存等越界 | 明确失败并清理 | status413；解析超时 status408 |

`ChannelQuality.origin` 来自声明；`processing` 标记转换、前填、推算等，不能用原值标签遮住补值。每个通道的 `source_indices` 指向未经删除/重排的原始采样索引；`metadata.source_rows` 给出 CSV 物理起始行或 JSON 记录序号。`conversion_steps` 保存因子、单位、比例及偏移；底盘 `composite_sources` 为每个输出样本分别记录贡献字段及其原索引。原始源字节另外保留，未被规范化内容覆盖。

限位元数据来自项目 6.x 资产的 `r1pro_2026_common.json`，小型快照位于 `app/assets/robots/r1pro_2026_joints.json`，包含源 SHA256 与每关节 provenance，不依赖导入 Isaac SDK。实际 robot ID 沿用 `configs/robot-asset.json` 的 `galaxea_r1pro_2026`。

## 资源边界

默认 ParseLimits：64MiB 原文件、20,000 原始或并集采样时刻、128 源/规范化通道、600,000 点、64KiB 单字段、1,000 issues。所有界限共同生效；达到一种边界就明确失败，不保证所有上限能同时取到。JSON DOM 只在隔离解析进程内加载。

解析进程 Linux RLIMIT_AS 为 768MiB，并设置 CPU/输出文件界限；解析与上传各有 30 秒超时。输出 JSON 上限为 min(256MiB, memory_bytes/3)。单 LogStore 一次只允许一个导入，第二个立即 IMPORT_BUSY，不积累无限队列。CPU 解析不在 asyncio 控制循环内进行；归档与查询的大块读写/校验在线程执行。取消后回收子进程与在途 IO，重复取消不会先放弃清理。

CLI 可用 `--max-file-bytes/--max-samples/--max-points/--memory-mib/--timeout-s` 调整。硬内存约束针对解析子进程；父进程仍需读取有界规范化结果，本轮没有宣称全服务固定内存上限或小时级日志吞吐。

## 安装扩展适配器

插件使用 `model_eval.replay_adapters` entry point；名称作为 parser ID，入口是 `factory(*, limits)`，返回有 `probe(path)->float` 与 `load(path, config)->ReplayData` 的适配器，可声明 suffixes。通过 `AdapterRegistry.discover_plugins()` 或 `LogStore(..., discover_plugins=True)` 显式启用。仅加载可信的已安装包，不接受上传的 Python 路径。

建议扩展 reader 后复用 `normalize_table`，保证 importer metadata 的 `parser/stats/issues/channel_descriptors` 等一致。registry 验证 ReplayData 与导入元数据；坏返回值、缺失元数据或未结构化的插件异常会变为 AdapterError。插件加载冲突原子拒绝；其自身可信代码的副作用不作回滚保证。
