# P1 T04 契约验证

2026-10-23，后端独立 Python 3.12.15 环境。T04 完成，T05 Vue/Mock 工作台尚未开始；正式业务请求仍返回明确的未接入结果。

| 检查 | 结果 |
|---|---|
| REST/WS/IPC/媒体/回放模型 | 164 项契约测试通过 |
| REST 路由 | 18 个，与 OpenAPI 自动导出一致 |
| 消息样例 | 49 份，同时通过 Pydantic 与标准 JSON Schema 验证 |
| TypeScript | TS 5.9.3 strict 编译通过；包括 28 项向量、seek 互斥、遥测速率、禁止 RPC 声称 presented 的正反用例 |
| 生成一致性 | JSON `--check` 通过；TS 再生成字节相同，frontend 副本与 backend 生成物相同 |
| 共享包隔离 | 在实际 IsaacSim Python 中独立导入成功，不加载 app/FastAPI/NumPy/IsaacSim |
| P0 既有及新增工具 | 系统 Python 独立运行 90 项测试通过；未用缺少 NumPy 的 API venv 冒充整套 P0 验证 |

原始测试输出：`outputs/p0/p1-contract-tests-final.log`、`outputs/p0/p0-fault-unit-tests-final.log`。依赖组合保存在 `requirements.lock`、两份 pyproject 与 `tools/contracts/package-lock.json`。

独立审查和集成检查发现的问题均已补回归并修复：跨日志/控制代次的连接快照、无法定位的日志告警、无效曲线值冒充实测、包角周期描述、默认制造渲染验证证据、缺失 PresentedFrame 导出、递归 JSON TS2502，以及导出类型遗漏 WS 必填标签和 seek 互斥。正式媒体目录还明确携带 64 个像素坐标，视频信息声明 UUID 与解码能力。

测试工具当前有一条 Starlette 对 httpx TestClient 的弃用提示；测试通过，未修改运行时依赖或压制失败。它不表示生产 API 已实现。

契约使用与复现见 [contracts-v1.md](../contracts-v1.md)。后续 T05～T07 按这些类型构建 Mock 工作台，T08～T14 再接日志、时间轴和 worker 服务。
