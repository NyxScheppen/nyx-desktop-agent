status: DONE

files changed:
- `nyx/memory/store.py`
- `nyx/memory/retrieval.py`
- `nyx/memory/facade.py`
- `tests/test_memory/test_store.py`
- `tests/test_memory/test_retrieval.py`
- `docs/test-inventory.md`
- `.superpowers/sdd/2026-09-13-memory-recall-ranking/task-2-report.md`

tests run and exact result:
- `pytest tests/test_memory/test_store.py -q` after writing failing tests: `8 failed, 16 passed in 2.06s` (`search_keywords` missing and old `upsert_edge` signature).
- `pytest tests/test_memory/test_store.py -q`: `24 passed in 0.59s`.
- `pytest tests/test_memory/test_store.py tests/test_db/test_db.py -q`: `41 passed in 1.09s`.
- `ruff check` before line-length cleanup: failed with 5 `E501` line-too-long errors.
- `ruff check`: `All checks passed!`.
- `pytest tests/test_memory/test_store.py tests/test_db/test_db.py -q`: `41 passed in 0.95s`.
- `pyright` before call-site cleanup: 18 errors from removed `search_keyword` and old `upsert_edge` calls.
- `pyright`: `0 errors, 0 warnings, 0 informations`.
- `pytest tests/test_memory/test_store.py tests/test_db/test_db.py tests/test_memory/test_retrieval.py tests/test_memory/test_facade.py -q`: `104 passed in 17.36s`.
- `pytest -q`: `800 passed, 1 skipped in 25.10s`.

commits made:
- `feat(memory): add capped keyword and typed edge store`

self-review notes and concerns:
- Implemented `KeywordSearchHit`, `search_keywords(tokens, limit)`, canonical typed `list_edges`, `upsert_edge`, `delete_edges`, and `list_edge_degrees`.
- Removed `search_keyword` from `MemoryStore`.
- Kept Task 2 boundary: no ANN, graph association rewrite, retrieval fusion, facade edge-building algorithm, configuration knob, or extra Repository/Service/Manager layer.
- Updated retrieval/facade/test retrieval call sites minimally so `pyright` remains clean before Task 5/6: retrieval still preserves the old temporary keyword/vector/association flow, and facade legacy edge building writes `MemoryEdgeKind.SEMANTIC` with `created_at=0.0`.
- `Memory.created_at` remains creation time and is not refreshed by `update_many`, `strengthen`, or `record_recall`.
- Concern: full public spec sync for 07/08/09 and memory facts remains deferred to the plan's later docs task; this task only updated `docs/test-inventory.md` as requested by the Task 2 brief.
