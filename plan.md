# Discussion Partner Implementation Plan

## Execution Rules

- Follow `docs/specs/15-discussion-partner.md` in order: P0 -> P1 -> P2 -> P3 -> P4.
- Each phase is implemented in isolation, then verified against that phase's specification acceptance criteria.
- After verification, stop and wait for explicit user approval before starting the next phase.
- If implementation changes a contract, object boundary, phase scope, or key design decision, stop and resubmit the plan for approval.
- Preserve existing user changes in the worktree.
- Before each work round, read this file and the applicable `AGENTS.md` files.

## Requirements and Assumptions

- The default companion path must not load discussion prompts, citation rules, knowledge context, or discussion tools.
- Discussion uses the existing `LlmClient.complete(tools=...)` and `LLMOutput.tool_calls` interfaces.
- Tool calling uses bounded text reinjection; no new message-based tool loop is introduced.
- `reading_evidence` remains the physical table; the contract concept is `knowledge_chunk`.
- Existing facts are not guessed into `source_chunk_id`.
- Annotation anchors use the existing SPEAK event `event_id`.
- The specification requires agent-driven end-to-end scenarios; no new pytest unit tests are required by the feature.

## P0 - Discussion Skill Skeleton

### Scope

- Add `should_load_discussion(message: str) -> bool`.
- Establish discussion patterns and citation discipline.
- Keep the default companion path free of discussion capability.
- Make no schema changes.

### Object Integrity Audit

- Entry: `nyx/expression/classifier.py`.
- Consumers: `nyx/expression/prompt.py`, `pipeline.py`, and `facade.py`.
- State transition: ordinary companion path versus discussion path.
- Bad cases: empty input, ordinary chat, explicit discussion intent, false-positive keywords.

### Expected Files

- `nyx/expression/classifier.py`
- `nyx/expression/prompt.py`
- `nyx/expression/pipeline.py`
- Existing expression regression or E2E driver files only when required by the acceptance scenario.

### Acceptance and Verification

- Ordinary messages do not load discussion prompt content, citation discipline, or knowledge context.
- Discussion intent is detected reliably for the specified scenarios.
- Existing default companion behavior does not regress.
- Run the relevant existing expression regression tests and the specification's automatic-trigger E2E scenario.

## P1 - Tool-calling Capability

### Scope

- Split the base prompt.
- Add declarative skill loading and manual `discussion=true` control.
- Add complete schemas for `memory_recall` and `knowledge_search`.
- Add bounded text-reinjection tool calling.
- Force the slow path for explicit or automatically detected discussion.

### Audit and Expected Files

- Audit `nyx/tools/registry.py`, expression pipeline/facade, `nyx/app_context.py`, and `nyx/bootstrap.py`.
- Preserve implicit retrieval behavior outside discussion mode.

## P2 - Two-layer Knowledge Base and Manual Ingestion

### Requirement and Approval State

- The user selected P2 first on 2026-10-23 after learning that P3 depends on unfinished P2.
- Deliver searchable source text and explicitly linked knowledge facts from manual text/URL input and existing reading/fetch paths.
- The user approved the phase scope, transaction-contract adjustment and consumer expansion on 2026-10-23. P2 implementation and phase verification are complete, with the existing host-test limitation below; the user explicitly authorized committing P2 after the acceptance report.

### Key Assumptions and Contract Decision

- Keep the physical `reading_evidence` table and existing `book:` / `web:` / `local:` source identities. Do not infer links for legacy facts.
- Specification 15 requires a committed chunk before digest and fact persistence. Existing specifications 09/12 require evidence and the final cursor update to commit together. These cannot both hold while knowledge facts commit independently with an immediate chunk foreign key.
- Proposed order: save a recoverable raw-source pending snapshot; persist its chunk idempotently; digest and save the extracted pending result; write knowledge with `source_chunk_id`; then advance cursor/profile and clear pending atomically. Replay uses the stored source snapshot and completed extraction, rather than changed/refetched source text.
- On digest, knowledge, or final-checkpoint failure, already committed chunks/knowledge remain and the old cursor/pending permit retry. A chunk can therefore be searchable before its reading task finishes. This replaces the old evidence/final-cursor rollback guarantee and must be synchronized in specifications 06/09/12/15.
- Existing pending rows without source text retain their old recovery behavior and receive no guessed chunk. Existing facts and chunks with NULL provenance/embedding stay valid.
- Generic chunks share the existing reflection consumer: newly ingested real source text can become pending reading evidence. `consumed_at` controls reflection consumption only; it never removes a chunk from knowledge search. This consumer expansion will be explicit in the contracts and acceptance report.
- Preserve the existing activity-summary path (`remember_activity`); do not present an activity summary as external source evidence.
- API creation counts must come from successful inserts in this ingestion call, not global before/after table counts. Extend the existing persistence return data as needed and document any compatible return-value change to `remember_knowledge`; existing callers may ignore it.

### Object Integrity Audit

| Object | Entries and consumers | State, failure, replay, deletion |
|---|---|---|
| Source chunk | EPUB, web tasks, exploration, manual text/URL, registered web_fetch; knowledge_search, fact provenance, existing reflection | Empty text creates nothing; bounded blocks are immutable; exact/semantic dedup stays within source; embedding failure leaves a NULL vector; replay reuses the chunk; consumed chunks remain searchable; deletion sets fact references to NULL |
| Knowledge fact provenance | remember_knowledge -> persistence -> fact-store apply; fact search/recent, REST, TypeScript, knowledge_search | Explicit per-call chunk only; no FactCandidate field and no legacy backfill; failed fact extraction retains source/memory; duplicate facts keep their established provenance; chunk deletion preserves the fact |
| Source pending/checkpoint | EPUB, web task and exploration preparation/recovery | Persist source snapshot before extraction; retain raw/extracted pending on failure; commit final cursor/profile/pending together; legacy pending remains readable; no use of unrelated or latest sources as fallback |
| Manual ingestion request | KnowledgeFeed -> client -> POST /api/knowledge/sources -> current character runtime | At least one nonblank text/URL; URL obeys web_enabled and existing SSRF checks; partial success reports actual created results; retry deduplicates; frontend ignores stale completion after character switching |

### Implementation Steps and Acceptance Checkpoints

1. **Schema and provenance.** Add migration 43 for nullable chunk location/embedding and fact source_chunk_id with ON DELETE SET NULL. Extend ReadingEvidence/MemoryFact, all SELECT/row mappings, REST and TS shapes. Pass one chunk ID through remember_knowledge -> _persist_memory -> _commit_memory -> fact-store apply. Verify a version-42 database upgrades without data loss or guessed links; deleting a chunk preserves facts.
2. **Chunk persistence and retrieval.** Reuse EmbedFn, AnnIndex and existing cosine thresholds. Keep original source text bounded to 6000 characters. Deduplicate chunks within their source and preserve knowledge-memory kind/source dedup. Search only knowledge facts, merge linked chunks with eligible vector/keyword candidates, deduplicate IDs, and preserve source metadata. Verify same-source replay, cross-source isolation, unrelated queries, consumed chunks and NULL embeddings. Bound each returned chunk to 2000 characters and the chunk block to 12000; retain P1's final tool-output limits.
3. **Reconnect ingestion.** Use existing MemoryFacade/Store rather than a new service layer. Reconnect EPUB, web-task and exploration source snapshots to chunk-first persistence and explicit fact provenance. At composition time distinguish the underlying fetch operation from the model-facing fetch tool: the registered tool ingests fetched text, while checkpoint-driven readers own their per-block ingestion, avoiding a second digest of the same fetch. Preserve URL network checks and snippet fallback attribution. Verify crash/replay windows with real shared SQLite, including changed remote text and old pending rows.
4. **Manual API and UI.** Add POST /api/knowledge/sources with text/url and actual created_memories/created_facts/chunks results. Add the specified KnowledgeFeed component, client/types, visible entry, submitting/error/success states and character-scope protection. Success text is “已进知识库”. Verify text, URL, both inputs, blank input, disabled web, unsafe URL, failed extraction and retries.
5. **Review and phase acceptance.** Synchronize affected contracts, facts, frontend client/chat documentation, tech-reference and test-inventory. Review against LessonsLearned, especially source-scoped dedup and checkpoint commit boundaries. Run agent-driven HTTP/SSE E2E, backend ruff/pyright/full existing pytest, and frontend tests/build. Report each P2 criterion with evidence and limitations; then stop for P3 approval.

### Expected Files

- Backend: `nyx/db.py`, `nyx/types.py`, `nyx/memory/{store,facts,retrieval,facade}.py`, `nyx/reading/integration.py`, `nyx/activity/{facade,exploration}.py`, `nyx/tools/web_fetch.py`, `nyx/app_context.py`, `nyx/api/routes.py`.
- Frontend: `frontend/src/types/api.ts`, `frontend/src/api/client.ts`, new `frontend/src/components/chat/KnowledgeFeed.tsx`, its existing chat mount/style files, and existing API/UI regression files if their fixtures/contracts require changes.
- Documentation: this plan; specifications 01/04/05/06/09/12/15 as affected; corresponding memory/activity/reading/bus facts; frontend client/chat contracts; `docs/tech-reference.md`, `docs/test-inventory.md`, and `docs/LessonsLearned.md` if review produces a reusable lesson.
- Add no new pytest unit tests. Use temporary agent E2E drivers with isolated SQLite and scripted LLM/embedding; state separately whether real-model citation quality was verified.

### Alternatives Considered

- Recommended: chunk-first durable stages with source-snapshot replay, following specification 15. This changes the old checkpoint boundary but reuses existing persistence and pending mechanisms.
- Rejected: hold a single outer transaction across chunk, digest, knowledge and cursor. Existing memory persistence opens its own transactions; avoiding nesting would require broader transaction/event changes and could hold the DB lock across LLM/embedding I/O.

## P2 Performance Follow-up (approved 2026-10-23)

- The user approved the three-step fix plan after read-only probes confirmed all four findings. Execute one step per approval gate; P3 remains unapproved.
- Baseline: three searches over 1000 chunks each load 1000 rows and build ANN; 30 inserts reload 435 previous rows/build 30 indexes; five digest items invoke five singleton batches; a 200000-character web task writes 103 checkpoints containing 20600000 full-body characters.
- Preserve provenance, source-local dedup, numeric/time/negation guards, NULL-vector rules, reflection consumption and serialized output budgets.

1. **Chunk search/dedup (current step).** Maintain per-facade incremental global/source ANN and substring postings; use bounded candidate queries, indexed raw-content hashes and a transactional SQLite ID-only change journal. Cold start loads once; committed insert/update/delete/cascade changes synchronize incrementally. Transaction-local queries use a disposable view so rollback cannot contaminate the resident cache. Keep indexes isolated by facade/database. Sorted runs avoid shifting or sorting every existing ID on insertion/query. Verify 1000/10000 chunks, dense buckets, common keywords, incremental writes, source isolation, substring thresholds, deletion, rollback, restart and linked NULL vectors. Stop after acceptance.
2. **Fact batching (pending step approval).** Fill source metadata then call existing remember_knowledge(items) once per raw block. Verify one batch extraction for five items, actual counts, attribution and partial failure/retry.
3. **Web snapshots (pending step approval).** Add an immutable task-source snapshot table with task FK CASCADE, atomically migrate legacy source_text, retain bounded checkpoint/pending and all existing durable stages. Verify a single full-body save for 200000 characters, changed remote text, restart and failure replay. This explicitly amends specification 09's no-activity-subtable constraint.

- Object audit: all five existing ingestion paths and store replay feed the same chunk index; consumers remain knowledge search and source-local dedup. The derived journal/cache carries no new user-facing state or fallback. Empty/missing chunks produce no candidates; failed transactions roll back journal entries; duplicate writes keep IDs; deletion evicts derived entries and leaves fact FK SET NULL. Cold restart reconstructs from SQLite.
- Files: step 1 nyx/db.py, nyx/memory/{ann,store,facade}.py; later steps activity/{store,facade}.py. Synchronize specifications 06/09/15 as affected, corresponding facts, LessonsLearned, plan and verification inventory. No new service layer/configuration/frontend change.
- Verification: temporary agent drivers with isolated real SQLite and scripted embeddings/LLM, existing relevant regressions, Ruff/Pyright each step; full backend suite at final step. Preserve and report the previously reproduced host unload-drain limitation.

### Step 1 Acceptance (2026-10-24)

- Implemented schema 44 raw-hash exact lookup and transactional ID change journal, resident global/source ANN and substring postings, incremental writes/deletes and isolated transaction-local views. No changes to fact batching or web checkpoints in this step.
- Temporary real-SQLite driver: 1000 and 10000 chunks each produced 0 warm evidence rows, 0 ANN builds and 2991 merged candidate visits across 3 searches. Each 30-write run inserted 30 chunks and synchronized only 30 rows without an ANN rebuild. Timings were approximately 0.022s/0.027s for the 3 warm queries and are indicative, not a latency guarantee.
- Passed 90 reference comparisons against HEAD's original ANN candidate/order behavior and incremental removal/reinsert; same-dimension projection sharing across sources, empty-source eviction and ANN dimension reset after deleting its last item; source isolation beyond the global candidate cap; raw exact/semantic replay and numeric/time/negation guards; keyword coverage/NULL-linked evidence/consumed evidence; update/delete/cascade/SET NULL; rollback and sequence reuse; transaction commit, synchronization failure recovery, facade/database isolation, 43-to-44 migration and indexed EXPLAIN; restart/cross-connection updates, lock-order concurrency and serialized output budgets.
- Existing affected regressions: initial 3 failures were stale table/index snapshots; after synchronizing existing assertions and fixing review findings, the full affected set was rerun together: 972 passed. No new pytest tests. Ruff/Pyright and diff whitespace checks passed. Full backend/HTTP-SSE/real-model quality were not rerun in this step; previous host unload-drain limitation remains as recorded below.
- Contracts 06/15, memory facts, LessonsLearned and test inventory synchronized. The specification and standards reviews found an empty-ANN dimension bug and repeated per-source projection allocation; both were reproduced, fixed and rechecked. Stop here for the user's approval before step 2; no commit authorized this round.

## P3 - Deep Research

### Scope

- Add `nyx/research/`.
- Inject `web_search` and `web_fetch` handlers.
- Implement adaptive deepening with bounded budget.
- Persist both source pages and synthesis conclusions in the knowledge base.

### Audit and Expected Files

- Audit research entrypoints, handler injection, budget termination, and knowledge ingestion.
- Cover empty search, fetch failure, budget exhaustion, and duplicate sources.

## P4 - Annotation Feedback

### Scope

- Add the `annotation` request payload.
- Inject annotation context into prompts.
- Add `annotation_feedback` persistence and `ANNOTATION_FEEDBACK` events.
- Feed annotation feedback into the fact/reflection intermediary.
- Add frontend selection, annotation, and send interactions.

### Audit and Expected Files

- Audit `nyx/api/routes.py`, `nyx/db.py`, expression/memory/event flow, and frontend chat types/client/store/components.
- Use SPEAK `event_id` as the annotation anchor.
- Cover missing selection, stale event IDs, empty annotations, and duplicate submissions.

## Cross-phase Verification

- Run the relevant backend regression tests with `python -m pytest`.
- When frontend code is involved, run the frontend test command from `frontend/`.
- Run the specification-defined agent-driven E2E scenarios for the current phase.
- Check `docs/LessonsLearned.md`, `docs/test-inventory.md`, applicable contracts/facts, and shared `AGENTS.md`/`CLAUDE.md` rules before completion.

## Current Gate

- Plan approved by user.
- `plan.md` written.
- P0 implementation and specification acceptance completed.
- P0 review findings fixed: companionship/negation no longer trigger discussion; knowledge questions without punctuation trigger discussion and the slow path.
- P0 follow-up verification: isolated HTTP/SSE E2E passed; expression tests passed; full backend suite has one reproduced failure in the unrelated character-unload drain path (`1288 passed, 1 failed, 1 skipped`).
- P1 implementation was explicitly approved by the user and is complete.
- P1 contract and engineering reviews completed; contracts, facts, frontend docs, and test inventory synchronized.
- P1 verification: ruff check . passed; pyright reported 0 errors; backend 1289 passed / 1 skipped; frontend 357 passed across 25 files; TypeScript/Vite production build passed.
- P1 agent HTTP/SSE E2E: 11 scenarios passed plus strict discussion payload validation. Verified automatic/forced modes, ordinary slow-path retrieval/tool compatibility, scope isolation, bounded multi-round reinjection, authorization checks, failed tools, duplicate calls, round/item limits, and durable segment recall counts.
- E2E used scripted LLM/embedding with real HTTP/SSE, EventBus, and isolated SQLite. Real-model discussion style and citation quality remain unverified; 2000/8000-character limits were source-reviewed.
- At P1 acceptance, knowledge_search returned knowledge facts and empty chunks, with null source_chunk_id; P2 now adds source chunks and explicit provenance. deep_research still returns an explicit unavailable result until P3.
- P2 approved scope is implemented: migration 43, source-local chunk dedup/search, explicit fact provenance, durable source-snapshot recovery, registered fetch ingestion, manual text/URL API and character-scoped KnowledgeFeed UI. Contracts/facts/frontend docs/test inventory/LessonsLearned synchronized.
- P2 agent E2E: 8 ingestion/search HTTP/SSE groups and 4 failure/recovery groups passed using real FastAPI/EventBus/isolated SQLite with scripted LLM/embedding. Covered migration/SET NULL, counts/retry, partial success, ingestion-to-tool-to-source reply, saved snapshots, no guessed legacy links, consumed/NULL-vector search, source dedup and serialized budget. Real-model discussion/citation quality remains unverified.
- P2 frontend: 361 passed across 25 files; TypeScript/Vite production build passed. Ruff and Pyright passed. Review fixes include saved-chunk digest and source-name replay, malformed URL/IDNA partial success, complete JSON chunk budgets, disabled-web raw-fetch injection and the existing 0.45 keyword coverage gate. Final 8-group HTTP/SSE rerun and two targeted real-DB probes passed after the last fixes; memory regressions passed 113 tests and API/fetch regressions passed 20 tests.
- P2 backend: first full run 4 failed / 1285 passed / 1 skipped; three old fixtures were corrected and 135 reading/reflection tests passed. The remaining host unload-drain test also hung in an independent unchanged P1 commit snapshot (over 75s). Excluding only that test, 1288 passed / 1 skipped / 1 deselected. The full-suite quality gate is not all green; no host behavior was changed.
- Fact extraction/storage remains best-effort: raw chunks and Memory survive failure, but duplicate Memory does not retry missing facts. This inherited limitation is explicit in specifications 06/15.
- The user explicitly authorized committing P2 after the acceptance report disclosed the known quality-gate limitation. P3 remains unapproved; wait for a separate user instruction before starting it.
- P2 performance follow-up step 1 is implemented and verified as recorded above. Await user approval before step 2 (fact batching); step 3 and P3 remain unapproved. Current follow-up changes are uncommitted.
