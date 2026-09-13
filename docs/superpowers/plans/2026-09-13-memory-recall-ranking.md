# Memory Recall Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the confirmed memory recall ranking spec: ANN-backed direct recall, capped keyword scoring, 2-hop typed association, bounded persistence dedup/contradiction candidates, typed memory edges, degree control, clustering, and synchronized docs/tests.

**Architecture:** Keep the existing Facade -> subsystem -> internal class shape. `MemoryStore` owns SQLite CRUD and row serialization, `AnnIndex` owns bounded approximate vector candidates, `MemoryGraph` owns typed-edge association/clustering, `MemoryRetrieval` owns chat recall ranking, and `MemoryFacade` owns persistence dedup/build-edge/contradiction side effects.

**Tech Stack:** Python 3.11+, FastAPI runtime, SQLite via `aiosqlite`, `networkx==3.6.1`, pytest, pyright strict, ruff.

**Spec:** `docs/specs/25-memory-recall-ranking.md`

## Global Constraints

- Read `docs/memory-system-facts.md` before changing memory code; after implementing this confirmed spec, update the facts table so it no longer contradicts the new recall order.
- Do not add configuration knobs; all candidate counts, weights, and thresholds from the spec are module constants.
- Do not add an extra Repository/Service/Manager layer; keep the existing module boundaries.
- Do not call LLM providers directly; LLM relation extraction and contradiction checks must use the existing `LlmClient`.
- `Memory.created_at` remains creation time and is not refreshed by `update_many`, `strengthen`, or `record_recall`.
- `strengthen(memory_id, now)` remains repeat-write/semantic-dedup merge: `recall_count+1`, `freshness=1.0`, no promote, no `memory_created`.
- Slow expression recall remains `MemoryFacade.search(message)` -> all returned memories enter prompt -> immediate `record_recall` per memory.
- Fast expression path still does no memory search, no `record_recall`, and no scene memory.
- Each task that changes tests must update `docs/test-inventory.md` in the same task.
- Each task ends with a focused commit. Before committing, run the focused tests for that task.

---

## File Structure

- Create `nyx/memory/ann.py`: deterministic Random Hyperplane LSH, `AnnCandidate`, `AnnIndex`, and embedding fingerprint helpers.
- Modify `nyx/enums.py`: add `MemoryEdgeKind`.
- Modify `nyx/types.py`: extend `MemoryEdge` with `kind: MemoryEdgeKind` and `created_at: float`.
- Modify `nyx/db.py`: add a migration that rebuilds `memory_edge` with canonical unordered `(from_id, to_id, kind)` primary key and `created_at`.
- Modify `nyx/memory/store.py`: add `KeywordSearchHit`; replace old `search_keyword` with capped `search_keywords`; implement canonical typed edge CRUD and degree listing.
- Modify `nyx/memory/graph.py`: replace `neighbors` with typed adjacency-backed `associate`; add `AssociationHit`; add `clusters`.
- Modify `nyx/memory/retrieval.py`: add keyword extraction, ranked fusion, ANN cache, new `search(query, direct_limit=20, association_limit=10)` semantics.
- Modify `nyx/memory/facade.py`: replace unbounded `_similar` pipeline with bounded persist semantic candidates; implement five-signal edge building, LLM relation extraction, degree pruning, and updated contradiction gating.
- Modify docs: `docs/memory-system-facts.md`, `docs/specs/01-types.md`, `docs/specs/04-db.md`, `docs/specs/07-memory-store.md`, `docs/specs/08-memory-retrieval.md`, `docs/specs/09-memory-facade.md`, `docs/specs/17-expression.md`, `docs/tech-reference.md`, `docs/test-inventory.md`.
- Modify tests: `tests/test_memory/test_ann.py`, `tests/test_memory/test_retrieval.py`, `tests/test_memory/test_graph.py`, `tests/test_memory/test_store.py`, `tests/test_memory/test_facade.py`, `tests/test_db/test_db.py`, `tests/test_expression/test_expression_facade.py`.

## Task 1: Types And Edge Schema

**Files:**
- Modify: `nyx/enums.py`
- Modify: `nyx/types.py`
- Modify: `nyx/db.py`
- Modify: `tests/test_memory/test_store.py`
- Modify: `tests/test_db/test_db.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Produces: `MemoryEdgeKind`, expanded `MemoryEdge`, and a `memory_edge` table with `(from_id, to_id, kind)` primary key.
- Consumes: existing `MemoryType`, `SearchMode`, migration system, and `MemoryStore.upsert_edge` until Task 2 replaces its signature.

- [ ] **Step 1: Add failing enum/dataclass tests**

Add assertions that `MemoryEdgeKind` has exactly these values and `MemoryEdge` defaults to semantic:

```python
from nyx.enums import MemoryEdgeKind
from nyx.types import MemoryEdge


def test_memory_edge_kind_values() -> None:
    assert {k.value for k in MemoryEdgeKind} == {
        "semantic", "entity", "keyword", "temporal",
        "same_topic", "elaborates", "contrasts", "causes",
        "updates_preference", "user_profile_link",
    }


def test_memory_edge_defaults() -> None:
    edge = MemoryEdge("a", "b")
    assert edge.kind is MemoryEdgeKind.SEMANTIC
    assert edge.weight == 1.0
    assert edge.created_at == 0.0
```

- [ ] **Step 2: Add failing DB migration tests**

In `tests/test_db/test_db.py`, update table/index expectations and add schema checks:

```python
async def test_memory_edge_schema_typed_and_canonical() -> None:
    conn = await _migrated_conn()
    try:
        cols = await (await conn.execute("PRAGMA table_info(memory_edge)")).fetchall()
        pk = {r["name"]: r["pk"] for r in cols}
        notnull = {r["name"]: r["notnull"] for r in cols}
        ddl = await (await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_edge'"
        )).fetchone()
    finally:
        await conn.close()
    assert pk == {"from_id": 1, "to_id": 2, "kind": 3, "weight": 0, "created_at": 0}
    assert notnull["kind"] == 1 and notnull["created_at"] == 1
    assert ddl is not None and "CHECK (from_id < to_id)" in ddl["sql"]
```

Add a migration-from-old-shape test by monkeypatching migrations to version 13, inserting both directions, then migrating to the new max version:

```python
async def test_memory_edge_migration_canonicalizes_reverse_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = db._MIGRATIONS
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        monkeypatch.setattr(db, "_MIGRATIONS", [m for m in full if m[0] <= 13])
        await db.migrate(conn)
        for mid in ("a", "b"):
            await conn.execute(
                "INSERT INTO memory (id, created_at, content, tag, summary, freshness, "
                "type, recall_count, aspect, embedding, content_hash, first_created_at) "
                "VALUES (?, 1.0, ?, 't', 's', 1.0, 'short_term', 0, '[]', NULL, ?, 1.0)",
                (mid, mid, mid),
            )
        await conn.execute("INSERT INTO memory_edge (from_id, to_id, weight) VALUES ('a', 'b', 0.4)")
        await conn.execute("INSERT INTO memory_edge (from_id, to_id, weight) VALUES ('b', 'a', 0.9)")
        await conn.commit()
        monkeypatch.setattr(db, "_MIGRATIONS", full)
        await db.migrate(conn)
        rows = await (await conn.execute(
            "SELECT from_id, to_id, kind, weight, created_at FROM memory_edge"
        )).fetchall()
    finally:
        await conn.close()
    assert [(r["from_id"], r["to_id"], r["kind"], r["weight"], r["created_at"]) for r in rows] == [
        ("a", "b", "semantic", 0.9, 0.0)
    ]
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_db/test_db.py tests/test_memory/test_store.py -q`

Expected before implementation: import failure for `MemoryEdgeKind` and schema assertions fail.

- [ ] **Step 4: Implement enum/dataclass/schema**

Add to `nyx/enums.py`:

```python
class MemoryEdgeKind(StrEnum):
    SEMANTIC = "semantic"
    ENTITY = "entity"
    KEYWORD = "keyword"
    TEMPORAL = "temporal"
    SAME_TOPIC = "same_topic"
    ELABORATES = "elaborates"
    CONTRASTS = "contrasts"
    CAUSES = "causes"
    UPDATES_PREFERENCE = "updates_preference"
    USER_PROFILE_LINK = "user_profile_link"
```

Update `nyx/types.py` imports and `MemoryEdge`:

```python
@dataclass
class MemoryEdge:
    from_id: str
    to_id: str
    kind: MemoryEdgeKind = MemoryEdgeKind.SEMANTIC
    weight: float = 1.0
    created_at: float = 0.0
```

Add migration version `14` to `nyx/db.py` that renames old table, creates the new schema with `CHECK (from_id < to_id)`, inserts canonicalized rows grouped by endpoints and kind, drops the old table, and keeps FK behavior:

```sql
ALTER TABLE memory_edge RENAME TO memory_edge_old;
CREATE TABLE memory_edge (
    from_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    to_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'semantic',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL DEFAULT 0.0,
    CHECK (from_id < to_id),
    PRIMARY KEY (from_id, to_id, kind)
);
INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at)
SELECT
    CASE WHEN from_id < to_id THEN from_id ELSE to_id END,
    CASE WHEN from_id < to_id THEN to_id ELSE from_id END,
    'semantic',
    MAX(weight),
    0.0
FROM memory_edge_old
WHERE from_id != to_id
GROUP BY
    CASE WHEN from_id < to_id THEN from_id ELSE to_id END,
    CASE WHEN from_id < to_id THEN to_id ELSE from_id END;
DROP TABLE memory_edge_old;
```

- [ ] **Step 5: Update tests that construct old edges**

Change `MemoryEdge(from_id="a", to_id="b", weight=1.0)` assertions to include default kind/created_at where equality matters.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_db/test_db.py tests/test_memory/test_store.py -q`

Expected: DB tests pass or only Task 2 store signature tests fail because old `upsert_edge` still exists.

- [ ] **Step 7: Update test inventory**

Update `docs/test-inventory.md` rows for `01-types`, `04-db`, and affected `07-memory-store` tests to reflect `MemoryEdgeKind`, typed edge schema, and migration.

- [ ] **Step 8: Commit**

```bash
git add nyx/enums.py nyx/types.py nyx/db.py tests/test_db/test_db.py tests/test_memory/test_store.py docs/test-inventory.md docs/specs/25-memory-recall-ranking.md docs/superpowers/plans/2026-09-13-memory-recall-ranking.md
git commit -m "feat(memory): add typed memory edge schema"
```

## Task 2: Store Keyword And Typed Edge CRUD

**Files:**
- Modify: `nyx/memory/store.py`
- Modify: `tests/test_memory/test_store.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: `MemoryEdgeKind`, expanded `MemoryEdge`, new `memory_edge` schema.
- Produces: `KeywordSearchHit`, `search_keywords(tokens, limit)`, typed canonical `list_edges`, `upsert_edge`, `delete_edges`, `list_edge_degrees`.

- [ ] **Step 1: Write failing keyword tests**

Replace old `test_search_keyword` tests with capped token tests:

```python
async def test_search_keywords_returns_field_hits_ordered_and_capped() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="alpha beta", summary="none", freshness=0.3, created_at=1.0))
        await store.add(_mem("m2", content="alpha", summary="alpha beta", freshness=0.7, created_at=2.0))
        await store.add(_mem("m3", content="beta", summary="none", freshness=1.0, created_at=3.0))
        hits = await store.search_keywords(["alpha", "beta"], limit=2)
        assert list(hits) == ["m2", "m1"]
        assert hits["m2"].summary_tokens == ["alpha", "beta"]
        assert hits["m2"].content_tokens == ["alpha"]
    finally:
        await db.conn.close()


async def test_search_keywords_empty_and_limit_zero_skip_db() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        assert await store.search_keywords([], limit=10) == {}
        assert await store.search_keywords(["alpha"], limit=0) == {}
    finally:
        await db.conn.close()
```

Keep wildcard escape coverage:

```python
async def test_search_keywords_escapes_wildcards() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="进度 100%"))
        await store.add(_mem("m2", content="进度 100 元"))
        hits = await store.search_keywords(["100%"], limit=10)
        assert list(hits) == ["m1"]
    finally:
        await db.conn.close()
```

- [ ] **Step 2: Write failing typed edge CRUD tests**

Add:

```python
async def test_typed_edges_canonicalize_and_filter_kind() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.upsert_edge("b", "a", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)
        await store.upsert_edge("a", "b", MemoryEdgeKind.ENTITY, 0.8, 11.0)
        semantic = await store.list_edges(MemoryEdgeKind.SEMANTIC)
        all_edges = await store.list_edges()
        assert semantic == [MemoryEdge("a", "b", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)]
        assert [e.kind for e in all_edges] == [MemoryEdgeKind.ENTITY, MemoryEdgeKind.SEMANTIC]
    finally:
        await db.conn.close()


async def test_delete_edges_and_list_edge_degrees() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        for mid in ("a", "b", "c"):
            await store.add(_mem(mid))
        await store.upsert_edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)
        await store.upsert_edge("b", "c", MemoryEdgeKind.KEYWORD, 0.5, 11.0)
        degrees = await store.list_edge_degrees(["b"])
        assert [(e.from_id, e.to_id, e.kind) for e in degrees["b"]] == [
            ("a", "b", MemoryEdgeKind.SEMANTIC),
            ("b", "c", MemoryEdgeKind.KEYWORD),
        ]
        await store.delete_edges([("a", "b", MemoryEdgeKind.SEMANTIC)])
        assert [(e.from_id, e.to_id, e.kind) for e in await store.list_edges()] == [
            ("b", "c", MemoryEdgeKind.KEYWORD)
        ]
    finally:
        await db.conn.close()
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_memory/test_store.py -q`

Expected: `search_keywords` and new typed edge signatures are missing.

- [ ] **Step 4: Implement `KeywordSearchHit` and capped keyword search**

Add near store constants:

```python
@dataclass
class KeywordSearchHit:
    memory_id: str
    summary_tokens: list[str]
    content_tokens: list[str]
```

Implement `search_keywords(self, tokens: list[str], limit: int) -> dict[str, KeywordSearchHit]` by loading candidate rows for escaped LIKE matches, accumulating unique summary/content tokens per memory in input-token order, sorting by:

```python
(
    -matched_unique_token_count,
    -len(summary_tokens),
    -len(content_tokens),
    -memory.freshness,
    -memory.created_at,
    memory.id,
)
```

Return an insertion-ordered `dict` built from the first `limit` sorted hits. Remove `search_keyword`.

- [ ] **Step 5: Implement canonical typed edge CRUD**

Add helper:

```python
def _canonical_edge_ids(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)
```

Update edge methods to serialize `MemoryEdgeKind.value` and deserialize using `MemoryEdgeKind(row["kind"])`.

Ordering for `list_edges`: `ORDER BY from_id ASC, to_id ASC, kind ASC`.

Ordering for `list_edge_degrees`: same ordering per memory id.

- [ ] **Step 6: Update delete cascade tests**

Old tests that inserted both `("a", "b")` and `("b", "a")` should now assert one canonical row per typed edge. `delete_many` still deletes edges with `from_id = ? OR to_id = ?`.

- [ ] **Step 7: Run focused tests**

Run: `pytest tests/test_memory/test_store.py tests/test_db/test_db.py -q`

Expected: PASS.

- [ ] **Step 8: Update test inventory and commit**

Update `docs/test-inventory.md` for `07-memory-store`.

```bash
git add nyx/memory/store.py tests/test_memory/test_store.py tests/test_db/test_db.py docs/test-inventory.md
git commit -m "feat(memory): add capped keyword and typed edge store"
```

## Task 3: ANN Index

**Files:**
- Create: `nyx/memory/ann.py`
- Create: `tests/test_memory/test_ann.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: `Memory`, `cosine` from `nyx.memory.retrieval` or a local shared-safe cosine helper.
- Produces: `AnnCandidate`, `AnnIndex.build`, `AnnIndex.query`, `hash_embedding`, `ann_fingerprint`.

- [ ] **Step 1: Write failing ANN tests**

Create `tests/test_memory/test_ann.py`:

```python
from nyx.enums import MemoryType
from nyx.memory.ann import AnnIndex, ann_fingerprint, hash_embedding
from nyx.types import Memory


def _mem(id: str, embedding: list[float] | None, created_at: float = 1.0) -> Memory:
    return Memory(id, created_at, id, "t", id, 1.0, MemoryType.SHORT_TERM, embedding=embedding)


def test_ann_empty_and_invalid_query() -> None:
    index = AnnIndex.build([])
    assert index.query([1.0], candidate_k=10) == []
    assert index.query([1.0], candidate_k=0) == []


def test_ann_skips_none_and_wrong_dimensions() -> None:
    index = AnnIndex.build([
        _mem("a", [1.0, 0.0]),
        _mem("bad", [1.0, 0.0, 0.0]),
        _mem("none", None),
    ])
    assert [c.memory_id for c in index.query([1.0, 0.0], 10)] == ["a"]
    assert index.query([1.0], 10) == []


def test_ann_candidate_limit_and_order_are_stable() -> None:
    memories = [
        _mem("a", [1.0, 0.0], created_at=1.0),
        _mem("b", [0.9, 0.1], created_at=3.0),
        _mem("c", [0.0, 1.0], created_at=2.0),
    ]
    index = AnnIndex.build(memories, planes=4, tables=2, seed=0)
    result = index.query([1.0, 0.0], candidate_k=2)
    assert [c.memory_id for c in result] == ["a", "b"]
    assert len(result) == 2


def test_hash_embedding_and_fingerprint_change_on_embedding_update() -> None:
    old = [_mem("a", [0.123456789])]
    new = [_mem("a", [0.223456789])]
    assert hash_embedding([0.123456789]) == hash_embedding([0.123456788])
    assert ann_fingerprint(old) != ann_fingerprint(new)
    assert ann_fingerprint(old) != ann_fingerprint([])
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_memory/test_ann.py -q`

Expected: import failure for `nyx.memory.ann`.

- [ ] **Step 3: Implement deterministic LSH**

Implement:

```python
@dataclass
class AnnCandidate:
    memory_id: str
    cosine: float


class AnnIndex:
    @classmethod
    def build(cls, memories: list[Memory], *, planes: int = 16, tables: int = 4, seed: int = 0) -> AnnIndex: ...
    def query(self, vector: list[float], candidate_k: int) -> list[AnnCandidate]: ...
```

Implementation rules:
- index only memories with non-`None` embedding matching the first valid embedding dimension.
- random planes use `rng = random.Random(seed)` and `rng.gauss(0.0, 1.0)` per dimension.
- query same bucket, Hamming radius 1, Hamming radius 2, then newest indexed memories by `(created_at DESC, id ASC)`.
- compute exact cosine only for bounded candidates and sort by `(cosine DESC, created_at DESC, id ASC)`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_memory/test_ann.py -q`

Expected: PASS.

- [ ] **Step 5: Update test inventory and commit**

Add `test_memory/test_ann.py` rows to `docs/test-inventory.md`.

```bash
git add nyx/memory/ann.py tests/test_memory/test_ann.py docs/test-inventory.md
git commit -m "feat(memory): add deterministic ANN index"
```

## Task 4: Typed Memory Graph Association And Clustering

**Files:**
- Modify: `nyx/memory/graph.py`
- Modify: `tests/test_memory/test_graph.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: `MemoryEdge`, `MemoryEdgeKind`.
- Produces: `AssociationHit`, `MemoryGraph(edges, memory_ids=None)`, `associate`, `clusters`.

- [ ] **Step 1: Replace old neighbor tests with association tests**

Add:

```python
from nyx.enums import MemoryEdgeKind
from nyx.memory.graph import AssociationHit, MemoryGraph
from nyx.types import MemoryEdge


def _edge(a: str, b: str, kind: MemoryEdgeKind, weight: float, created_at: float = 1.0) -> MemoryEdge:
    return MemoryEdge(a, b, kind, weight, created_at)


def test_associate_depth_two_scores_and_excludes_seeds() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.8),
        _edge("b", "c", MemoryEdgeKind.KEYWORD, 0.5),
    ])
    hits = g.associate({"a": 1.0}, depth=2, limit=10, exclude={"a"})
    assert [(h.memory_id, h.depth, h.via, h.kinds) for h in hits] == [
        ("b", 1, "a", ["semantic"]),
        ("c", 2, "b", ["semantic", "keyword"]),
    ]
    assert hits[0].score == 0.8
    assert hits[1].score == 1.0 * 0.8 * 0.5 * 0.55 * 0.75


def test_associate_parallel_typed_edges_take_best_path() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.TEMPORAL, 1.0),
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.7),
    ])
    [hit] = g.associate({"a": 1.0}, limit=10, exclude={"a"})
    assert hit.memory_id == "b"
    assert hit.kinds == ["semantic"]
```

Add clustering tests:

```python
def test_clusters_include_isolated_nodes_with_stable_ids() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 1.0),
    ], memory_ids={"a", "b", "z"})
    clusters = g.clusters()
    assert set(clusters) == {"a", "b", "z"}
    assert clusters["a"] == clusters["b"]
    assert clusters["z"] != clusters["a"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_memory/test_graph.py -q`

Expected: missing `AssociationHit`, missing `associate`, old `neighbors` behavior incompatible.

- [ ] **Step 3: Implement typed adjacency**

Use a private edge record or tuple, not `nx.Graph`, for association:

```python
_KIND_WEIGHT = {
    MemoryEdgeKind.SEMANTIC: 1.0,
    MemoryEdgeKind.ENTITY: 0.9,
    MemoryEdgeKind.KEYWORD: 0.75,
    MemoryEdgeKind.TEMPORAL: 0.35,
    MemoryEdgeKind.SAME_TOPIC: 1.05,
    MemoryEdgeKind.ELABORATES: 1.1,
    MemoryEdgeKind.CONTRASTS: 1.0,
    MemoryEdgeKind.CAUSES: 1.0,
    MemoryEdgeKind.UPDATES_PREFERENCE: 1.15,
    MemoryEdgeKind.USER_PROFILE_LINK: 1.1,
}
```

Canonicalize duplicate `(min_id, max_id, kind)` on construction, preserving the higher `weight`, then later `created_at`.

- [ ] **Step 4: Implement `associate`**

Breadth-expand up to `depth` from seeds. For every typed edge:
- path score is previous path score times `edge.weight * depth_decay * kind_weight`.
- depth decay is `1.0` for first hop and `0.55` for second hop.
- exclude seeds/direct ids from returned hits.
- if multiple paths reach a node, keep higher score, then lower depth, then lower `via`.
- `AssociationHit.kinds` stores the best path kinds by value string.

- [ ] **Step 5: Implement `clusters`**

Build a temporary `nx.Graph` only for clustering:
- add all `memory_ids`.
- aggregate same unordered pair by `max(edge.weight * cluster_kind_weight[kind])`.
- try `nx.community.louvain_communities(..., seed=0)`.
- fallback to `nx.community.greedy_modularity_communities(...)`.
- assign stable cluster ids by each community minimum memory id.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_memory/test_graph.py -q`

Expected: PASS.

- [ ] **Step 7: Update test inventory and commit**

Update `docs/test-inventory.md` graph rows.

```bash
git add nyx/memory/graph.py tests/test_memory/test_graph.py docs/test-inventory.md
git commit -m "feat(memory): add typed graph association"
```

## Task 5: Retrieval Fusion Search

**Files:**
- Modify: `nyx/memory/retrieval.py`
- Modify: `tests/test_memory/test_retrieval.py`
- Modify: `tests/test_expression/test_expression_facade.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: `AnnIndex`, `MemoryGraph.associate`, `MemoryStore.search_keywords`.
- Produces: `extract_keywords`, `RankedMemory`, `MemoryRetrieval.search(query, direct_limit=20, association_limit=10)`.

- [ ] **Step 1: Write failing keyword extraction tests**

Add:

```python
from nyx.memory.retrieval import extract_keywords


def test_extract_keywords_mixed_text() -> None:
    assert extract_keywords("这个 Alpha_1 和中文长句测试可以吗") == [
        "alpha_1", "中文长句测试",
    ]


def test_extract_keywords_long_cjk_windows_and_dedup() -> None:
    tokens = extract_keywords("诺斯艾兰骑士团诺斯艾兰")
    assert "诺斯艾" in tokens
    assert "诺斯艾兰" not in tokens
    assert tokens.count("诺斯艾") == 1
```

- [ ] **Step 2: Write failing retrieval ranking tests**

Use real store plus deterministic fake embed:

```python
async def test_search_fuses_vector_keyword_and_limits_direct_then_association() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("direct-vector", content="香蕉", summary="无", freshness=0.8, embedding=[1.0, 0.0]))
        await store.add(_mem("direct-keyword", content="alpha beta", summary="alpha beta", freshness=1.0, embedding=[0.0, 1.0]))
        await store.add(_mem("assoc", content="联想", summary="联想", freshness=1.0, embedding=None))
        await store.upsert_edge("direct-vector", "assoc", MemoryEdgeKind.SEMANTIC, 1.0, 1.0)
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        results = await retrieval.search("alpha", direct_limit=2, association_limit=1)
        assert [m.id for m in results] == ["direct-vector", "direct-keyword", "assoc"]
        assert results[0].sources == [SearchMode.VECTOR]
        assert results[1].sources == [SearchMode.KEYWORD]
        assert results[2].sources == [SearchMode.ASSOCIATION]
    finally:
        await db.conn.close()
```

Add direct-limit edge case:

```python
async def test_search_direct_limit_zero_returns_empty() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="alpha", embedding=[1.0, 0.0]))
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        assert await retrieval.search("alpha", direct_limit=0, association_limit=10) == []
    finally:
        await db.conn.close()
```

- [ ] **Step 3: Keep slow expression contract tests**

`tests/test_expression/test_expression_facade.py::test_reply_slow_records_recall` should remain valid because expression still calls `MemoryFacade.search(message)` and records every returned memory.

- [ ] **Step 4: Run tests and verify failure**

Run: `pytest tests/test_memory/test_retrieval.py tests/test_expression/test_expression_facade.py::test_reply_slow_records_recall -q`

Expected: missing `extract_keywords`, old `limit` signature, old search order assertions fail.

- [ ] **Step 5: Implement retrieval constants and helpers**

Add:

```python
_RECALL_VECTOR_CANDIDATE_K = 80
_RECALL_KEYWORD_CANDIDATE_K = 80
```

Implement `extract_keywords` exactly from the spec. Keep `cosine` and `rank_by_cosine` if tests/facade still consume them, but new retrieval should use `AnnIndex`.

- [ ] **Step 6: Implement ANN cache**

In `MemoryRetrieval`, cache:

```python
self._ann: AnnIndex | None = None
self._ann_fingerprint: tuple[tuple[str, float, int], ...] | None = None
```

Add a private async method:

```python
async def _ann_index(self, memories: list[Memory]) -> AnnIndex:
    fingerprint = ann_fingerprint(memories)
    if self._ann is None or fingerprint != self._ann_fingerprint:
        self._ann = AnnIndex.build(memories)
        self._ann_fingerprint = fingerprint
    return self._ann
```

- [ ] **Step 7: Implement fusion ranking**

Implement `search(query, direct_limit=20, association_limit=10)`:
- blank query -> `[]`.
- `direct_limit <= 0` -> `[]`.
- load `all_memories = await store.list_memories()` and `by_id`.
- embed failure catches exception and uses `query_vec=None`.
- vector candidates come from `AnnIndex.query`.
- keyword candidates come from `store.search_keywords(tokens, limit=_RECALL_KEYWORD_CANDIDATE_K)`.
- direct scores use the exact formula and tie-breaker from the spec.
- direct memory sources are `[VECTOR]`, `[KEYWORD]`, or `[VECTOR, KEYWORD]`.
- association seeds are `{memory.id: score}` for direct ranked.
- use `MemoryGraph(edges).associate(seeds, depth=2, limit=association_limit, exclude=set(seeds))`.
- append association memories after direct with `[ASSOCIATION]`.

- [ ] **Step 8: Update old tests**

Remove tests that expect `search("alpha", limit=1)` or `_vector_search` old top-5 behavior. Replace with direct/association limit tests and formula tests.

- [ ] **Step 9: Run focused tests**

Run: `pytest tests/test_memory/test_retrieval.py tests/test_expression/test_expression_facade.py::test_reply_slow_records_recall -q`

Expected: PASS.

- [ ] **Step 10: Update test inventory and commit**

Update `docs/test-inventory.md` retrieval and expression rows.

```bash
git add nyx/memory/retrieval.py tests/test_memory/test_retrieval.py tests/test_expression/test_expression_facade.py docs/test-inventory.md
git commit -m "feat(memory): fuse vector keyword recall ranking"
```

## Task 6: Facade Persistence, Edge Building, And Degree Control

**Files:**
- Modify: `nyx/memory/facade.py`
- Modify: `tests/test_memory/test_facade.py`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: `AnnIndex`, `PersistSemanticHit`, `MemoryStore.search_keywords`, typed edge CRUD, `MemoryEdgeKind`.
- Produces: bounded semantic persistence candidates, updated contradiction gate, five-signal edge building, LLM relation edges, and degree pruning.

- [ ] **Step 1: Write failing persist candidate tests**

Update semantic dedup tests to assert bounded candidate behavior through public facade calls:

```python
async def test_dedup_semantic_uses_bounded_candidates_and_returns_old_memory() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [1.0, 0.0]))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())
        assert memory.id == "old-1"
        assert [e for e in events if e.type is EventType.MEMORY_CREATED] == []
    finally:
        await database.conn.close()
```

Add contradiction candidate threshold test that still expects only top 5:

```python
async def test_contradiction_uses_top_five_persist_candidates() -> None:
    store, bus, database = await _new_stack()
    for i in range(6):
        await store.add(_mem(f"old-{i}", [0.8, 0.6]))
    llm = _FakeLlm({"scene_memory": _SCENE_JSON, "contradiction": json.dumps({"conflicts_with": None})})
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert llm.user_contents[1].count("- [old-") == 5
    finally:
        await database.conn.close()
```

- [ ] **Step 2: Write failing edge-building tests**

Add one test per signal, keeping each test under five assertions:

```python
async def test_build_edges_creates_semantic_keyword_and_temporal_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("semantic-old", [0.9, 0.1], content="旧语义"))
    await store.add(_mem("keyword-old", None, content="alpha beta", summary="alpha"))
    await store.add(_mem("recent-old", None, content="recent", summary="recent"))
    llm = _FakeLlm({"scene_memory": _scene("alpha beta new"), "relations": json.dumps({"relations": []})})
    evaluator = _FakeEvaluator()
    monkeypatch.setattr("nyx.memory.facade.time.time", lambda: 1000.0)
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        kinds = {e.kind for e in await store.list_edges()}
        assert MemoryEdgeKind.SEMANTIC in kinds
        assert MemoryEdgeKind.KEYWORD in kinds
        assert MemoryEdgeKind.TEMPORAL in kinds
    finally:
        await database.conn.close()
```

Add LLM relation edge test with output type chosen in implementation, for example `"memory_relation"`:

```python
async def test_build_edges_creates_llm_relation_edge() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6], content="用户喜欢猫"))
    llm = _FakeLlm({
        "scene_memory": _scene("用户现在更喜欢狐狸"),
        "memory_relation": json.dumps({"relations": [
            {"memory_id": "old-1", "kind": "updates_preference", "weight": 0.8}
        ]}),
        "contradiction": json.dumps({"conflicts_with": None}),
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert any(e.kind is MemoryEdgeKind.UPDATES_PREFERENCE for e in await store.list_edges())
    finally:
        await database.conn.close()
```

Add degree pruning test:

```python
async def test_prune_edges_limits_per_kind_and_total_degree() -> None:
    store, bus, database = await _new_stack()
    try:
        for mid in ["hub", *[f"m{i}" for i in range(20)]]:
            await store.add(_mem(mid, None))
        for i in range(20):
            await store.upsert_edge("hub", f"m{i}", MemoryEdgeKind.TEMPORAL, 0.01 + i / 100.0, float(i))
        facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
        await facade._prune_degrees({"hub"})
        assert len((await store.list_edge_degrees(["hub"]))["hub"]) <= 4
    finally:
        await database.conn.close()
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_memory/test_facade.py -q`

Expected: old `_similar`, old `_build_edges`, and old store edge signatures fail.

- [ ] **Step 4: Implement persistence semantic candidates**

Add:

```python
@dataclass
class PersistSemanticHit:
    memory: Memory
    cosine: float
```

Add constants from the spec. Replace `_similar` with:

```python
async def _persist_semantic_candidates(self, embedding: list[float]) -> list[PersistSemanticHit]:
    memories = await self._store.list_memories()
    index = AnnIndex.build(memories)
    candidates = index.query(embedding, _PERSIST_SEMANTIC_CANDIDATE_K)
    by_id = {m.id: m for m in memories}
    return [
        PersistSemanticHit(by_id[c.memory_id], c.cosine)
        for c in candidates
        if c.memory_id in by_id
    ]
```

Use this list for semantic dedup, edge building, and contradiction gate.

- [ ] **Step 5: Implement edge signal helpers**

Add pure helpers:
- `extract_entities(memory: Memory) -> set[str]`
- `_keyword_jaccard(new_tokens: set[str], old_tokens: set[str]) -> float`
- `_temporal_score(new_created_at: float, old_created_at: float) -> float`
- `_parse_relation_edges(raw: str, allowed_ids: set[str]) -> list[tuple[str, MemoryEdgeKind, float]]`

Use exact thresholds and candidate constants from the spec.

- [ ] **Step 6: Implement LLM relation extraction**

Call existing `self._llm.complete` with:
- `module="memory"`
- `output_type="memory_relation"`
- `json_mode=True`
- prompt containing new memory and up to five candidates with id/summary/content/tag.

On parse/call failure, log exception and continue without relation edges.

- [ ] **Step 7: Implement edge writes and degree pruning**

Write per-kind top 4 edges with `store.upsert_edge(new_id, old_id, kind, weight, now)`.

Implement `_prune_degrees(touched: set[str]) -> None`:
- load degrees with `store.list_edge_degrees`.
- first delete per-kind overflow by `prune_priority ASC, created_at ASC, from_id ASC, to_id ASC, kind.value ASC`.
- then delete total overflow by same order.
- loop until touched nodes satisfy limits.

- [ ] **Step 8: Run focused tests**

Run: `pytest tests/test_memory/test_facade.py tests/test_memory/test_store.py tests/test_memory/test_ann.py -q`

Expected: PASS.

- [ ] **Step 9: Update test inventory and commit**

Update `docs/test-inventory.md` facade rows for bounded persistence, relation edges, and degree pruning.

```bash
git add nyx/memory/facade.py tests/test_memory/test_facade.py docs/test-inventory.md
git commit -m "feat(memory): build bounded typed memory graph"
```

## Task 7: Documentation Synchronization

**Files:**
- Modify: `docs/memory-system-facts.md`
- Modify: `docs/specs/01-types.md`
- Modify: `docs/specs/04-db.md`
- Modify: `docs/specs/07-memory-store.md`
- Modify: `docs/specs/08-memory-retrieval.md`
- Modify: `docs/specs/09-memory-facade.md`
- Modify: `docs/specs/17-expression.md`
- Modify: `docs/tech-reference.md`
- Modify: `docs/test-inventory.md`

**Interfaces:**
- Consumes: implemented code and confirmed spec.
- Produces: synchronized docs with no old `keyword -> vector -> association` or unbounded `scored` contradiction wording.

- [ ] **Step 1: Update facts table**

In `docs/memory-system-facts.md`:
- Replace “语义 embedding 余弦 top-1 >= 0.95” with “bounded persist semantic candidates 内 top-1 cosine >= 0.95”.
- Replace retrieval order with “整句 embedding ANN 候选 + keyword LIKE 候选融合评分 -> direct top N -> 2 跳 association 追加”.
- Keep `Memory.sources` transient and REST-visible.

- [ ] **Step 2: Update specs 01/04/07/08/09/17**

Apply exactly the replacement points from `docs/specs/25-memory-recall-ranking.md`:
- `01-types`: `MemoryEdgeKind`, expanded `MemoryEdge`, transient `Memory.sources`.
- `04-db`: typed canonical edge schema and migration.
- `07-memory-store`: `KeywordSearchHit`, `search_keywords(tokens, limit)`, typed edge CRUD.
- `08-memory-retrieval`: `direct_limit`, `association_limit`, formula, ANN cache, keyword cap.
- `09-memory-facade`: bounded persistence candidates, five-signal edge building, degree pruning, contradiction gate.
- `17-expression`: slow channel still records recall for every returned memory.

- [ ] **Step 3: Update tech reference**

Add `nyx/memory/ann.py` and update memory method index. Include test files added/changed.

- [ ] **Step 4: Scan for stale contradictions**

Run:

```bash
rg -n "keyword -> vector -> association|search_keyword\\(|scored|top-1 >= 0.95|MemoryEdge\\(.*weight|memory_edge.*from_id, to_id\\)" docs nyx tests
```

Expected: only intentional historical comments or no matches. If a match is live contract text, update it.

- [ ] **Step 5: Commit docs**

```bash
git add docs/memory-system-facts.md docs/specs/01-types.md docs/specs/04-db.md docs/specs/07-memory-store.md docs/specs/08-memory-retrieval.md docs/specs/09-memory-facade.md docs/specs/17-expression.md docs/tech-reference.md docs/test-inventory.md
git commit -m "docs(memory): sync recall ranking contracts"
```

## Task 8: Full Quality Gate And Integration Cleanup

**Files:**
- Inspect: all modified files
- Modify: only files needed to fix quality gate failures

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: a passing branch ready for review.

- [ ] **Step 1: Run memory/db/expression focused suite**

Run:

```bash
pytest tests/test_memory tests/test_db tests/test_expression/test_expression_facade.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full backend test suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run ruff**

Run:

```bash
ruff check
```

Expected: zero errors.

- [ ] **Step 4: Run pyright**

Run:

```bash
pyright
```

Expected: zero errors.

- [ ] **Step 5: Inspect docs/code contract alignment**

Run:

```bash
rg -n "MemoryEdgeKind|search_keywords|direct_limit|association_limit|_PERSIST_SEMANTIC_CANDIDATE_K|MemoryGraph\\(" docs nyx tests
```

Expected:
- code has implementations matching `docs/specs/25-memory-recall-ranking.md`;
- docs include the same signatures;
- `MemoryGraph(edges, memory_ids=...)` appears for clustering tests/docs;
- expression does not mention direct/association knobs.

- [ ] **Step 6: Inspect git diff**

Run:

```bash
git status --short --branch
git diff --stat
git diff --check
```

Expected:
- no unrelated files;
- no whitespace errors;
- only planned code/docs/tests changed.

- [ ] **Step 7: Commit quality fixes**

If Step 1-6 required changes:

```bash
git add <changed-files>
git commit -m "fix(memory): satisfy recall quality gate"
```

If no changes were needed, skip this commit.

## Self-Review Checklist

- [ ] Spec coverage: every requirement in `docs/specs/25-memory-recall-ranking.md` maps to Task 1-8.
- [ ] Placeholder scan: plan contains no placeholder tokens, deferred-implementation wording, or vague “write tests for the above” phrasing.
- [ ] Type consistency: `MemoryEdgeKind`, `KeywordSearchHit`, `AssociationHit`, `PersistSemanticHit`, `direct_limit`, and `association_limit` are named consistently across tasks.
- [ ] Candidate bounds: vector recall, keyword recall, semantic persistence, edge building, LLM relation candidates, and fallbacks all have explicit caps.
- [ ] Docs sync: facts table and specs are updated after code so they describe implemented reality.
- [ ] Test inventory: every task that changes tests updates `docs/test-inventory.md`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-13-memory-recall-ranking.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
