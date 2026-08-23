import { useEffect, useRef, type ChangeEvent } from "react";
import { useMaterialsStore } from "../../stores/materialsStore";
import Panel from "../layout/Panel";

// 资料面板：上传课本/文档给尼克斯读。选文件即上传（落 workspace/uploads），
// 每本显示已读进度（read_chars/total_chars）+ 在读/读完状态。
export default function MaterialsPanel() {
  const materials = useMaterialsStore((s) => s.materials);
  const uploading = useMaterialsStore((s) => s.uploading);
  const error = useMaterialsStore((s) => s.error);
  const refresh = useMaterialsStore((s) => s.refresh);
  const upload = useMaterialsStore((s) => s.upload);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file !== undefined) void upload(file);
    e.target.value = ""; // 允许重复选同一文件
  };

  return (
    <Panel title="资料">
      <div className="bg-panel">
        <div className="bg-panel__row">
          <span className="bg-panel__label">上传课本/资料（.txt/.md/.json/.csv）</span>
          <div className="bg-panel__actions">
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.md,.json,.csv"
              className="bg-panel__file"
              hidden
              onChange={onFile}
            />
            <button
              type="button"
              className="bg-panel__btn"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? "上传中…" : "上传给尼克斯读"}
            </button>
          </div>
        </div>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {materials === null ? (
        "等待核心服务连接…"
      ) : materials.length === 0 ? (
        <p className="panel-item">还没有上传过资料</p>
      ) : (
        <ul className="panel-list">
          {materials.map((m) => {
            const done = m.read_chars >= m.total_chars;
            const pct =
              m.total_chars > 0
                ? Math.min(100, Math.round((m.read_chars / m.total_chars) * 100))
                : 0;
            return (
              <li key={m.path} className="panel-item">
                <span className="panel-item__main">
                  {m.filename}{" "}
                  <span className="panel-badge">{done ? "读完" : "在读"}</span>
                </span>
                <div className="material-progress">
                  <div className="material-progress__track">
                    <div
                      className="material-progress__fill"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
                <span className="panel-item__meta">
                  已读 {m.read_chars} / 共 {m.total_chars} 字（{pct}%）
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
