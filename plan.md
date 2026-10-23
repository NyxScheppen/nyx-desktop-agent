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

### Scope

- Add `location` and `embedding` to `reading_evidence`.
- Forward-fill `memory_fact.source_chunk_id` for explicitly linked new facts.
- Implement `knowledge_search` and `POST /api/knowledge/sources`.
- Unify ingestion and deduplicate within the same `kind + source_topic` boundary.

### Audit and Expected Files

- Audit `nyx/db.py`, `nyx/memory/{facts,store,retrieval,facade}.py`, `nyx/api/routes.py`, and related contracts/facts.
- Cover empty sources, duplicate ingestion, cross-topic names, and legacy rows without embeddings.

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
- P1 is paused pending explicit user approval.
