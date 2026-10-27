# T16 可追溯真机日志与暂定标定 Implementation Plan

> **For agentic workers:** 使用 writing-plans/TDD 与 dispatching-parallel-agents；独立文件任务可并行，集成由主代理完成，一次最终独立审查。沿用已批准的T16任务与2026-10-27用户允许先作假设的补充指令，无需重复审批路线。

**Goal:** 已下载的B041离线/在线日志可以在正式UI真实SDK回放，夹爪和底盘推算明确标识、原数据不变、参数可替换。

**Architecture:** 本地准备工具将Parquet数值列/JSONL事件与精确关联标注打包成 `.b041`。现有隔离导入worker内的新adapter读取有界便携包，复用规范化/时轴/分析，记录临时profile和转换溯源；正式帧携带底盘各轴质量，UI显示来源与标注。完整MCAP原频率适配不在当前已下载数据范围。

**Tech Stack:** 现有Python3.12/Pydantic/FastAPI/纯共享协议、Vue3/TS5.9；Parquet工具可使用已安装的独立pyarrow19.0.1环境，HTTP与SDK不安装新依赖。

**Spec:** 原todo `../../../.trae/documents/robot-log-replay-platform-todolist.md` T16；[便携包接口](t16-bundle-contract.md)；`outputs/real_logs/inspection-20261026/adaptation-report.md`与本轮只读审计；用户最新允许临时假设指令优先于旧“未确认不转换”限制。

## Global Constraints

- 两仓现有feature/p1-contracts继续；远端只读且本轮优先只用本地副本；raw27文件哈希前后不变，不执行归档团队代码。
- 首期夹爪默认native0/100到finger1 +0.05m/finger2 -0.05m、零位0，左右可独立改；四个finger均derived，保留原生标量及其measured/commanded来源，不静默clamp。
- full23索引0:7左臂、7:14右臂、14/15夹爪、16:19速度、19:23躯干；不可把packed27/prediction horizons当实际目标。
- 离线默认state回放；action另选basis，不替换feedback。在线只选实际feedback/target事件，request/prediction/aggregate保留诊断，不扩增成密集动作。
- 默认底盘只对连续离线state速度按body vx/vy/yaw-rate假设从本地原点做SE(2)积分；age/gap各0.2s上限，断点后累计有效性终止，不重锚拼接；在线稀疏、锁底盘、无字段保持不可用。六个底盘关节missing，虚拟轮角默认关闭。
- 默认插值gap0.25s，同采样/分析使用；源valid/source_time/age与绝对ns字符串保留，整数先减原点后转秒。
- 原件、准备bundle、导入archive的hash分开；映射版本/hash进入ReplayData。provisional状态、假设和方法在导入结果/同帧面板中可见。
- 标注只按精确source_id/episode/frame边界对齐；end_frame=N为半开末端，不访问第N帧。Rim参考来源不挂到在线时间轴；pending_review和training_approved=false不提升为确认真值。
- SDK6.2/PhysX/本机回环；所有真实数据验证只驱动仿真，绝不发命令给机器人。

## Review Focus

- 有限数值但valid=false、source_ns=-1/过期、重复来源时间不能变成有效实测或独立新采样。
- runtime预测/目标/反馈时序和locked底盘处理，旧reference标注不能错挂当前run。
- 假设换算、单位/方向、长缺口后的轨迹累计、未知底盘质量不能伪装成measured。
- 未知parser/恶意bundle/超额点数/标注边界/绝对ns精度/源文件路径不得突破解析限额或读取任意文件。
- 标注到达、日志切换/重连时不得覆盖同帧状态；错误/历史告警不能冒充当前机器人事实。

## Task 1: 本地便携包与真实字段审计（并行A）

Ownership: `scripts/prepare_b041.py`（可拆focused helper）、`tests/b041/test_prepare.py`；接口见bundlecontract。

- [x] RED/GREEN真实Parquet列选择、ns字符串、6组质量、源hash、精确侧车对齐、runtime事件白名单、只读/拒绝覆盖。
- [x] 原文件27个hash复核；实际472/762行和live39/25、Rim88/206取样验证，输出本地新目录；不把hold/home视为连续轨迹。
- [x] 针对性测试与独立工具环境Parquet分支验证通过，提交。

## Task 2: 底盘质量贯通（并行B）

Ownership: `proto/replay_protocol/models.py`、`app/replay/interpolator.py`质量部分、`sim_worker/`必要读回、delivery质量屏障、相关contracts/replay/worker测试。

- [x] RED/GREEN：ChassisPose增加可选3轴ChannelQuality（legacyNone）；采样→Target→readback→Rendered/SEI保留，伪造回执质量拒绝。
- [x] CPU测试及纯协议导入通过；主代理统一生成Schema/TS、检查SDK安装与前端拷贝。

## Task 3: 专用映射与导入（主代理）

Files: `app/adapters/b041/`profile/model/reader/转换、`app/adapters/mapping.py`专用选项、registry注册`.b041`；`configs/mappings/b041_provisional_v1.json`；tests/b041。

- [x] RED/GREEN：临时夹爪端点/方向/不clamp/改参、body速度SE(2)积分及valid锁存、ns精度、basis隔离、原始自定义通道、无效组/过期/重复来源/稀疏gap。
- [x] 复用normalize_table建立通道/统计/问题/映射，后处理专用质量与元数据；重复来源保留前填，invalid有限数值不当有效。可读取数据与映射版本不依赖当前配置文件路径。
- [x] 同采样/分析选取源声明gap与服务上限；生成本地derived/<source>/<profile>，真实逐点CPU数值核对、rawhash不变；隔离HTTP实际导入通过。

## Task 4: 标注接口与前端来源展示（可并行C，待模型就绪）

Files: REST LogAnnotations/AnnotationSegment/RecordedEvent模型与路由；前端generated、APIclient、annotation store/component、同帧底盘质量标记、上传.b041识别和说明；相关测试。

- [x] RED/GREEN：按上传log返回assumptions/segments/events/reference；未知资源/错边界/无标注保持明确状态，生成契约与前端同步。
- [x] 临时假设在上传/工作台可见，fingerderived及3轴底盘quality通过已呈现帧驱动；当前段只由已呈现位置选择。历史列表/待审边界与当前告警分开。
- [x] 日志切换/重连/晚到查询不污染当前帧；Mock与原18接口回归不破坏，新增接口按需请求。

## Task 5: 真实数据SDK/UI验证、文档及审查

- [x] 本地两份Parquet和在线live/Rim全部可准备/导入/CPU核验；至少一份离线及一份在线数据在真实SDK、正式UI验证上传/播放/倍速/seek/单步/曲线/标注和假设质量。
- [x] 独立像素ID/CRC/文字与源值对照，记录手工关键样本数值/方向，原件hash与转换profile；无MCAP/精确标定的边界单独列出。
- [x] 后端/前端/契约/浏览器适当回归和一次最终独立审查；修复重要问题，更新todo/README/进度。精确标定待确认，不将临时适配说成实物精确验收。

## 执行结论（2026-10-27）

T16 暂定适配完成。四份真实来源逐点 CPU 核验和两轮 SDK/UI 验证通过，原件与 catalog hash 不变；最终 892 项后端、233 项前端、14 项既有 Chrome、90 项 P0 CPU、7 项独立准备工具、Schema/TS/build 通过。一次独立审查的两项 Important 已 RED→GREEN 修复，见 [验证记录](../validation/t16-status.md)。物理精确标定、同次完整 MCAP 和 T17 长测仍为后续范围，不阻塞已授权的暂定回放。
