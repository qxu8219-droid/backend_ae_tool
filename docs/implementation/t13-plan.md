# T13 限位、事件与曲线实现计划

执行方式：executing-plans 内联实施与 TDD，一次最终独立审查；沿用已批准的 feature/p1-contracts checkout。

依据：`../../../.trae/documents/robot-log-replay-platform-todolist.md` T13，原项目计划和 T08～T12 已有契约。输出独立分析服务与曲线响应数据；HTTP/WS 绑定留 T14，正式浏览器媒体留 T15。远端严格只读，本轮不需访问远端；不转换未确认的夹爪/底盘数据。

## Global Constraints

- 28 关节 canonical 顺序；位置限位来自固定 common 配置并保留来源/hash。continuous 无位置限位，速度/力矩假设不能当位置限位。
- 上下界各 5% 行程为预警带，边界包含；硬限位允许 rad 1e-6 / m 1e-8 容差。固定零跨度只区分正常/越界。同一共享判定供分析与 worker 使用，worker 不 import app。
- 有效 measured/commanded 且非填充/推算处理才参与位置告警；保留二者来源差异。缺测/推算仍按质量显示，不宣称实测正常。
- 扫描各通道规范化原始采样，保留单点短峰。事件进入/退出是观测时刻，不假定亚采样阈值穿越；缺测或显式 max_gap_s 中断不推断正常退出。
- 曲线按每通道原时序查询，返回相对日志时间；与 T09 使用相同 gap 配置和包角展开语义。每有效段 min/max 分桶，保留无效段 null 断点；不足以表示必要断点和峰谷时明确报预算不足，不能静默丢失。
- 历史集合无 session/epoch 去重副作用；投递候选按已渲染身份/日志时刻前进，浏览器仍仅在已配对显示进度释放（沿用前端 history store）。不把服务端渲染冒称浏览器呈现。

## Review Focus

极端浮点范围/零跨度/非对称边界；质量来源或缺口变化时事件是否误合并；低点数预算是否掩盖短峰/连接缺口；越界或变更身份的查询是否污染缓存；旧渲染回执/seek/重播是否重复触发或提前展示。对应任务均增加回归覆盖。

## Task 1: 共享限位与可追溯元数据

文件：`proto/replay_protocol/limits.py`、`app/analysis/metadata.py`、`scripts/export_joint_metadata.py`、worker media 判定；`tests/analysis/test_limits.py`。

- [x] RED：非对称/单侧夹爪/连续/零跨度/容差/极端值/质量位图；common→缓存与hash/来源；实际 worker 快照使用同一规则。
- [x] GREEN：不可变限位规格与共享判断，单次编译；元数据导出及 --check；导入器复用受验证元数据读取。
- [x] 验证：分析限位与原 worker CPU 测试，元数据导出检查；预期全部通过。

## Task 2: 完整原样本事件索引与投递游标

文件：`app/analysis/models.py`、`events.py`、`cursor.py`；`tests/analysis/test_events.py`。

- [x] RED：原始窄峰、单点、峰值来源、正常退出/缺口/结束、来源变化、跨区间/边界、稳定 ID、10x 跨过短事件、重复帧/回退/新代次。
- [x] GREEN：按关节独立原时序预扫；稳定历史 ID；相交/进入/当前活动查询；游标只输出待前端配对释放的 AlertData，不修改历史。
- [x] 验证：查询顺序无关、原数据不变；预期全部通过。

## Task 3: 有界保峰曲线与质量呈现

文件：`app/analysis/curves.py`、`service.py`；`tests/analysis/test_curves.py`；前端 panelModel 与对应测试。

- [x] RED：多通道/时间范围/边界插值、窄峰、null/长缺口、极端值、预算2/奇数/不足、包角/质量/未知通道与原数据变更隔离；前端推算值不能显示实测限位正常。
- [x] GREEN：每段 min/max、有界分配、明确预算失败；返回现有 CurveResponse 和完整相关 LimitInterval；前端只调整质量判定展示，不改配对机制。
- [x] 验证：分析服务全量、前端单测/类型/构建；必要时原浏览器交互回归。

## Task 4: 集成证据、审查和交付记录

文件：`scripts/validate_analysis.py`、分析说明/证据、README 与开发清单。

- [x] 实际 CSV 导入→归档→预扫→低预算曲线→渲染候选投递；记录短峰保留、原件hash不变、时间与质量结果。
- [x] 因 worker 判定位图改变，用真实 SDK/独立 H.264 解码检查测量/指令/无效/推算的快照标志；合成 fixture，不宣称真机反馈。
- [x] 全量后端、P0 CPU、元数据/契约/TS 与前端检查；一次最终独立审查，重要问题 RED→GREEN 修复后完成验证、提交和记录。
