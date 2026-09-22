import { FormEvent, useEffect, useState } from "react";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import { useMemoryStore } from "../../stores/memoryStore";
import Panel from "../layout/Panel";
import FactGraph from "./FactGraph";

// 记忆面板：记忆列表/关键词查询与独立的当前事实关系图。
export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
  const facts = useMemoryStore((s) => s.facts);
  const query = useMemoryStore((s) => s.query);
  const error = useMemoryStore((s) => s.error);
  const factsError = useMemoryStore((s) => s.factsError);
  const refresh = useMemoryStore((s) => s.refresh);
  const search = useMemoryStore((s) => s.search);
  const [activeView, setActiveView] = useState<"memories" | "facts">("memories");
  const [input, setInput] = useState(query);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submitSearch = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    void search(input);
  };

  return (
    <Panel title="记忆">
      <div className="panel-tabs" role="tablist" aria-label="记忆内容类型">
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "memories"}
          className={activeView === "memories" ? "panel-tab panel-tab--active" : "panel-tab"}
          onClick={() => setActiveView("memories")}
        >
          记忆
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "facts"}
          className={activeView === "facts" ? "panel-tab panel-tab--active" : "panel-tab"}
          onClick={() => setActiveView("facts")}
        >
          事实图
        </button>
      </div>

      {activeView === "facts" ? (
        <FactGraph facts={facts} error={factsError} />
      ) : (
        <>
          <form className="panel-toolbar memory-search" onSubmit={submitSearch}>
            <label className="memory-search__label" htmlFor="memory-search-input">
              关键词
            </label>
            <input
              id="memory-search-input"
              className="panel-input memory-search__input"
              aria-label="记忆关键词"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="搜索记忆…"
            />
            <button className="panel-refresh" type="submit">查询</button>
          </form>
          {query !== "" && <p className="panel-item__meta">当前查询：{query}</p>}
          {error !== null && <p className="error-text">{error}</p>}
          {data === null ? (
            "等待核心服务连接…"
          ) : data.length === 0 ? (
            <p className="panel-item">暂无记忆</p>
          ) : (
            <ul className="panel-list">
              {data.map((m) => (
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
        </>
      )}
    </Panel>
  );
}
