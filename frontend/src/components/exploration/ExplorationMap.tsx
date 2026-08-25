import { useState } from "react";
import { useActivityStore } from "../../stores/activityStore";
import { useExplorationStore } from "../../stores/explorationStore";
import type { ExplorationNode } from "../../types/api";

// 历史足迹：activityStore.results 里 free_exploration 的 result.nodes（含 findings）。
function history(): { node: ExplorationNode; findings: string[] }[] {
  const results = useActivityStore((s) => s.results);
  return (results ?? [])
    .filter((a) => a.type === "free_exploration")
    .flatMap((a) => {
      const result = a.progress?.result as
        | { nodes?: ExplorationNode[]; findings?: string[] }
        | undefined;
      return (result?.nodes ?? []).map((node) => ({
        node,
        findings: result?.findings ?? [],
      }));
    });
}

export default function ExplorationMap({ onClose }: { onClose: () => void }) {
  const liveNodes = useExplorationStore((s) => s.liveNodes);
  const wishlist = useExplorationStore((s) => s.wishlist);
  const addWish = useExplorationStore((s) => s.addWish);
  const removeWish = useExplorationStore((s) => s.removeWish);
  const start = useExplorationStore((s) => s.start);
  const currentType = useActivityStore((s) => s.data?.current?.type);

  const [topic, setTopic] = useState("");
  const [detail, setDetail] = useState<{ name: string; findings: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const past = history();
  const exploring = currentType === "free_exploration";
  const live = exploring ? liveNodes : [];

  return (
    <aside className="exploration-map">
      <header className="exploration-map-head">
        <span>🗺️ 探索地图</span>
        <button className="map-close" onClick={onClose}>✕</button>
      </header>

      <div className="exploration-map-body">
        <button
          className="map-go"
          onClick={() => {
            setError(null);
            void start().catch((e) => setError(e instanceof Error ? e.message : String(e)));
          }}
        >
          出门探索
        </button>

        {error !== null && <div className="map-error">{error}</div>}

        <div className="map-nodes">
          {past.map((h, i) => (
            <div
              key={i}
              className={`map-node explored ${h.node.kind}`}
              onClick={() => setDetail({ name: h.node.name, findings: h.findings })}
            >
              <span className="node-glyph">✦</span>
              <span className="node-name">{h.node.name}</span>
            </div>
          ))}
          {live.map((n, i) => (
            <div key={`live-${i}`} className={`map-node live ${n.kind}`}>
              <span className="node-glyph">🦊</span>
              <span className="node-name">{n.name}</span>
            </div>
          ))}
          {wishlist.map((w, i) => (
            <div key={`wish-${i}`} className="map-node pending">
              <span className="node-glyph">◌</span>
              <span className="node-name">{w}</span>
              <button className="map-remove" onClick={() => removeWish(w)}>✕</button>
            </div>
          ))}
        </div>

        <div className="map-add">
          <input
            placeholder="加一个想探索的主题"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <button onClick={() => { addWish(topic); setTopic(""); }}>＋</button>
        </div>
      </div>

      {detail !== null && (
        <div className="map-detail" onClick={() => setDetail(null)}>
          <strong>{detail.name}</strong>
          {detail.findings.map((f, i) => (
            <p key={i}>{f}</p>
          ))}
        </div>
      )}
    </aside>
  );
}
