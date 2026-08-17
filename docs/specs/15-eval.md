# eval 三层（Evaluator + 结构/规则/LLM-judge）

> 范围：`eval/evaluator.py`（`Evaluator`，基础设施）+ `eval/rules.py`（`validate_structure` / `ooc_score` 纯函数）+ `eval/judge.py`（`should_judge` 纯函数 + `judge_relevance`）。`EvalReport`/`TokenUsage` 落库（04-db 的 `eval_report` / `token_usage` 两表）。
> 纯基础设施 spec：只做「三层评分 + token 记账 + 报告落库」，不含 Facade、不含 API 端点（薄封装归 18-api）、不含 store（Evaluator 直接持 db，对齐 EventBus）。
> **本文件自包含**：3 个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`EvalReport` / `TokenUsage` / `EvalScores` / `TokenUsageDict` / `LLMOutput`）、02-config（`EvalConfig`）、03-llm（`LlmClient`）、04-db（`Database` + `eval_report`/`token_usage` 两表 DDL）

## 用户故事

> 作为 Nyx 系统的开发者，我想要对所有 LLM 产出做三层评分（结构→规则→judge）+ 每次调用的 token 记账，以便原则 4「所有 LLM 产出和行动有 eval 评分」与原则 2「token 消耗可查可追溯」落地，前端 eval + token 看板（design §11）有数据源。

## 验收标准

- [ ] `evaluator.py` 含 `Evaluator`（`evaluate` / `list_reports` / `list_token_usage`），与「`eval/evaluator.py`（完整）」段代码逐字一致
- [ ] `rules.py` 含 `validate_structure` + `ooc_score` 纯函数；`judge.py` 含 `should_judge` 纯函数 + `judge_relevance`，与各自「（完整）」段逐字一致
- [ ] `evaluate` 三层：结构 → 规则 → judge（抽样），返回 `EvalReport`；落 `token_usage`（每次必记）+ `eval_report`（每次必记）+ 可选 judge 的 `token_usage`（抽样触发才记）；三连 INSERT 后统一 `commit()`（持久化）
- [ ] `should_judge`：`output_type == "judge"` 不递归 judge；`roll < sample_rate` 才触发
- [ ] 纯函数测全（`validate_structure` / `ooc_score` / `should_judge`）；`pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/eval/evaluator.py`、`nyx/eval/rules.py`、`nyx/eval/judge.py`（无 Facade、无 API、无数据变更——两表 DDL 已在 04-db）
- **库**：无新库（标准库 `json` / `random` / `time` / `uuid` + `aiosqlite` 仅作 `_row_to_*` 类型注解）
- **公开面**：`from nyx.eval.evaluator import Evaluator`；`from nyx.eval.rules import validate_structure, ooc_score`；`from nyx.eval.judge import should_judge, judge_relevance`（不加 `__all__`）
- **Evaluator 定位**：基础设施（非 Facade），**直接持 db**（`__init__(self, db, llm, config)`，对齐 05-event 的 EventBus 直接写 SQL、不单独 store.py 的模式——tech-ref §7 的 `eval/` 无 store.py，已锁）。锁约定同 EventBus：落库 SQL 都在 `async with self._db.lock:` 内，锁内只做 INSERT + `commit()`，纯计算（`_to_token_usage`）与 judge 的 LLM I/O 都在锁外；**三连 INSERT 后统一 `commit()`**（report + usage 同一事务原子提交，写后必 commit 对齐 04-db/05-event/14）
- **evaluate 的调用方（显式约定，防循环依赖）**：`Evaluator → LlmClient` 单向（judge 层要调 LLM 打分）；若反过来让 `LlmClient.complete` 内部调 `evaluate`，则 `LlmClient → Evaluator` 反向成环 + import 循环，还需回填构造顺序（鸡生蛋）。故 **evaluate 由各 Facade 在 `await llm.complete(...)` 后显式调用**；漏记靠「每个 Facade 测试断言 complete 后 evaluate 被调」+ 完成定义的 ripple 兜底
- **三层 + EvalScores 量纲（本 spec 定死，前端按量纲渲染）**：

  | 键 | 层 | 量纲 | 方向 |
  |---|---|---|---|
  | `format` | 结构校验 | 0.0 / 1.0 | 1.0 = 通过 |
  | `ooc` | 规则评分 | 0.0-1.0 | 越高越贴合（1.0=无 OOC） |
  | `relevance` | LLM-judge | 0.0 未评 / 1.0-5.0 | 越高越好 |

- **结构校验（`validate_structure`）**：只做通用「非空 + 长度上限」检查。**不做字段级 JSON 校验**——那是各 Facade 的 `_parse_*` 的活（11/12/14 已 fail-fast），eval 重复实现是反冗余。design §9.1 的「字段/数值合法」由 Facade 层承担
- **规则评分（`ooc_score`）**：design §9.2 第 1 档「关键词/语癖规则」。`_BLACKLIST`（崩人设的现代/AI 腔）+ `_WHITELIST`（Nyx 语癖）模块级常量（初始值可推翻，随 canon.md 校准）；**字段名 `ooc` 保留（01-types 锁定），语义 = 人设贴合度（越高越不 OOC，与 format/relevance 同向）**——黑名单命中扣分、白名单命中加分，`1.0 - (黑-白) * _OOC_STEP` 封顶 [0,1]，无命中默认满分
- **judge（`judge_relevance` + `should_judge`）**：design §9.1 第三层 + §9.2 第 3 档。抽样率 `config.judge_sample_rate`（02-config 默认 0.1，生产 10% 抽样；可设 0 关闭 judge，配合原则 1「减少 LLM 调用」）。`output_type == "judge"` 不递归 judge（防 judge 的 judge 死循环）。judge 环节失败不应崩整个 evaluate——**不 raise**：传输失败（超时/5xx）→ 容错 0.0、无 judge_output（返回 `None`，token 不记）；输出非法（JSON 解析失败 / 非 dict / score 非数字或布尔）→ 容错 0.0 但仍返回 judge_output（token 照记，原则 2）；score 合法但越界（如 `{"score":100}`）→ clamp 到 [1,5]（design §9.1 的 1-5 分，未评=0.0）
- **token 记账（`_to_token_usage`）**：`purpose = output.type`（03-llm line 119「type → TokenUsage.purpose」）、`model = output.model`、`input/output = output.token_usage`、`correlation_id = output.correlation_id`。被评产出必记；judge 产出（`module="eval"`、`purpose="judge"`）抽样触发才记——judge 的 token 也透明化（原则 2）
- **`output_id` 语义**：`EvalReport.output_id = output.id`。`LLMOutput.id` 由 03-llm `complete` 每次调用生成 uuid4（ripple 01-types 加字段 + 03-llm 生成），保证「同一事件多次 complete」（如 reply 的 think→speak）也能区分「哪次产出」
- **明确不做**：eval 结果自动反馈修正（design §9.3「纯记录 + 可视化，不自动反馈修正」）；`eval/store.py`（Evaluator 直接持 db）

### `eval/rules.py`（完整）

```python
"""eval 结构校验 + 规则评分：纯函数，无 IO、无 LLM、无 DB。"""

_MAX_CONTENT_LEN = 10000          # 结构校验长度上限（可推翻）
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


def validate_structure(content: str) -> float:
    """结构校验：content 非空且长度 ≤ 上限 → 1.0，否则 0.0。

    不做字段级 JSON 校验——那是各 Facade 的 _parse_* 的活（fail-fast），
    eval 只做通用结构检查，避免与 _parse_* 重复实现（反冗余）。
    """
    if not content or not content.strip():
        return 0.0
    if len(content) > _MAX_CONTENT_LEN:
        return 0.0
    return 1.0


def ooc_score(content: str) -> float:
    """人设贴合度（字段名 ooc 保留，01-types 锁定）：白名单命中加分、
    黑名单命中扣分，[0,1]。

    1.0 = 完全贴合（无 OOC），0.0 = 严重 OOC。越高越好（与 format/relevance 同向）。
    无命中默认满分（大部分正常输出无黑名单词 = 贴合）。
    """
    black = sum(1 for w in _BLACKLIST if w in content)
    white = sum(1 for w in _WHITELIST if w in content)
    return max(0.0, min(1.0, 1.0 - (black - white) * _OOC_STEP))
```

### `eval/judge.py`（完整）

```python
"""LLM-judge：语义质量 1-5 分，抽样触发（design §9.1 第三层 + §9.2 第 3 档）。"""
import json
import logging
from typing import Any, cast

from nyx.llm.client import LlmClient
from nyx.types import LLMOutput

_JUDGE_SYSTEM = (
    "你是尼克斯（Nyx）的人格评审。给下面这段 Nyx 的输出打分："
    "语义质量 + 与语境的相关性，按 JSON 输出 {score}，"
    "score 为 1-5 的整数（5=优秀，1=差）。"
)

_logger = logging.getLogger(__name__)


def should_judge(output_type: str, sample_rate: float, roll: float) -> bool:
    """是否触发 LLM-judge（纯函数）：judge 输出不递归 judge + 抽样命中。"""
    if output_type == "judge":
        return False
    return roll < sample_rate


async def judge_relevance(
    llm: LlmClient, output: LLMOutput
) -> tuple[float, LLMOutput | None]:
    """调 LLM 打分，返回 (score, judge 调用的 LLMOutput 供记账)。

    judge 环节失败不应崩整个 evaluate（eval 是纯记录性质）：
    - 传输失败（超时/5xx）→ 容错 0.0、无 judge_output（None，不记账）
    - JSON 解析失败 / 非 dict / score 非数字（含布尔）→ 容错 0.0，但仍返回
      judge_output（token 照记，原则 2）
    score 合法时 clamp 到 [1,5]（design §9.1 的 1-5 分），未评 = 0.0。
    """
    try:
        judge_output = await llm.complete(
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": output.content},
            ],
            module="eval",
            output_type="judge",
            correlation_id=output.correlation_id,
            json_mode=True,
        )
    except Exception:
        _logger.exception(
            "judge LLM 调用失败 correlation_id=%s", output.correlation_id
        )
        return 0.0, None
    try:
        data = json.loads(judge_output.content)
        if isinstance(data, dict):
            raw = cast(dict[str, Any], data).get("score")
        else:
            raw = None
        # clamp [1,5]；未评 / 布尔 = 0.0
        if raw is None or isinstance(raw, bool):
            score = 0.0
        else:
            score = max(1.0, min(5.0, float(raw)))
    except (TypeError, ValueError, OverflowError):
        # JSONDecodeError 是 ValueError 子类；float(超大 int) 溢出，一并覆盖
        score = 0.0
    return score, judge_output
```

### `eval/evaluator.py`（完整）

```python
"""eval Evaluator：三层评分 + token 记账 + 报告落库。基础设施（非 Facade），
直接持 db（对齐 EventBus）。
"""
import json
import random
import time
import uuid

import aiosqlite

from nyx.config import EvalConfig
from nyx.db import Database
from nyx.eval.judge import judge_relevance, should_judge
from nyx.eval.rules import ooc_score, validate_structure
from nyx.llm.client import LlmClient
from nyx.types import EvalReport, EvalScores, LLMOutput, TokenUsage


class Evaluator:
    """对所有 LLM 产出做三层评分 + token 记账（原则 4 + 原则 2）。"""

    def __init__(self, db: Database, llm: LlmClient, config: EvalConfig) -> None:
        self._db = db
        self._llm = llm
        self._sample_rate = config.judge_sample_rate

    async def evaluate(self, output: LLMOutput) -> EvalReport:
        """三层：结构 → 规则 → judge（抽样）。

        落 token_usage + eval_report，返回 EvalReport。
        """
        scores: EvalScores = {
            "format": validate_structure(output.content),
            "ooc": ooc_score(output.content),
            "relevance": 0.0,
        }
        judge_usage: TokenUsage | None = None
        if should_judge(output.type, self._sample_rate, random.random()):
            scores["relevance"], judge_output = await judge_relevance(self._llm, output)
            if judge_output is not None:
                judge_usage = self._to_token_usage(judge_output)
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
            if judge_usage is not None:
                await self._insert_token_usage(judge_usage)
            await self._db.conn.commit()   # 写后必 commit，三连 INSERT 原子提交
        return report

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

## 测试要点

- [ ] 单元测试 `tests/test_eval/`（`db = await connect(":memory:")`；fake `LlmClient.complete` 按 `output_type` 返回 fixture JSON——同 05/09/11/12 模式）：
  - [ ] **rules 纯函数**（`test_rules.py`，无 DB、无 async）：
    - [ ] `validate_structure`：空串 → 0.0；纯空白 → 0.0；正常 → 1.0；超长（`"x" * (_MAX_CONTENT_LEN + 1)`）→ 0.0
    - [ ] `ooc_score`：无命中 → 1.0（默认满分）；黑 1 白 0 → 0.5；黑 2 白 0 → 0.0；黑 3 白 0 → 0.0（封顶）；黑 1 白 1 → 1.0（白抵消黑）；白 2 黑 0 → 1.0（封顶不越界）
  - [ ] **judge**（`test_judge.py`）：
    - [ ] `should_judge` 纯函数：`("judge", 1.0, 0.0)` → False（不递归）；`("reply", 0.1, 0.05)` → True；`("reply", 0.1, 0.5)` → False
    - [ ] `judge_relevance`（fake `llm.complete` 返回 `{"score": 4}`）：返回 `(4.0, judge_output)`，且 `judge_output.type == "judge"`、`judge_output.module == "eval"`、`correlation_id` 透传
    - [ ] `judge_relevance` 容错不 raise（均 `(0.0, judge_output)`）：非法 JSON（`"["`）；合法非 dict（`"[]"`）；dict 但 score 非数字（`'{"score":"abc"}'`）；超大 int 溢出（`float()` OverflowError）
    - [ ] `judge_relevance` 传输失败（fake `complete` raise）→ `(0.0, None)`，不 raise
    - [ ] `judge_relevance` score 为布尔（`{"score": true}`）→ `(0.0, judge_output)`（堵 `float(True)==1.0` 的坑）
    - [ ] `judge_relevance` clamp [1,5]：`{"score":100}` → `5.0`；`{"score":0.5}` → `1.0`；`{"score":4}` → `4.0`（界内不动）
  - [ ] **evaluator**（`test_evaluator.py`，`db=:memory:` + fake llm；持久化测试用 `tmp_path` 临时文件库）：
    - [ ] `evaluate` 抽样路径（`EvalConfig(judge_sample_rate=1.0)`）：落 `eval_report` 1 条（`scores["relevance"] == judge 分`、`output_id == output.id`）+ `token_usage` 2 条（被评 `purpose == output.type` + judge `purpose == "judge"`）
    - [ ] `evaluate` 非抽样路径（`judge_sample_rate=0.0`）：`relevance == 0.0` + `token_usage` 仅 1 条（无 judge）
    - [ ] `evaluate` judge 传输失败（fake `complete` raise）：仍返回 `EvalReport`（`relevance == 0.0`）+ `token_usage` 仅 1 条（judge 无产出不记账），不 raise
    - [ ] `evaluate` 落库持久化：`db = await connect(str(tmp_path/"e.db"))`，`evaluate` 后 `close`，重开新连接 `list_reports`/`list_token_usage` 能读到已提交行（抓「写后不 commit」回归——`:memory:` 同连接读己写抓不住）
    - [ ] `list_reports`：插 2 条 → 按 `created_at DESC` 返回、`scores`/`token_usage` JSON 往返正确
    - [ ] `list_token_usage(since)`：只返回 `created_at >= since` 的行
- [ ] 集成测试：无（Evaluator 是基础设施，无 Facade 管道；各 Facade 调 evaluate 的编排归 18-api 组合根 + 各 Facade 测试）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：01-types `LLMOutput` 加 `id: str`（uuid4）字段；03-llm `complete` 生成 `id=str(uuid.uuid4())` + `import uuid`（测试补 `id` 非空断言）；tech-ref §5 `Evaluator` 补 `__init__(self, db: Database, llm: LlmClient, config: EvalConfig) -> None` 签名（EventBus/ToolRegistry 都列了 `__init__`，Evaluator 漏列）
- [ ] 下游约定：18-api 组合根注入 `evaluator = Evaluator(db, llm, config)`；各 Facade（09-memory-facade / 11-desire / 12-inner-life / 14-activity / 17-expression）在 `await llm.complete(...)` 后紧跟 `await evaluator.evaluate(output)`（实现时同步改这五份 spec 的 Facade `__init__` 加 `evaluator` 参数 + 调用点）；`/api/eval` → `list_reports`、`/api/tokens` → `list_token_usage`（tech-ref §4 已锁）
