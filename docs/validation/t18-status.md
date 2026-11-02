# T18 首期本地交付验收

日期：2026-11-02。**本地单会话交付验收完成**。前端提交 `232634f`；完整交付标记为两个仓库各自的本地 `t18-local-20261102`；分支保留 `feature/p1-contracts`，没有推送、合并或远端部署。机器摘要见 [t18-evidence.json](t18-evidence.json)，运行入口见 [交付手册](../delivery-v1.md)，排错见 [错误码说明](../troubleshooting-v1.md)。

本次使用现有 IsaacSim 6.2.0/PhysX、Python3.12.15、RTX5070Ti Laptop/驱动580.178.04、Chrome156.0.8078.73、Node26.8.1/npm11.19.0。API临时虚拟环境和前端依赖目录从锁定文件新安装；真实服务使用项目原有API venv与SDK venv，按全新进程、会话、存储目录启动。未清空shader cache，未从零重装SDK/操作系统，也未访问真机或数据服务器。

## 交付内容

- 主README统一为当前操作入口；补齐安装/启动、`.env.example`、资产与MediaMTX准备、正式端口、上传预算、日志保留及清理规则。
- 说明Mock、真实模式、合成CSV生成及明确的commanded/chassis映射；B041沿用专用profile，保留暂定和推算标识。
- 补充错误码、故障定位和浏览器能力要求；同步修正旧阶段文档中的“正式接口未接通”等描述，历史验收记录仍保留原日期和范围。
- 固定worker附加Pydantic2.14.1，补本地环境文件忽略规则。大型USD、私有日志、venv、媒体和工具二进制仍在Git之外。
- OpenAPI的会话容量错误描述修正为实际 `SESSION_CAPACITY`，并重新生成双方契约；协议字段和产品业务行为不变。

## 本轮结果

| 检查 | 结果 |
| --- | --- |
| 新API venv按lock安装、pip check、factory配置 | 通过；19个REST操作 |
| 新前端目录npm ci及真实模式构建 | 通过；保留依赖公告，见下文 |
| 资产与MediaMTX校验 | 53项资产checksum与固定网关二进制hash通过 |
| 后端全量 | 953通过，另5子测试；3项可选skip |
| 最终契约回归 | 182通过；Schema导出、关节元数据hash、TS检查通过 |
| 前端全量/真实构建 | 264通过；typecheck与build通过 |
| 既有Chrome回归 | 14通过；首次失败及修复记录保留 |
| P0 CPU回归 | 90通过 |
| 手册合成映射实际导入 | 241样本、28关节加3底盘通道均为有效commanded |
| Git与文档检查 | 原始数据/媒体/venv/资产/本地env被忽略；无大于1MiB的源码跟踪文件；文档相对链接通过 |

完整日志保存在 `outputs/t18/`。Starlette测试客户端弃用警告、前端约531KiB主chunk提示未改成失败或隐藏；本次未扩展为依赖主版本迁移/打包优化。

## 真实SDK与正式UI闭环

`outputs/t18/synthetic-03/` 使用手册相同合成CSV及映射，实际执行创建→上传→激活→播放/暂停/倍速/定位/单步→延迟与WS重连→worker故障→新sid重新上传并归零→关闭。

- 68次独立画面/源值检查通过。
- 健康短窗口 **19.231fps**，最大采样捕获至呈现延迟 **156.638ms**。
- worker被终止后 **864ms** 显示故障；新sid从零加载。
- 全部本次自有进程与服务端口释放；截图 `workbench.png` 已人工查看。

`outputs/t18/matrix-01/` 使用已保存B041 state日志，通过完整控制/消息故障、worker/网关中断、复制标签页、刷新、创建取消和重复会话流程。

- **82次**独立检查，**0错配**；decoded消息延迟25次、丢弃9次、重复11次、乱序11次，WS异常3次。
- worker故障显示 **876.608ms**；网关恢复后新采集帧 **1232.839ms** 到达。
- **212个已录无损屏幕帧**全部通过；预声明活动区间10.999559秒内211帧，另1帧独立分类通过。不是整个运行的全帧像素覆盖。
- 原始27文件、catalog与输入hash不变；清理错误为空，自有进程/TCP/UDP端口释放。

现场 `ss` 记录覆盖API、Vite、网关API/WHEP/ICE及4个worker端口，均回环监听；两次最终运行8份runtime报告均确认RTSP为127.0.0.1且wildcard override关闭。最后独立检查22个已用端口无残留监听。

## 电源档位对照与失败记录

本机最初处于 `power-saver`，CPU EPP为power，已接AC电源。synthetic-01为13.892fps；把逐值断言改为普通assert后synthetic-02仍为13.537fps，不能把低帧率全部归因测试开销。原生WS的同份B041阶段计时探针也只有14.174fps。

仅临时切换系统为balanced，同份B041原生WS探针通过60.050秒：平均 **19.030fps**、最低完整窗口 **18.503fps**、最大延迟 **361.019ms**。渲染编排均值从54.09ms降至23.87ms。该探针带内存计时钩子，不作为新30分钟验收；随后两次正式闭环使用正常SDK入口、同一balanced条件通过。

没有改变分辨率、画质、渲染等待、15fps/2s门槛或像素/源值核对规则。对照及正式验收都通过finally恢复原来的power-saver，`delivery-power.json`与最终读取结果一致。日常启动脚本不会自动改变电源设置；本机要达到上述性能，应先选择平衡或等效非省电档位。

失败目录 `failed-controls-first`、`synthetic-01`、`synthetic-02`、`native-profile-01` 保留。前两个synthetic失败后Playwright的trace合并迟迟未完成，外层330秒期限执行自有资源收尾；准确FPS失败与原始临时trace另存，不将launcher超时或失败测量改写为通过。

## 测试与审查修正

1. Mock控制用例先前在多轮倍速后已自然结束，再点击禁用暂停导致超时。每个倍速场景现在从零开始；原45秒期限、样本数和同帧断言不变，完整14项回归通过。
2. 真实合成测试改用普通数值assert，避免短窗口创建1700个框架步骤；测量在门槛判断前保存。原19个有效样本、14类故意错误和1个多圈等价样例在新旧oracle中结果一致；15fps/2s仍会使不达标运行失败。
3. 一次独立审查提出1项Important、1项Minor，均处理：手册明确合成commanded/chassis映射，并实际导入核对31通道；WS_CAPACITY移到握手前WebSocket拒绝，修正错误传输层。无遗留审查项。

审查时尚在运行的矩阵、最终电源恢复和端口清理，已由上述最终文件与独立检查补齐。后端实现基线fa6f6d0、前端基线7306e1e；本次交付完整文件由本地交付标记固定。新报告给出关键证据文件SHA256，旧T17材料作为历史阶段记录随交付保存。

## 依赖公告与后续范围

npm audit当前报告5个受影响包（2个critical开发依赖），仅生产依赖为2个moderate。没有执行强制升级；本地功能通过不等于安全验收。

- AJV公告涉及启用`$data`后由数据控制pattern；当前未启用。[官方公告](https://github.com/advisories/GHSA-2g4f-4pwh-qvx6)
- ECharts公告涉及`lines`加tooltip的HTML路径；当前注册的是`line`且未注册tooltip。[修复说明](https://github.com/apache/echarts/pull/21608)
- Vitest/mocker公告对应开发服务器插件入口；当前没有配置该入口。[官方公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8)
- Tinypool在测试进程已有原型污染时存在可利用路径，前置链未验证排除，不能仅按dev依赖忽略。[官方公告](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr)

在不可信测试代码、共享CI或公开服务范围前，需升级相关依赖并复验。另保留：精确夹爪/底盘标定、同次完整MCAP、远程部署与实际IP/RTP网络故障、多用户/认证、创建响应返回sid前强制关页的租约回收。T17的单核CPU<5%尚未达标，本次没有重做30分钟资源验收。
