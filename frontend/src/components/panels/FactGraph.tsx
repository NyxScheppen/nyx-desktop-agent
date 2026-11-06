import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { formatDate } from "../../lib/time";
import type { MemoryFact } from "../../types/api";

type Point = { x: number; y: number };
type GraphNode = Point & { key: string; name: string; type: string };
type FactGraphProps = { facts: MemoryFact[] | null; error: string | null };
const WIDTH = 900;
const HEIGHT = 520;
const nodeKey = (type: string, name: string): string => `${type}:${name}`;
const displayName = (name: string): string => name.length > 16 ? `${name.slice(0, 16)}…` : name;

function layout(facts: MemoryFact[]): GraphNode[] {
  const byKey = new Map<string, GraphNode>();
  for (const fact of facts) {
    for (const [type, name] of [[fact.subject_type, fact.subject], [fact.object_type, fact.object_value]]) {
      const key = nodeKey(type, name);
      if (!byKey.has(key)) {
        const i = byKey.size;
        byKey.set(key, { key, name, type, x: Math.cos(i * 2.39996) * (40 + 22 * Math.sqrt(i)), y: Math.sin(i * 2.39996) * (40 + 22 * Math.sqrt(i)) });
      }
    }
  }
  const nodes = [...byKey.values()];
  for (let step = 0; step < 100; step++) {
    const forces = new Map(nodes.map((n) => [n.key, { x: -n.x * 0.015, y: -n.y * 0.015 }]));
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const repulsion = 3600 / (distance * distance);
      const af = forces.get(a.key)!, bf = forces.get(b.key)!;
      af.x += dx / distance * repulsion; af.y += dy / distance * repulsion;
      bf.x -= dx / distance * repulsion; bf.y -= dy / distance * repulsion;
    }
    for (const fact of facts) {
      const a = byKey.get(nodeKey(fact.subject_type, fact.subject))!;
      const b = byKey.get(nodeKey(fact.object_type, fact.object_value))!;
      if (a === b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const spring = (distance - 140) * 0.025;
      const af = forces.get(a.key)!, bf = forces.get(b.key)!;
      af.x += dx / distance * spring; af.y += dy / distance * spring;
      bf.x -= dx / distance * spring; bf.y -= dy / distance * spring;
    }
    for (const node of nodes) {
      const force = forces.get(node.key)!;
      node.x += Math.max(-12, Math.min(12, force.x));
      node.y += Math.max(-12, Math.min(12, force.y));
    }
  }
  const minX = Math.min(...nodes.map((n) => n.x)), maxX = Math.max(...nodes.map((n) => n.x));
  const minY = Math.min(...nodes.map((n) => n.y)), maxY = Math.max(...nodes.map((n) => n.y));
  const scale = Math.min((WIDTH - 180) / Math.max(1, maxX - minX), (HEIGHT - 140) / Math.max(1, maxY - minY), 1.5);
  return nodes.map((n) => ({ ...n, x: WIDTH / 2 + (n.x - (minX + maxX) / 2) * scale, y: HEIGHT / 2 + (n.y - (minY + maxY) / 2) * scale }));
}

export default function FactGraph({ facts, error }: FactGraphProps) {
  const initial = useMemo(() => layout(facts ?? []), [facts]);
  const [viewportWidth, setViewportWidth] = useState(WIDTH);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const [selected, setSelected] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ key: string | null; start: Point; origin: Point } | null>(null);
  const arrowId = useId().replaceAll(":", "");
  const nodes = initial.map((n) => ({ ...n, ...positions[n.key] }));
  const byKey = new Map(nodes.map((n) => [n.key, n]));
  const fitView = useMemo(() => {
    const span = initial.length ? Math.max(...initial.map((n) => n.x)) - Math.min(...initial.map((n) => n.x)) : 0;
    const scale = Math.min(1, Math.max(0.3, (viewportWidth - 70) / Math.max(1, span)));
    return { scale, x: viewportWidth / 2 - WIDTH / 2 * scale, y: HEIGHT / 2 * (1 - scale) };
  }, [initial, viewportWidth]);
  const reset = (): void => { setPositions({}); setView(fitView); };
  useEffect(() => { setPositions({}); setView(fitView); setSelected(null); }, [fitView]);
  useEffect(() => {
    const element = svg.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setViewportWidth(Math.max(200, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [facts, error]);
  const pointAt = (clientX: number, clientY: number): Point => {
    const rect = svg.current!.getBoundingClientRect();
    return { x: (clientX - rect.left) * viewportWidth / rect.width, y: (clientY - rect.top) * HEIGHT / rect.height };
  };
  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    const wheel = (event: WheelEvent): void => {
      event.preventDefault();
      const point = pointAt(event.clientX, event.clientY);
      setView((old) => {
        const scale = Math.max(0.3, Math.min(4, old.scale * Math.exp(-event.deltaY * 0.0015)));
        return { scale, x: point.x - (point.x - old.x) * scale / old.scale, y: point.y - (point.y - old.y) * scale / old.scale };
      });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [facts, error, viewportWidth]);
  const begin = (event: ReactPointerEvent<SVGElement>, node?: GraphNode): void => {
    if (event.button !== 0) return;
    event.stopPropagation();
    const start = pointAt(event.clientX, event.clientY);
    drag.current = { key: node?.key ?? null, start, origin: node ? { x: node.x, y: node.y } : { x: view.x, y: view.y } };
    svg.current?.setPointerCapture(event.pointerId);
    if (node) setSelected(node.key);
  };
  const move = (event: ReactPointerEvent<SVGSVGElement>): void => {
    const gesture = drag.current;
    if (!gesture) return;
    const point = pointAt(event.clientX, event.clientY);
    const dx = point.x - gesture.start.x, dy = point.y - gesture.start.y;
    if (gesture.key === null) setView((v) => ({ ...v, x: gesture.origin.x + dx, y: gesture.origin.y + dy }));
    else setPositions((p) => ({ ...p, [gesture.key!]: { x: gesture.origin.x + dx / view.scale, y: gesture.origin.y + dy / view.scale } }));
  };
  if (error !== null) return <p className="error-text">{error}</p>;
  if (facts === null) return <p className="panel-item">等待事实层连接…</p>;
  if (facts.length === 0) return <p className="panel-item">暂无当前有效事实</p>;
  const selectedFacts = facts.filter((f) => nodeKey(f.subject_type, f.subject) === selected || nodeKey(f.object_type, f.object_value) === selected);
  return <div className="fact-graph">
    <div className="panel-toolbar">
      <span className="panel-item__meta">当前有效事实 · {facts.length} 条</span>
      <button type="button" aria-label="放大事实网" onClick={() => setView((v) => ({ ...v, scale: Math.min(4, v.scale * 1.25) }))}>＋</button>
      <button type="button" aria-label="缩小事实网" onClick={() => setView((v) => ({ ...v, scale: Math.max(0.3, v.scale / 1.25) }))}>－</button>
      <button type="button" onClick={reset}>重置视图</button>
      <span>{Math.round(view.scale * 100)}%</span>
    </div>
    <div className="fact-graph__canvas">
      <svg ref={svg} width="100%" height={HEIGHT} viewBox={`0 0 ${viewportWidth} ${HEIGHT}`}
        role="img" aria-label={`事实关系图，共 ${facts.length} 条事实`}
        onPointerDown={(e) => begin(e)} onPointerMove={move}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
        <defs><marker id={arrowId} markerHeight="6" markerWidth="6" orient="auto" refX="5" refY="3" viewBox="0 0 6 6"><path d="M0,0 L6,3 L0,6 Z" className="fact-graph__arrow" /></marker></defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {facts.map((fact) => {
            const a = byKey.get(nodeKey(fact.subject_type, fact.subject))!, b = byKey.get(nodeKey(fact.object_type, fact.object_value))!;
            const distance = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y));
            return <g key={fact.id} className={`fact-graph__edge${fact.polarity < 0 ? " fact-graph__edge--negative" : ""}`}>
              {a === b ? <path d={`M${a.x - 8},${a.y - 10} C${a.x - 65},${a.y - 90} ${a.x + 65},${a.y - 90} ${a.x + 8},${a.y - 10}`} markerEnd={`url(#${arrowId})`} fill="none" />
                : <line x1={a.x} y1={a.y} x2={b.x - (b.x - a.x) / distance * 17} y2={b.y - (b.y - a.y) / distance * 17} markerEnd={`url(#${arrowId})`} />}
              <text x={(a.x + b.x) / 2} y={a === b ? a.y - 65 : (a.y + b.y) / 2 - 6} textAnchor="middle">{fact.predicate}</text>
              <title>{`${fact.subject} ${fact.predicate} ${fact.object_value} · ${formatDate(fact.valid_from)} 至 ${fact.valid_until === null ? "至今" : formatDate(fact.valid_until)}`}</title>
            </g>;
          })}
          {nodes.map((node) => <g key={node.key} className={`fact-graph__node${selected === node.key ? " fact-graph__node--selected" : ""}`}
            transform={`translate(${node.x} ${node.y})`} role="button" tabIndex={0} aria-label={`实体：${node.name}`} aria-pressed={selected === node.key}
            onPointerDown={(e) => begin(e, node)} onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(node.key); }
              const delta: Record<string, Point> = { ArrowLeft: { x: -15, y: 0 }, ArrowRight: { x: 15, y: 0 }, ArrowUp: { x: 0, y: -15 }, ArrowDown: { x: 0, y: 15 } };
              if (delta[e.key]) { e.preventDefault(); setPositions((p) => ({ ...p, [node.key]: { x: node.x + delta[e.key].x, y: node.y + delta[e.key].y } })); }
            }}>
            <circle r="14" /><text y="32" textAnchor="middle">{displayName(node.name)}</text>
            <text y="48" textAnchor="middle" className="fact-graph__node-type">{node.type}</text><title>{node.name}（{node.type}）</title>
          </g>)}
        </g>
      </svg>
    </div>
    <p className="panel-item__meta">拖动节点调整位置，拖动画布平移，滚轮缩放。选中节点可查看事实来源；方向键可移动节点。红色虚线表示否定关系。</p>
    {selectedFacts.length > 0 && <ul className="panel-list">{selectedFacts.map((fact) => <li key={fact.id} className="panel-item">
      <span>{fact.subject} {fact.polarity < 0 ? "不" : ""}{fact.predicate} {fact.object_value}</span>
      <span className="panel-item__meta">{formatDate(fact.valid_from)} 至 {fact.valid_until === null ? "至今" : formatDate(fact.valid_until)} · 来源 {fact.source_memory_id ?? fact.source_segment_id ?? fact.source_chunk_id ?? "未记录"}</span>
    </li>)}</ul>}
  </div>;
}
