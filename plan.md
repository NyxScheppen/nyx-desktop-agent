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

- The user approved the three-step fix plan after read-only probes confirmed all four findings. Execute one step per approval gate. On 2026-10-24 the user authorized committing completed step 3 and starting P3.
- Baseline: three searches over 1000 chunks each load 1000 rows and build ANN; 30 inserts reload 435 previous rows/build 30 indexes; five digest items invoke five singleton batches; a 200000-character web task writes 103 checkpoints containing 20600000 full-body characters.
- Preserve provenance, source-local dedup, numeric/time/negation guards, NULL-vector rules, reflection consumption and serialized output budgets.

1. **Chunk search/dedup (completed and committed).** Maintain per-facade incremental global/source ANN and substring postings; use bounded candidate queries, indexed raw-content hashes and a transactional SQLite ID-only change journal. Cold start loads once; committed insert/update/delete/cascade changes synchronize incrementally. Transaction-local queries use a disposable view so rollback cannot contaminate the resident cache. Keep indexes isolated by facade/database. Sorted runs avoid shifting or sorting every existing ID on insertion/query. Verify 1000/10000 chunks, dense buckets, common keywords, incremental writes, source isolation, substring thresholds, deletion, rollback, restart and linked NULL vectors. Committed as 6e64bd6 on the user's instruction, 2026-10-24.
2. **Fact batching (completed and committed).** Fill source metadata then call existing remember_knowledge(items) once per raw block. Verify one batch extraction for five items, actual counts, attribution and partial failure/retry. On 2026-10-24 the user also approved an optional created_counts accumulator: update it after each successful persistence, preserve the existing exception/retry boundary, and sum per-block progress even on failure. The return value remains the counts for that call. A real-SQLite probe reproduced the need: failure on item 3 leaves 2 committed memories/facts but a plain batch call reports 0/0. No new durable object, consumer, fallback or batch-wide transaction is introduced; chunk/source/fact object boundaries remain those audited above. Committed as 1668230 on the user's instruction, 2026-10-24.
3. **Web snapshots (completed and committed as 34d201f).** Add an immutable task-source snapshot table with task FK CASCADE, atomically migrate legacy source_text, retain bounded checkpoint/pending and all existing durable stages. Verify a single full-body save for 200000 characters, changed remote text, restart and failure replay. This explicitly amends specification 09's no-activity-subtable constraint. The user authorized starting step 3 after committing step 2 on 2026-10-24.

- Object audit: all five existing ingestion paths and store replay feed the same chunk index; consumers remain knowledge search and source-local dedup. The derived journal/cache carries no new user-facing state or fallback. Empty/missing chunks produce no candidates; failed transactions roll back journal entries; duplicate writes keep IDs; deletion evicts derived entries and leaves fact FK SET NULL. Cold restart reconstructs from SQLite.
- Files: step 1 nyx/db.py, nyx/memory/{ann,store,facade}.py; later steps activity/{store,facade}.py. Synchronize specifications 06/09/15 as affected, corresponding facts, LessonsLearned, plan and verification inventory. No new service layer/configuration/frontend change.
- Verification: temporary agent drivers with isolated real SQLite and scripted embeddings/LLM, existing relevant regressions, Ruff/Pyright each step; full backend suite at final step. Preserve and report the previously reproduced host unload-drain limitation.

### Step 1 Acceptance (2026-10-24)

- Implemented schema 44 raw-hash exact lookup and transactional ID change journal, resident global/source ANN and substring postings, incremental writes/deletes and isolated transaction-local views. No changes to fact batching or web checkpoints in this step.
- Temporary real-SQLite driver: 1000 and 10000 chunks each produced 0 warm evidence rows, 0 ANN builds and 2991 merged candidate visits across 3 searches. Each 30-write run inserted 30 chunks and synchronized only 30 rows without an ANN rebuild. Timings were approximately 0.022s/0.027s for the 3 warm queries and are indicative, not a latency guarantee.
- Passed 90 reference comparisons against HEAD's original ANN candidate/order behavior and incremental removal/reinsert; same-dimension projection sharing across sources, empty-source eviction and ANN dimension reset after deleting its last item; source isolation beyond the global candidate cap; raw exact/semantic replay and numeric/time/negation guards; keyword coverage/NULL-linked evidence/consumed evidence; update/delete/cascade/SET NULL; rollback and sequence reuse; transaction commit, synchronization failure recovery, facade/database isolation, 43-to-44 migration and indexed EXPLAIN; restart/cross-connection updates, lock-order concurrency and serialized output budgets.
- Existing affected regressions: initial 3 failures were stale table/index snapshots; after synchronizing existing assertions and fixing review findings, the full affected set was rerun together: 972 passed. No new pytest tests. Ruff/Pyright and diff whitespace checks passed. Full backend/HTTP-SSE/real-model quality were not rerun in this step; previous host unload-drain limitation remains as recorded below.
- Contracts 06/15, memory facts, LessonsLearned and test inventory synchronized. The specification and standards reviews found an empty-ANN dimension bug and repeated per-source projection allocation; both were reproduced, fixed and rechecked. The user subsequently authorized committing step 1 and starting step 2; commit 6e64bd6 contains step 1 only.

### Step 2 Acceptance (2026-10-24)

- User authorized committing step 1 and starting step 2, then explicitly approved the optional created_counts parameter after the interrupted-batch probe exposed lost progress counts.
- ingest_source fills every item with the stored chunk's topic/name, then makes one remember_knowledge call per block. Five points now make one fact_extraction call instead of five. Extraction maps memory_index to the individual persisted Memory and shares only that block's source_chunk_id.
- Per-item persistence remains independent. The optional accumulator preserves committed progress on a later exception; returned counts remain local to the call. An injected third-item insert failure reports 2 memories/2 facts, preserves them, then replay creates only the remaining 3/3. No new durable state, transaction layer or fallback.
- Five temporary real-SQLite acceptance groups passed: five-point counts/provenance/replay; empty/single/multiple blocks/source isolation; digest partial failure, extraction fallback and fact-store failure; interrupted-batch retry; additive accumulator/duplicates/blanks/absent fact store. Uses real EventBus and scripted LLM, without new pytest files. Existing fact best-effort limitations remain; duplicate Memory does not repair missing facts.
- Existing regressions: memory 190 passed; API endpoints/web_fetch/reading integration/activity 277 passed, 467 total. Ruff/Pyright passed. Full backend suite is reserved for final step 3; HTTP/SSE and real-model quality were not rerun here. Historical host unload-drain limitation remains.
- Contracts 06/15, memory facts, LessonsLearned and test inventory synchronized. Standards and spec reviews passed with no blocking findings; spec review independently reran all five temporary acceptance groups. Clarified that batch extraction precedes per-item dedup: replay can still make one LLM call, but duplicate Memory skips fact writes and missing facts are not repaired. Subsequently committed as 1668230 on the user's instruction to commit and begin step 3.

### Step 3 Acceptance (2026-10-24)

- Schema 45 moves only the top-level string source_text from WEB task checkpoints into assigned_task_source, with task FK CASCADE; table/data/checkpoint/version changes share one migration transaction. Other fields and task kinds remain unchanged. Empty legacy strings retain the old completion behavior; invalid types remain untouched. New sources are validated and clipped to 200000 characters before the first immutable save.
- ActivityStore reads by task ID and returns the first stored body on conflict. The web runner fetches outside a transaction, commits the source before blocks, then keeps the existing pending/evidence/digest/knowledge/final-cursor stages. Checkpoints and task lists no longer load the full body. Task completion/failure retains the snapshot; deletion cascades it.
- Temporary real-SQLite acceptance used real ActivityFacade/MemoryFacade/FactStore/EventBus with scripted LLM. The red baseline failed because full source text remained in checkpoints. Green: 200000 characters / 34 chunks / 34 facts, one full-body insert and 102 checkpoint writes; maximum checkpoint length was 6570 characters under the scripted output. The original 103 checkpoints repeated 20600000 full-body characters. The measured checkpoint maximum is not a hard cap on arbitrary model outputs.
- All 11 injected failure windows passed with database close/reopen and changed remote text: source insert/commit, pending save/commit, evidence, digest, extraction save/commit, partial knowledge write, final save/commit. Committed snapshots prevent refetch; failed source commits permit refetch. Extracted pending skips digest, legacy pending replays before fetch and keeps NULL provenance when no chunk ID was stored.
- Migration from 44, atomic rollback, restart/idempotence, task/database isolation, first-write preservation, cascade deletion, size validation, standalone cancellation and outer-transaction rollback passed. Review reproduced SQLite NUL truncation/counting; migration now decodes/slices JSON in Python and the Store checks Python length. NUL suffix preservation, Chinese/emoji clipping and oversized NUL rejection are included in the temporary driver. Existing schema assertions updated without adding pytest tests.
- After the final NUL-boundary fix, existing activity/DB regressions: 210 passed. Full backend excluding only the known host unload-drain test: 1288 passed / 1 skipped / 1 deselected in 246.75s. That isolated test was also rerun this round and failed with TimeoutError; the full-suite quality gate remains unmet. Ruff/Pyright passed. No frontend changes; HTTP/SSE, frontend and real-model quality were not rerun for this performance step.
- Contracts 04/09/15, activity/bus facts, tech-reference, LessonsLearned and test inventory synchronized. Standards review passed with no violations or actionable smells. Spec review passed after the NUL migration/length issue was reproduced and fixed; its independent real-SQLite probe confirmed NUL/non-BMP preservation, pending retention, size rejection and first-write behavior. The user authorized committing step 3 and starting P3 on 2026-10-24.

## P3 - Deep Research

### Scope

- Add `nyx/research/`.
- Inject `web_search` and `web_fetch` handlers.
- Implement adaptive deepening with bounded budget.
- Persist both source pages and synthesis conclusions in the knowledge base.

### Implementation and Acceptance (authorized 2026-10-24)

- Step 3 was committed as `34d201f`; P3 is authorized under spec 15 sections 7.1/7.2. No P4 work or automatic P3 commit.
- Add `nyx/research/__init__.py` with run_research and build_deep_research_tool. Inject raw search/fetch, shared LlmClient and MemoryFacade.ingest_source at the composition root, with None handlers when web is disabled.
- Keep per-call budgets (at most 4 total subqueries, 6 unique attempted URLs, 2 search depths, 6000 characters/source and full report, 120 seconds including ingestion); no new user configuration or persistent research job.
- Validate evidence quotes and URLs against fetched text; validate report/compressed citations again. Treat fetched text as data in LLM prompts. Use exact excerpts labelled unfinished when synthesis is unavailable; never invent a conclusion on timeout.
- Use one timeout scope with completed local results retained. Sources are ingested as fetched; conclusions use the full validated report and explicit report-chunk provenance. Timeout/cancellation stops research orchestration and further LLM/ingestion steps; an already-running read-only HTTP thread from the existing raw handlers may finish under its own network timeout. No persistent research job or automatic recovery is added. Inherited best-effort fact limitations still apply.
- The return envelope (report + ingested + notice) fits 2000 serialized characters, counting JSON overhead. LLM compression gets the available budget; whole entries are retained or omitted, never sliced JSON. The final expression block also preserves complete deep_research JSON when little room remains.
- Add compatible per-chunk `dedup` metadata to ingest_source via per-insert bookkeeping (no global counts or before/after timestamp inference). Existing callers and return fields retain their meaning. The user explicitly approved the optional Facade/Store record_reading_evidence created_ids set on 2026-10-24; standalone inserts record after successful commit, caller-owned transaction sets are provisional and must be discarded on rollback.
- Object audit: research is a new bounded source/conclusion writer; old search, fact provenance, reflection and deletion consumers use the existing two-layer path. Reports are per-call ephemeral; only chunks/knowledge persist. Empty/failed/partial/replayed/deleted sources follow existing knowledge semantics. No new events, DB migration, API, frontend or retry queue.
- Expected code files: nyx/research/__init__.py, nyx/app_context.py, nyx/memory/{facade,store}.py (dedup bookkeeping only), nyx/expression/{pipeline,prompt}.py (complete result boundaries only).
- Documentation: specs 05/06/11/15/04 as affected, memory/expression/bus facts, tech-reference, test-inventory, LessonsLearned.
- Red/green temporary acceptance drivers (no new pytest unit tests): adaptive new questions; original and conclusion facts with correct chunk IDs; duplicate URLs/replay; disabled web/empty search/fetch and LLM failures; invalid quotes/URLs/JSON; source/report/serialized/time limits; partial timeout/cancellation; HTTP/SSE research -> reply -> knowledge_search. Then existing regressions, Ruff/Pyright, full backend with the known unload-drain issue separately disclosed, and two-axis code review.

### P3 Historical Review Checkpoint (2026-10-24; before supplemental approval)

- Five temporary acceptance groups passed, including real FastAPI HTTP/SSE, EventBus and isolated SQLite with scripted LLM/network; five boundary groups passed. These verify routing, structure, provenance and budgets, not real-model semantic quality.
- Existing full backend excluding only the known unload-drain test: 1288 passed / 1 skipped / 1 deselected. After the source-budget review fix, Ruff/Pyright and diff whitespace checks passed; existing memory/eval/expression/API/tools regressions: 575 passed / 1 skipped. The full-suite quality gate remains unmet because of the previously reproduced baseline failure.
- Two-axis review found excess searches after reaching six attempted sources. A red probe reproduced four searches/six fetches; the authorized fix now stops at two searches/six fetches and skips the deepen call. The five acceptance and five boundary groups passed again after this fix.
- The approved created_ids bookkeeping passed real-SQLite probes for actual insert, exact and semantic hits, cross-source isolation, ignored INSERT, failed standalone commit and caller-owned transaction rollback (discard provisional set).
- At that checkpoint, two red probes remained: timeout during source_digest left one committed chunk with zero returned receipts; all five research LLM stage types were absent from eval_log. Independent real-SQLite review also reproduced a commit-confirmation window: the write is committed but the cancelled await has not returned, so neither created_ids nor an upper-level accumulator can certify its result. Both implementation gaps are now closed under the approved supplemental plan below; the unconfirmed-commit boundary is explicitly disclosed.

### P3 Supplemental Plan (explicitly approved 2026-10-24)

Requirement: accurately report committed chunks when ingestion is interrupted, and record research model calls through the existing eval path. The previous approval covered record_reading_evidence.created_ids only; the following compatible signatures are additional changes.

1. Add keyword-only `committed_chunks: list[dict[str, str]] | None = None` to `MemoryFacade.ingest_source`. Immediately after a successful chunk write/dedup return, before awaiting digest or further reads, append the compact `{chunk_id, source_topic, dedup}` receipt to this caller-owned accumulator, deduplicating IDs. Research passes its per-call receipt list so timeout preserves committed progress. Existing return fields, exceptions, chunk-first commits and retries keep their current meaning; no callback, new table, task or transaction layer. In research the receipts describe committed standalone writes; callers using an outer transaction must discard provisional receipts on rollback.
2. Add keyword-only `evaluator: Evaluator | None = None` to `run_research` and `build_deep_research_tool`; inject the already-created evaluator from app_context. Evaluate every successful research LLMOutput before parsing JSON, including malformed JSON outputs. Isolate eval failures from otherwise valid model output, propagate cancellation, and keep eval awaits within the research deadline. Direct scripted callers can omit it; production wiring always supplies it.

User explicitly approved all three supplemental items on 2026-10-24, including the following commit-confirmation boundary: interrupted ingestion returns every confirmed receipt that fits the existing response budget. A commit may finish in SQLite before its cancelled await confirms success; in that window, do not guess a new/hit marker or perform work past the deadline. Explicitly state that ingestion was interrupted and receipts may be incomplete, directing callers to source lookup/idempotent retry. Already-confirmed receipts must survive later digest/fact cancellation. No background reconciliation or new status object is introduced.

Object audit: existing source chunks gain only an ephemeral progress consumer; all legacy ingestion/search/reflection/provenance/deletion consumers remain covered. Existing EvalRecord/eval_prompt gain research outputs via Evaluator, with the existing local storage, query, token dedup and failure semantics. No persistent states, events, user configuration or fallback decisions are added. Empty/dedup/failure/partial/replay/delete cases retain source semantics; eval replay uses a new LLM call ID as before.

Expected files: nyx/research/__init__.py, nyx/app_context.py, nyx/memory/facade.py; specs 04/05/06/10/15 and related facts/index/inventory/lessons as needed. No edits to Evaluator, schema, network handlers or frontend.

Validation: turn both real-SQLite red probes green; pause during digest, fact extraction and conclusion ingestion and compare durable IDs with returned receipts/new-hit markers; test failed chunk commit and replay. Reproduce actual commit followed by a stalled confirmation await; require an explicit incomplete-receipt notice, no fabricated receipt, and successful idempotent discovery on retry. Verify research eval rows, tokens, final prompt lookup, malformed JSON recording, eval-store failure isolation and composition-root injection. Rerun five acceptance groups, five boundaries, relevant existing memory/eval/expression/API/tools regressions, Ruff/Pyright and two-axis review. No new pytest unit tests.

### P3 Final Acceptance (2026-10-24; user authorized commit)

- Implemented the approved committed_chunks accumulator, Evaluator injection and incomplete-receipt disclosure. Confirmed source/conclusion receipts survive digest/fact cancellation; unconfirmed commits do not fabricate new/hit and can be discovered by source lookup/idempotent retry.
- Five acceptance groups (including real HTTP/SSE), five boundary groups and six supplemental real-SQLite groups passed. Verified adaptive discovery, original/conclusion provenance/search/replay, exact citations, budgets, complete prompt JSON, interruption/transaction boundaries, production eval wiring, five stage types, token/call IDs/full prompts, malformed JSON recording and eval failure/cancellation isolation. No new pytest tests.
- Final backend regression excluding only the previously reproduced host unload-drain baseline: 1288 passed / 1 skipped / 1 deselected in 125.79s. Ruff/Pyright passed. The complete pytest quality gate remains unmet because the baseline case is excluded; no host behavior changed.
- Standards and Spec code reviews found no remaining code findings; stale approval text has been synchronized. Contracts/facts/index/inventory/LessonsLearned updated. Scripted LLM/network with real SQLite/EventBus/HTTP/SSE verifies plumbing, not real-model semantic/discussion quality.
- The user approved committing P3 and starting P4 on 2026-10-24. P4 begins with read-only investigation and a detailed implementation plan for approval under the root review rule.

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
- At P1 acceptance, knowledge_search returned knowledge facts and empty chunks, with null source_chunk_id; P2 added source chunks and explicit provenance. P3 replaces the old deep_research placeholder.
- P2 approved scope is implemented: migration 43, source-local chunk dedup/search, explicit fact provenance, durable source-snapshot recovery, registered fetch ingestion, manual text/URL API and character-scoped KnowledgeFeed UI. Contracts/facts/frontend docs/test inventory/LessonsLearned synchronized.
- P2 agent E2E: 8 ingestion/search HTTP/SSE groups and 4 failure/recovery groups passed using real FastAPI/EventBus/isolated SQLite with scripted LLM/embedding. Covered migration/SET NULL, counts/retry, partial success, ingestion-to-tool-to-source reply, saved snapshots, no guessed legacy links, consumed/NULL-vector search, source dedup and serialized budget. Real-model discussion/citation quality remains unverified.
- P2 frontend: 361 passed across 25 files; TypeScript/Vite production build passed. Ruff and Pyright passed. Review fixes include saved-chunk digest and source-name replay, malformed URL/IDNA partial success, complete JSON chunk budgets, disabled-web raw-fetch injection and the existing 0.45 keyword coverage gate. Final 8-group HTTP/SSE rerun and two targeted real-DB probes passed after the last fixes; memory regressions passed 113 tests and API/fetch regressions passed 20 tests.
- P2 backend: first full run 4 failed / 1285 passed / 1 skipped; three old fixtures were corrected and 135 reading/reflection tests passed. The remaining host unload-drain test also hung in an independent unchanged P1 commit snapshot (over 75s). Excluding only that test, 1288 passed / 1 skipped / 1 deselected. The full-suite quality gate is not all green; no host behavior was changed.
- Fact extraction/storage remains best-effort: raw chunks and Memory survive failure, but duplicate Memory does not retry missing facts. This inherited limitation is explicit in specifications 06/15.
- The user explicitly authorized committing P2 after the acceptance report disclosed the known quality-gate limitation, and authorized starting P3 on 2026-10-24.
- P2 performance follow-up steps 1, 2 and 3 are committed as 6e64bd6, 1668230 and 34d201f. The user explicitly approved the P3 supplemental signatures and commit-confirmation boundary on 2026-10-24. P3 implementation and functional acceptance are complete with the quality-gate limitation above disclosed. The user authorized committing P3 and starting P4 investigation; P4 implementation awaits its detailed plan approval.
