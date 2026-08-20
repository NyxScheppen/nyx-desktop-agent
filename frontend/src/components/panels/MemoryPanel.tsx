import { useEffect, useState } from "react";
import { exportMemories } from "../../api/client";
import { useMemoryStore } from "../../stores/memoryStore";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import Panel from "../layout/Panel";

// 记忆浏览器面板（README §5）：REST 快照 + SSE memory_* 触发 refresh。
// 枚举值经 MEMORY_TYPE_LABELS 转中文；头部「导出」按钮走 POST /api/export 触发下载。
function download(text: string, filename: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
  const error = useMemoryStore((s) => s.error);
  const refresh = useMemoryStore((s) => s.refresh);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onExport = (fmt: "json" | "md") => async () => {
    setExportError(null);
    try {
      const text = await exportMemories(fmt);
      download(text, fmt === "json" ? "memories.json" : "memories.md");
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Panel title="记忆">
      <div className="panel-toolbar">
        <button type="button" className="panel-refresh" onClick={onExport("json")}>
          导出 JSON
        </button>
        <button type="button" className="panel-refresh" onClick={onExport("md")}>
          导出 Markdown
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {exportError !== null && <p className="error-text">{exportError}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : (
        <ul className="panel-list">
          {data.map((m) => (
            <li key={m.id} className="panel-item">
              <span className="panel-item__main">{m.summary || m.content}</span>
              <span className="panel-item__meta">
                {m.tag} · {MEMORY_TYPE_LABELS[m.type]} · freshness={m.freshness.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
