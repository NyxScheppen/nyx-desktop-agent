import { useEffect } from "react";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import { useMemoryStore } from "../../stores/memoryStore";
import Panel from "../layout/Panel";

// 记忆面板（内在详情 tab）：列出全部记忆，每项标题（summary）+ 完整内容（content）。
export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
  const error = useMemoryStore((s) => s.error);
  const refresh = useMemoryStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="记忆">
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
                {m.tag} · {MEMORY_TYPE_LABELS[m.type]}
              </span>
              <span className="panel-item__body">{m.content}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
