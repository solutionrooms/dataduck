// Left-to-right task DAG: columns by dependency depth, boxes with curved arrows.

import { useMemo, type ReactNode } from "react";
import type { StateKind } from "../lib/runState";

export interface DagNode {
  key: string;
  dependsOn: string[];
  title: string;
  subtitle?: string;
  kind?: StateKind | "none";
  icon?: ReactNode;
  /** Edge label per dependency, e.g. a condition outcome ("true"/"false"). */
  outcomes?: Record<string, string | undefined>;
}

const W = 220;
const H = 56;
const GAP_X = 72;
const GAP_Y = 20;
const PAD = 24;

interface Placed {
  node: DagNode;
  x: number;
  y: number;
}

function layout(nodes: DagNode[]): { placed: Map<string, Placed>; width: number; height: number } {
  const byKey = new Map(nodes.map((n) => [n.key, n]));
  const level = new Map<string, number>();
  const visiting = new Set<string>();

  const depth = (key: string): number => {
    const known = level.get(key);
    if (known !== undefined) return known;
    if (visiting.has(key)) return 0; // cycle guard
    visiting.add(key);
    const node = byKey.get(key);
    const deps = (node?.dependsOn ?? []).filter((d) => byKey.has(d));
    const l = deps.length === 0 ? 0 : 1 + Math.max(...deps.map(depth));
    visiting.delete(key);
    level.set(key, l);
    return l;
  };
  nodes.forEach((n) => depth(n.key));

  const columns: DagNode[][] = [];
  for (const n of nodes) {
    const l = level.get(n.key) ?? 0;
    (columns[l] ??= []).push(n);
  }

  // Order each column by the average row of its parents to reduce crossings.
  const row = new Map<string, number>();
  columns.forEach((col, ci) => {
    if (ci > 0) {
      col.sort((a, b) => {
        const avg = (n: DagNode) => {
          const rs = n.dependsOn.map((d) => row.get(d)).filter((r): r is number => r !== undefined);
          return rs.length ? rs.reduce((s, r) => s + r, 0) / rs.length : 0;
        };
        return avg(a) - avg(b);
      });
    }
    col.forEach((n, i) => row.set(n.key, i));
  });

  const maxRows = Math.max(1, ...columns.map((c) => c?.length ?? 0));
  const totalH = maxRows * H + (maxRows - 1) * GAP_Y;
  const placed = new Map<string, Placed>();
  columns.forEach((col, ci) => {
    if (!col) return;
    const colH = col.length * H + (col.length - 1) * GAP_Y;
    const offset = (totalH - colH) / 2;
    col.forEach((n, i) => {
      placed.set(n.key, { node: n, x: PAD + ci * (W + GAP_X), y: PAD + offset + i * (H + GAP_Y) });
    });
  });
  return {
    placed,
    width: PAD * 2 + columns.length * W + Math.max(0, columns.length - 1) * GAP_X,
    height: PAD * 2 + totalH,
  };
}

const EDGE_COLOR: Partial<Record<string, string>> = {
  success: "#3fb86b",
  failed: "#f0605d",
  running: "#4aa3ff",
};

export function DagGraph({
  nodes,
  selected,
  onSelect,
  minHeight,
}: {
  nodes: DagNode[];
  selected?: string | null;
  onSelect?: (key: string) => void;
  minHeight?: number;
}) {
  const { placed, width, height } = useMemo(() => layout(nodes), [nodes]);

  const edges: { d: string; color: string; label?: string; lx: number; ly: number }[] = [];
  for (const p of placed.values()) {
    for (const dep of p.node.dependsOn) {
      const from = placed.get(dep);
      if (!from) continue;
      const x1 = from.x + W;
      const y1 = from.y + H / 2;
      const x2 = p.x - 2;
      const y2 = p.y + H / 2;
      const mid = (x1 + x2) / 2;
      const color = EDGE_COLOR[from.node.kind ?? ""] ?? "#5f6872";
      edges.push({
        d: `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`,
        color,
        label: p.node.outcomes?.[dep],
        lx: mid,
        ly: (y1 + y2) / 2,
      });
    }
  }

  return (
    <div className="dag-scroll" style={{ minHeight }}>
      <div className="dag" style={{ width, height }}>
        <svg className="edges" width={width} height={height}>
          <defs>
            {["#5f6872", "#3fb86b", "#f0605d", "#4aa3ff"].map((c) => (
              <marker
                key={c}
                id={`arrow-${c.slice(1)}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
              </marker>
            ))}
          </defs>
          {edges.map((e, i) => (
            <g key={i}>
              <path d={e.d} stroke={e.color} strokeWidth={1.6} fill="none" markerEnd={`url(#arrow-${e.color.slice(1)})`} />
              {e.label && (
                <text x={e.lx} y={e.ly - 6} fill="#aeb5bd" fontSize="11" textAnchor="middle">
                  {e.label}
                </text>
              )}
            </g>
          ))}
        </svg>
        {[...placed.values()].map(({ node, x, y }) => (
          <button
            key={node.key}
            type="button"
            className={`dag-node k-${node.kind ?? "none"}${selected === node.key ? " selected" : ""}`}
            style={{ left: x, top: y, width: W, height: H }}
            onClick={() => onSelect?.(node.key)}
            title={node.subtitle ? `${node.title}\n${node.subtitle}` : node.title}
          >
            {node.icon}
            <span className="node-text">
              <div className="node-title">{node.title}</div>
              {node.subtitle && <div className="node-sub">{node.subtitle}</div>}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
