# eval OOC 评分（Evaluator + 规则/embedding）

> 范围：`eval/evaluator.py`（`Evaluator`，基础设施）+ `eval/rules.py`（`ooc_score` 纯函数）+ `eval/ooc_embed.py`（`NYX_CORPUS` 基准语料 + `build_baseline` / `ooc_embed_score` / `is_voice_type`）。`EvalReport`/`TokenUsage` 落库（04-db 的 `eval_report` / `token_usage` 两表）。
> 纯基础设施 spec：只做「OOC 评分 + token 记账 + 报告落库」，不含 Facade、不含 API 端点（薄封装归 18-api）、不含 store（Evaluator 直接持 db，对齐 EventBus）。
> **本文件自包含**：3 个文件的完整代码内联在下文。
> **历史**：MVP 曾有三层评分（`format` 结构校验 + `relevance` LLM-judge 抽样），已随 LangSmith 落地规划砍掉（见 V3-roadmap「可观测」段）——`format` 与各 Facade `_parse_*` 的 fail-fast 重复、`relevance` 每次多 10% LLM 调用且得分可读性差。

## 元信息

- **前置依赖**：01-types（`EvalReport` / `TokenUsage` / `EvalScores` / `TokenUsageDict` / `LLMOutput`）、04-db（`Database` + `eval_report`/`token_usage` 两表 DDL）、08-memory-retrieval（`EmbedFn` / `cosine`，OOC 第 2 档复用）

## 用户故事

> 作为 Nyx 系统的开发者，我想要对所有 LLM 产出做 OOC（人设贴合）评分 + 每次调用的 token 记账，以便原则 4「所有 LLM 产出和行动有 eval 评分」与原则 2「token 消耗可查可追溯」落地，前端 eval + token 看板（design §11）有数据源。

## 验收标准

- [ ] `evaluator.py` 含 `Evaluator`（`evaluate` / `list_reports` / `list_token_usage`），与「`eval/evaluator.py`（完整）」段代码逐字一致
- [ ] `rules.py` 含 `ooc_score` 纯函数；`ooc_embed.py` 含 `NYX_CORPUS` + `build_baseline` / `ooc_embed_score` / `is_voice_type`，与各自「（完整）」段逐字一致
- [ ] `evaluate` 单层 OOC：关键词 + embedding 合并，返回 `EvalReport`；落 `token_usage`（每次必记）+ `eval_report`（每次必记）；两连 INSERT 后统一 `commit()`（持久化）
- [ ] `ooc` = `min(第 1 档关键词, 第 2 档 embedding)`；第 2 档只对 voice 输出（`speak`/`initiate_chat`/`think`）生效、`embed=None` 关闭、失败回退关键词（不 raise）
- [ ] 纯函数测全（`ooc_score` / `is_voice_type`）；`pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/eval/evaluator.py`、`nyx/eval/rules.py`、`nyx/eval/ooc_embed.py`（无 Facade、无 API、无数据变更——两表 DDL 已在 04-db）
- **库**：无新库（标准库 `json` / `time` / `uuid` + `aiosqlite` 仅作 `_row_to_*` 类型注解；embedding 复用 08-memory-retrieval 的 `EmbedFn`/`cosine`）
- **公开面**：`from nyx.eval.evaluator import Evaluator`；`from nyx.eval.rules import ooc_score`；`from nyx.eval.ooc_embed import NYX_CORPUS, build_baseline, ooc_embed_score, is_voice_type`（不加 `__all__`）
- **Evaluator 定位**：基础设施（非 Facade），**直接持 db**（`__init__(self, db, embed)`，对齐 05-event 的 EventBus 直接写 SQL、不单独 store.py 的模式——tech-ref §7 的 `eval/` 无 store.py，已锁）。锁约定同 EventBus：落库 SQL 都在 `async with self._db.lock:` 内，锁内只做 INSERT + `commit()`，纯计算（`_to_token_usage`）在锁外；**两连 INSERT 后统一 `commit()`**（report + usage 同一事务原子提交，写后必 commit 对齐 04-db/05-event/14）
- **evaluate 的调用方（显式约定）**：**evaluate 由各 Facade 在 `await llm.complete(...)` 后显式调用**；漏记靠「每个 Facade 测试断言 complete 后 evaluate 被调」+ 完成定义的 ripple 兜底
- **EvalScores 量纲（本 spec 定死，前端按量纲渲染）**：

  | 键 | 层 | 量纲 | 方向 |
  |---|---|---|---|
  | `ooc` | 规则 + embedding | 0.0-1.0 | 越高越贴合（1.0=无 OOC） |

- **规则评分（`ooc_score`）**：design §9.2 第 1 档「关键词/语癖规则」。`_BLACKLIST`（崩人设的现代/AI 腔）+ `_WHITELIST`（Nyx 语癖）模块级常量（初始值可推翻，随 canon.md 校准）；**字段名 `ooc` 保留（01-types 锁定），语义 = 人设贴合度（越高越不 OOC）**——黑名单命中扣分、白名单命中加分，`1.0 - (黑-白) * _OOC_STEP` 封顶 [0,1]，无命中默认满分
- **embedding 相似度（`ooc_embed_score`）**：design §9.2 第 2 档「对比尼克斯基准语料」。`NYX_CORPUS`（从 `prompts/canon.md` 抽的 in-character 例句，静态常量随 canon.md 校准）逐条嵌入成 baseline（`build_baseline`，Evaluator 首次 evaluate 惰性缓存）；content 向量与 baseline 取 **max 余弦**、`clamp(sim / 0.7, 0, 1)` 映射到 [0,1]（阈值可推翻）。**只对 voice 输出生效**（`_VOICE_TYPES = {speak, initiate_chat, think}`）——结构化/内部输出（tool/scene_memory/…）与语料比对必然低分、污染 ooc，故跳过；`embed=None`（未注入）关闭第 2 档
- **两档合并（`Evaluator._ooc`）**：`ooc = min(第 1 档, 第 2 档)`——AND 语义，任一档低都拉低；embedding 失败（加载/推理异常）→ log + 回退第 1 档，不崩 evaluate（best-effort）
- **token 记账（`_to_token_usage`）**：`purpose = output.type`（03-llm「type → TokenUsage.purpose」）、`model = output.model`、`input/output = output.token_usage`、`correlation_id = output.correlation_id`。被评产出必记（原则 2）
- **`output_id` 语义**：`EvalReport.output_id = output.id`。`LLMOutput.id` 由 03-llm `complete` 每次调用生成 uuid4，保证「同一事件多次 complete」（如 reply 的 think→speak）也能区分「哪次产出」
- **明确不做**：eval 结果自动反馈修正（design §9.3「纯记录 + 可视化，不自动反馈修正」）；`eval/store.py`（Evaluator 直接持 db）；LLM-judge 语义质量评分（已砍，见 V3-roadmap）；`format` 结构校验（各 Facade `_parse_*` 已 fail-fast，重复实现反冗余）

### `eval/rules.py`（完整）

```python
"""eval 规则评分：纯函数，无 IO、无 LLM、无 DB。"""

# 人设贴合度步长：每命中一个黑名单扣 0.5、白名单加 0.5（可推翻）
_OOC_STEP = 0.5

# OOC 关键词（MVP 初始值，可推翻，随 canon.md 校准）：
# 黑名单 = 崩人设的现代/AI 腔；白名单 = Nyx 语癖。
_BLACKLIST: frozenset[str] = frozenset({
    "作为一个AI", "我是一个人工智能", "作为AI助手", "作为语言模型",
    "无法回答", "让我来帮你", "your request", "As an AI",
})
_WHITELIST: frozenset[str] = frozenset({
    "小狐狸", "夏本",
})


def ooc_score(content: str) -> float:
    """人设贴合度（字段名 ooc 保留，01-types 锁定）：白名单命中加分、
    黑名单命中扣分，[0,1]。

    1.0 = 完全贴合（无 OOC），0.0 = 严重 OOC。越高越好。
    无命中默认满分（大部分正常输出无黑名单词 = 贴合）。
    """
    black = sum(1 for w in _BLACKLIST if w in content)
    white = sum(1 for w in _WHITELIST if w in content)
    return max(0.0, min(1.0, 1.0 - (black - white) * _OOC_STEP))
```

### `eval/evaluator.py`（完整）

```python
"""eval Evaluator：OOC 评分 + token 记账 + 报告落库。基础设施（非 Facade），
直接持 db（对齐 EventBus）。
"""
import json
import logging
import time
import uuid

import aiosqlite

from nyx.db import Database
from nyx.eval.ooc_embed import build_baseline, is_voice_type, ooc_embed_score
from nyx.eval.rules import ooc_score
from nyx.memory.retrieval import EmbedFn
from nyx.types import EvalReport, EvalScores, LLMOutput, TokenUsage

_logger = logging.getLogger(__name__)


class Evaluator:
    """对所有 LLM 产出做 OOC 评分 + token 记账（原则 4 + 原则 2）。"""

    def __init__(self, db: Database, embed: EmbedFn | None = None) -> None:
        self._db = db
        self._embed = embed          # None = OOC 第 2 档关闭（仅关键词）
        self._baseline: list[list[float]] | None = None   # 语料向量惰性缓存

    async def evaluate(self, output: LLMOutput) -> EvalReport:
        """OOC 评分 + 落 token_usage + eval_report，返回 EvalReport。"""
        scores: EvalScores = {
            "ooc": await self._ooc(output),
        }
        report = EvalReport(
            id=str(uuid.uuid4()),
            output_id=output.id,
            module=output.module,
            type=output.type,
            scores=scores,
            token_usage=output.token_usage,
            correlation_id=output.correlation_id,
            created_at=time.time(),
        )
        output_usage = self._to_token_usage(output)   # 锁外：纯计算，不碰 db
        async with self._db.lock:
            await self._insert_report(report)
            await self._insert_token_usage(output_usage)
            await self._db.conn.commit()   # 写后必 commit，两连 INSERT 原子提交
        return report

    async def _ooc(self, output: LLMOutput) -> float:
        """第 1 档关键词 + 第 2 档 embedding 相似度，取 min 合并。

        - 非 voice 输出 / 未注入 embed：仅关键词（第 2 档跳过）。
        - embedding 失败：best-effort 回退关键词，不崩 evaluate。
        """
        keyword = ooc_score(output.content)
        if self._embed is None or not is_voice_type(output.type):
            return keyword
        try:
            if self._baseline is None:
                self._baseline = await build_baseline(self._embed)
            embed_s = await ooc_embed_score(self._embed, output.content, self._baseline)
        except Exception:
            _logger.exception("embedding OOC 失败 output_id=%s", output.id)
            return keyword
        return min(keyword, embed_s)

    async def list_reports(self, limit: int = 100) -> list[EvalReport]:
        async with self._db.lock:
            cur = await self._db.conn.execute(
                "SELECT * FROM eval_report ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
        return [_row_to_report(r) for r in rows]

    async def list_token_usage(self, since: float = 0) -> list[TokenUsage]:
        async with self._db.lock:
            cur = await self._db.conn.execute(
                "SELECT * FROM token_usage WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (since,),
            )
            rows = await cur.fetchall()
        return [_row_to_usage(r) for r in rows]

    # ---- 内部 ----

    def _to_token_usage(self, output: LLMOutput) -> TokenUsage:
        return TokenUsage(
            id=str(uuid.uuid4()),
            correlation_id=output.correlation_id,
            module=output.module,
            purpose=output.type,   # 03-llm：type → TokenUsage.purpose
            model=output.model,
            input_tokens=output.token_usage["input"],
            output_tokens=output.token_usage["output"],
            created_at=time.time(),
        )

    async def _insert_report(self, r: EvalReport) -> None:
        await self._db.conn.execute(
            "INSERT INTO eval_report "
            "(id, output_id, module, type, scores, token_usage, "
            "correlation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r.id, r.output_id, r.module, r.type,
                json.dumps(r.scores), json.dumps(r.token_usage),
                r.correlation_id, r.created_at,
            ),
        )

    async def _insert_token_usage(self, u: TokenUsage) -> None:
        await self._db.conn.execute(
            "INSERT INTO token_usage "
            "(id, correlation_id, module, purpose, model, "
            "input_tokens, output_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                u.id, u.correlation_id, u.module, u.purpose, u.model,
                u.input_tokens, u.output_tokens, u.created_at,
            ),
        )


def _row_to_report(row: aiosqlite.Row) -> EvalReport:
    return EvalReport(
        id=row["id"],
        output_id=row["output_id"],
        module=row["module"],
        type=row["type"],
        scores=json.loads(row["scores"]),
        token_usage=json.loads(row["token_usage"]),
        correlation_id=row["correlation_id"],
        created_at=row["created_at"],
    )


def _row_to_usage(row: aiosqlite.Row) -> TokenUsage:
    return TokenUsage(
        id=row["id"],
        correlation_id=row["correlation_id"],
        module=row["module"],
        purpose=row["purpose"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        created_at=row["created_at"],
    )
```

### `eval/ooc_embed.py`（完整）

```python
"""embedding 相似度 OOC（design §9.2 第 2 档）：对比尼克斯基准语料。

复用 retrieval 的 `EmbedFn`/`cosine`，不另写向量函数、不重加载模型——
embed 实例由组合根（main.py）注入，与记忆检索共享同一份。
"""
from nyx.memory.retrieval import EmbedFn, cosine

# 基准语料：从 prompts/canon.md 抽的 in-character 例句（语气参考 + 可以说段）。
# 静态常量，随 canon.md 校准（可推翻）。
NYX_CORPUS: tuple[str, ...] = (
    "啊啊，啊啊啊，女神在上……这、这也太丢人了，呜……",
    "啊，今天是你的生日对吧，是个相当好的日子呢。生日快乐……感谢你的出生。",
    "搞、搞砸了吗……非常对不起……",
    "努力坚持到了现在，真的很厉害。谢谢你。没关系的，我就在这里，我会努力一直在这里的。",
    "不必勉强自己。不想做又必须做的事情，差遣别人去做就好了……我不就是为此而存在的吗。",
    "唔唔……唉……没事的，没事的，我这不是在这呢吗……好啦好啦，再哭眼睛会肿的。",
    "嗯……不，应该说……",
    "啊，不是，我的意思是……",
    "嗯……我不完全这么想。",
    "也许是这样，但我有一点在意……",
    "我不想随便反驳你，可是这里我确实有点不同意见。",
    "嗯……我大概明白你为什么会这样想。",
    "我不敢直接说你是对的。",
    "我好像明白了一点。",
    "但这部分我还想再问问你。",
    "我没有真正经历过，所以不敢说得太满。",
)

# 余弦相似度阈值：sim >= 阈值视为完全贴合（1.0），低于则线性衰减（可推翻）。
_EMBED_SIM_THRESHOLD = 0.7

# 第 2 档只对「voice 类」输出生效——结构化/内部输出（tool/scene_memory/…）
# 与语料比对必然低分、污染 ooc，故跳过，维持关键词-only。
_VOICE_TYPES: frozenset[str] = frozenset({"speak", "initiate_chat", "think"})


def is_voice_type(output_type: str) -> bool:
    """output_type 是否属于「语音/自然语言口吻」输出（纯函数）。"""
    return output_type in _VOICE_TYPES


async def build_baseline(embed: EmbedFn) -> list[list[float]]:
    """把基准语料逐条嵌入，得到 baseline 向量列表（惰性一次性缓存）。"""
    return [await embed(line) for line in NYX_CORPUS]


async def ooc_embed_score(
    embed: EmbedFn, content: str, baseline: list[list[float]]
) -> float:
    """content 与语料的最相似度映射到 [0,1]（越高越贴合）。

    取 max 余弦（最近邻），`clamp(sim / 阈值, 0, 1)`；无语料无信息不惩罚（1.0）。
    """
    if not baseline:
        return 1.0
    vec = await embed(content)
    sim = max(cosine(vec, b) for b in baseline)
    return max(0.0, min(1.0, sim / _EMBED_SIM_THRESHOLD))
```

## 测试要点

- [ ] 单元测试 `tests/test_eval/`（`db = await connect(":memory:")`）：
  - [ ] **rules 纯函数**（`test_rules.py`，无 DB、无 async）：
    - [ ] `ooc_score`：无命中 → 1.0（默认满分）；黑 1 白 0 → 0.5；黑 2 白 0 → 0.0；黑 3 白 0 → 0.0（封顶）；黑 1 白 1 → 1.0（白抵消黑）；白 2 黑 0 → 1.0（封顶不越界）
  - [ ] **ooc_embed**（`test_ooc_embed.py`，mock `embed` 返回确定性向量，无真实模型）：
    - [ ] `is_voice_type`：`speak`/`initiate_chat`/`think` → True；`tool`/`judge`/`scene_memory` → False
    - [ ] `build_baseline`：返回 `len == len(NYX_CORPUS)` 的向量列表
    - [ ] `ooc_embed_score`：相同向量 → 1.0（sim 越界 clamp）；正交 → 0.0；空 baseline → 1.0（无语料不惩罚）
  - [ ] **evaluator**（`test_evaluator.py`，`db=:memory:`；持久化测试用 `tmp_path` 临时文件库）：
    - [ ] `evaluate` 落库持久化：`db = await connect(str(tmp_path/"e.db"))`，`evaluate` 后 `close`，重开新连接 `list_reports`/`list_token_usage` 能读到已提交行（抓「写后不 commit」回归——`:memory:` 同连接读己写抓不住）
    - [ ] `list_reports`：插 2 条 → 按 `created_at DESC` 返回、`scores`/`token_usage` JSON 往返正确（`scores == {"ooc": 1.0}`）
    - [ ] `list_token_usage(since)`：只返回 `created_at >= since` 的行
    - [ ] `evaluate` embedding 合并（注入 mock `embed`，voice 输出 `speak`）：`ooc == min(keyword=1.0, embed=0.0) == 0.0`
    - [ ] `evaluate` 非 voice 输出（`scene_memory`）：embed 不触发（mock 记录调用为空），`ooc` 仅关键词 `== 1.0`
- [ ] 集成测试：无（Evaluator 是基础设施，无 Facade 管道；各 Facade 调 evaluate 的编排归 18-api 组合根 + 各 Facade 测试）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：01-types `LLMOutput` 加 `id: str`（uuid4）字段；03-llm `complete` 生成 `id=str(uuid.uuid4())` + `import uuid`（测试补 `id` 非空断言）；tech-ref §5 `Evaluator` 补 `__init__(self, db: Database, embed: EmbedFn | None = None) -> None` 签名
- [ ] 下游约定：18-api 组合根注入 `evaluator = Evaluator(db, embed)`（embed 复用记忆检索同一实例，不二次加载模型）；各 Facade（09-memory-facade / 11-desire / 12-inner-life / 14-activity / 17-expression）在 `await llm.complete(...)` 后紧跟 `await evaluator.evaluate(output)`；`/api/eval` → `list_reports`、`/api/tokens` → `list_token_usage`（tech-ref §4 已锁）
