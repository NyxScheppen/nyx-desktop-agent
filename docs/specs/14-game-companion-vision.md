# 游戏陪玩：多游戏视觉观察与验证

> 本文件定义游戏陪玩第一阶段的“看懂画面”契约：目标游戏窗口捕获、OCR/视觉解析、跨帧稳定、profile 规则验证、证据与置信度、会话事件和记忆闸门。
> 本阶段不做键盘/鼠标模拟，不尝试自动执行游戏选择。
> 本 spec 已获确认；实现以 `nyx/` 与 `frontend/` 源文件为准，并同步维护 `docs/facts/`、`docs/tech-reference.md` 和 `docs/test-inventory.md`。

## 元信息

- **前置依赖**：
  - 01-types：共享枚举、`Event`、`Activity`、`Memory`；
  - 02-config：现有 `VisionConfig`；
  - 03-llm：`VisionClient`、`LlmClient`；
  - 04-module-bus-system：durable event、SSE、运行时和组合根；
  - 06-memory-system：记忆写入、去重和 `MemoryKind.ACTIVITY`；
  - 09-activity：活动生命周期、checkpoint、恢复；
  - 11-expression：`mutter`、`question`、聊天上下文；
  - 10-eval：视觉/语义模型调用的可观测记录。
- **首发平台**：Windows Tauri 桌面版。
- **首发 profile**：`disco_elysium`、`reigns` 和 `generic_text`。
- **实现文件（计划）**：
  - `nyx/activity/screen.py`：只做 Rust frame bridge 的 bytes/尺寸/identity/revision 校验和 fake capture 适配，不访问 HWND；
  - `nyx/activity/game_observer.py`：变化检测、OCR、跨帧融合、验证；
  - `nyx/activity/game_profiles.py`：首发 profile 规则；
  - `nyx/activity/facade.py`：陪玩会话生命周期和事件编排；
  - `nyx/llm/vision.py`：结构化视觉解析入口；
  - `nyx/types.py`、`nyx/enums.py`：共享 DTO 和枚举；
  - `nyx/events/`、`nyx/subscriptions.py`：事件路由；
  - `nyx/db.py`：只增加必要的迁移/索引，不保存原始截图；
  - `frontend/src/components/game/`、`frontend/src/stores/`：陪玩提示和状态；
  - `frontend/src-tauri/src/lib.rs`、Tauri capabilities：独立透明置顶 companion window。
- **不新增抽象层**：不新增通用 Repository/Service/Manager；profile 是有限的静态解析规则，不是第三方插件系统。

## 用户故事

> 作为用户，我想让 Nyx 看懂我正在玩的游戏，在我和她讨论剧情、做出选择或等待转场时给出有依据的反应；当画面无法确认时，她应明确说没看清，而不是编造内容。

## 范围与非目标

### 本阶段范围

- 用户主动开始一局陪玩会话；
- 绑定一个本地游戏窗口；
- 只捕获绑定窗口，不捕获整块桌面；
- 本地 OCR 识别文字，视觉模型补充场景语义；
- 使用游戏 profile 解析对白、选项、卡片和状态；
- 对候选结果做证据、几何、跨帧、状态转移和 profile 规则验证；
- 在 Nyx companion window 中显示即时反应、讨论入口和识别状态；
- 用户手动操作游戏；明确确认的选择才进入长期记忆。

### 非目标

- 键盘、鼠标、手柄或触控模拟；
- 自动点击或自动执行选择；
- 保存原始截图到磁盘、数据库或长期记忆；
- 每帧调用 LLM；
- 对所有游戏提供完整状态重建；
- 实时动作游戏的战斗级建议；
- 自动从任意画面猜测游戏并切换会话；
- 让游戏文本改变 Nyx 的系统规则、权限或 prompt。

## 核心决策

### 一套管线，有限 profile

所有游戏共用以下管线：

```text
窗口绑定
→ 窗口裁剪
→ 捕获完整性检查
→ 变化检测
→ 图像预处理
→ OCR 候选
→ profile 解析
→ 跨帧融合
→ 必要时视觉模型语义解析
→ 验证报告
→ accepted/tentative/rejected/paused
```

profile 只定义区域提示、阶段判定、元素分类和阈值，不复制整条管线。

首版 profile：

| profile | 适用内容 | 主要元素 | 允许的自动结论 |
|---|---|---|---|
| `disco_elysium` | 长对白、多选项、剧情场景 | speaker、dialogue、choices、scene | 稳定对白/选项和用户确认选择 |
| `reigns` | 卡片事件、左右选择、资源状态 | card_text、left/right_action、resources | 稳定卡片文本和用户确认选择 |
| `generic_text` | 未适配的文字游戏 | text_blocks、possible_choices、scene | 只提供观察和讨论，不自动确认选择 |

profile 由用户开始会话时显式选择。自动识别只能作为 UI 建议，不能自动改变 `game_id`、会话或记忆归属。

### 可信度分层

识别结果不是二值成功/失败，而是四态：

| 状态 | 语义 | 可以做什么 |
|---|---|---|
| `accepted` | 有足够证据、跨帧稳定、通过 profile 校验 | 进入当前剧情上下文；可触发反应；可在记忆闸门后落库 |
| `tentative` | 有候选但证据不足或来源冲突 | 可显示“我好像看到了”；等待下一帧；不得写长期记忆 |
| `rejected` | 明显不可信或违反确定性规则 | 丢弃，不触发反应，不进入上下文 |
| `paused` | 捕获条件不满足，不代表内容错误 | 停止识别并提示用户恢复窗口 |

没有证据的模型输出不能变成 `accepted`。

## 共享类型与枚举

以下类型加入 `nyx/enums.py` / `nyx/types.py`；字段以此为唯一实现契约。

```python
class GameProfile(StrEnum):
    DISCO_ELYSIUM = "disco_elysium"
    REIGNS = "reigns"
    GENERIC_TEXT = "generic_text"


class GamePhase(StrEnum):
    UNKNOWN = "unknown"
    EXPLORATION = "exploration"
    DIALOGUE = "dialogue"
    CHOICE = "choice"
    TURN = "turn"
    TRANSITION = "transition"


class ObservationStatus(StrEnum):
    ACCEPTED = "accepted"
    TENTATIVE = "tentative"
    REJECTED = "rejected"
    PAUSED = "paused"


class VisionResultStatus(StrEnum):
    OK = "ok"
    DISABLED = "disabled"
    MALFORMED = "malformed"
    FAILED = "failed"


class CorrectionField(StrEnum):
    DIALOGUE = "dialogue"
    SPEAKER = "speaker"
    CHOICE = "choice"


class GameSessionStatus(StrEnum):
    OBSERVING = "observing"
    PAUSED = "paused"
    ENDED = "ended"


class TextSource(StrEnum):
    OCR = "ocr"
    VISION = "vision"
    USER = "user"


class EvidenceSource(StrEnum):
    OCR = "ocr"
    CROP = "crop"
    GEOMETRY = "geometry"
    TEMPORAL = "temporal"
    USER = "user"
```

DTO 语义：

```python
class PixelRect:
    left: int
    top: int
    right: int
    bottom: int


class WindowCandidate:
    window_id: str  # "hwnd:0x<lowercase-hex>"
    hwnd: int
    pid: int
    process_name: str
    process_path: str | None
    process_start_time_ms: int
    title: str
    client_bounds_physical: PixelRect
    scale_factor: float
    foreground: bool
    minimized: bool


class WindowTarget(WindowCandidate):
    profile: GameProfile
    game_id: str
    profile_version: int


class WindowIdentity:
    window_id: str
    hwnd: int
    pid: int
    process_name: str
    process_start_time_ms: int


class ValidatedFrame:
    capture_id: str
    image_bytes: bytes
    width: int
    height: int
    window_id: str
    expected_revision: int


class GameTextBlock:
    id: str
    text: str
    bbox: tuple[int, int, int, int]
    line_index: int
    confidence: float
    char_confidences: list[float]
    source: TextSource
    evidence_ids: list[str]


class GameChoice:
    id: str
    text: str
    order: int
    bbox: tuple[int, int, int, int] | None
    confidence: float
    evidence_ids: list[str]


class DialogueCorrectionValue:
    text: str


class SpeakerCorrectionValue:
    text: str


class ChoiceCorrectionValue:
    choice_id: str | None
    text: str
    order: int | None


class GameChoiceConfirmation:
    session_id: str
    revision: int
    choice_id: str
    choice_text: str
    confirmed: bool
    event_id: str
    current_observation: AcceptedObservationSnapshot


class GameCorrectionResult:
    session_id: str
    base_revision: int
    correction_id: str
    field: CorrectionField
    value: DialogueCorrectionValue | SpeakerCorrectionValue | ChoiceCorrectionValue
    reason: str
    event_id: str
    applied: bool


class GameCorrectionRequest:
    revision: int
    correction_id: str
    field: CorrectionField
    value: DialogueCorrectionValue | SpeakerCorrectionValue | ChoiceCorrectionValue
    reason: str


class GameSessionState:
    session_id: str
    activity_id: str
    game_id: str
    profile: GameProfile
    profile_version: int
    threshold_version: int
    status: GameSessionStatus
    revision: int
    observation_hash: str | None
    window_identity: WindowIdentity
    last_observation: AcceptedObservationSnapshot | None
    corrections: list[GameCorrectionResult]
    pending_choice: GameChoice | None
    remote_vision_enabled: bool
    error: str | None


class ObservationEvidence:
    id: str
    source: EvidenceSource
    ref: str
    bbox: tuple[int, int, int, int] | None


class GameObservation:
    session_id: str
    game_id: str
    profile: GameProfile
    profile_version: int
    threshold_version: int
    phase: GamePhase
    status: ObservationStatus
    speaker: str | None
    speaker_evidence_ids: list[str]
    dialogue: list[GameTextBlock]
    text_blocks: list[GameTextBlock]
    choices: list[GameChoice]
    visible_entities: list[str]
    entity_evidence_ids: list[str]
    scene_summary: str | None
    scene_evidence_ids: list[str]
    confidence: float
    observation_hash: str
    captured_at: float
    revision: int
    evidence: list[ObservationEvidence]
    uncertainties: list[str]


class AcceptedObservationSnapshot:
    """Durable, bounded, image-free observation snapshot."""

    session_id: str
    game_id: str
    profile: GameProfile
    profile_version: int
    threshold_version: int
    revision: int
    phase: GamePhase
    observation_hash: str
    captured_at: float
    speaker: str | None
    speaker_evidence_ids: list[str]
    dialogue: list[GameTextBlock]
    text_blocks: list[GameTextBlock]
    choices: list[GameChoice]
    visible_entities: list[str]
    entity_evidence_ids: list[str]
    scene_summary: str | None
    scene_evidence_ids: list[str]
    confidence: float
    evidence: list[ObservationEvidence]
    uncertainties: list[str]
```

视觉模型专用 DTO：

```python
class GameImageCrop:
    id: str
    bbox: tuple[int, int, int, int]
    image_bytes: bytes
    mime_type: str  # 固定为 "image/png"


class GameVisionRequest:
    session_id: str
    profile: GameProfile
    profile_version: int
    remote_vision_allowed: bool
    crops: list[GameImageCrop]
    ocr_candidates: list[GameTextBlock]
    previous_observation: AcceptedObservationSnapshot | None


class GameVisionResult:
    status: VisionResultStatus
    phase: GamePhase
    speaker: str | None
    speaker_evidence_ids: list[str]
    dialogue_evidence_ids: list[str]
    choice_evidence_ids: list[str]
    entity_evidence_ids: list[str]
    scene_evidence_ids: list[str]
    visible_entities: list[str]
    scene_summary: str | None
    uncertainties: list[str]
    error_code: str | None
```

`GameVisionResult.status` 是 VisionClient transport/result 状态，不等同于最终
`ObservationStatus`，且不允许增加 `rejected` 成员。观察管线转换规则固定为：

| VisionResult | 观察管线行为 |
|---|---|
| `ok` | 校验 schema/evidence，合并 OCR 后按 score 决定 accepted/tentative/rejected |
| `disabled` | 不产生视觉候选；本地 OCR 独立通过硬门槛时仍可 accepted，否则 tentative/rejected |
| `malformed` | 该次视觉候选为 rejected；不修复、不重试 JSON；本地 OCR 仍继续独立判断 |
| `failed` | 记录 timeout/provider/cancel；丢弃视觉候选，本地 OCR 仍继续；无稳定 OCR 时最多 tentative/rejected |

只有 capture invalid/失焦/黑帧等窗口条件才产生 `ObservationStatus.PAUSED`；Vision 失败本身
不会把 session 静默改成 paused。

`GameImageCrop` 和 `GameVisionRequest` 是仅存在于一次调用内存中的 transient DTO，允许包含
图片 bytes；它们不得进入 event、checkpoint、memory、eval 或 prompt snapshot。`GameObservation`
和 `AcceptedObservationSnapshot` 是 durable/domain DTO，绝不包含图片。

`GameImageCrop` 的限制：最多 4 个 crop；单个 crop 最大 `1600x900`、编码后最大
1 MiB；一次请求总大小最大 3 MiB。只发送目标窗口内的 crop，不发送原始整屏。

`AcceptedObservationSnapshot` 的有界限制：最多 64 个 text blocks、8 个 dialogue blocks、8 个 choices、16
个 visible entities、8 个 uncertainties；text/dialogue 每项最多 512 字符、choice 每项最多 256
字符、scene_summary 最多 512 字符、reason/uncertainty 每项最多 128 字符、evidence 最多 64
项。所有 `id`/`ref` 最多 96 个 UTF-8 字节；每个 evidence id 数组最多 16 项，所有 evidence
引用合计最多 128 项；每个 `char_confidences` 最多 512 项，必须与规范化文本 code point
数量一致，并按最多 3 位小数序列化；uncertainties 合计最多 1024 个 UTF-8 字节。

最终事件预算：使用 canonical JSON（UTF-8、`ensure_ascii=False`、紧凑 separators）序列化
`observation_snapshot` 后必须 `<= 12 KiB`，再加事件 envelope 后必须 `<= 16 KiB`。accepted
前必须执行最终序列化预检查；任一预算超限则本次 observation 不得成为 accepted，也不得
写入 checkpoint/event，返回 `observation_payload_too_large`，继续保留上一 accepted observation。

约束：

- `confidence` 和每个字段置信度都归一化到 `[0.0, 1.0]`；
- `bbox` 使用目标窗口内部坐标，不使用屏幕绝对坐标；
- `dialogue`、`choices` 的每个元素必须能追溯到 `evidence_ids`；
- `visible_entities` 和 `scene_summary` 必须分别引用 `entity_evidence_ids` 和 `scene_evidence_ids`；
- `scene_summary` 可以没有文字证据，但必须引用局部截图证据，并允许为 `tentative`；
- `observation_hash` 由规范化的结构化内容和 profile 生成，不包含时间戳；
- session 创建时 `revision=0`，表示尚无 accepted observation；第一份 accepted observation
  分配 `revision=1`，之后每份新 accepted observation 单调加一；
- transient `GameImageCrop`/`GameVisionRequest` 可以在单次调用内存中持有图片；durable/domain DTO、
  event payload、checkpoint、memory、eval 和日志都不得持有原始图片。
- `GameVisionRequest.ocr_candidates` 最多 128 个 block；单 block 文本最多 512 字符；所有
  OCR 文本 UTF-8 合计最多 16 KiB；每个 block 的 `char_confidences` 最多 512 项。超限时不
  截断 evidence，而是跳过远程 Vision 调用并继续本地 OCR 验证。

`phase` 只表示游戏语义；捕获/识别是否暂停只由 `status` 表示。暂停时不产生新的
`GameObservation` revision，UI 继续显示最近一次稳定 phase。

## 窗口捕获契约

### 绑定

```python
async def list_game_windows() -> list[WindowCandidate]: ...


async def bind_game_window(
    profile: GameProfile,
    game_id: str,
    window_id: str,
) -> WindowTarget: ...
```

`game_id` 是用户确认的稳定 slug（例如 `disco_elysium`、`reigns` 或用户输入的
`my_game`），不是从窗口标题或进程名猜出来的值。profile 不自动决定 game_id；用户可以
选择 `disco_elysium` profile 并给出自定义 game_id，但 profile 与 game_id 会同时写入
session/checkpoint。

Windows `WindowCandidate` 的最小字段：

```text
window_id: "hwnd:0x<lowercase-hex>"
hwnd: u64
pid: u32
process_name: str
process_path: str | None
process_start_time_ms: int
title: str
client_bounds_physical: (left, top, right, bottom)
scale_factor: float
foreground: bool
minimized: bool
```

`list_game_windows` 由 Tauri Rust command 实现，只返回可见、非 Nyx 自身窗口、拥有有效
client area 的顶层窗口。绑定时必须再次读取并校验 `hwnd + pid + process_start_time_ms`；
只匹配标题、复用已销毁 HWND 或 PID 被重用都返回 `window_identity_mismatch`。

绑定结果必须包含：

```text
window_id
process_name
title
bounds
scale_factor
pid
process_start_time_ms
client_bounds_physical
profile_version
```

`WindowIdentity` 只表示可用于恢复校验的 OS 身份；`WindowTarget` 在 identity 之上附加当前
客户区 bounds、scale factor、foreground/minimized、profile、game_id 和 profile_version。
checkpoint/session state 只保存 `WindowIdentity`，恢复时重新枚举得到新的 `WindowTarget`，
再校验 identity；不能把旧 bounds、DPI 或 profile 配置当成仍然有效的 OS 身份。

只以标题匹配不算稳定绑定。首发捕获后端固定为 Windows Graphics Capture（WGC）按窗口捕获；
不使用 BitBlt/PrintWindow 作为静默 fallback。独占全屏、DRM/受保护表面或 WGC 返回黑帧时
进入 `paused`，不退回全屏截图。

### 捕获职责

实际 WGC 捕获全部由 `frontend/src-tauri/src/lib.rs` 的 Windows 原生实现完成；Python
`nyx/activity/screen.py` 不访问 HWND、不创建 WGC/WinRT 对象。Python 只提供：

```python
def validate_bridge_frame(
    target: WindowTarget,
    image_bytes: bytes,
    capture_id: str,
    expected_revision: int,
) -> ValidatedFrame: ...
```

`ValidatedFrame` 是 transient DTO，仅供当前观察调用使用；其 `image_bytes` 不得进入
checkpoint/event/memory/eval/log，也不得跨 await 长期缓存。该函数负责 PNG 解码、尺寸/字节
限制、session/window identity/revision 检查和测试 fake；
真实 WGC frame 的产生、缩放、PNG 编码、frame pool 和句柄释放都在 Rust。

Rust 捕获语义：

- 只捕获目标窗口内容区域；
- 标题栏、任务栏和窗口外区域不进入图片；
- companion window 的区域必须从捕获区域中排除；
- 失败、黑帧、空帧、bounds 越界或窗口已销毁时抛出可分类错误；
- 不允许捕获失败后退回全屏截图；
- 捕获发生在后台线程或等价非 UI 阻塞路径，不阻塞 Tauri 主线程。
- Rust 将窗口客户区缩放到最长边不超过 1920、编码为 PNG；单帧最大 4 MiB，超过则按比例
  缩小，仍超过则丢弃并返回 `capture_too_large`；
- WGC session、frame pool、D3D/WinRT 句柄必须在停止、窗口销毁和异常路径释放；
- 取消使用 capture request id，取消后不得把迟到的 PNG 发送到后端。

Tauri 到 Python 的桥接为本地二进制端点：

```text
POST /api/game-companion/bridge/sessions/{session_id}/frames
Content-Type: image/png
X-Nyx-Capture-Id: uuid
X-Nyx-Window-Id: hwnd:0x...
X-Nyx-Window-Pid: decimal
X-Nyx-Window-Start-Time: decimal-ms
X-Nyx-Expected-Revision: decimal
```

body 只允许 PNG，`Content-Length` 必须为 `1..4 MiB`；session、window identity、revision
不匹配返回 409；过大返回 413；未绑定或已暂停返回 409。桥接端点只接受 Tauri 本地
trusted origin，不能由普通浏览器页面调用。

### 捕获状态检查

以下任一条件成立时生成 `paused`，不运行 OCR/视觉模型：

- 目标窗口不存在、最小化或未完成显示；
- 无法确认前台状态，且 profile 要求前台观察；
- 捕获为空、全黑或尺寸低于 profile 最小尺寸；
- bounds 或 DPI 映射发生变化但尚未重新捕获；
- 画面被系统弹窗或已知 overlay 覆盖，且无法排除；
- companion window 可能污染截图且无法裁剪。

## 变化检测与背压

### hash gate

每一帧先缩放到固定小尺寸，计算窗口 hash 和 profile 关键区域 hash。

- 背景小幅动画不触发完整 OCR；
- 对白、选项、卡片区域明显变化才触发识别；
- 变化检测只作为优化，不能作为可信度证据；
- hash 相同但用户主动请求重新识别时，仍允许强制识别。

### 请求并发

同一 session 同一时刻最多一个主识别任务：

- frame bridge 在 session 级 single-flight 锁已占用时立即返回 `409 frame_busy`，不排队、
  不启动第二个 OCR/视觉任务；客户端应等待下一次采样并继续使用原 durable revision；
- 获取锁后必须重新读取 session/checkpoint，再校验状态、窗口 identity 和
  `expected_revision`，不能复用获取锁前的旧快照；
- OCR、tentative pending 更新、accepted record 必须在该锁保护范围内完成；
- 旧结果不能覆盖新结果、不能触发旧剧情反应、不能写入新选择；已提交的 durable
  checkpoint 不因临时 frame 被丢弃而改变。

## 图像预处理与 OCR

### 预处理

对 profile 指定区域生成有限数量的变体：

1. 原始 RGB；
2. 2x 放大、灰度、对比度增强；
3. 轻度锐化和自适应二值化。

禁止无限尝试预处理或对每一帧运行大量模型。每个区域最多运行三种变体，选择置信度和几何一致性最高的结果。

### 本地 OCR

首版固定使用 `rapidocr_onnxruntime==1.4.4` 单引擎；其 transitive runtime 使用项目锁文件
解析出的 `onnxruntime`，不同时引入 PaddleOCR/Tesseract 作为静默 fallback。模型使用英文
+ 拉丁标点默认包；如果 fixture 证明中文 UI 需要支持，必须
在 profile 中显式选择多语言模型，不自动切换语言包。

OCR 运行约束：

- CPU 为默认执行设备；GPU/DirectML 是显式安装选项，不影响输出契约；
- 首次模型初始化在 session 开始或后台预热完成，不在 Tauri UI 线程执行；
- 单个 crop OCR 超时 2 秒，整帧 OCR 超时 5 秒；
- 初始化/推理失败返回 `ocr_unavailable`，不抛出到主循环；若没有可接受的旧观察，结果为
  `tentative` 或 `rejected`，不自动暂停整个 session；
- RapidOCR 同时只允许一个实际 worker 执行；协程超时不会提前释放 worker gate。底层线程
  结束前，后续请求返回 `ocr_busy`，不再向 executor 提交新任务；线程结束后 gate 才释放；
- OCR 输出的 bbox 是目标客户区像素坐标，行和字符 confidence 归一化到 `[0,1]`；
- 测试注入 fake OCR，不加载真实模型。

引擎必须提供：

- 文本；
- 文字框；
- 行顺序；
- 字符/行置信度；
- 可注入 fake 供测试。

OCR 结果为空不是异常；它只表示当前候选没有识别到文字。

### OCR 硬拒绝规则

以下结果直接 `rejected`，不进入视觉语义阶段：

- 文字框越出窗口或 profile 允许区域；
- 文字框面积、行高、行距明显不可能；
- 文字框互相重叠超过 profile 允许比例；
- 字符以乱码/不可见字符为主；
- 结果行数超过区域可容纳上限；
- 文本长度超过该区域像素宽度在当前字体估计下的上限；
- OCR 结果只在一张过渡帧中出现且没有其他证据。

轻微拼写、标点或大小写差异属于规范化，不属于拒绝。

## Profile 解析

### `disco_elysium`

- `profile_version = 1`；最小客户区为 `960x540`，低于该尺寸仍可捕获但所有文本最多为
  `tentative`；
- 使用客户区归一化坐标（`x1,y1,x2,y2`，范围 `[0,1]`）：
  - `speaker_hint = (0.05, 0.42, 0.95, 0.62)`；
  - `dialogue = (0.05, 0.52, 0.95, 0.86)`；
  - `choice_candidates = (0.05, 0.58, 0.95, 0.98)`；
  - `scene = (0.00, 0.00, 1.00, 0.60)`；
- profile 先在这些区域内 OCR，再将文字框映射回客户区坐标；
- 选项按 `y` 升序排序；若相邻行的 y 差小于 `0.5 * median_line_height`，按 x 升序作为
  次级排序；相邻选项文字框重叠比例 `>0.35` 则该组 `rejected`；
- `speaker_hint` 只提供 speaker 候选，不足以单独确认 speaker；
- 只有对白/选项区域连续稳定后才改变 `phase`；
- 场景和人物只作为视觉模型补充，不以单帧推测写入记忆。

### `reigns`

- `profile_version = 1`；最小客户区为 `800x450`；
- 归一化区域：`card = (0.16, 0.08, 0.84, 0.86)`、`left_action = (0.00, 0.20, 0.30, 0.82)`、
  `right_action = (0.70, 0.20, 1.00, 0.82)`、`resources = (0.00, 0.00, 1.00, 0.20)`；
- 卡片文本按卡片内 y/x 顺序合并；左右选择分别输出 `order=0/1`；
- 资源只接受已注册模板 `people/church/army/treasury` 的图标匹配和相邻数值；模板匹配
  分数 `<0.92` 时输出 `unknown`，不能猜图标；
- 拖动或转场中不确认选择；
- 选择结果必须由下一稳定卡片或用户确认支持；
- 旧卡片残影不能和新卡片合并；卡片 hash 改变超过一次稳定窗口时开启新卡片节点。

### `generic_text`

- `profile_version = 1`；不假定对白框、卡片或固定资源位置；
- OCR 全部游戏内容区域并保留文字框；
- 视觉模型只能返回候选 `text_blocks`、可能的 `choices` 和 `scene_summary`；
- 不自动确认用户选择；
- 不自动把 `tentative` 内容写入记忆。

### profile 不匹配降级

profile 不是自动切换器。若出现以下任意两项：关键区域连续三次为空、区域内文字框几何
违反规则、客户区宽高比变化超过 10%、或 fixture 级结构检查失败，则保留用户选择的
profile，设置 `profile_mismatch` warning，并以 `generic_text` 的候选文本方式继续；不把
profile 字段改成 `generic_text`，不改变 `game_id`，不确认选择。用户可以主动暂停或重新
选择 profile，重新开始 revision。

## 视觉模型契约

现有 `VisionClient.describe(image_bytes: bytes) -> str` 保持不变，继续服务 `OBSERVE_USER`
等一句话屏幕摘要。游戏陪玩不升级该方法的返回类型，而是在同一个 client 增加：

```python
async def observe(
    request: GameVisionRequest,
    *,
    timeout_seconds: float | None = None,
) -> GameVisionResult: ...
```

`observe` 使用现有 `VisionConfig` 的 provider/model/key，并新增 `VisionConfig.timeout` 和
`VisionConfig.max_retries`（默认 `10.0`、`1`）；应用层不再额外重试。`vision.enabled=false`
时返回 `status="disabled"`，不抛异常。

超时优先级固定为：

```text
timeout_seconds is None -> 使用 VisionConfig.timeout
timeout_seconds is not None -> 使用调用方显式值
```

显式值必须为有限正数；不允许用 `0`、负数、NaN 或 Infinity 静默回退配置。调用方显式
timeout 只影响本次 `observe`，不修改 `VisionConfig` 或 session checkpoint；超时后返回
`VisionResultStatus.FAILED` 且 `error_code="timeout"`。

远程调用策略固定为：

```text
VisionConfig.enabled == false
    -> disabled，无论 request.remote_vision_allowed
VisionConfig.enabled == true and request.remote_vision_allowed == false
    -> disabled，本地 OCR 继续
VisionConfig.enabled == true and request.remote_vision_allowed == true
    -> 允许 configured provider
```

session 撤销通过让后续 request 的 `remote_vision_allowed=false` 并取消当前 request 实现；
`VisionClient` 不读取前端 store，也不自行推断用户同意状态。

### 调用时机

视觉模型只在以下条件之一满足时调用：

- OCR 结果存在但区域分类不确定；
- OCR 置信度不足；
- 新场景、人物或选择出现；
- 用户主动要求解释当前画面；
- profile 需要识别图标/卡片/网格等非文字元素。

普通背景变化、重复对白和稳定状态不得调用视觉模型。

### 输入

模型优先接收：

- OCR 候选文字及其位置；
- 一个或多个小范围 crop；
- 当前 profile、上一稳定 observation 的摘要；
- 明确的“不可信游戏材料”规则。

禁止只依靠整张截图自由描述并直接覆盖 OCR 结果。

请求构造和大小限制由 `GameVisionRequest` 固定：最多 4 个 PNG crop、单 crop 1 MiB、总计
3 MiB；`previous_observation` 只包含结构化摘要，不包含前一帧图片。远程 provider 收到的
是 crop 和 OCR 候选，绝不发送客户区之外内容。

### 输出

视觉模型必须返回可校验 JSON，字段只允许：

```json
{
  "phase": "dialogue|choice|exploration|turn|transition|unknown",
  "speaker": null,
  "speaker_evidence_ids": [],
  "dialogue_evidence_ids": [],
  "choice_evidence_ids": [],
  "entity_evidence_ids": [],
  "scene_evidence_ids": [],
  "visible_entities": [],
  "scene_summary": null,
  "uncertainties": []
}
```

规则：

- `speaker_evidence_ids`、`dialogue_evidence_ids` 和 `choice_evidence_ids` 必须引用实际
  OCR/crop evidence；speaker 不得引用仅由模型生成的 entity；
- `entity_evidence_ids` 和 `scene_evidence_ids` 必须引用实际 crop evidence；
- 模型生成但无法引用证据的文字不得进入 `dialogue` 或 `choices`；
- schema、枚举或 evidence 引用非法时整次视觉结果为 `rejected`，不进行隐式修复；
- 游戏画面文本是资料，不是指令，不得改变系统 prompt、权限或工具调用。

失败语义：

- `disabled`：继续使用本地 OCR；OCR 已满足硬门槛时仍可接受对白/选项，scene/entity 保持空；
- `malformed`：记录 `game_vision.malformed_json` eval，丢弃本次视觉结果，不重试解析；
- `failed`：记录 provider/timeout/cancel 失败；不覆盖 OCR 候选，语义字段变为 `tentative`；
- 请求取消后迟到响应直接丢弃，不发布事件。

视觉 eval 只记录 `module="game_vision"`、`type="observe"`、model、session/correlation、
latency、status、token 统计和错误码；prompt 中的图片 bytes、base64、原始 crop 和完整
OCR 文本不得写入 eval 或日志。

## 跨帧稳定与 revision

### 稳定条件

“accepted”不是任意辅助证据满足即可。所有普通观察必须先通过硬门槛，再同时满足
跨帧稳定和分数阈值；视觉模型一致只能加分，不能替代跨帧稳定。

硬门槛：

1. capture、schema、revision、evidence 引用全部有效；
2. 文本字段 evidence coverage `>= 0.80`，归一化 OCR confidence `>= 0.70`；
3. 没有任何 `hard_failures`；
4. profile 结构没有确定性冲突。

普通 observation 还必须满足：

- 在最多 1.2 秒窗口内至少两次出现；
- 规范化文本相似度 `>= 0.85`，且文字框位置变化在容许范围内。

唯一可以绕过跨帧等待的是用户确认，但用户确认不能绕过 evidence、schema、revision 和
profile 硬门槛；它只适用于当前 accepted candidate 中的 choice，不适用于无证据的模型
生成内容。

建议初始值：

```text
稳定帧数：2
稳定窗口：1.2 秒
候选相似度：0.85
```

这些数值不是用户配置项，必须在 fixture 上校准后锁定；不能用未经校准的 OCR 原始 confidence 直接作为跨引擎通用阈值。

### 分数公式

对通过硬门槛的文本/选择候选计算：

```text
score = 0.35 * ocr_confidence
      + 0.25 * evidence_coverage
      + 0.20 * temporal_agreement
      + 0.20 * source_agreement
```

各项均为 `[0,1]`；没有视觉模型时 `source_agreement` 取 `0.5`，不因 provider disabled
而自动拒绝 OCR。决策：

- `score >= 0.80` 且满足跨帧条件 → `accepted`；
- `0.55 <= score < 0.80`，或只有一帧但硬门槛通过 → `tentative`；
- `< 0.55` 或任一硬失败 → `rejected`；
- 捕获不可用 → `paused`。

`ValidationReport.score` 必须保存四项分量、最终分数和阈值版本，不能只保存一个无法解释的数字。

### revision 规则

- 每个 session 创建时 revision 为 `0`；第一份 accepted observation 为 `1`，之后单调递增；
- 新稳定内容、profile 切换、窗口重新绑定都会推进 revision；
- 迟到结果必须携带产生时的 revision；
- revision 不匹配的结果返回 `stale_observation`，不产生任何副作用；
- 相同 `observation_hash` 是幂等 no-op，不重复事件、不重复反应、不重复记忆。

### observation_hash canonicalization

`observation_hash` 只表示可见语义内容，不表示时间、置信度或证据位置。Python 是首发事实
来源，Rust 不自行计算该 hash。算法固定为：

1. 从 accepted snapshot 构造对象，只保留：`game_id`、`profile.value`、`profile_version`、
   `threshold_version`、`phase.value`、`speaker`、有序 `dialogue[].text`、有序
   `text_blocks[].text`、有序 `choices[].text`、规范化后排序的 `visible_entities`、
   `scene_summary`；
2. 排除 `session_id`、`revision`、`captured_at`、`confidence`、所有 bbox、所有 evidence id、
   `uncertainties`、status 和 correction overlay；
3. 所有字符串先做 Unicode NFC，再把连续 Unicode whitespace 合并为一个 ASCII space 并 trim；
4. `dialogue` 和 `choices` 保留游戏显示顺序；`visible_entities` 按 NFC 后的
   `casefold()`、再按原字符串排序；
5. enum 使用 `.value`，`None` 保留为 JSON `null`，列表统一为 JSON array；不包含 float，
   不允许 NaN/Infinity；
6. 使用 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"),
   allow_nan=False)` 得到 UTF-8 bytes；
7. `sha256:<lowercase-hex>` 即为 hash。

固定 fixture：

```text
canonical JSON:
{"choices":["Continue"],"dialogue":["Why?"],"game_id":"disco_elysium","phase":"dialogue","profile":"disco_elysium","profile_version":1,"scene_summary":"A conversation","speaker":"Kim Kitsuragi","text_blocks":[],"threshold_version":1,"visible_entities":["Kim Kitsuragi"]}

observation_hash:
sha256:a383799494a856dad522e83ce523aa727f4943bbfc38153c6d5b34d92c464761
```

任何实现必须用该 fixture 和 Unicode/空白/列表顺序回归测试；不能以 Python dict 默认顺序、
语言默认大小写或 provider 返回的 JSON 文本直接作为 hash。

## 验证报告

内部验证结果使用：

```python
class ValidationReport:
    status: ObservationStatus
    score: float
    score_components: dict[str, float]
    threshold_version: int
    hard_failures: list[str]
    soft_warnings: list[str]
    evidence_ids: list[str]
    checked_revision: int
```

### 硬失败

以下任何一项成立，结果必须 `rejected` 或 `paused`：

- 捕获不可信；
- revision 已过期；
- schema 非法；
- 字段没有证据；
- OCR 几何明显非法；
- 模型返回 OCR/crop 不存在的对白；
- 违反 profile 的确定性结构；
- 当前是转场/拖动/失焦状态；
- 同一帧内存在无法解决的关键字段冲突。

### 软警告

以下情况可以 `tentative`：

- 文字置信度一般但几何合理；
- 场景语义只能由视觉模型推断；
- speaker 不确定但对白稳定；
- 选项文本稳定但用户是否已选择尚未确认；
- 少量标点、大小写或 OCR 拼写差异。

## 记忆与表达闸门

### 事实层级

必须区分：

```text
observed_fact       屏幕上稳定看见的内容
user_confirmed      用户明确确认的选择/修正
nyx_interpretation  Nyx 对剧情的理解
```

`nyx_interpretation` 不能覆盖 `observed_fact`；两者都不能伪装成用户选择。

### 进入当前上下文

- `accepted` observation 可进入当前陪玩上下文；
- `tentative` 只可作为带不确定性提示；
- `rejected`、`paused` 不进入剧情上下文；
- 当前上下文只保留最近稳定节点和有界 checkpoint，不保存原始截图。

### 进入长期记忆

只有以下事件可以产生 memory candidate：

- 用户明确确认的关键选择；
- 用户主动表达的偏好或评价；
- session 结束时的稳定剧情摘要；
- 用户手动纠正的识别结果。

使用 `MemoryKind.ACTIVITY`，topics 至少包含游戏名和 profile；本阶段不新增 `MemoryKind.GAME`。

记忆候选必须携带来源 event id、session id 和 observation revision。整合失败保留 checkpoint，不能丢失已确认事实。

记忆消费者不与选择事件共用一个业务事务：`game_choice_confirmed` 与活动 checkpoint
在同一事务内提交；`memory.game_choice_confirmed` 作为 durable delivery 消费该事件，
使用 `(event_id, consumer_id)` 和 session/revision effect marker 幂等写入 memory。若进程
在事件提交后崩溃，重启由 EventBus 重放；若记忆写入失败，事件保持 retry/dead-letter，
不回滚用户已经确认的选择。

### 选择确认

以下情况不能自动写成用户选择：

- 鼠标悬停；
- 选项高亮但未提交；
- 卡片拖动中；
- 选择文字只出现一帧；
- OCR 只能识别出半个选项；
- 用户操作与识别任务并发且 revision 已改变。

首版由用户点击“我们选了这个”确认；下一稳定画面只用于补充结果，不替代确认动作。

## 会话和事件

陪玩会话唯一使用现有 Activity 承载，不建立独立 session 表或 GameFacade。

实现必须增加 `ActivityType.GAME_COMPANION`，但它只能由显式入口启动，不参与欲望排程；
`ActivityEnergyDelta.game_companion` 固定为 `0`。09-activity 的可恢复类型增加
`GAME_COMPANION`，其 stale recovery 统一转为 `PAUSED`，不自动重新捕获窗口。

`activity.id` 与 `game_session_id` 是两个不同的 UUID：

- `activity.id` 是活动状态机主键；
- `game_session_id` 是陪玩会话主键；
- 二者在 `progress.game_companion` 中互相引用；
- 所有陪玩事件的 `correlation_id = game_session_id`；
- `activity_start/activity_end/activity_interrupted` 的 correlation 也使用 game session id，便于整合。

### 唯一 checkpoint 结构

`Activity.progress["game_companion"]` 固定为：

```json
{
  "session_id": "uuid",
  "game_id": "disco_elysium",
  "profile": "disco_elysium",
  "profile_version": 1,
  "threshold_version": 1,
  "remote_vision_enabled": false,
  "window_identity": {
    "window_id": "hwnd:0x1234",
    "pid": 1234,
    "process_name": "game.exe",
    "process_start_time_ms": 0
  },
  "status": "observing|paused|ended",
  "last_accepted_revision": 7,
  "last_observation_hash": "sha256:...",
  "last_observation": {
    "session_id": "uuid",
    "game_id": "disco_elysium",
    "profile": "disco_elysium",
    "profile_version": 1,
    "threshold_version": 1,
    "revision": 7,
    "observation_hash": "sha256:...",
    "captured_at": 0.0,
    "phase": "dialogue",
    "speaker": "...",
    "speaker_evidence_ids": ["ocr:3"],
    "dialogue": [],
    "text_blocks": [],
    "choices": [],
    "visible_entities": [],
    "entity_evidence_ids": [],
    "scene_summary": null,
    "scene_evidence_ids": [],
    "confidence": 0.91,
    "evidence": [],
    "uncertainties": []
  },
  "corrections": [
    {
      "correction_id": "uuid",
      "base_revision": 7,
      "base_observation_hash": "sha256:...",
      "field": "speaker",
      "value": {"text": "..."},
      "reason": "..."
    }
  ],
  "confirmed_choice_keys": ["7:2"],
  "checkpoint_seq": 8,
  "memory_cursor": {
    "last_event_id": "event-log-id",
    "last_event_kind": "game_choice_confirmed",
    "last_revision": 7,
    "last_correction_id": null
  },
  "ended_at": null
}
```

`window_identity` 是必填的 native 绑定快照，`window_identity.window_id` 必须等于顶层
`window_id`；缺失、PID/启动时间为非正数或两者不一致都拒绝创建会话。这样不会创建一个
随后所有真实 frame 都必然身份不匹配的占位 session。

创建事务尚无 accepted observation 时，`last_accepted_revision=0`、`last_observation_hash=null`、
`last_observation=null`、`corrections=[]`；上面的非空对象展示第一份或后续 checkpoint 的形状。

约束：

- `last_observation` 是完整的 `AcceptedObservationSnapshot` durable copy，不是仅供 UI 的投影；
  因此可以在重启后恢复成完整 accepted observation；另外最多保留 64 个 correction overlay
  和 128 个 choice key；不持久化 tentative、rejected、paused 帧或原始图片；
- `checkpoint_seq` 每次成功写入 accepted observation 或 confirmed choice 加一；
- 游戏 checkpoint mutation 使用 `checkpoint_seq` 条件更新（CAS）；并发的旧对象若未命中
  当前 sequence，不发布事件并返回 `game_state_conflict`。`observation_events` 最多保留最近
  128 个 hash→event 映射；accepted 新 revision 会清理上一 revision 的 choice 幂等索引，
  避免 progress JSON 无界增长。全局视觉关闭时，session 的 `remote_vision_enabled` 强制为
  `false`，不能由 start 请求覆盖。
- profile/version/threshold version 随 checkpoint 保存，恢复时不允许用新 profile 重新解释旧 observation；
- 进程重启从该 progress 恢复 session id、revision 和 hash，不从内存计数器重新开始；
- window identity 只用于恢复时重新校验，不能保证重启后窗口仍存在。

correction overlay 规则：

- 按 `(event_timestamp ASC, event_id ASC, correction_id ASC)` 排序；event id 只作为同 timestamp
  的稳定 tie-breaker，不按字符串数值大小解释；
- 同一 `correction_id` 重试是 no-op；同一 field 的有效投影取排序后最后一条，但旧 overlay
  仍保留直到 64 条上限，用于审计和重放；
- 超过 64 条时删除最旧且已经被 memory cursor 覆盖的 overlay；若最旧 overlay 尚未成功
  整合，则拒绝新 correction 并返回 `correction_storage_limit`；
- `memory_cursor` 指向最后一个已经完成 memory effect 的 durable game event，而不是
  observation revision 或 overlay 数量；初始值为四个字段全为 `null`；
- memory consumer 只在 memory 写入和 effect marker 同一事务提交后推进 cursor；memory 失败、
  retry_wait 或 dead-letter 时 cursor 不前进，重放仍从该 event 继续；
- session 结束整合时，输入按 event cursor 之后的 accepted snapshot、choice 和 correction
  overlay 顺序构造；有效 correction overlay 合并到整合输入，但不改写原 observation event；
  整合成功后 cursor 指向最后一个已处理 event，失败则保留原 cursor 和全部未处理 overlay。

### 生命周期与事务边界

| 操作 | 同一事务内写入 | 崩溃恢复语义 |
|---|---|---|
| start | Activity `PENDING/RUNNING`、初始 progress、`GAME_SESSION_STARTED`、`ACTIVITY_START` | 任一未提交则全部不可见；已提交但未启动捕获时重启为 `PAUSED` |
| accepted observation | progress 新 revision/hash/last observation、`GAME_OBSERVATION` | 已提交事件必可重放；未提交 observation 不存在，不补猜 |
| pause/interrupt | Activity `PAUSED`、progress.status、`ACTIVITY_INTERRUPTED` | 任务取消后重读状态；旧帧不能继续发布 |
| confirm choice | confirmed choice key、progress、`GAME_CHOICE_CONFIRMED` | 事件和 checkpoint 同时存在或同时不可见 |
| stop | Activity `COMPLETED`、ended_at、progress.status=`ended`、`ACTIVITY_END` | 已提交即不重复 finalize；未提交按原状态恢复 |

上述写入必须使用 04-module-bus 的事务内 event append API；禁止先 commit Activity 再
单独 publish 业务事件。Event delivery 展开在业务事务提交后异步进行，不影响本地 checkpoint
原子性。

启动恢复规则：

- 遗留 `PENDING`/`RUNNING` 的 `GAME_COMPANION` 一律转 `PAUSED`，保留同一 activity/session id、
  revision、profile 和最后 accepted observation；不发布新的 `GAME_SESSION_STARTED`；
- 恢复不会自动重新绑定 HWND，也不会自动调用 OCR/视觉模型；用户显式 resume 后重新枚举并
  校验窗口 identity；
- resume 成功复用原 activity/session id，下一 observation revision 从 checkpoint 继续；
- 首次 accepted observation 从 `revision=0` 推进到 `revision=1`；choice confirmation 不创建新
  observation revision；
- 不存在合法 progress、profile_version 或 session_id 时转 `ABANDONED` 并记录可诊断错误，
  不创建新 session 替代旧事实。

### Facade 公开方法

实现放在现有 `ActivityFacade`，不新增 GameFacade：

```python
async def start_game_companion(
    profile: GameProfile,
    game_id: str,
    window_id: str,
    remote_vision_enabled: bool = False,
) -> Activity: ...

async def pause_game_companion(session_id: str) -> None: ...

async def resume_game_companion(session_id: str) -> None: ...

async def stop_game_companion(session_id: str) -> None: ...

async def confirm_game_choice(
    session_id: str,
    revision: int,
    choice_id: str,
) -> GameChoiceConfirmation: ...
```

语义：

- `start` 只接受显式用户操作；绑定失败不创建运行中的活动；
- 同一 session 只能有一个主识别循环；重复 `start` 返回当前活动或明确冲突；
- `pause` 停止捕获和识别，但保留 checkpoint；
- `resume` 重新校验窗口 identity/bounds，不能复用旧 bounds；
- `stop` 先停止捕获任务，再写最后 checkpoint，最后发布 `activity_end`；
- `confirm_game_choice` 必须匹配当前 `session_id + revision + choice_id`，不匹配返回冲突，不能确认旧选项；
- 所有方法必须在副作用前校验当前 session/revision，遵守总线和项目既有 CAS 约定。

`choice_id` 只在 `(session_id, revision, observation_hash)` 内有效。返回：

```json
{
  "session_id": "...",
  "revision": 7,
  "choice_id": "2",
  "choice_text": "...",
  "confirmed": true,
  "event_id": "event-log-id",
  "current_observation": {}
}
```

同一 key 重复确认返回同一个结果且不重复发布事件；同一 revision 确认不同 choice 返回
`choice_already_confirmed`；revision 不是当前值返回 `stale_choice`；choice 不在当前
accepted observation 返回 `choice_not_found`。`confirm_game_choice` 永远只产生
`confirmation_source=user_click`；用户修正走独立 corrections endpoint，不复用 choice confirmation。

确认事件和 `progress.game_companion.confirmed_choice_keys` 在同一个 SQLite 事务内提交，
并通过 EventBus 的事务内 append API 写入 event_log；事务失败时两者都不可见。

事件最小集合：

| 事件 | 产生条件 | payload 必需字段 |
|---|---|---|
| `game_session_started` | 会话绑定成功 | `session_id, activity_id, game_id, profile, profile_version, threshold_version, revision=0, remote_vision_enabled, window_identity` |
| `game_observation` | 新的 `accepted` observation | `session_id, game_id, revision, phase, profile_version, threshold_version, observation_hash, evidence_summary, observation_snapshot` |
| `game_choice_confirmed` | 用户明确确认选择 | `session_id, revision, choice_id, choice_text, confirmation_source` |
| `game_observation_corrected` | 用户修正已观察事实 | `session_id, base_revision, base_observation_hash, correction_id, field, value, reason, source` |
| `activity_end` | 会话结束 | 复用活动事件，`progress.game_companion` 含最终 checkpoint |

以下内容不得进入事件 payload：原始截图、cookie、窗口外文本、未验证的模型生成对白。
`game_observation.observation_snapshot` 是事件对应事实的 immutable copy；异步消费者只能使用
该 snapshot，不能回读会被后续 revision 覆盖的当前 checkpoint。

相同 `(session_id, revision, observation_hash)` 重试必须是幂等 no-op；旧 revision 返回冲突，不冻结或修改新状态。

事件 payload 的最小结构：

```json
{
  "session_id": "...",
  "game_id": "...",
  "profile": "disco_elysium",
  "revision": 7,
  "phase": "dialogue",
  "observation_hash": "...",
  "observation_snapshot": {
    "session_id": "...",
    "game_id": "disco_elysium",
    "profile": "disco_elysium",
    "profile_version": 1,
    "threshold_version": 1,
    "revision": 7,
    "phase": "dialogue",
    "observation_hash": "...",
    "captured_at": 0.0,
    "speaker": "...",
    "speaker_evidence_ids": ["ocr:3"],
    "dialogue": [],
    "text_blocks": [],
    "choices": [],
    "visible_entities": [],
    "entity_evidence_ids": [],
    "scene_summary": null,
    "scene_evidence_ids": [],
    "confidence": 0.91,
    "evidence": [],
    "uncertainties": []
  },
  "evidence_summary": {
    "accepted_fields": ["speaker", "dialogue"],
    "evidence_count": 4,
    "status": "accepted"
  }
}
```

`game_choice_confirmed` 额外包含：

```json
{
  "choice_id": "2",
  "choice_text": "...",
  "confirmation_source": "user_click"
}
```

`game_observation_corrected` payload 固定为：

```json
{
  "session_id": "...",
  "base_revision": 7,
  "base_observation_hash": "sha256:...",
  "correction_id": "uuid",
  "field": "dialogue|speaker|choice",
  "value": {"text": "..."},
  "reason": "...",
  "source": "user"
}
```

`correction_id` 是用户请求携带的幂等键；同一 session/correction_id 重试返回同一个 event，
不重复记忆。correction 不推进 observation revision、不改变原 observation_hash，因为它是
用户注释而不是新捕获事实；它以 bounded overlay 写入 progress，并由 memory 消费者引用
correction event，而不是覆盖原 observation event。

`value` 按 field 判别：

```json
{"field":"dialogue", "value":{"text":"..."}}
{"field":"speaker", "value":{"text":"..."}}
{"field":"choice", "value":{"choice_id":"2", "text":"...", "order":1}}
```

dialogue/speaker 的 `text` 最多 512 字符；choice 的 `choice_id` 最多 64 字符、`text` 最多
256 字符、`order` 为 `null` 或 `0..7`。自然语言修正必须先由 UI 映射为上述结构，后端不
解析自由格式。

后端执行 discriminated validation，不依赖 Python union 的自动猜测：

```text
field == CorrectionField.DIALOGUE
    -> value 的 key 集合必须正好为 {"text"}
field == CorrectionField.SPEAKER
    -> value 的 key 集合必须正好为 {"text"}
field == CorrectionField.CHOICE
    -> value 的 key 集合必须正好为 {"choice_id", "text", "order"}
```

其中 dialogue/speaker 的 `text` 为非空 trimmed string；choice 的 `choice_id` 可为 null，
`text` 为非空 trimmed string，`order` 为 null 或 `0..7`。额外字段、缺失字段、错误类型、
空文本和与 field 不匹配的对象统一返回 `invalid_correction_value`，不写 overlay、不写事件。
Pydantic/手写解析必须先读取 `field`，再选择唯一 schema；禁止尝试 union 分支直到成功。

事件只保存结构化摘要和引用 id，不保存 crop、base64、完整 OCR 原文或未确认场景解释。

### 总线接线

`nyx/enums.py` 必须增加以下 `EventType` 成员，值与表中相同；事件由后端验证后以
`Source.INTERNAL` 写入，`correlation_id` 固定为 `game_session_id`：

| EventType | RouteSpec consumer | 行为 |
|---|---|---|
| `GAME_SESSION_STARTED` / `game_session_started` | 无 | durable + SSE，供前端初始化；不创建 delivery |
| `GAME_OBSERVATION` / `game_observation` | `expression.game_observation` | durable + SSE；仅 accepted；handler 按冷却和 phase 决定是否发 mutter |
| `GAME_CHOICE_CONFIRMED` / `game_choice_confirmed` | `memory.game_choice_confirmed`、`expression.game_choice_confirmed` | durable + SSE；记忆消费者写活动记忆，表达消费者可生成一次反应 |
| `GAME_OBSERVATION_CORRECTED` / `game_observation_corrected` | `memory.game_observation_corrected` | durable + SSE；写入用户修正，不重写原事件 |

`expression.game_observation` 和 `memory.game_choice_confirmed` 都必须从 event payload 的
immutable snapshot/choice fields 读取事实；禁止在 handler 中按 session id 回读当前
`last_observation` 代替 event snapshot。

对应 handler 公开入口固定为：

```python
async def on_game_observation(event: Event) -> None: ...
async def on_game_choice_confirmed(event: Event) -> None: ...
async def on_game_observation_corrected(event: Event) -> None: ...
```

同名函数分别注入 expression/memory consumer；handler 不重新截图、不调用视觉模型确认旧
结果，只消费 event payload。`GAME_SESSION_STARTED` 只广播，不注册 handler。

RouteSpec 必须由 04-module-bus 的单一路由表派生；不允许在 `subscriptions.py` 手写旁路
订阅。`GAME_SESSION_STARTED` 没有消费者但仍进入 event_log 和 SSE；另外三个事件各有稳定
consumer id、delivery、重试和 dead-letter 语义。

`GAME_SESSION_STARTED` 包含前端初始化所需的 `activity_id` 和 `remote_vision_enabled`，前端
收到后可以直接 hydrate session；`GET /api/game-companion/sessions/{id}` 只用于 SSE 丢失、应用重启或主动刷新，
不是正常启动事件的必需第二次请求。

事件校验：

- `event_type`、`source`、`correlation_id`、session/game/profile/revision 类型必须匹配；
- `game_observation` 只允许 `status=accepted`，`evidence_summary` 至少包含 accepted fields、
  evidence_count、profile_version 和 threshold_version；
- `choice_confirmed` 必须有当前 revision 的 choice id/text 和 confirmation source；
- payload UTF-8 JSON 最大 16 KiB；超过返回 413/受理失败，不截断；
- 所有事件都广播 SSE；SSE 丢失不影响 durable event 和 delivery；
- 事件重放不重新调用视觉模型、不重新确认选择；消费者必须使用 EventBus effect marker 幂等。

### 必须同步的既有契约

实现这份 spec 时必须在同一变更中更新下列既有 spec；不能只新增本文件：

- `01-types.md`：加入 `GameProfile`、`GamePhase`、`ObservationStatus`、`GAME_*` EventType；
- `02-config.md`：加入 `VisionConfig.timeout`、`VisionConfig.max_retries`，以及
  `ActivityEnergyDelta.game_companion=0` 的校验；
- `03-llm.md`：保留 `VisionClient.describe(bytes)->str`，新增 `observe(GameVisionRequest)->GameVisionResult`；
- `04-module-bus-system.md`：加入上述四个事件的 RouteSpec、consumer id、SSE 和 effect marker 规则；
- `09-activity.md`：增加 `GAME_COMPANION`，禁止 scheduler 自动映射，纳入 stale recovery 的可恢复类型，
  并引用本文件的 progress 结构；
- `11-expression.md`：定义 game observation/choice 的 mutter、聊天上下文和 stale context 失败语义；
- `06-memory-system.md`：明确 activity memory 的 source event、session/game topics 和修正事件幂等。

在这些同步完成前，不得开始实现或宣称事件/恢复契约已经闭合。

## 关键 bad case 与处理契约

### 捕获与平台

| 场景 | 必须行为 |
|---|---|
| 游戏窗口失焦/最小化 | `paused`，停止 OCR/LLM |
| 独占全屏返回黑帧 | `paused`，不使用全屏 fallback |
| 多显示器/DPI 变化 | 重新读取 bounds，丢弃旧坐标结果 |
| companion overlay 被截入 | 排除 overlay 区域；无法排除则 `paused` |
| Steam/Discord 弹窗遮挡 | 标记污染；不产生确定事实 |
| 游戏进程退出 | 结束 session，保存最后 durable checkpoint |
| 窗口标题变化 | 以 process/window identity 校验，不单靠标题 |
| 捕获调用慢 | 后台执行，不能阻塞 Tauri UI 线程 |

### OCR 与视觉

| 场景 | 必须行为 |
|---|---|
| 字体描边/阴影 | 有限预处理变体；不无限重试 |
| 打字机效果/滚动 | `tentative`，等待稳定 |
| OCR 乱码/区域越界 | `rejected` |
| OCR 与视觉模型冲突 | 优先保留 OCR 证据，字段降为 `tentative` 或拒绝 |
| 模型补写屏幕外对白 | 无 evidence，硬拒绝 |
| 图标替代文字 | 允许视觉模型解释，但必须引用 crop，默认 `tentative` |
| 多语言/混排 | profile 明确语言；无法确认时降低置信度 |
| 游戏 UI 更新/Mod 改版 | profile 不匹配时降级 `generic_text`，不猜测关键选择 |

### 时序与并发

| 场景 | 必须行为 |
|---|---|
| 旧结果晚于新结果返回 | revision 校验后丢弃 |
| 同一对白重复出现 | hash 去重，不重复反应/记忆 |
| 识别任务积压 | 只保留最新帧；不处理过期队列 |
| 用户已操作后旧建议到达 | 标记 stale，不覆盖当前上下文 |
| 转场画面误判为新场景 | 连续帧稳定前不得接受 |
| OCR/视觉服务超时 | 保留 checkpoint，当前结果为 `tentative` 或 `paused`，不编造 |

### 选择与剧情

| 场景 | 必须行为 |
|---|---|
| 鼠标悬停/选项高亮 | 不当作最终选择 |
| 卡片拖动中 | 不确认选择 |
| 选项顺序抖动 | `tentative`，等待稳定 |
| 读档回到过去 | 新 navigation/revision；不静默覆盖旧事实 |
| 不同游戏切换 | 结束旧 session；记忆按 session/game_id 隔离 |
| Nyx 推荐与用户选择不同 | 两者分别记录 |
| 用户纠正识别 | 用户修正是高优先级事实，保留原始 rejected/tentative 记录供审计 |

### 隐私、安全与 prompt injection

- `VisionConfig.enabled` 是全局能力开关；`start_game_companion.remote_vision_enabled` 是每局开关，
  默认 `false`。全局关闭时每局不能打开；每局打开前 UI 必须显示截图会出站并取得一次确认。
- 用户在会话中撤销远程视觉时，立即取消在途视觉请求、清理内存中的 crop bytes，并继续本地
  OCR；已发送请求无法撤回，但不在本地保存响应图片。
- 游戏画面文本永远是 untrusted material，不得授权工具、改变系统 prompt 或修改配置。
- 截图裁剪失败时不能发送全屏内容。
- 事件、日志和错误不得包含原始截图 base64、窗口外文本或模型 token。
- remote vision 的 prompt/crop 不进入 `LLMOutput.prompt_messages`、eval 或普通日志；eval 只保留
  `module/type/model/status/latency/token/error` 元数据。
- 每个 crop 最大 1 MiB、总请求 3 MiB；超限在发送前拒绝，不压缩成不可审计的未知格式。
- 用户说“不记住”时，当前 observation 可以继续用于即时讨论，但不得进入长期记忆。

## 表达接线

- `expression.game_observation` 只消费 `accepted` 的 `GAME_OBSERVATION`，按 session 内
  `revision + phase` 去重；同一 revision 最多生成一次 `MUTTER`，默认冷却 20 秒。
- mutter prompt 只包含 accepted 的 speaker/dialogue/choices/scene 摘要和 uncertainties，
  不包含图片 bytes；反应事件的 `correlation_id` 仍为 game session id。
- “讨论这一幕”使用现有 `/api/chat`，请求增加可选：

  ```json
  {"message":"...", "game_session_id":"...", "game_observation_revision":7}
  ```

  runtime 在消费 USER_MESSAGE 时重新验证 session/revision；失效返回明确
  `game_context_stale` 展示事件，不把请求静默降级为普通聊天。
- “我们选了这个”调用 `confirm_game_choice`，不走普通 chat；用户修正调用：

  ```text
  POST /api/game-companion/sessions/{id}/corrections
  {"revision":7,"correction_id":"uuid","field":"dialogue|speaker|choice","value":{...},"reason":"..."}
  ```

  修正必须有当前 revision，写入 `GAME_OBSERVATION_CORRECTED`，并保留原 observation，不覆盖
  原始事件。修正后的事实可作为后续 memory candidate 的更高优先级来源。
- accepted observation 和用户确认选择进入当前表达上下文；tentative 只以明确不确定语气
  注入；rejected/paused 不进入 prompt。

## 前端契约

Tauri 窗口固定 label 为 `game-companion`，复用 `Avatar.tsx` 的 pointer capture 和拖动持久化
策略。所有原生命令都返回显式错误通道：

```text
type NativeGameResult<T> = Result<T, NativeGameError>

class NativeGameErrorCode(StrEnum):
    WINDOW_ENUMERATION_FAILED = "window_enumeration_failed"
    COMPANION_WINDOW_CREATE_FAILED = "companion_window_create_failed"
    COMPANION_WINDOW_POSITION_FAILED = "companion_window_position_failed"
    COMPANION_WINDOW_NOT_FOUND = "companion_window_not_found"
    COMPANION_WINDOW_LOST = "companion_window_lost"

game_list_windows() -> NativeGameResult<WindowCandidate[]>
game_companion_open() -> NativeGameResult<()>
game_companion_close() -> NativeGameResult<()>
game_companion_set_visible(visible: bool) -> NativeGameResult<()>
game_companion_set_position(x: f64, y: f64) -> NativeGameResult<()>
```

`NativeGameError` 固定为 `{code: NativeGameErrorCode, message: str}`；命令不得跨 IPC 抛
未结构化异常。前端只按 `code` 分支，不能按 message 文本匹配。
`x/y` 使用 Tauri 逻辑像素（DIP），允许负值表示左侧/上侧显示器；Rust 负责按目标显示器
scale factor 转换为物理像素。`WindowCandidate.client_bounds_physical` 始终是物理像素。

命令语义：

- `game_list_windows` 排除 Nyx 主窗口、companion window 和最小化/无客户区窗口；枚举失败返回
  `window_enumeration_failed`；
- `game_companion_open` 若 label 已存在且可用则 show/focus 并返回成功；句柄已销毁则清理旧
  registry 后重建；创建失败返回 `companion_window_create_failed`；
- `game_companion_close` 对不存在的 label 是幂等成功；
- `game_companion_set_visible` 对已关闭窗口返回 `companion_window_not_found`；
- `game_companion_set_position` 使用当前窗口 monitor 的 DIP 坐标，定位失败返回
  `companion_window_position_failed`；
- companion window 崩溃/Destroyed 时不自动恢复捕获；前端收到 `companion_window_lost`，
  backend session 进入 paused，用户显式重新 open/resume。

React `gameCompanionStore` 至少保存：

```text
sessionId, gameId, profile, profileVersion, activityId,
status, phase, revision, observationHash, dialogue, choices,
sceneSummary, uncertainties, pendingChoice, remoteVisionEnabled, error
```

新增 REST/bridge：

```text
POST /api/game-companion/sessions
GET  /api/game-companion/sessions/{id}
POST /api/game-companion/sessions/{id}/pause
POST /api/game-companion/sessions/{id}/resume
POST /api/game-companion/sessions/{id}/stop
POST /api/game-companion/sessions/{id}/choice
POST /api/game-companion/sessions/{id}/corrections
POST /api/game-companion/bridge/sessions/{id}/frames
```

请求/响应固定为：

```json
POST /api/game-companion/sessions
{
  "game_id": "disco_elysium",
  "profile": "disco_elysium",
  "window_id": "hwnd:0x1234",
  "remote_vision_enabled": false,
  "window_identity": {
    "window_id": "hwnd:0x1234",
    "hwnd": 4660,
    "pid": 1234,
    "process_name": "game.exe",
    "process_start_time_ms": 1700000000000
  }
}
// 201
{
  "session_id": "uuid",
  "activity_id": "uuid",
  "game_id": "disco_elysium",
  "profile": "disco_elysium",
  "profile_version": 1,
  "threshold_version": 1,
  "status": "observing",
  "revision": 0,
  "window_identity": {}
}
```

pause/resume/stop 的 request 都是 `{ "expected_revision": 7 }`，response 都是
`GameSessionState`；pause 返回 `status="paused"`，resume 返回 `status="observing"`，stop
返回 `status="ended"`。

```json
POST /api/game-companion/sessions/{id}/choice
{"revision":7,"choice_id":"2"}
// 200 GameChoiceConfirmation

POST /api/game-companion/sessions/{id}/corrections
{
  "revision": 7,
  "correction_id": "uuid",
  "field": "dialogue|speaker|choice",
  "value": {"text": "..."},
  "reason": "..."
}
// 200 GameCorrectionResult
```

`GET /api/game-companion/sessions/{id}` 返回 `GameSessionState`，其中 `last_observation` 是完整
`AcceptedObservationSnapshot | null`，另带 bounded corrections、pending choice 和当前
status；不返回图片。

`POST /api/game-companion/bridge/sessions/{id}/frames` 成功返回：

```json
{
  "accepted": true,
  "status": "accepted|tentative|rejected|paused",
  "revision": 7,
  "observation_hash": "sha256:...",
  "validation": {"score": 0.91, "hard_failures": [], "soft_warnings": []}
}
```

当本地 OCR 尚未可用时，frame 仍只返回明确的 `accepted=false`、`status="rejected"`、
`error_code="ocr_unavailable"` 和 validation，不返回成功但无效果的空 observation；不写入
checkpoint/event。真实 OCR worker 可用后，accepted/tentative/rejected 按观察管线处理。

相同 accepted 画面再次提交时，若其 hash 已是 durable checkpoint 的当前 hash，响应仍可
返回 `accepted=true`，但 `revision` 和 `observation_hash` 必须返回 durable checkpoint 的
值，不得返回未写入的候选 revision；该重放不得追加新的 `game_observation` 事件。

同一 session 的另一帧正在 OCR/视觉处理时，bridge 返回 `409 frame_busy`；这不是 session
revision 冲突，客户端不得推进 revision。accepted snapshot 或事件 envelope 超过预算时，
返回 `413 observation_payload_too_large`，保留上一份 durable observation。

所有 mutation 请求必须携带 session/revision（start 除外）；response 返回当前 revision 和
状态。SSE 的 `GAME_*` 帧按 session id 更新 store；丢失 SSE 后 store 通过
`GET /api/game-companion/sessions/{id}` 重新读取当前 checkpoint，不重放图片。

HTTP 错误统一为：

```json
{
  "error": {
    "code": "stale_choice",
    "message": "choice belongs to an old revision",
    "session_id": "uuid",
    "current_revision": 8
  }
}
```

SSE `GAME_*` 帧统一为：

```json
{
  "event": "game_observation",
  "id": "event-log-id",
  "timestamp": 0.0,
  "correlation_id": "session-uuid",
  "content": {}
}
```

REST/bridge 错误码固定为：

```text
400 invalid_profile | invalid_game_id | invalid_payload | invalid_correction_value
404 session_not_found | choice_not_found
409 stale_choice | choice_already_confirmed | session_state_conflict
    window_identity_mismatch | stale_observation | overlay_polluted
    correction_storage_limit | game_context_stale | frame_busy
413 capture_too_large | payload_too_large | observation_payload_too_large
422 capture_invalid | profile_mismatch
409 game_state_conflict
503 ocr_unavailable | vision_unavailable | database_unavailable
```

错误码与 HTTP status 是固定映射，不允许 endpoint 自行选择：

| HTTP | 错误码 | 语义 |
|---|---|---|
| 400 | `invalid_correction_value` | correction field/value 判别校验失败 |
| 409 | `correction_storage_limit` | 未整合 overlay 已达到 64 条，不能安全淘汰 |
| 409 | `game_context_stale` | chat 消费时 session/revision 已失效，禁止普通回复降级 |
| 409 | `frame_busy` | 同一 session 已有识别任务，当前 frame 未排队或执行 |
| 413 | `observation_payload_too_large` | accepted snapshot 或 event envelope 超过序列化预算 |

409 必须返回当前 session 状态和 revision（不返回图片）；413 必须在 JSON/Pydantic 解析前
按 `Content-Length` 拒绝（bridge body）或在 accepted 最终序列化预检查时拒绝
（observation payload）；503 不得将当前 accepted checkpoint 清空。

companion window 规则：

- 当前识别状态：观察中/没看清/已暂停；
- Nyx 一句即时反应；
- “讨论这一幕”；
- “我们选了这个”；
- 暂停/恢复观察；
- 当前 profile 和游戏名。

提示卡不得遮挡已绑定游戏窗口的关键区域。默认不 click-through，允许拖动；若与游戏客户区
重叠超过 5%，Rust 将 frame 标记为 `overlay_polluted` 并暂停捕获，不把 overlay 像素当作游戏内容。
隐藏或暂停后不再捕获窗口。

## 测试要点

### 单元测试

- 窗口 bounds 与 DPI 坐标转换；
- 捕获失败/黑帧/空帧分类；
- hash 去重和变化阈值；
- OCR 文字框几何校验；
- 文本规范化与相似度；
- 连续帧稳定器；
- revision/CAS 迟到结果丢弃；
- evidence 引用校验；
- profile 阶段和区域解析；
- `accepted/tentative/rejected/paused` 决策；
- 用户选择确认幂等；
- 不同 session/game_id 记忆隔离。
- score 四项分量和 threshold_version 序列化；单帧 OCR+vision 一致只能 tentative；
- user confirmation 只绕过跨帧等待，不绕过 evidence/profile/revision 硬门槛；
- `GameTextBlock`、scene/entity evidence 引用完整且非法引用必拒绝；
- `GameVisionResult.speaker_evidence_ids` 从 Vision JSON/fake 正确传递到 observation 和 snapshot；
- `event_id` 在所有 DTO/API/SSE 中统一为 `str`；
- correction value 按 field 使用结构化 union，不接受裸字符串；
- session-start event 可以直接 hydrate `activity_id` 与 remote-vision 状态；
- canonical hash fixture、Unicode NFC、空白、排序和 enum serialization 回归通过；
- `VisionClient.observe` 的 disabled/malformed/failed/cancelled 分支；
- RapidOCR 初始化/单 crop 超时和最大尺寸限制。
- `ValidatedFrame` 字段、transient 生命周期和图片不落盘约束；
- `NativeGameErrorCode` 封闭值与前端错误分支；
- `WindowIdentity` 恢复校验不复用旧 bounds/DPI/profile。
- `observe(timeout_seconds=None)` 使用 VisionConfig.timeout；显式有限值覆盖配置；非法值拒绝；
- correction field/value discriminated validation：额外键、缺键、错误 union 分支均拒绝；
- fixture manifest/golden/calibration 版本缺失或不匹配时 fail fast；
- correction overlay 排序、同 field 最后有效投影、64 条上限和 memory cursor 不前进/推进；

### Fixture 集

fixture 输入是实现的一部分，不能只留在 spec 外部。固定目录为
`tests/fixtures/game_companion/`：

```text
tests/fixtures/game_companion/
  manifest.json
  disco_elysium/
    dialogue_basic/input.png
    dialogue_basic/ocr.json
    dialogue_basic/golden.json
    choice_three/input.png
    choice_three/ocr.json
    choice_three/golden.json
    transition/input.png
    transition/ocr.json
    transition/golden.json
  reigns/
    card_left_right/input.png
    card_left_right/ocr.json
    card_left_right/golden.json
    drag_in_progress/input.png
    drag_in_progress/ocr.json
    drag_in_progress/golden.json
  generic_text/
    scattered_text/input.png
    scattered_text/ocr.json
    scattered_text/golden.json
  failures/
    black_frame/input.png
    overlay_polluted/input.png
    malformed_vision/vision.json
```

每个 case 的 `golden.json` 至少包含：

```json
{
  "case_id": "disco_elysium/dialogue_basic",
  "profile": "disco_elysium",
  "profile_version": 1,
  "expected_status": "accepted",
  "expected_phase": "dialogue",
  "expected_text_blocks": [],
  "expected_dialogue": [],
  "expected_choices": [],
  "expected_speaker": null,
  "expected_scene_summary": null,
  "expected_hard_failures": [],
  "expected_soft_warnings": []
}
```

`ocr.json` 使用 bridge 后的客户区像素坐标，字段为 `text/bbox/line_index/confidence/char_confidences`；
不在 fixture 中调用真实 OCR。`manifest.json` 固定记录 `fixture_version`、图片尺寸、DPI、
语言、profile version 和阈值版本。fixture 不包含用户隐私、账号、窗口外内容或未授权商业
游戏存档；需要真实游戏素材时只使用用户提供且已脱敏的截图。

至少准备：

- 《极乐迪斯科》对白、选项、转场、不同窗口尺寸截图；
- 《王权/Reigns》卡片、左右选择、拖动中、资源变化截图；
- 未适配游戏的通用文字截图；
- 黑帧、失焦、overlay 污染、DPI 变化、过渡帧；
- OCR 乱码、模型无 evidence、OCR/视觉冲突；
- 读档分支和重复对白序列。

### 阈值校准记录

`tests/fixtures/game_companion/calibration.json` 是唯一的阈值校准记录，格式固定为：

```json
{
  "calibration_version": 1,
  "fixture_version": 1,
  "profiles": {
    "disco_elysium": {
      "profile_version": 1,
      "stable_frames": 2,
      "stable_window_seconds": 1.2,
      "text_similarity": 0.85,
      "accepted_score": 0.80,
      "tentative_score": 0.55,
      "evidence_coverage": 0.80,
      "ocr_confidence": 0.70
    },
    "reigns": {
      "profile_version": 1,
      "stable_frames": 2,
      "stable_window_seconds": 1.2,
      "text_similarity": 0.85,
      "accepted_score": 0.80,
      "tentative_score": 0.55,
      "evidence_coverage": 0.80,
      "ocr_confidence": 0.70
    },
    "generic_text": {
      "profile_version": 1,
      "stable_frames": 2,
      "stable_window_seconds": 1.2,
      "text_similarity": 0.85,
      "accepted_score": 0.80,
      "tentative_score": 0.55,
      "evidence_coverage": 0.80,
      "ocr_confidence": 0.70
    }
  },
  "dataset_cases": ["..."],
  "measured_at": "2026-09-18T00:00:00Z",
  "notes": "..."
}
```

实现启动时必须读取与 profile/version 匹配的 calibration record；缺失、版本不匹配或字段
非法时 fail fast，不使用代码内隐式默认值。更新阈值必须递增 `calibration_version` 和
`threshold_version`，并重新运行全部 profile fixture；旧 checkpoint 继续使用其保存的版本。

### 集成测试

- fake capture + fake OCR + fake VisionClient 的完整观察管线；
- 视觉模型超时不阻塞会话；
- 旧 revision 不能发布事件或写记忆；
- accepted observation 只发布一次；
- tentative/rejected 不进入长期记忆；
- 用户确认选择后写入一次 `game_choice_confirmed`；
- session 结束后 checkpoint 可恢复且不重复整合。
- Activity stale recovery 将 GAME_COMPANION 置为 PAUSED，窗口 identity 失效时不自动捕获；
- Activity progress 与 game event 在同一事务内提交，模拟 commit 前/后的崩溃恢复；
- EventType、RouteSpec、SSE、consumer effect marker 和 dead-letter 重放；
- 讨论请求消费时 session/revision 失效返回 `game_context_stale`，不普通回复；
- Tauri window enumeration、WGC 黑帧、DPI、overlay overlap 和 frame bridge 413/409。

### E2E / Windows

- 真实 Tauri companion window 不遮挡主窗口；
- 绑定、暂停、恢复、关闭游戏窗口；
- 多显示器/DPI 下截图坐标正确；
- companion window 不进入游戏截图；
- 慢 OCR/视觉调用不冻结 UI；
- 真实 fixture 窗口只验证捕获和事件，不要求安装完整商业游戏作为 CI 依赖。

### 性能与质量目标

- 前台/窗口状态检查不超过 1 秒粒度；
- 普通静止画面不触发 OCR/视觉重复调用；
- 同一 session 不出现并发主识别任务；
- 从稳定画面变化到 `GameObservation` 的 P95 延迟由 fixture 测量并记录；
- stale 结果覆盖 accepted 结果次数必须为 0；
- 低置信度结果写入长期记忆次数必须为 0。
- 同一 `(session_id, revision, observation_hash)` 事件最多一次；
- accepted snapshot 和 event envelope 的 serialized UTF-8 budget 检查为硬门槛；
- 进程重启后 `session_id/revision/profile_version` 与最后 checkpoint 一致；
- 无远程视觉时不会产生图片出站请求；用户撤销后在途请求被取消、后续只走 OCR。
- correction memory 写失败时 cursor 不前进，重放后成功才推进；session end 将有效 overlay
  合并到整合输入但不改写原 observation event。

## 实施顺序

1. 同步 01/02/03/04/09/11/06 既有契约，固定 GAME_COMPANION Activity 承载；
2. 完成 `rapidocr_onnxruntime==1.4.4`、WGC 和 Python 3.12 打包 smoke test；
3. 实现窗口枚举、绑定、捕获完整性检查、frame bridge 和 hash gate；
4. 接入本地 OCR，并完成 `generic_text`；
5. 实现跨帧稳定、证据校验、score/revision 丢弃；
6. 增加 `disco_elysium` profile；
7. 增加 `reigns` profile；
8. 增加 `VisionClient.observe` 慢路径和 eval；
9. 接入 Activity progress、事务内事件、RouteSpec、表达和记忆闸门；
10. 接入 companion window、REST/SSE store；
11. 用 fixture 和 Windows smoke test 校准 threshold/profile version。

## 待审核决策

以下项目仍需审核，但不再保留互斥实现方向：

1. **OCR 依赖落锁**：算法和引擎已经固定为 `rapidocr_onnxruntime==1.4.4`；实现前仍需在项目 Python 3.12/打包环境完成依赖 smoke test，并锁定 transitive versions。
2. **远程视觉**：本地 OCR 默认开启、远程/云端视觉默认关闭；若启用远程视觉，必须在 UI 中提示截图会出站，并且仍不得持久化原图。
3. **阈值校准**：`2 帧 / 1.2 秒 / 0.85 相似度` 是初始候选，不是未经 fixture 验证的永久常量；最终值必须由 Disco、Reigns 和 generic fixture 共同校准，并递增 `threshold_version`。
4. **profile 版本**：profile 规则变化会影响识别事实；活动 checkpoint、observation、event 和 memory source 都保存 `profile_version`，旧版本结果不能被新规则静默重解释。

## 完成定义

- [ ] 目标窗口裁剪失败不会退回全屏；
- [ ] OCR、视觉模型、profile、跨帧验证均有可注入测试；
- [ ] 所有 accepted 字段都有 evidence；
- [ ] tentative/rejected/paused 不产生确定剧情记忆；
- [ ] stale revision 不产生任何副作用；
- [ ] 《极乐迪斯科》和《王权/Reigns》共享核心管线且各自通过 fixture；
- [ ] 原始截图不持久化、不进入事件 payload；
- [ ] 用户选择与 Nyx 建议可区分；
- [ ] `ruff check` 零报错；
- [ ] `pyright` 零报错；
- [ ] `pytest` 全绿；
- [ ] `docs/facts/`、`docs/tech-reference.md`、`docs/test-inventory.md` 与实现同步；
- [ ] 质量门前查阅并按需更新 `docs/LessonsLearned.md`。
