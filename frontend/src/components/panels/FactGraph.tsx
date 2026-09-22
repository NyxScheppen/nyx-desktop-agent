import { useMemo } from "react";
import type { MemoryFact } from "../../types/api";

const NODE_WIDTH = 152;
const NODE_HEIGHT = 48;
const COLUMN_GAP = 72;
const ROW_GAP = 32;
const PADDING = 24;
const MAX_COLUMNS = 3;

type FactGraphProps = {
  facts: MemoryFact[] | null;
  error: string | null;
};

type GraphNode = {
  key: string;
  name: string;
  type: string;
  x: number;
  y: number;
};

function nodeKey(type: string, name: string): string {
  return `${type}:${name}`;
}

function displayName(value: string): string {
  return value.length > 18 ? `${value.slice(0, 18)}…` : value;
}

function formatDate(timestamp: number): string {
  if (!Number.isFinite(timestamp)) return "未知时间";
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? "未知时间" : date.toLocaleDateString();
}

function formatValidity(fact: MemoryFact): string {
  const until = fact.valid_until === null ? "至今" : formatDate(fact.valid_until);
  return `${formatDate(fact.valid_from)} – ${until}`;
}

export default function FactGraph({ facts, error }: FactGraphProps) {
  const graph = useMemo(() => {
    const nodesByKey = new Map<string, Omit<GraphNode, "x" | "y">>();
    for (const fact of facts ?? []) {
      const subjectKey = nodeKey(fact.subject_type, fact.subject);
      const objectKey = nodeKey(fact.object_type, fact.object_value);
      nodesByKey.set(subjectKey, {
        key: subjectKey,
        name: fact.subject,
        type: fact.subject_type,
      });
      nodesByKey.set(objectKey, {
        key: objectKey,
        name: fact.object_value,
        type: fact.object_type,
      });
    }
    const nodes = [...nodesByKey.values()].sort((a, b) => a.key.localeCompare(b.key));
    const columns = Math.min(MAX_COLUMNS, Math.max(1, nodes.length));
    const rows = Math.ceil(nodes.length / columns);
    const positioned = nodes.map((node, index) => ({
      ...node,
      x: PADDING + (index % columns) * (NODE_WIDTH + COLUMN_GAP),
      y: PADDING + Math.floor(index / columns) * (NODE_HEIGHT + ROW_GAP),
    }));
    const positions = new Map(positioned.map((node) => [node.key, node]));
    return {
      nodes: positioned,
      positions,
      width: PADDING * 2 + columns * NODE_WIDTH + (columns - 1) * COLUMN_GAP,
      height: Math.max(
        160,
        PADDING * 2 + rows * NODE_HEIGHT + Math.max(0, rows - 1) * ROW_GAP,
      ),
    };
  }, [facts]);

  if (error !== null) return <p className="error-text">{error}</p>;
  if (facts === null) return <p className="panel-item">等待事实层连接…</p>;
  if (facts.length === 0) return <p className="panel-item">暂无当前有效事实</p>;

  return (
    <div className="fact-graph">
      <p className="panel-item__meta">当前有效事实 · {facts.length} 条</p>
      <div className="fact-graph__canvas">
        <svg
          width={graph.width}
          height={graph.height}
          viewBox={`0 0 ${graph.width} ${graph.height}`}
          role="img"
          aria-label={`事实关系图，共 ${facts.length} 条事实`}
        >
          <defs>
            <marker
              id="fact-graph-arrow"
              markerHeight="6"
              markerWidth="6"
              orient="auto"
              refX="5"
              refY="3"
              viewBox="0 0 6 6"
            >
              <path d="M0,0 L6,3 L0,6 Z" className="fact-graph__arrow" />
            </marker>
          </defs>
          {facts.map((fact) => {
            const source = graph.positions.get(nodeKey(fact.subject_type, fact.subject));
            const target = graph.positions.get(nodeKey(fact.object_type, fact.object_value));
            if (source === undefined || target === undefined) return null;
            const negative = fact.polarity < 0;
            return (
              <g key={fact.id} className={negative ? "fact-graph__edge fact-graph__edge--negative" : "fact-graph__edge"}>
                <line
                  x1={source.x + NODE_WIDTH / 2}
                  y1={source.y + NODE_HEIGHT / 2}
                  x2={target.x + NODE_WIDTH / 2}
                  y2={target.y + NODE_HEIGHT / 2}
                  markerEnd="url(#fact-graph-arrow)"
                />
                <text
                  x={(source.x + target.x + NODE_WIDTH) / 2}
                  y={(source.y + target.y + NODE_HEIGHT) / 2 - 4}
                  textAnchor="middle"
                >
                  {fact.predicate}
                </text>
                <title>
                  {`${fact.subject} ${fact.predicate} ${fact.object_value} · ${formatValidity(fact)}`}
                </title>
              </g>
            );
          })}
          {graph.nodes.map((node) => (
            <g key={node.key} className="fact-graph__node">
              <rect x={node.x} y={node.y} width={NODE_WIDTH} height={NODE_HEIGHT} rx="5" />
              <text x={node.x + NODE_WIDTH / 2} y={node.y + 20} textAnchor="middle">
                {displayName(node.name)}
              </text>
              <text x={node.x + NODE_WIDTH / 2} y={node.y + 36} textAnchor="middle" className="fact-graph__node-type">
                {node.type}
              </text>
              <title>{`${node.name}（${node.type}）`}</title>
            </g>
          ))}
        </svg>
      </div>
      <p className="panel-item__meta">虚线/红色边表示否定关系，节点下方为实体类型。</p>
    </div>
  );
}
