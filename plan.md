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
- P3 was committed as `f12ffda` (`feat(research): add bounded deep research with knowledge provenance`). No P4 implementation is included in that commit.

## P4 - Annotation Feedback

### Requirement, Approval State and Execution

- **Goal:** 用户选择当前角色的一条 SPEAK 回复，引用其中片段并发送批注；本轮回复能看到批注，反馈另行持久化，经 LLM 中介更新画像/对话事实，纠错进入现有反思链路。
- **Spec:** `docs/specs/15-discussion-partner.md` 第 8 节、批注对象完整性、前端契约和批注 E2E；共享契约沿用 specs 01/04/06/08/11。
- **Architecture:** 复用当前 Chat API、ExpressionFacade、MemoryFacade/Store、FactStore 和 durable EventBus。增加两条独立消费路由，避免反馈抽取等待阻塞表达，也避免回复终局短路丢失尚未记录的反馈。
- **Tech stack:** 现有 Python/FastAPI/SQLite/LangGraph 和 React/TypeScript/Zustand；不增加依赖、配置项、服务层或新源文件。
- **Approval:** P3 已按授权提交为 f12ffda；用户已明确批准本节完整 P4 实施方案。4.1–4.5 实现、文档同步与验收完成，用户已在收到验收报告后明确授权提交 P4。
- 批准后在本会话按以下检查点顺序执行，执行方式采用 `superpowers:executing-plans`。每个检查点先红后绿；P4 完整验收后报告并等待用户审批，不自动提交。
- 本节同时提交新增类型、签名、schema、路由、反思 reason 和持久回执语义供审批，避免实施时隐式扩大契约。若这些关键决定变化，先更新本计划重新审核。

### Key Assumptions and Contract Supplements for Approval

1. `annotation` 是可选的本轮用户输入。无该字段时保留现有 prompt、讨论三态、问题绑定和发送行为；批注本身不强制讨论模式或慢通道。
2. 锚点必须是当前角色库中精确 ID 对应的 SPEAK；不是“最新回复”。`quote` 必须是该 SPEAK 正文中的连续原文片段，保留原样，不裁切、不改写。历史 SPEAK 也可以批注。
3. 三个嵌套字段均为严格字符串、禁止全空白：`reply_event_id` 长度 1–128，`quote` 和 `comment` 各 1–2000 个 Unicode 字符。前端按相同字符计数限制。缺字段、错误类型、空白或超长返回 422；未知/跨角色 ID 返回 404；非 SPEAK、引用不匹配及同时传 `annotation`/`reply_to` 返回 422。无法查证锚点或受理事件失败沿用 503，不降级猜测锚点。
4. 批注不会回答另一条 waiting 问题：前端发送时跳过隐式/显式 reply_to，后端在批注分支跳过 `answer_waiting`。普通聊天仍沿用旧绑定规则；不改变 waiting 的暂停、超时或恢复策略。
5. 去重键严格使用原始 `(reply_event_id, comment)`，不 trim 后判等。同键不同 quote 时第一条已记录快照保留；每次新请求的 quote 仍需通过锚点校验。反馈记录和中介幂等；用户再次发送消息仍是独立聊天回合，不承诺 HTTP 重试只产生一次回复。
6. **“记录”与“学到事实”分开。** API 对带批注的成功受理增加 `annotation_persistence: "recorded" | "pending"`。只有已查证存在反馈行才返回 recorded；首次请求通常 pending，查反馈状态失败也只记日志并返回 pending。`ANNOTATION_FEEDBACK` 的提交后 SSE 回执把 UI 升为“反馈已记录”，不表示事实抽取或反思已完成。
7. 录入故障走现有 delivery 的至多 5 次尝试及 dead_letter，不引入永久重试器。重启恢复未完成 delivery；达到 dead_letter 后保持待确认，用户可按同键再次发送以重试。丢回执只显示未确认；重载查询既有事件日志，或同键发送时确认，绝不把超时当作已保存。
8. LLM 调用、JSON 解析失败是 best-effort：保留已记录反馈、记日志并结束该次中介，不做正则/确定性事实或纠错兜底。事实 SQL 或派生事件提交失败则回滚整次中介事务并交 delivery 重试。外部 LLM/eval 均在事务外；取消不吞掉。反馈旁路故障与回复隔离；全库不可用仍受既有受理/终局持久化边界约束。
9. 批注不直接写 `knowledge` 事实、Memory 知识或证据块，不把引用当作外部真相；复用 profile/conversation 的已有查询、去重及时间语义。只记录用户明确表达的自身信息或对回复的反馈。反思保留既有时间过期检查、CAS、effect 和 REFLECTION_DONE 机制。

### Object Integrity Audit

| 对象 | 入口与写入 | 消费者与发现 | 状态、失败、恢复、删除 |
|---|---|---|---|
| ReplyAnnotation | Chat API 校验；USER_MESSAGE 保留完整已受理快照；重放复用该快照 | runtime → reply → ReplyState → build_user_prompt；前端用户消息投影 | 无批注无变化；无效输入不受理；锚点仅精确查询；不新增单独删除；之后原事件不可用也不改写已受理快照 |
| annotation_feedback | 独立 USER_MESSAGE 消费者；schema 46 新表 | 同键状态查询；ANNOTATION_FEEDBACK 中介；前端提交回执 | 未记录 → 同事务记录行/事件/effect；失败回滚后重试；同键只一个 canonical 事件；不做历史回填；无独立删除功能，随角色数据库既有生命周期处理 |
| 反馈派生事实 | ANNOTATION_FEEDBACK → 统一 fact_extraction → FactStore.apply | 现有 profile/conversation 查询、表达检索和事实 API | LLM 失败保留原反馈且不猜测；成功候选/REFLECTION/effect 原子提交；重放成功 effect 不重复抽取；沿用事实时间/去重/删除语义，不增加反馈专属事实来源列 |
| 纠错 REFLECTION | 中介严格 bool 判定为纠错后派生，携带反馈 event ID 和有界原文/批注摘要 | 现有 inner_life.reflection；状态与 REFLECTION_DONE 的旧消费者 | 新 reason annotation_correction；原有过期/CAS/事务/effect 规则；失败由反思 delivery 恢复，不回滚已记录反馈，不直接改慢变量 |
| annotationDraft 与回执状态 | 单条 SPEAK 选区/点击；发送前捕获角色代数与完整草稿 | ChatInput、MessageBubble、chatStore、SSE dispatch、history | 草稿 → 发送 → pending/recorded；录入成功与回复成功分别处理；失败保留草稿；成功仅清理同代且未编辑的草稿；切换/reset 清空，旧回包不能污染新角色 |

| Bad case | 决定与验收目标 |
|---|---|
| 空 | annotation 缺失/null 沿用普通聊天；字段缺失、全空白、无选中回复或空批注拒绝发送/422 |
| 失败 | 锚点查证失败不受理；反馈单独录入失败仍能回复且 UI 待确认；LLM/eval 失败隔离，事实 SQL 失败不得留半套事实/effect/反思 |
| 部分完成 | 回复已终局但反馈未记录，独立 recording delivery 仍可恢复；反馈已记录但中介失败不撤销回执；回执只证明记录，不能宣称学会 |
| 乱序 | 回执可早于 POST 响应或 USER_MESSAGE SSE，按原始键合并且 recorded 不降为 pending；前端按角色代数拦截迟到成功、失败及 timer |
| 重放 | 同键串行/并发请求只有一行/一个反馈事件/一次成功中介；事务失败可重试，已有 effect 跳过 LLM；无批注旧 USER_MESSAGE 消费为 no-op |
| 删除 | 不增加批注删除 API；表不设会因锚点清理而级联删除反馈的 FK，保留 quote 快照；事实和角色删除沿用既有路径 |

### Interfaces, Schema and Event Chain

以下均为本计划明确提请批准的接口；普通调用方可省略新增可选参数。`ReplyAnnotation` 同样用于前后端数据形状，不增加通用封装。

```python
class ReplyAnnotation(TypedDict):
    reply_event_id: str
    quote: str
    comment: str

# EventBus：按 event_log 主键精确读取，复用现有 Event 行映射。
async def get_event(self, event_id: str) -> Event | None: ...

# MemoryStore：find 返回 canonical ANNOTATION_FEEDBACK 的 event_id；record 参加外层事务。
async def find_annotation_feedback(self, reply_event_id: str, comment: str) -> str | None: ...
async def record_annotation_feedback(self, event: Event) -> bool: ...

# MemoryFacade：API 只经门面查询；两个 handler 由 subscriptions 绑定 consumer_id。
async def has_annotation_feedback(self, reply_event_id: str, comment: str) -> bool: ...
async def record_annotation_feedback(self, event: Event, consumer_id: str) -> None: ...
async def apply_annotation_feedback(self, event: Event, consumer_id: str) -> None: ...

# 保留 reply 的全部原参数，在末尾加 annotation=None；ReplyState 增加同类型可选字段。
async def reply(self, msg: str, correlation_id: str, reply_to: str | None = None,
                activity_context: ActivityContext | None = None,
                discussion: bool | None = None,
                annotation: ReplyAnnotation | None = None) -> None: ...
def build_user_prompt(message: str, context: list[Message],
                      annotation: ReplyAnnotation | None = None) -> str: ...
```

上述为接口签名。`_AnnotationPayload` 是 routes 内与现有 `_ChatPayload` 同风格的私有 Pydantic 输入模型；其字段/限制按上节执行，`_ChatPayload.annotation` 默认 None。

```sql
CREATE TABLE annotation_feedback (
    event_id TEXT PRIMARY KEY,
    reply_event_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    comment TEXT NOT NULL,
    user_event_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(reply_event_id, comment)
);
```

- `event_id` 为 canonical ANNOTATION_FEEDBACK 的 ID；`user_event_id` 是首次记录它的 USER_MESSAGE ID；created_at 使用 canonical 事件时间。不增加 FK、独立 status/retry 表或 hash 索引；UNIQUE 自带索引服务精确键查找。迁移 45 → 46，已有 `annotations` 阅读笔记表保持原用途。
- `EventType.ANNOTATION_FEEDBACK` 的值为 `annotation_feedback`，content 为 `{reply_event_id, quote, comment, user_event_id}`；沿用首次 USER_MESSAGE 的 correlation_id，SSE 继续使用已有 event_id，不增加锚点字段。

```text
POST /api/chat → USER_MESSAGE（完整 annotation 快照）
  ├─ expression.user_message → 注入批注 → 正常回复
  └─ memory.annotation_recording → 行 + ANNOTATION_FEEDBACK + recording effect（同事务）
       └─ memory.annotation_feedback → LLM（事务外）
            → profile/conversation 事实 + 可选 REFLECTION + mediation effect（同事务）
                 └─ inner_life.reflection → 既有反思链路
```

- 只在 `ROUTE_SPECS` 增加 `USER_MESSAGE / memory.annotation_recording / record_annotation_feedback` 和 `ANNOTATION_FEEDBACK / memory.annotation_feedback / apply_annotation_feedback`，subscriptions 解析它们；兼容 ROUTING 由源表派生。
- recording 先排除无批注/已有 effect；事务内重新确认 effect，以 UNIQUE 插入行，只有实际新增才 append canonical 事件，同时写 effect。提交后 announce；缺失唤醒由既有扫描恢复。重复键不重建事件、不覆盖第一份快照。
- mediation 先查 effect，再调用 LLM；事务内再次检查并原子应用候选/append 纠错反思/写 effect。并发成功副作用唯一；失败回滚 effect。LLM best-effort 跳过也完成本次消费，避免无限付费重放；不把它标成事实更新成功。

### LLM Mediation Contract

复用 `_FACT_EXTRACTION_SYSTEM`、`parse_fact_extraction`、`LlmClient.complete(module="memory", output_type="fact_extraction", json_mode=True)` 及现有 Evaluator。仅本批注调用附加严格的来源规则与以下输出扩展，不修改普通事实抽取形状或 FactCandidate。

```json
{
  "entities": [],
  "facts": [
    {"scope":"profile","subject":"用户","subject_type":"person","predicate":"回复偏好","object":"希望先说明不确定性","asserted":true,"mode":"multi","polarity":1},
    {"scope":"conversation","subject":"用户","subject_type":"person","predicate":"回复反馈","object":"认为这次回答过于肯定","asserted":true,"mode":"multi","polarity":1}
  ],
  "is_correction": true
}
```

- prompt 用结构化输入分别标记 quote（Nyx 旧回复，仅供定位）和 comment（用户反馈）；要求只从 comment 的明确用户表达抽取，外部知识纠错只能记为“用户认为/指出某回复有误”。
- 顶层必须有 facts 数组与严格 bool 的 is_correction，非法 JSON/形状整次跳过并日志。逐项 scope 只允许 profile/conversation，asserted 必须 true；按 scope 拆组后复用 `parse_fact_extraction(source_scope="quoted")` 的数量、名称和关系模式约束。
- 代码再次限制候选 subject 为“用户”；conversation 谓词仅允许“回复反馈”。未知 scope、外部主体、未断言候选丢弃。profile 仅接收用户自身的明确属性/偏好。绝不调用 knowledge 摄入/词法 fallback；语义归因质量仍需单独说明真实模型是否验收。
- 对两组候选分别 `FactStore.apply(candidates, None, "profile" | "conversation")`，valid_from 使用反馈事件时间；不创建新的 Memory 或伪造 source_memory_id/source_chunk_id。
- is_correction=true 时即使 facts 为空也 append 一个 REFLECTION；reason=`annotation_correction`，evidence 沿用既有 `{source_id, summary}`（source_id=反馈 event_id，summary 总计≤1000 字符，含 quote/comment）。不直接改人格/叙事；false 不触发。补齐 inner_life reason 白名单及展示标签。
- LLM 成功输出在解析前进入 Evaluator，非法 JSON 同样记账；eval 失败只记日志，不丢有效候选，取消继续传播。

### Implementation Checkpoints and Acceptance

#### 4.1 — 输入、锚点和完整 prompt 传递

**Files:** `nyx/types.py`、`nyx/api/routes.py`、`nyx/events/bus.py`、`nyx/runtime.py`、`nyx/expression/{facade,pipeline,prompt}.py`；同步 specs 01/04/11/15 及 expression/bus facts。

- [x] **Red:** 仓库外临时 E2E driver 构造真实隔离 SQLite/EventBus/FastAPI，发布已知 SPEAK 后 POST 批注；断言受理 USER_MESSAGE 携带完整快照、捕获的表达 LLM user prompt 含 `[对上轮回复的批注]`、quote/comment。当前代码缺少快照/上下文，必须观察到失败。
- [x] **Green:** 增加上节输入模型和精确 get_event；校验成功后 annotation 跟随 durable USER_MESSAGE 进入 runtime/reply/ReplyState；在共有 user_prompt 调用点传给 build_user_prompt。历史为空也包含批注块；引用按数据展示，不作为 system 指令。保留 runtime 的签名兼容与回复终局短路，反馈另由 4.2 路由处理。
- [x] **验收:** FAST、SLOW、讨论工具判断/最终回复及续写 prompt 均保留批注；无 annotation 的 prompt 与原行为一致。覆盖未知/跨角色/非 SPEAK、引用拼接或篡改、空白、错类型、超长、中文/emoji、旧历史 SPEAK、annotation+reply_to；有 waiting 问题时批注不 claim 它，普通回复仍能回答。
- [x] **检查:** 数据和签名文档先同步再写实现；只验证当前已交付的上下文能力，不把尚未实现的回执/中介当完成。

#### 4.2 — 持久记录、独立消费和可确认回执

**Files:** `nyx/db.py`、`nyx/enums.py`、`nyx/memory/{store,facade}.py`、`nyx/events/routing.py`、`nyx/subscriptions.py`、`nyx/api/routes.py`；同步 specs 01/04/06/15 与 memory/bus facts。

- [x] **Red:** 临时 driver 发送相同键的两次/并发批注，查 SQL 行、事件及 effect；暂停 recording 后先完成 SPEAK，重启恢复。当前无表/独立路由/回执，断言失败。
- [x] **Green:** 增加原子 schema46 迁移、Store 和 Facade 的查询/recording 接口及 recording 路由，按上节事务算法落库。此检查点只注册 recording；4.3 实现中介 handler 后再注册 mediation 路由。API 只附加只读已记录/pending 状态，不等待消费者，反馈状态查询单独失败不拒绝已通过锚点验证的聊天。
- [x] **验收:** 45 库升级数据/旧阅读批注不丢失；迁移注入错误时表/version 一起回滚。相同键、不同 quote、并发键及跨角色隔离符合定义；每键仅一行/一个事件。行插入、事件 append、effect 和 commit 逐处失败均不留半套状态；重试/重启恢复。回复已终局不妨碍 recording；无批注消息 no-op；反馈数据库单点注入失败时表达仍输出且回执状态 pending。
- [x] **检查:** 已 recorded 同键请求能返回 recorded；dead_letter 不伪称可无限自愈。4.2/4.3 在同一 P4 变更内配齐后才作最终运行验收，不发布中间状态。

#### 4.3 — 反馈事实与纠错反思

**Files:** `nyx/memory/facade.py`、`nyx/inner_life/{facade,reflection}.py`；同步 specs 06/08/15 与 memory/inner-life facts；复用 FactStore/parser，不改其通用签名。

- [x] **Red:** 临时 driver 用脚本化 fact_extraction 分别输出用户偏好、纠错、称赞及带外部实体/knowledge scope 的候选，断言只有允许的 profile/conversation 事实且纠错有 REFLECTION。当前无中介，断言失败。
- [x] **Green:** 实现 apply_annotation_feedback 的 JSON 校验/过滤、事务外 LLM/eval、事务内事实/反思/effect，再注册 mediation 路由及 subscriptions handler；补 annotation_correction reason 与证据标签，保留旧反思准备/提交/CAS/过期规则。
- [x] **验收:** 用户偏好可经已有事实 API 查询；批评写成用户反馈而非世界真相。原 quote 里的断言、外部主体、unknown scope、缺 asserted 不入事实；不创建知识事实/Memory/证据块。true/false/空 facts/非 bool/坏 JSON/LLM 异常均按契约处理。eval 的 token、调用历史、最终 prompt 可查，eval 故障不影响有效输出；取消不被吞掉。
- [x] **检查:** SQL apply、REFLECTION append、effect、commit 分别注入失败，验证全回滚且 delivery 可重试；成功重放不重复调用 LLM/事实/反思。故意遗漏 recording 提交后 announce，重启/扫描仍处理 durable feedback delivery。反思失败不影响反馈记录和回复；独立恢复、REFLECTION_DONE 和过期事件行为可观测。分别说明记录、候选提交、反思完成三个结果。

#### 4.4 — 单条 SPEAK 批注 UI 与发送状态

**Files:** `frontend/src/types/api.ts`、`frontend/src/api/{client,dispatch}.ts`、`frontend/src/hooks/useSSE.ts`、`frontend/src/stores/chatStore.ts`、`frontend/src/components/chat/{MessageBubble,ChatInput}.tsx`、`frontend/src/index.css`；现有测试 `frontend/tests/{api,stores,sse,characterScope}.test.ts` 和 `frontend/tests/chatPanel.test.tsx`；同步 frontend 01/02/03/05 和 test-inventory。

```ts
export interface ReplyAnnotation {
  reply_event_id: string;
  quote: string;
  comment: string;
}
// ChatRequest 增加 annotation?: ReplyAnnotation，保留 message/reply_to/discussion。
// postChat 的前三个参数不变，在末尾加 annotation?: ReplyAnnotation。
// 返回 {event_id: string; annotation_persistence?: "recorded" | "pending"}。
// ChatMessage 增加 annotation?: ReplyAnnotation，供用户消息投影/历史和 quote 展示。
annotationDraft: ReplyAnnotation | null;
annotationPersistence: Record<string, "pending" | "recorded">;
beginAnnotation(event_id: string, quote: string): void;
updateComment(text: string): void;
cancelAnnotation(): void;
sendAnnotation(): Promise<boolean>;
```

- [x] **Red:** 先在上述现有 frontend 测试中覆盖 SPEAK 选择→编辑→发送 payload；回执先到、请求晚回；A→B→A 后旧请求/失败不清新草稿；现有代码缺少 action/界面/解析，观察失败。
- [x] **Green:** MessageBubble 只允许已完整显示的 SPEAK 进入批注。原生选区必须完全属于同一正文 span；无选区点击可预填整条≤2000字符的回复，更长时提示选择片段，不静默截断。跨气泡选区不选取；ASK/THINK/读书问题不进入批注。ChatInput 内显示引用、comment 文本域、取消/发送；不增加组件抽象。
- [x] **Green:** sendMessage/sendAnnotation 共用现有发送锁、reply tracker、earlyReplies、超时和角色代数保护。sendAnnotation 使用 `message=comment` 加完整 annotation，并沿用 discussionMode；只跳过 waiting 绑定，普通输入草稿保留。失败保留批注，成功仅清掉同代且完全未编辑的原草稿；新编辑/新选择不被旧请求清掉。
- [x] **Green:** SSE 注册 annotation_feedback，parser 保留 USER_MESSAGE.annotation，dispatch 按键 `JSON.stringify([reply_event_id, comment])` 合并持久回执。发送前建立本地键，回执先到也不丢失；POST 的 pending 不能覆盖 recorded。用户批注消息显示引用和“反馈持久化待确认”/“反馈已记录”；不宣称画像已更新。
- [x] **验收:** 普通聊天、讨论三态、等待问题选择、双击锁、类型打字机与历史回填不回归。reset/角色切换清空草稿和本地回执状态。loadHistory 同时读既有 annotation_feedback 事件，按键恢复已确认状态，缺回执保守 pending；乱序到来的历史不能降级已确认状态。API 422/404/503 可见且草稿保留；同键 canonical 回执能确认所有对应消息。

#### 4.5 — 完整 E2E、同步文档和阶段验收

**Files:** 上述实现文件；`docs/specs/{01-types,04-module-bus-system,06-memory-system,08-inner-life,11-expression,15-discussion-partner}.md`、`docs/facts/{memory-system,expression-system,module-bus-system,inner-life-system}-facts.md`、`docs/frontend/{01-sse,02-stores,03-chat-panel,05-client}.md`、`docs/tech-reference.md`、`docs/test-inventory.md`、`docs/LessonsLearned.md` 和本计划。既有后端测试只有在枚举/路由/schema/调用签名断言确因本次契约变化需要同步时修改，不新增 pytest 单测；允许同步的范围为 `tests/test_db/test_db.py`、`tests/test_event/{test_routing,test_event,test_bus}.py` 和 `tests/test_api/{test_subscription,test_endpoints,test_context,test_tick_loop}.py`。

- [x] **端到端主线:** 启动隔离真实 HTTP/SSE 后端，以脚本化 LLM/embedding 驱动“收 SPEAK → 选原文 → POST 批注 → 同轮 prompt 与回复 → 持久回执 → profile/conversation 事实 API → 纠错 REFLECTION/REFLECTION_DONE”。临时 driver 保存在仓库外，记录可复现命令、断言结果与失败窗口；不使用真实用户数据。
- [x] **故障主线:** 重跑 4.1–4.4 的输入、事务、幂等、重启、乱序、角色隔离及 LLM 故障场景；对比 annotation_feedback/event_log/event_delivery/event_effect/memory_fact 和前端可见状态。单独验证反馈旁路不直接写知识，普通讨论工具仍按已有授权工作。
- [x] **质量门:** 先查 LessonsLearned，运行 `ruff check .`、`pyright`、`python -m pytest`；前端运行 `npm test -- --run` 和 `npm run build`。若全量再次出现已知宿主 unload-drain 失败，单列失败结果并只为定位该基线补跑排除该例的回归，不能把全量标记通过，也不扩大到宿主修复。
- [x] **Review:** 按 code-review skill 做规范与 spec 两轴审查；核对所有新增对象入口/消费者、事务重放、签名、来源边界、前后端事件集合、测试快照和新增教训。不触碰用户现有 CLAUDE.md 改动、两份已删除设计文档和 data/；本次不修改工程规则。
- [x] **验收报告:** 逐条列出 spec15 批注验收项、HTTP/SSE/实库证据、前端测试构建、完整质量门结果。脚本化 LLM 只证明流程和结构；真实模型对批注的理解与学习质量是否另行验收要明确说明。报告后等待用户审批，不自动提交。

### Alternatives and Plan Self-review

- 选择独立 durable recording + mediation 两消费者：若录入仅挂在 expression.user_message，回复终局后的重放短路会跳过未完成反馈；若在 Chat API 同步抽取，则增加响应等待并把反馈失败耦合到聊天受理。
- pending/recorded 复用既有 event_log/SSE/history；不新增状态端点、轮询器或重试任务。事实不新增来源外键；原始反馈证据留在表和 canonical 事件中。
- 自审覆盖：输入/锚点/prompt 对应 4.1；表/事件/录入失败/幂等对应 4.2；中介/知识边界/纠错反思对应 4.3；选择/草稿/回执/角色隔离对应 4.4；主线 E2E/现有回归/文档与质量门对应 4.5。
- 自审一致性：唯一事件名 annotation_feedback；去重、查询、前端回执都使用同一原始键；三个可选 annotation 参数与前端字段一致；不把 recorded 当作事实更新完成；无未决定的数据对象、接口或状态分支。

**P4 审核状态（2026-10-24）**：用户已明确批准以上完整实施方案；4.1–4.5 已完成，用户已在收到验收报告后明确授权提交。

### P4 Acceptance Results（2026-10-24，用户已授权提交）

- **4.1 输入与上下文：通过。** 真实 HTTP/SSE 验证完整批注快照随 USER_MESSAGE 持久化并进入同轮表达 prompt；FAST、SLOW、讨论工具判断/最终回复/续写均保留引用与评论。严格类型、非空/Unicode 长度、精确 SPEAK 锚点、连续原文、互斥 reply_to 和 404/422/503 边界通过；跨角色锚点用隔离数据库验证。批注不强制讨论通道，也不认领 waiting，后续普通回复仍能回答原问题。
- **4.2 持久记录与回执：通过。** schema45→46 保留旧阅读笔记/批注（含中文/emoji）；迁移失败回滚。原始键并发去重、首次快照、每键唯一行/事件、录入行/事件/effect/提交四处失败与恢复通过。表达终局后仍独立录入，丢失 announce 后扫描/重启恢复；同键重放返回 recorded。录入连续5次失败进入 dead_letter 时，聊天仍回复且回执 pending，未伪称已记录或无限自愈。
- **4.3 反馈事实与反思：通过。** 现有事实 API 可查 profile/conversation 候选；仅保留明确用户归因，conversation 限制为“回复反馈”，不直接写知识事实、Memory 或证据块。true/false/空事实/非法 JSON/非 bool、LLM/eval 故障、取消及四处事务失败/重试通过。实际 eval HTTP 验证合法/非法 JSON 的 token、调用历史和完整 prompt；纠错 REFLECTION 的证据、失败隔离/恢复、REFLECTION_DONE 和过期 no-op 通过。
- **4.4 前端：通过。** 既有5个文件新增15项测试，覆盖单条已完成 SPEAK 选区/长文限制、编辑/发送/错误保留、共享发送锁、waiting 旁路、回执先到/recorded 不降级、历史恢复和角色/reset 隔离。规格审查发现 loadHistory 仅检查角色 scope 后，补充本地 generation 守卫；events/pending 两个 await 阶段回归均先红后绿。UI 通过组件/store 测试验收，未另做真实浏览器人工操作验收。
- **4.5 E2E 与质量门：通过。** 仓库外临时驱动完成真实 SQLite/EventBus/FastAPI 的3组 HTTP/SSE 主线、5组边界、4组补充与回滚计数探针。完整后端 **1289 passed / 1 skipped（120.38s）**，未排除用例，历史宿主 unload-drain 失败本次未复现；前端 **376 passed / 25文件**，TypeScript/Vite 生产构建、Ruff、Pyright（0 errors / 0 warnings）通过。工程规范审查无发现，规格审查的历史恢复问题已修复并验证；相关契约、事实、前端文档、测试快照和教训已同步。
- **验收边界：** LLM/外部输出使用脚本化替身，验证流程、结构和故障边界；真实模型对批注的理解、学习效果以及讨论风格/引用质量仍未验收，spec15 总体 E2E 对应项保留未完成。P3 已提交 `f12ffda`；用户已在收到上述验收结果和限制后授权提交 P4。

本机临时驱动复现命令（文件在仓库外，不随代码提交）：

```powershell
python $env:TEMP\nyx-p4-acceptance.py
python $env:TEMP\nyx-p4-boundaries.py
python $env:TEMP\nyx-p4-counter.py
python $env:TEMP\nyx-p4-extra.py
```

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
- P2 performance follow-up steps 1, 2 and 3 are committed as 6e64bd6, 1668230 and 34d201f. The user explicitly approved the P3 supplemental signatures and commit-confirmation boundary on 2026-10-24. P3 is committed as f12ffda. The detailed P4 plan was approved; P4 implementation and the 4.1–4.5 acceptance above are complete, with the real-model quality limitation disclosed. The current full backend run passes without exclusions. The user explicitly authorized committing P4 after receiving the acceptance report.
