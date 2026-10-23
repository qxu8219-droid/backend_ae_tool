# T08 日志导入验证

日期：2026-10-23。已实现实际 CSV/JSON 参考导入器与归档服务，验证输入均为合成样例，不代表真机日志字段已确认，也未接入真实 HTTP 会话或 IsaacSim 渲染。

## 完成范围

- 有界 CSV 分块读取、JSON records/列布局/独立时间通道、显式 parser 与后缀/probe 选择、可信已安装插件入口。
- 严格映射配置、Decimal 时间归一化、单位/比例/偏移、四夹爪方向、显式底盘变换、自定义标量；原采样时间保留，插值/重采样/轮角推导留在 T09。
- 重复/倒退时间、非有限值、首值、长缺口、整列无效的确定策略；每样本质量、行号/源索引、转换步骤、复合底盘来源，以及版本化限位来源。
- 独立解析进程、文件/样本/点数/内存/超时限制、单任务准入、重复取消保护；原始字节、映射与规范化数据原子入库，查询隔离、哈希校验、失败清理及删除期间防止结果再次出现。
- 离线 import/list/show/channels 命令行与可重复实际导入探针。默认 ASGI 仍保持 503；本轮没有绕过会话管理创建假 worker。

## 最终验证

完整测试 **305 项通过**：141 项 adapters 测试，164 项既有契约测试。包含实际子进程 CSV/JSON 导入、原始字节保存、查询重开、跨命名空间拒绝、超时回收、内存限制、取消/删除竞态及 CLI 流程。唯一 warning 为已有 Starlette/httpx TestClient 弃用提示。

`export_contracts.py --check` 和 TypeScript 契约检查通过；wire Schema 未改变。前端源码本轮没有变化，不重复宣称前端或 P0 浏览器重新验收。

实际探针：698,027 字节合成 CSV、1,200 原采样时刻、28 关节，规范化为 33,600 个关节采样值。启动隔离进程、解析和归档共约 **1.117 秒**；10ms 心跳共观测 85 次，最大事件循环间隔 **54.20ms**；子进程峰值 RSS **75,300,864 字节（约 71.8MiB）**。原文件 SHA256、四夹爪中的正负方向检查、限位快照与实际 common config SHA256 均通过。

这是一段小型合成输入的观测，不是小时级日志吞吐、全服务内存保证或实时控制接口延迟验收。默认硬约束为 Linux 解析子进程 768MiB 地址空间，详细独立界限见 [导入说明](../log-import-v1.md)。

## 审查与修复

独立审查发现四项 Important 问题，均先复现再修复：自定义 mm/cm/deg 单位错配；失败清理取消导致导入槽永久占用；底盘旋转丢失另一贡献轴的前填来源；插件返回无效类型/缺失元数据泄漏未结构化异常。另补重复取消的 IO 完成保护，以及 CLI 显式 null 映射文件拒绝。回归用例在 `tests/adapters/test_review_regressions.py` 和 `test_cli.py`。

精确数值见 [t08-evidence.json](t08-evidence.json)。原始测试输出、JUnit、合成源与归档在 `outputs/t08/`，不纳入源码 Git。可运行：

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/validate_import.py \
  --output-dir outputs/t08/new-probe
```

下一项 T09：在该 ReplayData 上实现确定性插值、底盘采样和有明确前提的虚拟轮角。若 `relative_origin_s` 非零，采样器须正确把播放位置映射到源时间域。
