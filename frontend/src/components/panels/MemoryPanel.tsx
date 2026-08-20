import { useEffect } from "react";
import { useMemoryStore } from "../../stores/memoryStore";
import Panel from "../layout/Panel";

// 记忆浏览器面板（README §5）：REST 快照 + SSE memory_* 触发 refresh。
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
      ) : (
        <ul className="panel-list">
          {data.map((m) => (
            <li key={m.id} className="panel-item">
              <span className="panel-item__main">{m.summary || m.content}</span>
              <span className="panel-item__meta">
                {m.tag} · {m.type} · freshness={m.freshness.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
