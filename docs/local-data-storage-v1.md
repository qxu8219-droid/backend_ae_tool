# 真机原始数据的本地保存

已分析并下载的样本保存在 `backend/data/real_logs/`，整个目录被 Git 忽略。此次继续开发只复制了现有本地检查快照，没有再次连接远端，也没有改动真机或服务器文件。

```text
data/real_logs/
  catalog.json                 # 来源、格式、文件大小、SHA256、适配状态
  raw/
    runtime/<run>/             # 4 次运行的原始文件，共 19 个
    parquet/<source_id>/        # 2 份原始 full23 episode.parquet
    metadata/                  # 标注、来源链接、数据集信息、边界状态、下载清单
    provenance/                # 原始 profile 与团队适配代码，仅作参考
  derived/<source_id>/<profile_id>/
    recording.b041             # 可上传的选定数值/标注/事件便携包
    mapping.json               # 本轮转换使用的专用 profile
    normalized/                # 隔离导入归档，含完整映射快照与 ReplayData
    comparison.json            # 独立数值、质量与标注核对结果
    manifest.json              # 原件、bundle、派生文件 hash 与映射快照 ID
```

当前目录登记 12 个来源条目、27 个文件；全部逐文件校验 SHA256，原始副本设为只读。`catalog.json` 是项目生成的索引；原数据没有改列、改单位或重采样。`.py` 来源文件仅归档，不执行。初次分析快照仍保留在 `outputs/real_logs/inspection-20261026/`。

后续新增数据先放新的来源目录并登记，不覆盖已有原件。T16 已为两份离线 Parquet、live 与 Rim 生成 `derived/<source_id>/b041_ee2_body_velocity_v1/`，上述文件均已落盘；具体来源、使用命令与 UI 上传步骤见 [B041 回放说明](b041-replay-v1.md)。准备 bundle 的独立目录还包含 `qc.json` 与准备阶段的 `manifest.json`，不能将它与导入后的派生 manifest 混用。

已确认手臂/躯干 18 个关节的 rad 映射。用户于 2026-10-27 授权夹爪、底盘先采用明确的暂定假设：EE2 默认指关节位移为 `q1=g/2000 m`、`q2=-g/2000 m`，不代表绝对指尖间距；离线 state 的 body 速度从本地原点积分为相对轨迹，不能当作实测 world/odom 位姿。四个 finger 和积分底盘保持 derived，六个底盘关节保持 missing；在线无位姿时不可依据 target 移动底盘。保留每组原始 valid/source_time/age、无效有限值和精确时钟，不将 action 当反馈或将预测扩展成执行轨迹。

原始 27 个文件和 `catalog.json` 在本轮转换前后均保持不变。原件 hash、bundle hash、导入归档 hash 与映射版本分开保存，归档中的 `conversion_steps` 记录实际公式、源字段、质量和参数。每次改参数选择唯一新 `profile_id` 和新输出目录，保留此前派生证据；完整映射快照可原样重新导入，修改假设只使用 `b041` 专用参数。团队精确资料到达后重建新的派生版本，不修改原件或覆盖旧结果。完整 MCAP 与物理精确标定尚未完成；T17 本地长测已完成，见 [验证记录](validation/t17-status.md)。

可从已下载检查目录重新生成一个**新的**归档目录：

```bash
.venv/bin/python scripts/stage_replay_data.py \
  --inspection-dir outputs/real_logs/inspection-20261026 \
  --output-dir outputs/real_logs-copy
```

该工具仅操作本地、拒绝覆盖已有输出目录，不调用 SSH、不进行标定或日志转换。后续远端补充读取继续遵守“只读，禁止修改”的约束。
