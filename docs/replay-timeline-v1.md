# 回放时间轴 v1（T10）

本文描述 `app/replay/timeline.py`、`app/replay/delivery.py` 的 CPU 回放核心，以及后续服务接入时必须保留的语义。T10 提供确定性的状态机、采样调度和交付证据核验；尚未实现 T11 的真实 Worker/传输或 T14 的运行时服务绑定。

## 核心边界与入口

`Timeline` 接收已准备好的 T09 `ReplaySampler`，通过显式调用 `tick()` 产生目标。核心不启动线程、后台任务、定时循环、IsaacSim、GPU 渲染器、编码器、ZMQ 或 HTTP 服务，也不执行 `sleep()`。文件读取、日志导入和采样器准备必须在外部完成；`resolve_log(log_id)` 应当只查找当前会话的缓存采样器，不得在调用期间做 I/O。

```python
Timeline(
    session_id,
    resolve_log,                 # Callable[[str], ReplaySampler]
    clock=time.monotonic,
    target_hz=60.0,
    ack_timeout_s=10.0,
    delivery=None,
)
```

默认 `DeliveryTracker` 使用同一时钟。注入自定义 tracker 时，调用方需保持会话与时钟一致。核心没有并发锁；后续驱动应在同一个串行执行上下文中调用控制、定时回调和回执处理。

| 接口 | 作用 |
| --- | --- |
| `load(log_id)` | 查找准备好的采样器，生成零时刻关键帧 |
| `play(speed=None)`、`pause()`、`stop()` | 接受播放控制并生成可靠关键帧 |
| `seek(time_s=...)` 或 `seek(frame_index=...)` | 跳到日志时间或原始采样索引 |
| `step("forward" / "backward")` | 按原始采样时间向前或向后单步，并暂停 |
| `set_speed(speed)` | 锚定当前位置后设置倍率 |
| `tick()` | 检查时钟、超时并在需要时生成一个新目标，返回快照 |
| `snapshot()` | 读取最近记录的状态；不推进时钟、不采样、不检查超时 |
| `take_rpc()` / `take_target()` | 一次取出一个可发送的 RPC / 最新流目标 |
| `receive_rpc(response)` / `receive_event(event)` | 验证 Worker 回执，返回是否接纳 |
| `worker_failed(code, message)` | 将会话置为终态错误并返回快照 |
| `diagnostics()`、`error` | 读取有界队列计数、运行标记及错误原因 |

控制方法返回 `PlaybackSnapshot`，只表示控制已被接受。未来 REST 层应继续使用 `ack_stage="accepted"`，不能把此返回值当作 Worker 已应用、已渲染或浏览器已显示的证明。

## 六状态与七控制

下表假定参数符合共享协议且时钟、Worker 未发生运行故障。`p` 是控制到达时按旧倍率计算的逻辑位置；等待关键确认时它保持在锚点。`D` 为日志时长，`q` 为裁剪后的 seek 位置，`step(p)` 为相邻原始采样时间。`E+` 表示 `playback_epoch` 增加；未标记时 epoch 保持。

| 当前状态 | `load` | `play` | `pause` | `stop` | `seek` | `step` | `set_speed` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `idle` | `ready, 0, E+` | 拒绝 `NO_LOG_LOADED` | 拒绝 `NO_LOG_LOADED` | 拒绝 `NO_LOG_LOADED` | 拒绝 `NO_LOG_LOADED` | 拒绝 `NO_LOG_LOADED` | 拒绝 `NO_LOG_LOADED` |
| `ready` | `ready, 0, E+` | `playing, p`；末端重播见下文 | `paused, p` | `ready, 0, E+` | `ready, q, E+` | `paused, step(p), E+` | `ready, p`，更新倍率 |
| `playing` | `ready, 0, E+` | `playing, p`；末端重播见下文 | `paused, p` | `ready, 0, E+` | `playing, q, E+`；`q=D` 时为 `ended` | `paused, step(p), E+` | `playing, p`；`p=D` 时为 `ended`，更新倍率 |
| `paused` | `ready, 0, E+` | `playing, p`；末端重播见下文 | `paused, p` | `ready, 0, E+` | `paused, q, E+` | `paused, step(p), E+` | `paused, p`，更新倍率 |
| `ended` | `ready, 0, E+` | 从 `0` 重播，`E+` | `paused, p` | `ready, 0, E+` | `paused, q, E+` | `paused, step(p), E+` | `ended, p`，更新倍率 |
| `error` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` | 拒绝 `SESSION_ERROR` |

上述两种状态拒绝均为 `409`。请求参数先按共享模型验证，因此非法参数可能先产生 `400 INVALID_REQUEST`。`snapshot()` 在全部六个状态均可读取，错误状态也保留最后已知信息。

共同规则与边界：

- 每个成功接受的显式控制都增加 `control_seq` 和 `target_seq`，并生成新的可靠关键帧。重复 `pause`、相同倍率、相同 seek 位置、边界单步等即使不改变姿态，也会产生新的顺序屏障。重复 `play` 在普通播放位置保留当前逻辑位置；两次调用之间实际经过的播放时间仍然有效。
- `load`、`stop`、`seek`、`step` 以及末端重播增加 epoch。切换日志或 epoch 会清空旧 `applied_position` 和 `rendered_identity`。同一日志、同一 epoch 的普通控制可保留此前已观察的位置，但这些位置并非新控制的确认。
- `load` 从任意非错误状态回到 `ready`、时间 `0`，保留当前倍率；即使加载同一日志也重新生成 epoch 和 `load_log` 前缀。未知日志为 `404 LOG_NOT_FOUND`；解析器异常、非 `ReplaySampler` 返回值、无法生成目标或采样器会话/日志不匹配为 `422 INVALID_LOG`。正常参数或日志拒绝不接受新控制、不改变既有播放上下文。
- `play` 在 `ended` 或逻辑位置已经到达 `D` 时回到 `0` 并增加 epoch，包括先 seek 到末端再播放。`play(speed=...)` 同时可更新倍率；不传倍率则保留原值。
- `seek` 必须恰好提供 `time_s`、`frame_index` 其中一个，不能同时提供、全省略或显式传空值。时间裁剪到 `[0, D]`；有符号原始采样索引裁剪到第一/最后样本。索引不是 `target_seq`、`capture_id` 或渲染帧号。
- `playing` 中 seek 到末端成为 `ended`；从 `ended` seek 到任意位置（包括仍然是末端）成为 `paused`；`ready`、`paused` 的 seek 保持各自状态。
- `step` 使用 T09 原始采样时间轴：向前找严格晚于 `p` 的样本，向后找严格早于 `p` 的样本，越界停在端点。每次都进入 `paused`。插值位置的 `source_sample_index` 可以为 `None`，原始样本位置才带对应索引。
- 倍率必须为有限数值且属于 `[0.1, 10.0]`；布尔值、字符串、NaN、无穷大等不符合共享模型。变速先按旧倍率锚定 `p`，再使用新倍率，位置不会因倍率变化跳跃。
- 单样本日志是合法的零时长日志，已由 T10 核心测试覆盖：`D=0`，目标时间和 `progress` 均为 `0`。`load` 仍进入 `ready`；`play` 直接进入 `ended`，增加 epoch 并可靠交付零时刻关键帧，不启动流式播放。seek/step/pause 仍遵循上表，位置始终为 `0`。空日志在导入/`ReplayData` 边界被拒绝，无法构造有效的准备采样器；没有加载日志的 `idle` 与已加载单样本日志是不同状态。
- 所有序号使用共享协议的安全整数范围。`target_seq` 在会话内不因 load、seek 或重播重置；序号耗尽产生 `SEQUENCE_EXHAUSTED` 并要求新会话。

## 逻辑时钟与 60 Hz 调度

默认时钟是 `time.monotonic`，也可以注入确定性的时钟函数。返回值必须是有限、非递减的数值；相等允许，布尔值不允许。墙上时间、Worker 的 `applied_wall_s` 和 `captured_wall_s` 不用于计算播放位置。

运行中的位置由锚点计算：

```text
p(now) = min(D, anchor_position + (now - anchor_time) * speed)
```

每次接受显式控制时先确定新锚点，然后将内部 `running` 置为假并排队可靠关键帧。即使快照状态已是 `playing`，关键帧尚未收到匹配的 rendered 证据时，逻辑时间也不会继续前进。`accepted` 或 `applied` 回执不能启动逻辑时钟；当当前关键帧的有效 rendered RPC/事件完成确认，才将 `anchor_time` 设为接收时刻并恢复运行。因此 load 后首帧、seek 位置等不会在等待确认期间被后续播放目标赶过。

例如，seek 到 `1.0 s` 后等了 `2 s` 才收到关键帧渲染证明，确认后的第一个 `0.1 s`、倍率 `2` 的播放区间应到达 `1.2 s`。等待的 `2 s` 不计入日志时间。普通流目标的回执不会暂停或重新锚定播放时钟。

默认 `target_hz=60.0`，调用方可以更快或更慢地调用 `tick()`。核心只在到达下一调度边界时生成目标；调用过早不产生新目标，调用延迟则跳过遗漏的节拍并只生成当前最新位置，不补发一串历史帧。流式 outbox 最多保留一个目标，新目标替换尚未取走的旧目标。60 Hz 是目标生成节拍，不保证 Worker、传输、渲染或浏览器达到相同帧率。

调度使用 `interval=1/target_hz`；间隔必须有限且大于零。下一边界通过 `fmod` 余数保持节拍相位，避免计算巨大的遗漏帧数或建立补帧列表。在浮点时钟较大、间隔小于当前可表示精度时，若 `now + offset` 不能前进，则用 `math.nextafter(now, +inf)` 选择下一个可表示时刻。因此相同时钟值反复 tick 不会无限产生新目标。比较边界时仅采用 `min(1e-12, interval * 1e-9)` 的舍入容差；若下一有限时刻无法表示，产生 `INVALID_CLOCK`。时钟回退、非有限值、错误类型或调用异常也属于时钟故障，不能用墙上时间或清零锚点静默恢复。

自然播放到达 `D` 时，`tick()` 进入 `ended`，生成一次可靠末帧和 `pause_barrier`，保留当前 epoch、`control_seq`，增加 `target_seq`。之后重复 tick 不再生成末帧。末帧也必须等待 rendered 证明；状态 `ended` 本身不证明末帧已被渲染。

## 可靠控制与可替换流目标

每次显式控制都会清理上一控制的待发送 RPC、关键帧、流目标和已取出目标账本，建立当前 `(session_id, log_id, playback_epoch, control_seq)` 上下文。较新的显式控制覆盖旧控制是一项明确的策略；旧控制的迟到回执不能释放新控制的等待。已从 outbox 取出的网络消息不能被核心撤回，真实 Worker 仍须用 epoch、control 和 target 顺序屏障拒绝旧消息。

关键帧的 RPC 序列为可选前缀，加 `apply_keyframe`：

| 场景 | 前缀与关键帧 |
| --- | --- |
| 显式 `load` | `load_log` → `apply_keyframe` |
| 当前日志还没有确认加载 | 强制 `load_log` → `apply_keyframe`，包括快速连续的 play/pause/seek 等 |
| 已加载日志的 pause、stop、step、自然末帧 | `pause_barrier` → `apply_keyframe` |
| 已加载日志的其他控制 | `apply_keyframe` |

前缀必须收到当前请求的 `accepted` 回执，才能取出下一个 RPC。每次 `take_rpc()` 只暴露一个请求，而且同一活动请求不会再次被取出。`load_log` 的当前前缀确认才可标记 `loaded_log_id`；被覆盖的旧加载确认无此作用。`apply_keyframe` 即使收到 `accepted` 或 `applied`，仍保持活动且 `critical_pending=True`。当前已取出关键帧的有效 rendered 事件也可以直接完成等待，无须等对应 rendered RPC 抵达。

关键等待期间 `take_target()` 返回空。正常流目标使用 `TargetMessage`，不依赖每帧 RPC 确认；流目标交付成功与否仍需由外部传输处理。`take_target()` 不是发布成功回执，取出之后的证据资格也不是实际网络到达或硬件执行的证明。

有界结构如下：

| 结构 | 上限/寿命 |
| --- | --- |
| 可靠 RPC 序列 | 前缀和关键帧合计最多两个；同一时刻最多一个活动请求 |
| 待发送流目标 | 最多一个，保留最新 |
| 已取出目标账本 | 默认 `capacity=64`，正整数可配置；超过上限按取出顺序淘汰最早目标 |
| 当前关键帧 | 最多一个，新控制替换旧控制 |
| 已观察位置 | 一个 applied 位置和一个 rendered 身份 |

`capacity=64` 限制的是证据账本，不是允许排队 64 个流帧。账本只登记实际经 `take_target()` 取出的流目标，或经 `take_rpc()` 取出的 `apply_keyframe`，排队但尚未取出的目标不具备回执资格。已经淘汰目标的迟到证据会被拒绝。队列清理不重置会话级目标序号、Worker 事件序号和 capture 下界。诊断提供当前队列数量及累计计数，不保存无限增长的历史事件列表。

## 回执验证与证据层级

`receive_rpc()`、`receive_event()` 先按共享 Pydantic 协议验证输入。非法、外会话、旧上下文、未知、重复或倒序回执返回 `False`，不能改变当前位置或完成当前关键等待。

具体检查包括：

1. RPC 必须匹配当前活动请求的 `session_id`、不透明 `request_id`、epoch 和 control。`request_id` 用于相等关联，不用于排序。前缀仅接纳 `accepted`；关键帧阶段只能向 `accepted → applied → rendered` 前进，允许直接收到较高阶段的有效证据。
2. Worker 事件必须属于当前会话，且 `seq` 严格大于此前接纳的事件序号。`ready`、`health` 不产生姿态证明；当前合法 `error` 事件会使会话失败。applied/rendered 事件还必须通过目标证据核验。
3. 帧的会话、日志、epoch、control 必须与当前交付上下文完全一致，`target_seq` 必须存在于已取出的目标账本中。未来 control/epoch/target、旧日志、尚未取出的目标均不能确认当前控制。
4. `log_time_s`、`duration_s`、`progress`、`source_sample_index` 必须与该目标一致，回执 `source_joints` 必须等于目标的原始 `joints`。实际回读 `joints`、`chassis` 可以不同，例如 Worker 的角度表达或实际回读；不能把实际回读冒充源目标值。
5. `applied_position.target_seq` 只前进。rendered 的 `target_seq` 不回退，`capture_id` 必须严格超过会话级 capture 下界。更新 rendered 可以来自落后于最新 applied、但仍在当前上下文账本中的目标，不能把 applied 位置倒退。同一目标如有新的合法 capture 可更新渲染身份，重复 capture 不行。
6. rendered 必须为合法 `RenderedFrame`，包含 `raw_identity_verified=True`，且满足共享协议的 capture/render 身份与时间约束。此字段是 Worker 对真实原始图像身份的声明；T10 只验证协议和已发目标关联，本身没有图像，也不独立完成 GPU/视频身份验证。

超时检查发生在接纳回执之前。到期后才抵达的匹配证明不能救回会话。旧回执不能更新当前 loaded 状态、applied/rendered 位置或释放新请求。

四个层次必须分别表达：

| 层次 | T10 中的含义 | 不能由它推导的结论 |
| --- | --- | --- |
| 控制接受 | 状态机接受控制，生成目标和交付任务 | Worker 已收到或已应用 |
| Worker applied | 已取出目标的有效 `AppliedFrame` 证据 | 图像已渲染 |
| Worker rendered | 已取出目标的有效 `RenderedFrame` 证据 | 编码器或浏览器已经显示该图像 |
| 浏览器 presented | 后续真实视频/浏览器链路的显示证明 | T10 当前没有此证明 |

`snapshot().target_position` 是最后生成的目标意图，而不是调用 snapshot 时按时钟外推的新位置，也不是已取出、已发布、已应用或已显示的证明。运行中两次 tick 之间读 snapshot 仍可看到同一个目标；控制方法可以利用内部投影位置生成更新的目标。`applied_position` 和 `rendered_identity` 则记录已接纳的 Worker 证据，可能落后于最新意图。

尤其不能用最新 `PlaybackSnapshot.state` / `speed` 直接补写一张旧 Worker 帧的 `FrameSnapshot` 元数据：同一 epoch 的普通控制允许快照保留较旧 rendered 身份，而最新控制状态或倍率已经改变。T14 必须按匹配的 target/control 身份关联生成该帧时的状态和倍率，并为自然末帧等同 control、不同 target 的情况保留正确语义。T10 当前不生成 `FrameSnapshot`，也没有可供任意旧帧查询的无限状态历史。

## 超时、运行错误与恢复

关键帧超时默认 `10 s`，从可靠任务进入队列时开始，包含前缀等待、取出之前的等待以及关键帧确认等待。以 `now >= deadline` 判定到期，收到 accepted/applied 不重置时限。由于核心没有后台线程，检查发生于后续控制、tick、取消息或接收回执；单独调用 `snapshot()` 不会触发到期转换。

| 故障 | 结果 |
| --- | --- |
| 当前可靠任务到期 | `WORKER_TIMEOUT`，状态码 `504` |
| 当前 RPC 返回失败、Worker error 事件或 `worker_failed()` | 使用 Worker/调用方错误码和消息，状态码 `502` |
| 时钟失效或回退、调度边界不可表示 | `INVALID_CLOCK`，停止该会话的调度与交付 |
| 安全整数序号耗尽 | `SEQUENCE_EXHAUSTED`，状态码 `500` |

运行故障会进入终态 `error`、停止逻辑时间、关闭 delivery、清空待发送与已取出账本，并保留最后已知位置用于状态诊断。原始错误可从 `timeline.error` 查看；后续正常控制（包括 `load`）均拒绝为 `409 SESSION_ERROR`，不可在原对象内重新加载恢复。应新建会话及对应采样器/Worker 上下文，再从零加载日志。

控制入口发现运行故障时抛出 `TimelineError`；`tick()` 返回错误快照，接收入口返回 `False`（当前 Worker 失败回执本身已被接纳时可返回 `True`），取消息入口返回 `None`。构造时无效配置或时钟会直接拒绝构造。接入层既要检查返回布尔值，也要读取错误状态，不能把“某回执被接纳”理解为“会话仍健康”。

## 外部回调驱动示意

以下是接线伪代码，不是已经实现的 T11/T14 传输。`real_transport`、外部调度器和状态发布函数均为待实现依赖；它们必须发送真实协议消息并把实际收到的回执交回核心。不能为了推进播放而在这些回调里合成 rendered 证明。

```python
# 外部已完成导入和 ReplaySampler 准备；此字典查找不做 I/O。
timeline = Timeline(session_id, prepared_samplers.__getitem__)

def drain_outbox():
    request = timeline.take_rpc()
    if request is not None:
        real_transport.send_rpc(
            request,
            on_response=on_rpc_response,
            on_failure=on_transport_failure,
        )

    target = timeline.take_target()
    if target is not None:
        real_transport.publish_target(target)
        # 发送返回不构造 AppliedFrame/RenderedFrame。

def on_rpc_response(response):
    timeline.receive_rpc(response)
    publish_control_status(timeline.snapshot())
    drain_outbox()  # 前缀确认后才可能取出 apply_keyframe。

def on_worker_event(event):
    timeline.receive_event(event)
    publish_control_status(timeline.snapshot())
    drain_outbox()

def on_transport_failure(code, message):
    timeline.worker_failed(code, message)
    publish_control_status(timeline.snapshot())

def on_timer():
    publish_control_status(timeline.tick())
    drain_outbox()

def on_seek(time_s):
    accepted = timeline.seek(time_s=time_s)  # 异常由服务层映射。
    drain_outbox()
    return accepted  # 未来 REST 包装仍为 ack_stage="accepted"。

# 外部定时器周期调用 on_timer；所有回调在同一串行上下文执行。
# real_transport 的回调表示真实传输结果，不在 send_rpc 内同步伪造确认。
```

取出消息之后，网络发送失败需要由外部适配器明确处理；核心不会自动把同一活动 RPC 再次取出或替代传输重试策略。若 Worker 已失联，应报告失败或等待关键确认超时。普通流目标允许最新覆盖，可靠关键帧则必须完成确认、被新显式控制覆盖，或以错误结束。

## CPU 验证范围

`tests/replay/timeline_support.py` 中的 `ManualClock`、`FakeWorker` 和 `pump()` 仅用于测试。`pump()` 有循环上限，只排空当前可用任务，不推进时钟、不 tick、不 sleep。FakeWorker 模拟 load/pause 屏障和单调 capture；其 `RenderedFrame` 及 `raw_identity_verified=True` 是明确的合成测试声明，没有实际图像采集。

`scripts/validate_timeline.py` 在新的隔离输出目录内生成合成 CSV，通过真实 `LogStore` 导入、真实 `ReplaySampler` 采样和真实 Timeline 控制，然后用测试 FakeWorker 提供合成回执，并写出 `trace.json` 与 `evidence.json`。它不读取仓库 USD/assets 或真实机器人日志，不启动 GPU、IsaacSim、ZMQ、编码器或浏览器。该探针可以证明 CPU 导入、时间轴和证据记账的组合行为，不能作为真实硬件、渲染画面或视频显示验收。

测试入口还分别覆盖状态矩阵、重复控制、边界位置、时钟及调度异常、速度锚定、关键等待、迟到/重复/外上下文证据、有界背压和终态恢复策略。实际测试结果、计数与运行环境由对应验证状态记录维护，本文不把协议测试结果转换为 T11/T14 已完成的声明。
