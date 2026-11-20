import frontendPolicy from "../../../../nyx/settings/frontend.json";
import memoryPolicy from "../../../../nyx/settings/memory.json";
import { FormEvent, useEffect, useState } from "react";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import { useMemoryStore } from "../../stores/memoryStore";
import { formatDateTime } from "../../lib/time";
import type { FactScope, Memory } from "../../types/api";
import Panel from "../layout/Panel";
import FactGraph from "./FactGraph";
import { KnowledgeFeed } from "../chat/KnowledgeFeed";

const FACT_VIEWS: { scope: FactScope; label: string }[] = memoryPolicy.frontend.MemoryPanel.FACT_VIEWS as { scope: FactScope; label: string }[];

// 记忆面板：普通记忆与知识库共用一个入口，知识库内包含条目和事实网。
export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
  const conversations = useMemoryStore((s) => s.conversations);
  const conversationsError = useMemoryStore((s) => s.conversationsError);
  const conversationsMore = useMemoryStore((s) => s.conversationsMore);
  const conversationsLoading = useMemoryStore((s) => s.conversationsLoading);
  const loadConversations = useMemoryStore((s) => s.loadConversations);
  const [filter, setFilter] = useState("all");
  const facts = useMemoryStore((s) => s.facts);
  const query = useMemoryStore((s) => s.query);
  const factScope = useMemoryStore((s) => s.factScope);
  const error = useMemoryStore((s) => s.error);
  const factsError = useMemoryStore((s) => s.factsError);
  const refresh = useMemoryStore((s) => s.refresh);
  const search = useMemoryStore((s) => s.search);
  const selectFactScope = useMemoryStore((s) => s.selectFactScope);
  const [activeView, setActiveView] = useState<"memories" | "facts" | "feed">("memories");
  const [mode, setMode] = useState<"memory" | "knowledge">("memory");
  const [input, setInput] = useState(query);
  const [page, setPage] = useState(0);
  const conversationEntries: (Omit<Memory, "kind"> & { kind: "conversation" })[] = (conversations ?? []).map((item) => ({
    id: item.id, created_at: item.ended_at, summary: item.summary || frontendPolicy.copy.MemoryPanel.text,
    content: item.raw_content, kind: "conversation", type: item.long_term ? "long_term" : "short_term",
    topics: [], freshness: 1, recall_count: item.recall_count, sources: [],
  }));
  const visibleData = data === null && (mode === "knowledge" || conversations === null) ? null : [...(data ?? []), ...conversationEntries]
    .filter((memory) => mode === "knowledge" ? memory.kind === "knowledge"
      : memory.kind !== "knowledge" && (filter === "all" || memory.kind === filter))
    .sort((a, b) => b.created_at - a.created_at);
  const kindLabels: Record<string, string> = { conversation: frontendPolicy.copy.MemoryPanel.conversation, episode: frontendPolicy.copy.MemoryPanel.episode, activity: frontendPolicy.copy.MemoryPanel.activity, lesson: frontendPolicy.copy.MemoryPanel.lesson, reflection: frontendPolicy.copy.MemoryPanel.reflection, user_profile: frontendPolicy.copy.MemoryPanel.user_profile, reading: frontendPolicy.copy.MemoryPanel.reading, interaction: frontendPolicy.copy.MemoryPanel.interaction };
  const pages = Math.max(1, Math.ceil((visibleData?.length ?? 0) / 5));
  const currentPage = Math.min(page, pages - 1);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    setActiveView("memories");
    setInput("");
    setPage(0);
    if (mode === "knowledge") {
      void search("", "knowledge");
    } else {
      void search("", null);
    }
  }, [mode, search]);

  const submitSearch = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setPage(0);
    void search(input, mode === "knowledge" ? "knowledge" : null);
  };

  return (
    <Panel title={frontendPolicy.copy.MemoryPanel.title}>
      <div className="panel-tabs memory-mode-tabs" role="tablist" aria-label={frontendPolicy.copy.MemoryPanel.aria_label}>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "memory"}
          className={mode === "memory" ? "panel-tab panel-tab--active" : "panel-tab"}
          onClick={() => setMode("memory")}
        >
          普通记忆
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "knowledge"}
          className={mode === "knowledge" ? "panel-tab panel-tab--active" : "panel-tab"}
          onClick={() => setMode("knowledge")}
        >
          知识库
        </button>
      </div>

      <div className="panel-tabs memory-subtabs" role="tablist" aria-label={mode === "knowledge" ? frontendPolicy.copy.MemoryPanel.text_2 : frontendPolicy.copy.MemoryPanel.text_3}>
        {mode === "memory" ? <>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "memories"}
            className={activeView === "memories" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setActiveView("memories")}
          >
            记忆
          </button>
          {FACT_VIEWS.map(({ scope, label }) => (
            <button
              key={scope}
              type="button"
              role="tab"
              aria-selected={activeView === "facts" && factScope === scope}
              className={activeView === "facts" && factScope === scope ? "panel-tab panel-tab--active" : "panel-tab"}
              onClick={() => {
                setActiveView("facts");
                void selectFactScope(scope);
              }}
            >
              {label}
            </button>
          ))}
        </> : <>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "memories"}
            className={activeView === "memories" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setActiveView("memories")}
          >{frontendPolicy.copy.MemoryPanel.button}</button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "facts" && factScope === "knowledge"}
            className={activeView === "facts" && factScope === "knowledge" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => { setActiveView("facts"); void selectFactScope("knowledge"); }}
          >{frontendPolicy.copy.MemoryPanel.button_2}</button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "feed"}
            className={activeView === "feed" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setActiveView("feed")}
          >{frontendPolicy.copy.MemoryPanel.button_3}</button>
        </>}
      </div>

      {activeView === "feed" ? (
        <KnowledgeFeed embedded />
      ) : activeView === "facts" ? (
        <FactGraph facts={facts} error={factsError} />
      ) : (
        <>
          {mode === "memory" && <div className="panel-tabs" role="group" aria-label={frontendPolicy.copy.MemoryPanel.aria_label_2}>
            {[["all", frontendPolicy.copy.MemoryPanel.text_4], ...Object.entries(kindLabels)].map(([value, label]) => (
              <button key={value} type="button" aria-pressed={filter === value}
                className={filter === value ? "panel-tab panel-tab--active" : "panel-tab"}
                onClick={() => { setFilter(value); setPage(0); }}>{label}</button>
            ))}
          </div>}
          <form className="panel-toolbar memory-search" onSubmit={submitSearch}>
            <label className="memory-search__label" htmlFor="memory-search-input">{frontendPolicy.copy.MemoryPanel.label}</label>
            <input
              id="memory-search-input"
              className="panel-input memory-search__input"
              aria-label={mode === "knowledge" ? frontendPolicy.copy.MemoryPanel.text_5 : frontendPolicy.copy.MemoryPanel.text_6}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={frontendPolicy.copy.MemoryPanel.placeholder}
            />
            <button className="panel-refresh" type="submit">{frontendPolicy.copy.MemoryPanel.button_4}</button>
          </form>
          {query !== "" && <p className="panel-item__meta">{frontendPolicy.copy.MemoryPanel.p}{query}</p>}
          {error !== null && <p className="error-text">{error}</p>}
          {mode === "memory" && conversationsError !== null && <p className="error-text">{frontendPolicy.copy.MemoryPanel.p_2}{conversationsError}</p>}
          {visibleData === null ? (
            frontendPolicy.copy.MemoryPanel.text_7
          ) : visibleData.length === 0 ? (
            <p className="panel-item">{frontendPolicy.copy.MemoryPanel.p_3}</p>
          ) : (
            <ul className="panel-list">
              {visibleData.slice(currentPage * 5, currentPage * 5 + 5).map((m) => (
                <li key={m.id} className="panel-item">
                  <span className="panel-item__main">{m.summary || m.content}</span>
                  <span className="panel-item__meta">
                    {kindLabels[m.kind] ?? m.kind} · {MEMORY_TYPE_LABELS[m.type]} · {formatDateTime(m.created_at)}
                  </span>
                  <details><summary>{frontendPolicy.copy.MemoryPanel.summary}</summary><pre className="memory-original">{m.content}</pre></details>
                </li>
              ))}
            </ul>
          )}
          {mode === "memory" && conversationsMore && <button type="button" disabled={conversationsLoading}
            onClick={() => void loadConversations(true)}>{conversationsLoading ? frontendPolicy.copy.MemoryPanel.text_8 : frontendPolicy.copy.MemoryPanel.text_9}</button>}
          {visibleData !== null && visibleData.length > 0 && (
            <div className="ledger-pagination">
              <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{frontendPolicy.copy.MemoryPanel.button_5}</button>
              <span>{currentPage + 1} / {pages}</span>
              <button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{frontendPolicy.copy.MemoryPanel.button_6}</button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
