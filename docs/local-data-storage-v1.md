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
```

当前目录登记 12 个来源条目、27 个文件；全部逐文件校验 SHA256，原始副本设为只读。`catalog.json` 是项目生成的索引；原数据没有改列、改单位或重采样。`.py` 来源文件仅归档，不执行。初次分析快照仍保留在 `outputs/real_logs/inspection-20261026/`。

后续新增数据先放新的来源目录并登记，不覆盖已有原件。确认映射后，转换结果使用 `derived/<source_id>/<mapping_version>/`，记录原件 hash、映射版本、时钟、单位、通道质量与处理过程；这是约定的后续输出位置，当前尚未生成或接入该转换器。

已确认手臂/躯干 18 个关节的 rad 映射。夹爪标定、底盘字段/坐标含义以及同次采集原始 bag 的可用性仍待团队确认；不把夹爪 0–100 值直接当米，也不把底盘速度当世界位姿。保留每组原始时间戳、valid/source_ns/age 等质量字段，之后按真实来源区分 measured、commanded、derived、missing。

可从已下载检查目录重新生成一个**新的**归档目录：

```bash
.venv/bin/python scripts/stage_replay_data.py \
  --inspection-dir outputs/real_logs/inspection-20261026 \
  --output-dir outputs/real_logs-copy
```

该工具仅操作本地、拒绝覆盖已有输出目录，不调用 SSH、不进行标定或日志转换。后续远端补充读取继续遵守“只读，禁止修改”的约束。
