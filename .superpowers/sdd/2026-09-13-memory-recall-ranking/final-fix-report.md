# Final Fix Report: Memory Recall Zero-Cosine ANN Candidates

## Status

Fixed the final review finding in `nyx/memory/retrieval.py`.

`MemoryRetrieval._vector_scores()` no longer drops ANN candidates whose cosine is `0.0` or negative. Every `AnnIndex.query(...)` result is now added to the vector direct-candidate pool with `vector_score = (cosine + 1.0) / 2.0`, clamped to `[0, 1]`, matching `docs/specs/25-memory-recall-ranking.md` and `docs/memory-system-facts.md`.

## Root Cause

The ranking refactor implementation kept a stale positive-cosine filter:

```python
if candidate.cosine <= 0.0:
    continue
```

That filter belonged to older cosine-only ranking behavior, but the confirmed recall-ranking contract says ANN candidates are included and normalized into low vector scores. Because `_rank_direct()` derives `SearchMode.VECTOR` from presence in `vector_scores`, skipped zero-cosine candidates were omitted from direct recall and lost their `VECTOR` source.

## Changes

- Removed the `candidate.cosine <= 0.0` skip in `nyx/memory/retrieval.py`.
- Added `test_search_sources_zero_cosine_vector_candidate` in `tests/test_memory/test_retrieval.py`.
- Updated `test_search_fuses_vector_keyword_and_limits_direct_then_association` because the orthogonal keyword candidate is now also correctly marked as `VECTOR`.
- Updated `docs/test-inventory.md` to reflect the new regression test and adjusted source expectation.

## TDD Evidence

The new regression test was written before the production fix.

Red run:

```text
pytest tests/test_memory/test_retrieval.py::test_search_sources_zero_cosine_vector_candidate -q
FAILED: expected ["A"], got []
```

Green run after removing the filter:

```text
pytest tests/test_memory/test_retrieval.py::test_search_sources_zero_cosine_vector_candidate -q
1 passed
```

## Verification

Required pytest:

```text
pytest tests/test_memory/test_retrieval.py tests/test_expression/test_expression_facade.py::test_reply_slow_records_recall -q
16 passed
```

Focused lint:

```text
ruff check nyx/memory/retrieval.py tests/test_memory/test_retrieval.py docs/test-inventory.md
All checks passed!
```

Focused type check:

```text
pyright nyx/memory/retrieval.py tests/test_memory/test_retrieval.py
0 errors, 0 warnings, 0 informations
```

Pyright also emitted its usual available-version notice: `v1.1.411 -> v1.1.414`.

## Concerns

No blocking concerns. Git reported LF-to-CRLF warnings for touched files during diff display, consistent with the local checkout's line-ending configuration.
