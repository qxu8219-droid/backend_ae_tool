# B041 真机日志回放与暂定映射

T16 已将本地保存的两份 B041 full23 Parquet、live 与 Rim 事件日志接入正式导入和回放流程。用户于 2026-10-27 授权先采用明确的夹爪、底盘假设；默认 profile 为 `b041_ee2_body_velocity_v1`，状态为 `provisional`。这些结果用于检查日志与仿真回放，尚不代表实物精确标定或硬件执行验收。

原件位于 `data/real_logs/raw/`，27 个原始文件及 `catalog.json` 保持不变。`.b041` 是从原件选取数值、质量、标注和事件后生成的 UTF-8 JSON 便携包，不包含完整相机/MCAP 数据。HTTP 导入与 SDK 环境无需 PyArrow，也没有为此新增依赖；Parquet 读取只在本地准备阶段进行，runtime JSONL 准备仅用 Python 标准库。目录与保留规则见 [本地数据说明](local-data-storage-v1.md)，包结构见 [bundle 契约](implementation/t16-bundle-contract.md)。

## 现有文件与直接使用

以下四个来源已经生成 `data/real_logs/derived/<source_id>/b041_ee2_body_velocity_v1/`：

| 来源 | 类型 | 标注关联 |
| --- | --- | --- |
| `RBA60725B041_20261016100605618_RAW` | 离线 full23 Parquet | `source_frames` |
| `RBA60725B041_20261020114048516_RAW` | 离线 full23 Parquet | `source_frames` |
| `live_run_20261023T123221109918Z` | 在线运行事件 | `none` |
| `rim_R2_case02_20261021T052946929344Z` | 在线运行事件 | `reference_only` |

每个派生目录包含：

```text
recording.b041      # 可上传的选定数值/事件便携包
mapping.json        # 本次使用的 B041 profile
normalized/         # 隔离 LogStore 导入归档及完整映射快照
comparison.json     # 独立数值、质量、时钟、缺口与标注核对结果
manifest.json       # 原件、bundle、派生文件 hash 及映射快照 ID
```

在 `backend/` 执行以下命令可离线导入一份现有 bundle；它不创建仿真会话：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/import_log.py \
  --store outputs/imports-b041 --session b041-inspection \
  import data/real_logs/derived/RBA60725B041_20261020114048516_RAW/b041_ee2_body_velocity_v1/recording.b041 \
  --mapping data/real_logs/derived/RBA60725B041_20261020114048516_RAW/b041_ee2_body_velocity_v1/mapping.json
```

正式 UI 的使用步骤：

1. 在 `backend/` 用 `ISAACSIM_PYTHON=/path/to/isaacsim62/bin/python bash scripts/run_backend.sh` 启动服务，在 `frontend/` 用 `VITE_USE_MOCK=false npm run dev` 启动工作台，创建会话并等待就绪。
2. 在“日志与映射”选择上述 `recording.b041`。展开“解析器与映射配置”，将同目录 `mapping.json` 的完整 JSON 内容粘贴到“映射 JSON”；解析器可留空，由 `.b041` 后缀选择 `b041_bundle_v1`。不要把映射文件当作日志上传。
3. 查看导入统计、诊断及映射快照，再点“激活此日志”。留空映射会使用服务端默认 B041 profile；要复现既有结果，应提供保存的映射。
4. 播放、暂停、定位、单步或调整倍速；在同帧画布查看指关节与底盘来源，在“标注与来源”查看暂定假设、当前源帧阶段及待审状态。当前阶段只依据已呈现帧选取，历史事件单独列出。

完整的导出映射快照也可以**原样**重新导入。修改假设时，应复制 [B041 profile 配置](../configs/mappings/b041_provisional_v1.json)，只用其中 `b041` 专用参数调整；不要修改通用 `timestamp`、`joints`、`chassis`、`custom_channels` 或 `options` 来覆盖 B041 固定源字段。除默认值或与当前 B041 转换完全一致的导出值外，这些 generic overrides 会被拒绝，HTTP 返回 400。改 `basis` 时尤其应使用 `adapter`＋`b041` 配置，避免旧完整快照中固定来源字段与新 basis 冲突。

## 夹爪假设与可改参数

full23 的 14、15 槽分别为左右夹爪原生标量 `g`。[官方2026驱动接口](https://docs.galaxea-dynamics.com/R1Pro/docs/2026/software_interface/r1pro_driver/)定义反馈为0–100mm行程；这不等于已经确认历史数据到模型的几何零位，也不能把低层电机角度或高层目标直接混作同一单位。默认按 EE2 两指对称关节行程换算：

```text
q1 = g / 2000 m
q2 = -g / 2000 m
```

例如 `g=100` 得到 `q1=+0.05 m`、`q2=-0.05 m`。这里是 USD 指关节的位移坐标，**不是绝对指尖间距**；两指间的几何零位和实物开口仍需团队精确资料。每只手的两个坐标来自同一个标量，左右共四个 finger 始终为 `derived`，不会因 profile 改为 `confirmed` 而成为独立实测。

左右 `left_gripper`、`right_gripper` 可分别设置 `native_closed`、`native_open`、`zero1_m`、`zero2_m`、`travel1_m`、`travel2_m`；要求 `native_open > native_closed`。通式为：

```text
q_finger = zero_finger_m
         + (g - native_closed) / (native_open - native_closed) * travel_finger_m
```

默认端点是 0/100，两个零位均为 0，行程分别为 +0.05/-0.05 m。超出原生端点的有限数值会保留并给出诊断，不静默截断。原标量同时保留在 `raw.state.left_gripper`、`raw.action.left_gripper` 等自定义通道，分别保留反馈/指令来源。

## 底盘、state/action 与有效性

full23 索引使用零基切片：`0:7` 左臂、`7:14` 右臂、`14:16` 左右夹爪、`16:19` 底盘速度、`19:23` 躯干。18 个手臂/躯干关节按 rad 使用。`basis=state` 默认选择反馈；`basis=action` 另行选择记录指令，不能作为实际执行或反馈证明。packed27、请求状态、模型预测 horizon 和聚合统计不会摊开成实际动作。

默认底盘将连续离线 `state[16:19]` 暂作 body 坐标 `vx`、`vy`（m/s）、`wz`（rad/s），以区间左端速度作 SE(2) 积分，从本地 `(x,y,yaw)=(0,0,0)` 出发。结果是相对推算轨迹，三轴均为 `derived`，没有实测 world/odom 定位或漂移修正。即使选择 action basis，底盘也只可能由同记录的 state 速度推算，不使用 action 速度冒充执行位姿。

`b041.chassis` 可设 `mode=auto|integrate_body|unavailable`、`velocity_frame=body|world`、`initial_pose`、三轴 `velocity_scale`/`velocity_bias` 和 `max_gap_s`；改变坐标假设必须留下新的 profile。默认源年龄上限 `max_source_age_s=0.2` 秒，积分相邻样本间隔上限 `chassis.max_gap_s=0.2` 秒。任何无效、过期、缺值或过大间隔都会终止本条累计轨迹的有效性，此后即使源值恢复也不重新锚定或跨断点拼接；保留的末值不等于有效位姿。

在线数据只有实际记录的 `feedback23` 与 `target23` 事件快照，时钟是事件 `monotonic`，没有每组传感器采集时间与 age；不虚构这些字段。无可靠位姿、锁底盘或在线稀疏记录均保持底盘不可用，固定场景位置不能解释为实测静止，也不能依据 target 速度移动底盘。`integrate_body` 不绕过上述源类型限制。六个底盘轮/转向关节保持 `missing`，虚拟轮角默认关闭。

离线六组原始 `valid`、`source_time`、`age_s` 与原行号保留。无效但有限的值仍可用于审计，其质量不会被提升；`source_time=-1`、未来/回退时间或过期来源不能成为有效实测。重复来源时间表示因果保持，标为 `forward_filled` 并追溯首次来源，不当作新采样。绝对纳秒时钟以字符串保存，先精确减起点再转换为相对秒，不能用浮点绝对纳秒重新计算。

默认 `max_interpolation_gap_s=0.25` 秒约束采样与曲线分析；较长缺口不插成连续有效数据，曲线显示断点。它与底盘积分的 0.2 秒上限分别生效。runtime 的 home 无运动序列、hold 聚合没有可用反馈/目标序列时会明确拒绝准备，不能据此补造轨迹。

## 标注、历史事件与溯源

离线标注只在 `source_id`、episode、实际帧数及边界时钟精确匹配后标为 `source_frames`。区间为 `[start_frame,end_frame)`；`end_frame=N` 是末端边界，不读取不存在的第 N 帧。来源或边界不匹配会拒绝准备，不能靠近似时间猜测对齐。

若所选 action basis 的尾部没有动作记录，阶段结束时间仍按完整来源帧边界保留，可能晚于所选回放末时刻；阶段可定位起点和事件仍必须位于所选时域。不会把缺失 action 补成反馈，或截短原始标注边界来使接口通过。

Rim 的 selection 是旧来源参考，标为 `reference_only`，不能映射为当前 run 的阶段或用于 seek。`pending_review`、边界待审核和 `training_approved=false` 原样展示，不升级成确认真值。历史事件只是诊断记录，不等于当前机器人状态；无法对齐或超出当前 basis 时间域的事件 `time_s=null`，不可定位。接口为 `GET /api/v1/sessions/{sid}/logs/{lid}/annotations`。

原件 SHA256、便携包 SHA256、导入归档 hash 分别记录。`ReplayData` 还保存映射快照 ID、profile、原样本质量及 `conversion_steps`：夹爪保留公式/原生单位/标定参数，底盘保留 state 速度来源、积分参数和有效前缀依赖。底盘的 `source_indices` 指向当前端点，不表示只依赖单个样本。HTTP 解析不会根据 bundle 内的来源路径读取任意本地文件；原件 hash 在上传包中是溯源声明，由本地准备/验证工具与保存的原件核对。

## 准备与重新生成

以下命令均在 `backend/` 执行。使用新的输出目录和唯一 `profile_id`，工具拒绝覆盖已有目录或向 `raw/` 写入。Parquet 分支需要本机**已存在**的独立 `outputs/real_logs/inspection-20261026/tools-env/bin/python`（含 PyArrow）；无需向 HTTP、SDK 或系统环境安装它。

先为本轮生成唯一标识，再准备两份离线和两份在线 bundle：

```bash
B041_RUN="b041-$(date +%Y%m%dT%H%M%S)-$$"

for B041_SOURCE in RBA60725B041_20261016100605618_RAW RBA60725B041_20261020114048516_RAW; do
  env -u PYTHONPATH -u PYTHONHOME \
    outputs/real_logs/inspection-20261026/tools-env/bin/python scripts/prepare_b041.py parquet \
    "data/real_logs/raw/parquet/$B041_SOURCE/episode.parquet" \
    --source-id "$B041_SOURCE" \
    --annotations data/real_logs/raw/metadata/annotations.reviewed.json \
    --output-dir "outputs/t16/$B041_RUN-bundles/$B041_SOURCE"
done

for B041_SOURCE in live_run_20261023T123221109918Z rim_R2_case02_20261021T052946929344Z; do
  env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/prepare_b041.py runtime \
    "data/real_logs/raw/runtime/$B041_SOURCE" \
    --output-dir "outputs/t16/$B041_RUN-bundles/$B041_SOURCE"
done
```

每个准备目录生成 `recording.b041`、`manifest.json` 与 `qc.json`。确认各命令成功后，将默认配置复制成独立 profile；如要调整假设，在下面标明的位置只修改 `b041` 参数：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python - "$B041_RUN" <<'PY'
import json
import sys
from pathlib import Path

profile = json.loads(Path("configs/mappings/b041_provisional_v1.json").read_text())
profile["b041"]["profile_id"] = sys.argv[1]
# 在此分别调整 left_gripper/right_gripper 或 chassis 参数；保持 basis="state"。
with Path(f"outputs/t16/{sys.argv[1]}-profile.json").open("x") as stream:
    json.dump(profile, stream, ensure_ascii=False, allow_nan=False, indent=2)
    stream.write("\n")
PY

env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_b041.py \
  --bundles-root "outputs/t16/$B041_RUN-bundles" \
  --profile "outputs/t16/$B041_RUN-profile.json" \
  --derived-root data/real_logs/derived \
  --output-dir "outputs/t16/$B041_RUN-validation"
```

`validate_b041.py` 使用真实隔离 LogStore 导入和独立数值公式，生成 `derived/<source_id>/<profile_id>/` 及 `evidence.json`，前后核对 27 份 raw 文件和 catalog 的 hash。它要求输入 profile 使用 state basis，并额外对这批数据中较晚的离线来源检查 action basis；此命令因此准备上述四个来源，不应当作任意单份 runtime 包的通用验证器。普通单包导入使用前面的 `import_log.py`。

团队提供精确夹爪端点、几何零位、速度坐标和底盘来源后，修改专用 profile、使用新的 `profile_id` 重新生成并核对；不要修改原件或覆盖既有派生证据。确认状态也应依据资料与核验结果更新，不能只为隐藏暂定提示而改为 `confirmed`。

## 验证范围

本轮两份离线、live 与 Rim 已通过本地数值/质量核对及真实 IsaacSim 6.2 正式 UI 回放验证，见 [T16 验证记录](validation/t16-status.md)。所有验证只驱动本地仿真，没有向机器人发送命令。完整 MCAP 原频率适配与物理精确标定尚未完成。T17 已另行完成本地长测，见 [T17记录](validation/t17-status.md)；其结果不代表实物标定完成。
