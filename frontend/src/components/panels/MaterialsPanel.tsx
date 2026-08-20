import { useEffect, useRef, type ChangeEvent } from "react";
import { useMaterialsStore } from "../../stores/materialsStore";
import Panel from "../layout/Panel";

// 资料面板：上传课本/文档给尼克斯读。选文件即上传（落 workspace/uploads），
// 读书结果经 activity_start/activity_end 走活动面板可见。
export default function MaterialsPanel() {
  const files = useMaterialsStore((s) => s.files);
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
      {files === null ? (
        "等待核心服务连接…"
      ) : files.length === 0 ? (
        <p className="panel-item">还没有上传过资料</p>
      ) : (
        <ul className="panel-list">
          {files.map((f) => (
            <li key={f} className="panel-item">
              {f}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
