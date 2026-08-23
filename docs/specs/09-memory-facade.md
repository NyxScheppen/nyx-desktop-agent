# 记忆门面（Facade）

> 范围：`memory/facade.py`（`MemoryFacade`：场景化记忆创建 + 检索委托 + 想起升级 + 新鲜度衰减/容量淘汰 + 导出）。
> Facade 层 spec：记忆生命周期（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；纯 CRUD 在 07（`MemoryStore`）、三层检索在 08（`MemoryRetrieval`）、embedding 工厂在 08（`build_embed`）。
> 矛盾检测走「embedding 召回门控 + 独立单任务 LLM 调用」：召回 top-K 候选、相似度过阈值才 +1 调用，无候选则 0 调用（design §5.3）。
> **本文件自包含**：`MemoryFacade` 完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `Event` / `EventType` / `MemoryType` / `Source`）、02-config（`MemoryConfig`：`short_term_capacity` / `promote_threshold` / `freshness_decay`）、03-llm（`LlmClient.complete`）、05-event（`EventBus.publish`）、07-memory-store（`MemoryStore`）、08-memory-retrieval（`MemoryRetrieval` / `EmbedFn` / `rank_by_cosine`）、15-eval（`Evaluator`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `MemoryFacade` 把记忆生命周期的三件事（场景化记忆创建、想起升级、新鲜度淘汰）加上检索委托与导出统一成一个门面，以便表达管道（17）只调 `search` / `create_scene_memory` / `record_recall`、仪表盘只调 `list_memories` / `export`；场景记忆与矛盾检测分离成两次调用，矛盾检测只在召回候选过阈值时才发起（调用数可控、判断单任务），所有 LLM 调用和事件发布都走可注入的 client / bus。

## 验收标准

- [ ] `facade.py` 含 `MemoryFacade` + `decay_freshness` / `_parse_scene` / `_build_scene_prompt` / `_has_negation` / `_content_preview` / `_build_contradiction_prompt` / `_parse_contradiction` / `_join_list` / `_activity_memory_fields` / `_memory_to_dict` / `_memory_to_markdown`，与「`memory/facade.py`（完整）」段代码逐字一致
- [ ] 六个公开方法签名与 tech-ref §5 逐字一致：`create_scene_memory(reply_context: dict[str, str]) -> Memory` / `remember_activity(event: Event) -> None` / `search(query) -> list[Memory]` / `record_recall(memory_id) -> None` / `list_memories(tag, type) -> list[Memory]` / `export(fmt) -> str`
- [ ] `create_scene_memory`：LLM 调用 1（`json_mode=True`、`module="memory"`、`output_type="scene_memory"`）产出三样 → 入短期（`freshness=1.0`）→ 算 embedding → 建边 → 矛盾检测（门控，可能调用 2）→ 命中矛盾发布 `reflection` → 衰减+淘汰 → 发布 `memory_created` → 返回 `Memory`
- [ ] `remember_activity(event)`：读 `event.content["type"]`/`["result"]` 确定性映射（reading→note/book、creation→content/title、free_exploration→notes/findings，tag=活动类型值）；读书/创作/探索三类有产出才写，其余（rest/observe_user/idle_reflection、空 result）跳过；走 embed→入短期→建边→门控矛盾检测→淘汰→发布 `memory_created` 同一条管线，**无 LLM 调用**（除门控触发的矛盾判断）
- [ ] `remember_knowledge(items, correlation_id)`：读书提取的客观知识点入长期记忆（`tag="knowledge"`、`type=LONG_TERM`、无 LLM、确定性拼好）；items 每项 `{topic, content}`，content 空则跳过；复用 `_persist_memory` 入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰），`type=LONG_TERM` 豁免短期淘汰、知识点不随时间冲掉、供创作检索参考（`list_memories(tag="knowledge")`）
- [ ] 矛盾检测门控：`embedding=None` 或召回 top-K 候选相似度全低于 `_CONTRADICTION_SIM_THRESHOLD` → **0 次**矛盾 LLM 调用；有候选过阈值 → **1 次**矛盾 LLM 调用（`output_type="contradiction"`），单任务判 `conflicts_with`
- [ ] 两处 LLM 产出后紧跟 `await evaluator.evaluate(output)`：`create_scene_memory`（`output_type="scene_memory"`）与 `_detect_contradiction`（`output_type="contradiction"`，仅门控触发时）
- [ ] 三杠杆落地：候选判据用 `summary + content 前 N 字`（非只 summary）；召回 `_RECALL_TOP_K=5`；新记忆含否定/转折词时矛盾 prompt 附「重点核对」提示（`_has_negation` 纯函数）
- [ ] `record_recall`：`recall_count+1`；短期满 `promote_threshold` 次升级长期 + 发布 `memory_promoted`；长期不重复升级
- [ ] `decay_freshness` 纯函数：线性衰减、下限 0、`now < created_at` 不变
- [ ] `search` / `list_memories` 纯委托（不重写 SQL）；`export` 支持 `json` / `md`，非法 `fmt` → `ValueError`
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/memory/facade.py`（无 API、无数据变更、无新表）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`）
- **公开面**：`from nyx.memory.facade import MemoryFacade`（不加 `__all__`；`decay_freshness` / `_parse_scene` 等 helper 私有或纯函数，纯函数优先测全）
- **依赖注入**：7 个构造参数（`store` / `retrieval` / `bus` / `llm` / `evaluator` / `config` / `embed`）。这是 Facade 层的 DI 构造器（跨 5 个前置 spec 的注入点），不是「>3 参数就该拆解」的场景——每个都是单一职责的外部依赖。`embed` 与 `retrieval` 共享**同一实例**（组合根 18-api 注入），创建时算 embedding、检索时读 embedding 用同一模型
- **生命周期归 09**：新鲜度衰减、容量淘汰是 09-facade 的生命周期逻辑；短期→长期升级的「何时升」（阈值）在 09、原子「加一 + 条件升型」在 07 的 `record_recall`（单锁，见 07 边界）。本 spec 落这三件事：
  - **升级**：`record_recall` 委托 store 的原子 `record_recall(memory_id, promote_threshold)`（单锁「加一 + 条件升型」，避免跨方法竞态）→ 返回 `True` 则发布 `memory_promoted`
  - **淘汰**：短期数量 > `short_term_capacity` → 挤掉 `freshness` 最低的短期（平局按 `created_at` 早的优先，避免稳定排序误删最新）
  - **衰减**：见下「新鲜度衰减」
- **`create_scene_memory` 流程**（design §5.3）：
  1. `_build_scene_prompt(reply_context)` → `llm.complete(json_mode=True, output_type="scene_memory")` **一次**产出 `{content, tag, summary}`（回归三样，场景记忆调用不再承担矛盾判断）
  2. `_parse_scene` 纯函数解析校验（结构非法 `ValueError`，错误可溯源）
  3. `Memory(id=uuid4, created_at=now, freshness=1.0, type=SHORT_TERM, ...)`；`embed` 非空则算 `embedding` 存列（持久化，同 07/08 决策）
  4. `store.add` → 一次 `_similar` 全表余弦排序（建边 top-3 与矛盾召回 top-5 共用，避免重复扫描）→ `_build_edges` → `_detect_contradiction`（门控，可能 +1 调用；best-effort，失败 log 后跳过 reflection 不反噬创建）→ 命中矛盾 publish `reflection` → `_decay_and_evict` → publish `memory_created`
- **`remember_activity` 流程**（design §8.2/§8.6 活动记忆，落地的确定性写入）：
  1. `_activity_memory_fields(event.content["type"], event.content["result"])` 确定性映射，非读书/创作/探索类型或空 result → `None` 直接 return（不调 LLM、绝不编造）
  2. `Memory(freshness=1.0, type=SHORT_TERM, tag=活动类型值)`；`embed` 非空则算 `embedding`
  3. 复用 `store.add` → `_similar` → `_build_edges` → `_detect_contradiction`（门控，可能 +1 调用）→ `_decay_and_evict` → publish `memory_created`——与 `create_scene_memory` 同一条入库管线，只缺开头的 LLM 场景构建
- **矛盾检测 = 门控 + 独立单任务调用（决策：C 方案，准确率优先，已与用户确认）**：召回候选（embedding 余弦 top-K）做**门控**，判断交给**独立 LLM 调用**——单任务判矛盾，准确率最高，代价是**有条件地 +1 调用**。门控**无损**：矛盾 ⟹ 语义相近（"喜欢猫" vs "讨厌猫"同话题才矛盾），不同话题的旧记忆不可能与新记忆矛盾，所以「相似度过阈值才判断」不损失准确率，只省掉无谓调用。`embedding=None`（未启用向量层）→ 直接跳过
- **三杠杆（决策：B 方案，不增调用，已与用户确认）**：
  1. **候选判据用全文截断**：`_content_preview` 给 `summary + content 前 60 字`（非只 summary），矛盾常藏细节，判据更实 → 漏报↓
  2. **召回 `_RECALL_TOP_K=5`**：比建边的 `_EDGE_TOP_K=3` 大，减少漏召回；两者值不同，故分开常量
  3. **否定词规则预筛**：`_has_negation` 纯函数检测新记忆是否含否定/转折锚点（`不`/`没`/`别`/`讨厌`/`恨`/`拒绝`/`否认`/`放弃`/`再也不` 等），命中则在矛盾 prompt 附「重点核对是否推翻旧记忆」提示，把模型注意力引到最可疑方向。**软信号非判定**：`不`/`没` 高频、会误命中，但只增一句提示、不影响门控，模型自己看内容裁决——误报无害、漏报才有害
- **门控阈值 `_CONTRADICTION_SIM_THRESHOLD=0.6`（决策，可推翻）**：sentence-transformers 余弦同话题中文约 0.6–0.9、不同话题约 0.1–0.4，0.6 作「同话题」分界合理；要调翻一处
- **建边与矛盾候选复用 `_similar`（跨模块去重）**：`_similar(query_vec, exclude_id)` 是「排除某 id 后、query 向量 vs 全表记忆余弦排序（`s>0` 保留、降序）」的共享 helper；建边取 `[:_EDGE_TOP_K]`、矛盾门控取 `[:_RECALL_TOP_K]` 再 `s >= threshold` 过滤。两处各自调用（余弦 O(N)、本地 ≤ 几百条，代价可忽略，不值得为省这点把 scored 传参破坏两方法内聚）。核心「打分+过滤+排序」循环不在 facade 重写——复用 08 抽出的 `rank_by_cosine` 纯函数（`_similar` 只做 exclude + 委托，与 08 `_vector_search` 同一份实现，facade 不再直接 import `cosine`）
- **`reply_context` 契约**：`dict[str, str]`，键 `correlation_id`（溯源）/ `user_message`（用户说了什么）/ `nyx_think`（尼克斯内心）/ `nyx_speak`（尼克斯说了什么）——由 17-expression 慢通道填充。缺键 `KeyError`（fail-fast，契约违反立即暴露，不静默降级）
- **建边机制（决策，可推翻）**：新记忆与已有记忆按 `embedding` 余弦相似度建边，`_EDGE_TOP_K=3`、`weight=相似度`、`s > 0` 才建；`embed=None` 或新记忆无 embedding → 跳过。方向 `new → old`，`MemoryGraph` 无向所以方向无关
- **新鲜度衰减（决策，可推翻）**：纯函数 `decay_freshness(freshness, created_at, now, rate) = max(0, freshness - rate × elapsed_days)`，`rate` 单位「/天」（`SECONDS_PER_DAY=86400.0`，共享常量见 events/event.py；02-config 的 `freshness_decay=0.01` 未标单位，此处定为「0.01/天」，要改单位翻 events/event.py 一处）。触发点 = `create_scene_memory` 的 `_decay_and_evict` 扫描：读全表 → 收集变化 → 一次 `store.update_many` 批量回写 → 短期满则一次 `store.delete_many` 批量挤掉最低新鲜度（平局按 `created_at` 早的优先）。**局限**：两次创建之间新鲜度不变；但衰减单调（越旧越衰减），相对顺序保持，「长期只新鲜度下降、检索排后」的语义不破坏。2 次 commit/次创建（N 条衰减不再 N 次 commit），本地单用户 ≤ 几百条记忆，可接受
- **事件 content（tech-ref §4 未定义这两者的 SSE payload，最小化）**：`memory_created` / `memory_promoted` = `{"memory_id": id}`；`reflection` = `{"summary": str}`（含冲突双方 id 的可溯源字符串）。SSE 完整 payload 形状归 18-api/frontend 细化
- **`record_recall` 的 `correlation_id`（已知局限）**：tech-ref 签名只有 `record_recall(memory_id)`，无上游 correlation，故 `memory_promoted` 的 `correlation_id = memory_id`（溯源到记忆本身，与触发它的 reply 因果链在此断开）。`memory_created` / `reflection` 用 `reply_context["correlation_id"]` 接上 reply 链
- **`search` / `list_memories` 纯委托**：不重写 SQL、不做二次过滤；衰减/淘汰已在写入侧处理
- **新增 `output_type="contradiction"`**：落 `TokenUsage.purpose`（01-types 该字段是自由字符串，design 注释 `reply / scene_memory / desire / reflection / ...` 属开放集合，新增无冲突）

### `memory/facade.py`（完整）

```python
import json
import logging
import time
from typing import Any, cast
from uuid import uuid4

from nyx.config import MemoryConfig
from nyx.enums import EventType, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval, rank_by_cosine
from nyx.memory.store import MemoryStore
from nyx.types import Event, Memory

_SCENE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "把下面这段对话写成一条第一人称场景记忆（尼克斯视角）：用户说了什么、你内心怎么想、最后说了什么。"
    "只输出 JSON，键：content（正文）、tag（标签）、summary（一句话总结），"
    "三者都是非空字符串。"
)

_CONTRADICTION_SYSTEM = (
    "你是记忆一致性检查员。给出一条新记忆和若干候选旧记忆，判断新记忆是否与其中某条"
    "在同一话题上结论明显矛盾（例如喜欢/讨厌、做过/没做过、相信/不相信）。"
    "只输出 JSON，键：conflicts_with（若与某条候选矛盾，填那条记忆的 id；"
    "否则填 null）。"
    "不要把「不同话题」误判为矛盾，也不要把「细节不同」误判为矛盾。"
)

_EDGE_TOP_K = 3
_RECALL_TOP_K = 5
_CONTRADICTION_SIM_THRESHOLD = 0.6
_CONTENT_PREVIEW_CHARS = 60
_NEGATION_WORDS = ("不", "没", "别", "讨厌", "恨", "拒绝", "否认", "放弃", "再也不")


def decay_freshness(
    freshness: float, created_at: float, now: float, rate: float
) -> float:
    """新鲜度线性衰减（rate/天），下限 0。纯函数。"""
    elapsed_days = max(0.0, now - created_at) / SECONDS_PER_DAY
    return max(0.0, freshness - rate * elapsed_days)


def _parse_scene(raw: str) -> tuple[str, str, str]:
    """解析场景记忆 LLM 的 JSON 产出 → (content, tag, summary)；
    结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"场景记忆 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    content = parsed.get("content")
    tag = parsed.get("tag")
    summary = parsed.get("summary")
    if not isinstance(content, str) or not content:
        raise ValueError("场景记忆 JSON 缺 content 或非空字符串")
    if not isinstance(tag, str) or not tag:
        raise ValueError("场景记忆 JSON 缺 tag 或非空字符串")
    if not isinstance(summary, str) or not summary:
        raise ValueError("场景记忆 JSON 缺 summary 或非空字符串")
    return content, tag, summary


def _build_scene_prompt(ctx: dict[str, str]) -> str:
    """reply_context → 场景记忆 prompt。纯函数；缺键 KeyError（fail-fast）。"""
    return (
        f"用户说：{ctx['user_message']}\n"
        f"你心里想：{ctx['nyx_think']}\n"
        f"你回答说：{ctx['nyx_speak']}\n"
    )


def _has_negation(text: str) -> bool:
    """新记忆正文是否含否定/转折锚点
    （软信号，非判定：命中则矛盾 prompt 提示重点核对）。纯函数。"""
    return any(w in text for w in _NEGATION_WORDS)


def _content_preview(m: Memory) -> str:
    """候选旧记忆预览：summary + content 前 N 字
    （矛盾判断的判据，而非只给 summary）。"""
    if len(m.content) <= _CONTENT_PREVIEW_CHARS:
        body = m.content
    else:
        body = m.content[:_CONTENT_PREVIEW_CHARS] + "…"
    return f"{m.summary} | {body}"


def _build_contradiction_prompt(memory: Memory, candidates: list[Memory]) -> str:
    """新记忆 + 候选旧记忆（预览）→ 矛盾判断 prompt。纯函数。"""
    lines = [
        f"新记忆：{memory.content}",
        "\n候选旧记忆：",
    ]
    lines.extend(f"- [{m.id}] {_content_preview(m)}" for m in candidates)
    if _has_negation(memory.content):
        lines.append("\n提示：新记忆含否定/转折语气，请重点核对是否推翻了某条旧记忆。")
    return "\n".join(lines)


def _parse_contradiction(raw: str) -> str | None:
    """解析矛盾判断 LLM 的 JSON 产出 → conflicts_with（旧记忆 id 或 None）。

    conflicts_with 类型错（数字/对象）抛 ValueError；缺键视为 None（无矛盾，
    安全默认——漏报优于误报）。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"矛盾判断 JSON 应是对象，得到 {type(data).__name__}")
    conflicts_with = cast(dict[str, Any], data).get("conflicts_with")
    if conflicts_with is not None and not isinstance(conflicts_with, str):
        raise ValueError("矛盾判断 JSON 的 conflicts_with 应是字符串或 null")
    return conflicts_with


def _join_list(value: Any) -> str:
    """list[str] → 换行拼接；str → 原样；None/空 → 空串。纯函数。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x) for x in cast(list[object], value))
    return ""


_SUMMARY_MAX_CHARS = 80


def _activity_memory_fields(
    activity_type: object, result: object
) -> tuple[str, str, str] | None:
    """活动 result → (content, summary, tag)；非读书/创作/探索或空 result → None。

    不调 LLM：直接取活动真实产出，绝不凭空编造。
    """
    if not isinstance(activity_type, str) or not isinstance(result, dict):
        return None
    parsed = cast(dict[str, Any], result)
    if activity_type == "reading":
        content_key, summary_key = "note", "book"
    elif activity_type == "creation":
        content_key, summary_key = "content", "title"
    elif activity_type == "free_exploration":
        content_key, summary_key = "notes", "findings"
    else:
        return None
    content = _join_list(parsed.get(content_key))
    summary = _join_list(parsed.get(summary_key))
    if not content.strip() or not summary.strip():
        return None
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS] + "…"
    return content, summary, activity_type


def _memory_to_dict(m: Memory) -> dict[str, Any]:
    return {
        "id": m.id,
        "created_at": m.created_at,
        "content": m.content,
        "tag": m.tag,
        "summary": m.summary,
        "freshness": m.freshness,
        "type": m.type.value,
        "recall_count": m.recall_count,
        "aspect": m.aspect,
        "embedding": m.embedding,
    }


def _memory_to_markdown(m: Memory) -> str:
    return f"## {m.summary}\n\n{m.content}\n\n标签：{m.tag}"


class MemoryFacade:
    """记忆模块门面：场景化记忆创建 + 检索 + 想起升级 + 新鲜度衰减/淘汰 + 导出。

    生命周期逻辑（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；
    纯 CRUD 在 MemoryStore（07）、三层检索在 MemoryRetrieval（08）。
    矛盾检测走「embedding 召回门控 + 独立单任务 LLM 调用」，无候选则 0 调用。
    """

    def __init__(
        self,
        store: MemoryStore,
        retrieval: MemoryRetrieval,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: MemoryConfig,
        embed: EmbedFn | None = None,
    ) -> None:
        self._store = store
        self._retrieval = retrieval
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._config = config
        self._embed = embed          # 与 retrieval 共享同一实例（组合根注入）
        self._logger = logging.getLogger(__name__)

    async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
        """慢通道场景化记忆：LLM 产出 content/tag/summary
        → 入短期 → 建边 → 门控矛盾检测 → 淘汰。"""
        output = await self._llm.complete(
            [
                {"role": "system", "content": _SCENE_SYSTEM},
                {"role": "user", "content": _build_scene_prompt(reply_context)},
            ],
            module="memory",
            output_type="scene_memory",
            correlation_id=reply_context["correlation_id"],
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        content, tag, summary = _parse_scene(output.content)
        now = time.time()

        memory = Memory(
            id=str(uuid4()),
            created_at=now,
            content=content,
            tag=tag,
            summary=summary,
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
            recall_count=0,
            aspect=[],
            embedding=None,
        )
        if self._embed is not None:
            memory.embedding = await self._embed(content)

        await self._store.add(memory)

        # 一次全表余弦排序，建边（top-3）与矛盾召回（top-5）共用，避免重复扫描
        scored: list[tuple[float, Memory]] | None = None
        if memory.embedding is not None:
            scored = await self._similar(memory.embedding, memory.id)
        await self._build_edges(memory, scored)
        await self._detect_contradiction(
            memory, scored, reply_context["correlation_id"]
        )

        await self._decay_and_evict(now)

        await self._bus.publish(
            internal_event(
                EventType.MEMORY_CREATED,
                {"memory_id": memory.id},
                reply_context["correlation_id"],
            )
        )
        return memory

    async def remember_activity(self, event: Event) -> None:
        """活动记忆：把 activity_end.result 落成一条短期记忆（无 LLM）。

        只写读书/创作/探索三类有产出的活动；rest/observe_user/idle_reflection
        （result 空或类型不匹配）跳过。入库管线与 create_scene_memory 相同
        （embed → 建边 → 门控矛盾检测 → 淘汰），只缺开头的 LLM 场景构建。
        """
        mapped = _activity_memory_fields(
            event.content.get("type"), event.content.get("result")
        )
        if mapped is None:
            return
        content, summary, tag = mapped
        now = time.time()

        memory = Memory(
            id=str(uuid4()),
            created_at=now,
            content=content,
            tag=tag,
            summary=summary,
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
            recall_count=0,
            aspect=[],
            embedding=None,
        )
        if self._embed is not None:
            memory.embedding = await self._embed(content)

        await self._store.add(memory)

        scored: list[tuple[float, Memory]] | None = None
        if memory.embedding is not None:
            scored = await self._similar(memory.embedding, memory.id)
        await self._build_edges(memory, scored)
        await self._detect_contradiction(memory, scored, event.correlation_id)

        await self._decay_and_evict(now)

        await self._bus.publish(
            internal_event(
                EventType.MEMORY_CREATED,
                {"memory_id": memory.id},
                event.correlation_id,
            )
        )

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str
    ) -> None:
        """读书提取的客观知识点入长期记忆（tag='knowledge'，无 LLM，确定性拼好）。

        items 每项 {topic, content}；content 空则跳过。复用 _persist_memory
        入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰）。type=LONG_TERM 使其
        豁免短期淘汰，知识点不随时间冲掉，供创作时检索参考（list_memories）。
        """
        for item in items:
            content = (item.get("content") or "").strip()
            topic = (item.get("topic") or "").strip()
            if not content:
                continue
            memory = Memory(
                id=str(uuid4()),
                created_at=time.time(),
                content=content,
                tag="knowledge",
                summary=topic or content[:_SUMMARY_MAX_CHARS],
                freshness=1.0,
                type=MemoryType.LONG_TERM,
                recall_count=0,
                aspect=[],
                embedding=None,
            )
            await self._persist_memory(memory, correlation_id)

    async def search(self, query: str) -> list[Memory]:
        return await self._retrieval.search(query)

    async def record_recall(self, memory_id: str) -> None:
        """记录一次「想起」：recall_count+1；
        短期满 promote_threshold 次升级长期并发布 memory_promoted。"""
        promoted = await self._store.record_recall(
            memory_id, self._config.promote_threshold
        )
        if promoted:
            await self._bus.publish(
                internal_event(
                    EventType.MEMORY_PROMOTED, {"memory_id": memory_id}, memory_id
                )
            )

    async def list_memories(
        self,
        tag: str | None = None,
        type: MemoryType | None = None,
    ) -> list[Memory]:
        return await self._store.list_memories(tag, type)

    async def export(self, fmt: str) -> str:
        """记忆导出：json = JSON 数组字符串，
        md = 每记忆一个「## 总结 + 正文 + 标签」段。"""
        memories = await self._store.list_memories()
        if fmt == "json":
            return json.dumps(
                [_memory_to_dict(m) for m in memories], ensure_ascii=False, indent=2
            )
        if fmt == "md":
            return "\n\n".join(_memory_to_markdown(m) for m in memories)
        raise ValueError(f"未知导出格式 {fmt!r}（应为 json/md）")

    async def _detect_contradiction(
        self,
        memory: Memory,
        scored: list[tuple[float, Memory]] | None,
        correlation_id: str,
    ) -> None:
        """门控矛盾检测：召回 top-K 相似候选，相似度过阈值的才发独立 LLM 判断；
        无候选或全低于阈值 → 0 调用跳过。命中矛盾 → 发布 reflection。"""
        if scored is None:
            return
        candidates = [
            m for s, m in scored[:_RECALL_TOP_K] if s >= _CONTRADICTION_SIM_THRESHOLD
        ]
        if not candidates:
            return
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _CONTRADICTION_SYSTEM},
                    {
                        "role": "user",
                        "content": _build_contradiction_prompt(memory, candidates),
                    },
                ],
                module="memory",
                output_type="contradiction",
                correlation_id=correlation_id,
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            conflicts_with = _parse_contradiction(output.content)
        except Exception:
            # 矛盾检测是 best-effort：记忆已合法入库，判矛盾失败
            # （传输超时/5xx、JSON 结构非法）不反噬记忆创建主流程，
            # 只跳过本次 reflection（漏报优于误报，同 _parse_contradiction 默认）。
            self._logger.exception(
                "矛盾检测失败 memory_id=%s correlation_id=%s",
                memory.id, correlation_id,
            )
            return
        if conflicts_with is not None:
            await self._bus.publish(
                internal_event(
                    EventType.REFLECTION,
                    {
                        "summary": (
                            f"场景记忆 {memory.id} 与旧记忆 {conflicts_with} 矛盾，"
                            "触发反思"
                        )
                    },
                    correlation_id,
                )
            )

    async def _similar(
        self, query_vec: list[float], exclude_id: str | None = None
    ) -> list[tuple[float, Memory]]:
        """query 向量与全表记忆的余弦排序
        （s>0 才保留，可排除某 id）；纯计算 + store 读。"""
        memories = [
            m for m in await self._store.list_memories() if m.id != exclude_id
        ]
        return rank_by_cosine(query_vec, memories)

    async def _build_edges(
        self, memory: Memory, scored: list[tuple[float, Memory]] | None
    ) -> None:
        """新记忆与已有记忆按 embedding 余弦相似度建边（top-K，weight=相似度）。"""
        if scored is None:
            return
        for s, m in scored[:_EDGE_TOP_K]:
            await self._store.upsert_edge(memory.id, m.id, s)

    async def _decay_and_evict(self, now: float) -> None:
        """新鲜度统一衰减（回写）+ 短期容量淘汰（满则挤掉最新鲜度最低的）。"""
        memories = await self._store.list_memories()
        changed: list[Memory] = []
        for m in memories:
            decayed = decay_freshness(
                m.freshness, m.created_at, now, self._config.freshness_decay
            )
            if decayed != m.freshness:
                m.freshness = decayed
                changed.append(m)
        if changed:
            await self._store.update_many(changed)
        short_term = [m for m in memories if m.type is MemoryType.SHORT_TERM]
        if len(short_term) > self._config.short_term_capacity:
            short_term.sort(key=lambda m: (m.freshness, m.created_at))
            overflow = len(short_term) - self._config.short_term_capacity
            overflow_ids = [m.id for m in short_term[:overflow]]
            await self._store.delete_many(overflow_ids)
```

## 测试要点

- [ ] 单元测试 `tests/test_memory/test_facade.py`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = MemoryStore(db)`；`retrieval = MemoryRetrieval(store, embed)`；fake `LlmClient.complete` 按 `output_type` 分支返回 fixture 并记录调用（`scene_memory` → 三样 JSON、`contradiction` → `conflicts_with` JSON）；fake `Evaluator`（记录 `evaluate` 调用）；`EventBus` 用真实例 + 订阅 recording handler，`run()` 作 task 驱动——同 05-event 模式）：
  - [ ] **纯函数**：
    - [ ] `decay_freshness`：`now == created_at` → 不变；1 天后 → `freshness - rate`；`now < created_at` → 不变；衰减到负 → 夹到 0
    - [ ] `_parse_scene`：合法 JSON → 3 元组；缺 `tag` → `ValueError`；空串 → `ValueError`；JSON 是数组 → `ValueError`
    - [ ] `_build_scene_prompt`：含三输入（`user_message`/`nyx_think`/`nyx_speak`）；缺键 → `KeyError`
    - [ ] `_has_negation`：`"我不喜欢猫"` → `True`；`"我喜欢猫"` → `False`
    - [ ] `_content_preview`：`content` 短于 60 字 → 不截断、含 `summary`；长于 60 字 → 截到 60 字 + `…`
    - [ ] `_build_contradiction_prompt`：含新记忆 `content` + 候选 `id` + 候选预览；新记忆含否定词 → 含「重点核对」句；不含否定词 → 无该句
    - [ ] `_parse_contradiction`：`conflicts_with` 字符串 → 该串；`null` → `None`；数字 → `ValueError`；缺 `conflicts_with` 键 → `None`
    - [ ] `_memory_to_dict`：`type` 是 `.value` 字符串、`embedding` 透传
    - [ ] `_memory_to_markdown`：含 `summary` 与 `content`
    - [ ] `_join_list`：`str` 原样、`list` 换行拼接、空 `list`/`None`/非 str-list → `""`
    - [ ] `_activity_memory_fields`：reading→`(note, book)`、creation→`(content, title)`、free_exploration→`(notes 拼接, findings 拼接)`；非目标类型/空 result/空内容/类型非 str → `None`；summary 超 80 字截断
  - [ ] **create_scene_memory**：
    - [ ] fake LLM 返回 `{"content","tag","summary"}` → 返回 `Memory` 各字段正确（`content`/`tag`/`summary`、`freshness==1.0`、`type is SHORT_TERM`、`embedding` 已算且 = fake embed(content)）；`evaluator.evaluate` 被调 1 次（收到 `output_type="scene_memory"` 的 `LLMOutput`）
    - [ ] 发布 `memory_created`：`content["memory_id"] == memory.id`、`source is INTERNAL`、`correlation_id == reply_context["correlation_id"]`
    - [ ] **矛盾检测门控**：
      - [ ] `embed=None` → 仅 1 次 LLM 调用（`scene_memory`），无 `contradiction` 调用，无 `reflection`
      - [ ] 有 embedding 但旧记忆相似度 < 阈值（fake embed 造正交向量）→ 无 `contradiction` 调用，无 `reflection`
      - [ ] 有候选过阈值（fake embed 造相同/高相似向量）→ 第 2 次调用 `output_type="contradiction"`；fake LLM 返回 `conflicts_with=<旧记忆id>` → 发布 `reflection`（`content["summary"]` 含冲突双方 id）；第 2 次 `complete` 后 `evaluator.evaluate` 再被调 1 次（`output_type="contradiction"`）
      - [ ] contradiction 返回 `null` → 不发 `reflection`
    - [ ] **三杠杆落地**：矛盾 prompt 含候选 `content` 截断预览（非只 summary）；召回 `_RECALL_TOP_K=5`（造 5 条高相似旧记忆 → 矛盾 prompt 候选 ≤ 5）；新记忆含否定词 → prompt 含「重点核对」句
    - [ ] **建边**：先建一条含 embedding 的记忆，再 create 一条相似 embedding 的记忆 → 新记忆有到旧记忆的 `memory_edge`（`weight > 0`）
    - [ ] **淘汰**：`MemoryConfig(short_term_capacity=1, ...)`，create 第二条 → 旧的那条（freshness 更低）被删，`list_memories()` 只剩新的一条
    - [ ] **衰减回写**：monkeypatch `time.time` 使两条创建间隔 1 天 → 旧记忆的 `freshness` 被衰减（`< 1.0`）
  - [ ] **remember_activity**：
    - [ ] reading/creation/free_exploration 三类 mock `activity_end` 事件 → 各写一条 `Memory`（`content`/`summary`/`tag` 正确、`type is SHORT_TERM`）、发布 `memory_created`、**无 LLM 调用**（`llm.calls == []`）
    - [ ] rest/observe_user/idle_reflection 或空 result → 不写、无 `memory_created`
    - [ ] 有相似旧记忆 + embed（fake embed 造高相似向量）→ 门控触发 1 次 `contradiction` 调用（参与矛盾判断，无 `scene_memory`）
  - [ ] **search / list_memories**：`search` 委托 fake `MemoryRetrieval`（返回预设 list）；`list_memories(tag, type)` 委托真 store（过滤/排序同 07）
  - [ ] **record_recall**：
    - [ ] 未达 `promote_threshold` → `recall_count` 递增、`type` 仍 `SHORT_TERM`、无 `memory_promoted`
    - [ ] 达阈值 → `type is LONG_TERM` + 发布一条 `memory_promoted`
    - [ ] 已是 `LONG_TERM` → 只 `recall_count` 递增，不再发布
  - [ ] **export**：`export("json")` → `json.loads` 能还原出记忆列表（含 `type` 字符串）；`export("md")` → 含某条记忆的 `summary`/`content`；`export("csv")` → `ValueError`
- [ ] 集成测试：无（Facade 的 LLM 全 mock、DB 用 `:memory:`；与表达管道的真实编排归 17）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 17-expression 慢通道调 `create_scene_memory` / `record_recall` / `search`，不各自调 store/retrieval；18-api 组合根构建 `embed`（按 `config.embedding.model`）→ `retrieval` → `facade` 并注入 `store` / `bus` / `llm` / `evaluator` / `config`；矛盾检测是门控独立调用（无候选 0 调用、有候选过阈值 1 调用）
