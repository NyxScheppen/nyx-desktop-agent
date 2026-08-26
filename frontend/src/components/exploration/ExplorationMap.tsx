import { useState } from "react";
import { useExplorationStore } from "../../stores/explorationStore";
import type { FloorNode } from "../../types/api";

// 节点类型标签 + 精力消耗（与后端 enter_cost 常量镜像；仅展示，不驱动逻辑）
const KIND_LABEL: Record<FloorNode["kind"], string> = {
  real: "真实节点",
  dead_end: "死路",
  safe_room: "安全房",
};
const KIND_COST: Record<FloorNode["kind"], string> = {
  real: "-6 精力",
  dead_end: "-4 精力",
  safe_room: "+30 精力",
};

function EnergyBar({ energy }: { energy: number }) {
  const pct = Math.max(0, Math.min(100, energy));
  return (
    <div className="dungeon-hud__energy">
      <div className="dungeon-hud__label">火把 · 精力（燃料）</div>
      <div className="dungeon-hud__energy-track">
        <div className="dungeon-hud__energy-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="dungeon-hud__energy-num">
        {Math.round(energy)}
        <span className="dim">/100</span>
      </span>
    </div>
  );
}

function NodeCard({
  node,
  disabled,
  onEnter,
}: {
  node: FloorNode;
  disabled: boolean;
  onEnter: () => void;
}) {
  return (
    <button
      type="button"
      className={`dungeon-node dungeon-node--${node.kind}`}
      disabled={disabled}
      onClick={onEnter}
    >
      <span className={`dungeon-node__badge dungeon-node__badge--${node.kind}`}>
        {KIND_LABEL[node.kind]}
      </span>
      <div className="dungeon-node__name">{node.name}</div>
      <div className="dungeon-node__snippet">{node.snippet}</div>
      <div className="dungeon-node__cost">
        {KIND_COST[node.kind]}
        {node.may_encounter ? <span className="dungeon-node__risk"> · 可能触发遭遇</span> : null}
      </div>
    </button>
  );
}

// 逐层地牢探索视图（design §7）：HUD（精力/目标/深度/托管）+ 本层 4 槽 + 下楼/撤退 + 展开地图 + 道具栏占位。
export default function ExplorationMap() {
  const decision = useExplorationStore((s) => s.decision);
  const activityId = useExplorationStore((s) => s.activityId);
  const autopilot = useExplorationStore((s) => s.autopilot);
  const choosing = useExplorationStore((s) => s.choosing);
  const error = useExplorationStore((s) => s.error);
  const history = useExplorationStore((s) => s.history);
  const start = useExplorationStore((s) => s.start);
  const choose = useExplorationStore((s) => s.choose);
  const toggleAutopilot = useExplorationStore((s) => s.toggleAutopilot);

  const [mapOpen, setMapOpen] = useState(false);

  const pick = (choice: string) => {
    if (autopilot) void toggleAutopilot(false); // 随时接管：托管中点任意选项先关托管
    void choose(choice);
  };

  const safeRoom: FloorNode = {
    name: "休息整理", url: "", kind: "safe_room",
    snippet: "+30 精力 · 写进记忆 · 可安全撤退", may_encounter: false,
  };

  return (
    <section className="side-panel dungeon">
      <header className="side-panel__header dungeon__header">
        <span className="side-panel__title">探索地牢</span>
        {decision !== null && (
          <button
            type="button"
            className={`dungeon-autopilot${autopilot ? " dungeon-autopilot--on" : ""}`}
            aria-pressed={autopilot}
            disabled={activityId === null}
            onClick={() => void toggleAutopilot(!autopilot)}
          >
            {autopilot ? "托管中 · 点此接管" : "托管 · 让尼克斯自己走"}
          </button>
        )}
      </header>

      <div className="side-panel__body">
        {decision === null ? (
          <>
            <button type="button" className="dungeon-go" onClick={() => void start()}>
              出门探索
            </button>
            {error !== null && <div className="dungeon-error">{error}</div>}
          </>
        ) : (
          <>
            <div className="dungeon-hud">
              <EnergyBar energy={decision.energy} />
              <div className="dungeon-hud__goal">
                <div className="dungeon-hud__label">欲望（目标）</div>
                <div className="dungeon-hud__value">{decision.focus}</div>
              </div>
              <div className="dungeon-hud__floor">
                <div className="dungeon-hud__label">深度</div>
                <div className="dungeon-hud__value">第 {decision.floor} 层</div>
                <div className="dungeon-hud__hint">越下越险</div>
              </div>
            </div>

            <div className="dungeon-floor">
              {decision.nodes.map((n, i) => (
                <NodeCard key={i} node={n} disabled={choosing} onEnter={() => pick(`node:${i}`)} />
              ))}
              <NodeCard node={safeRoom} disabled={choosing} onEnter={() => pick("safe_room")} />
            </div>

            <div className="dungeon-actions">
              <button
                type="button"
                className="dungeon-descend"
                disabled={choosing}
                onClick={() => pick("descend")}
              >
                下楼 · 追线索往下
              </button>
              <button
                type="button"
                className="dungeon-retreat"
                disabled={choosing}
                onClick={() => pick("retreat")}
              >
                撤退 · 正常结算
              </button>
              <button
                type="button"
                className="dungeon-map-toggle"
                aria-pressed={mapOpen}
                onClick={() => setMapOpen((v) => !v)}
              >
                展开地图 {mapOpen ? "▴" : "▾"}
              </button>
            </div>

            {mapOpen && (
              <div className="dungeon-map">
                <div className="dungeon-map__label">地图 · 已走过的楼层</div>
                {Array.from({ length: decision.floor }, (_, i) => i + 1).map((f) => {
                  const nodes = history.filter((h) => h.floor === f);
                  return (
                    <div key={f} className="dungeon-map__floor">
                      <span
                        className={`dungeon-map__glyph${
                          f === decision.floor ? " dungeon-map__glyph--cur" : ""
                        }`}
                      >
                        ◆
                      </span>
                      <span className="dungeon-map__floor-name">第 {f} 层</span>
                      {nodes.length > 0 && (
                        <span className="dungeon-map__nodes">
                          {nodes.map((n) => n.name).join(" · ")}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            <div className="dungeon-inventory">
              <div className="dungeon-inventory__label">道具（道具系统后续接入）</div>
              <div className="dungeon-inventory__slots">
                {Array.from({ length: 6 }, (_, i) => (
                  <div key={i} className="dungeon-inventory__slot" />
                ))}
              </div>
            </div>

            {error !== null && <div className="dungeon-error">{error}</div>}
          </>
        )}
      </div>
    </section>
  );
}
