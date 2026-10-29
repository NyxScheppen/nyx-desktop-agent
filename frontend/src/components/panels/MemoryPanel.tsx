import { FormEvent, useEffect, useState } from "react";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import { useMemoryStore } from "../../stores/memoryStore";
import type { FactScope } from "../../types/api";
import Panel from "../layout/Panel";
import FactGraph from "./FactGraph";
import { KnowledgeFeed } from "../chat/KnowledgeFeed";

const FACT_VIEWS: { scope: FactScope; label: string }[] = [
  { scope: "profile", label: "画像事实" },
  { scope: "conversation", label: "对话事实" },
];

// 记忆面板：普通记忆与知识库共用一个入口，知识库内包含条目和事实网。
export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
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
  const visibleData = data?.filter((memory) => mode === "knowledge"
    ? memory.kind === "knowledge" : memory.kind !== "knowledge") ?? null;
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
    <Panel title="记忆">
      <div className="panel-tabs memory-mode-tabs" role="tablist" aria-label="记忆范围">
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

      <div className="panel-tabs memory-subtabs" role="tablist" aria-label={mode === "knowledge" ? "知识库内容" : "普通记忆内容"}>
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
          >知识条目</button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "facts" && factScope === "knowledge"}
            className={activeView === "facts" && factScope === "knowledge" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => { setActiveView("facts"); void selectFactScope("knowledge"); }}
          >事实网</button>
          <button
            type="button"
            role="tab"
            aria-selected={activeView === "feed"}
            className={activeView === "feed" ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setActiveView("feed")}
          >投喂知识</button>
        </>}
      </div>

      {activeView === "feed" ? (
        <KnowledgeFeed embedded />
      ) : activeView === "facts" ? (
        <FactGraph facts={facts} error={factsError} />
      ) : (
        <>
          <form className="panel-toolbar memory-search" onSubmit={submitSearch}>
            <label className="memory-search__label" htmlFor="memory-search-input">关键词</label>
            <input
              id="memory-search-input"
              className="panel-input memory-search__input"
              aria-label={mode === "knowledge" ? "知识库关键词" : "记忆关键词"}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="搜索记忆…"
            />
            <button className="panel-refresh" type="submit">查询</button>
          </form>
          {query !== "" && <p className="panel-item__meta">当前查询：{query}</p>}
          {error !== null && <p className="error-text">{error}</p>}
          {visibleData === null ? (
            "等待核心服务连接…"
          ) : visibleData.length === 0 ? (
            <p className="panel-item">暂无记忆</p>
          ) : (
            <ul className="panel-list">
              {visibleData.slice(currentPage * 5, currentPage * 5 + 5).map((m) => (
                <li key={m.id} className="panel-item">
                  <span className="panel-item__main">{m.summary || m.content}</span>
                  <span className="panel-item__meta">
                    {m.kind} · {MEMORY_TYPE_LABELS[m.type]}
                  </span>
                  <span className="panel-item__body">{m.content}</span>
                </li>
              ))}
            </ul>
          )}
          {visibleData !== null && visibleData.length > 0 && (
            <div className="ledger-pagination">
              <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
              <span>{currentPage + 1} / {pages}</span>
              <button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
