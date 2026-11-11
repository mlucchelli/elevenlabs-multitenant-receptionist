import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { Step, Vars, Workflow, WorkflowNode } from "./types";

type Props = { workflow: Workflow; node: string | null; steps: Step[]; vars: Vars | null };

function useNarrow() {
  const query = "(max-width: 759px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}

type Box = { x: number; y: number; w: number };

/** Columns by distance from the start node; inside a column, the order the edges are declared in. */
function layout(workflow: Workflow, narrow: boolean) {
  const depth = new Map<string, number>();
  const start = workflow.nodes.find(n => n.type === "start") ?? workflow.nodes[0];
  const queue = [start.id];
  depth.set(start.id, 0);
  while (queue.length) {
    const id = queue.shift()!;
    for (const edgeId of workflow.nodes.find(n => n.id === id)?.edge_order ?? []) {
      const target = workflow.edges.find(e => e.id === edgeId)?.target;
      if (target && !depth.has(target)) {
        depth.set(target, depth.get(id)! + 1);
        queue.push(target);
      }
    }
  }
  const columns: WorkflowNode[][] = [];
  for (const [id, d] of depth) (columns[d] ??= []).push(workflow.nodes.find(n => n.id === id)!);

  const W = narrow ? 360 : 640;
  const row = narrow ? 36 : 38;
  const H = Math.max(...columns.map(c => c.length)) * row + 8;
  const widths = columns.map((_, d) => (d === 0 ? (narrow ? 44 : 58) : d === columns.length - 1 ? (narrow ? 140 : 176) : narrow ? 118 : 160));
  const gap = (W - 8 - widths.reduce((a, b) => a + b, 0)) / Math.max(1, columns.length - 1);

  const boxes = new Map<string, Box>();
  let left = 4;
  columns.forEach((column, d) => {
    column.forEach((n, i) => boxes.set(n.id, { x: left + widths[d] / 2, y: H / 2 + (i - (column.length - 1) / 2) * row, w: widths[d] }));
    left += widths[d] + gap;
  });
  return { W, H, boxes, boxHeight: row - 8 };
}

/**
 * The workflow drawn from the agent config. The current node, the path taken
 * and each move are live; stages switched off for this business are dashed.
 */
export function WorkflowMap({ workflow, node, steps, vars }: Props) {
  const narrow = useNarrow();
  const { W, H, boxes, boxHeight } = layout(workflow, narrow);

  const visited = new Set(steps.flatMap(s => [s.from, s.to]));
  const walked = new Set(steps.map(s => s.edge));
  const last = steps.at(-1);
  const closed = (gate: string | null) => gate !== null && vars !== null && vars[gate] !== "true";
  const label = (id: string) => workflow.nodes.find(n => n.id === id)?.label ?? id;
  const lastEdge = workflow.edges.find(e => e.id === last?.edge);
  const why = last && lastEdge ? (last.back ? lastEdge.back : lastEdge.when) : null;

  return (
    <article className="card tile wide flowmap">
      <header>
        <span className="tile-icon"><Icon name="route" size={16} /></span>
        <h3>Workflow</h3>
        <code>{node ? label(node) : "not started"}</code>
      </header>

      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Agent workflow">
        {workflow.edges.map(edge => {
          const a = boxes.get(edge.source), b = boxes.get(edge.target);
          if (!a || !b) return null;
          const x1 = a.x + a.w / 2, x2 = b.x - b.w / 2, mid = (x1 + x2) / 2;
          const d = `M${x1} ${a.y} C${mid} ${a.y} ${mid} ${b.y} ${x2} ${b.y}`;
          return (
            <g key={edge.id} className={`wf-edge${walked.has(edge.id) ? " walked" : ""}${closed(edge.gate) ? " off" : ""}`}>
              <path d={d} />
              {last?.edge === edge.id && <Traveller key={last.id} d={d} back={last.back} />}
            </g>
          );
        })}
        {workflow.nodes.map(n => {
          const box = boxes.get(n.id);
          if (!box) return null;
          const gate = workflow.edges.find(e => e.target === n.id && e.gate)?.gate ?? null;
          const off = closed(gate);
          const state = n.id === node ? " current" : visited.has(n.id) ? " visited" : "";
          return (
            <g key={n.id} className={`wf-node${state}${off ? " off" : ""}`} transform={`translate(${box.x} ${box.y})`}>
              <title>{off ? `${gate} is false for this business, so the agent can never get here` : n.tools.join(", ")}</title>
              <rect x={-box.w / 2} y={-boxHeight / 2} width={box.w} height={boxHeight} rx={boxHeight / 2} />
              <text dy="0.35em">{n.label}</text>
            </g>
          );
        })}
      </svg>

      <p className="wf-why" key={last?.id ?? "idle"}>
        {last && lastEdge ? (
          <>
            <b>{label(last.from)} → {label(last.to)}</b>
            {why ? ` ${why}` : " Unconditional: every call starts here."}
          </>
        ) : (
          "Dashed stages are switched off for this business. Start a call to watch the agent move through the rest."
        )}
      </p>
    </article>
  );
}

/** A dot that runs along the edge once, in the direction the agent moved. */
function Traveller({ d, back }: { d: string; back: boolean }) {
  const motion = useRef<SVGAnimateMotionElement>(null);
  useEffect(() => {
    motion.current?.beginElement();
  }, []);
  return (
    <circle r="5" className="wf-dot">
      <animateMotion ref={motion} path={d} dur="0.8s" begin="indefinite" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1" keyTimes="0;1" keyPoints={back ? "1;0" : "0;1"} />
    </circle>
  );
}
