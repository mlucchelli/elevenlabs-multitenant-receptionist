import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { Step, Vars, Workflow, WorkflowNode } from "./types";

type Props = { workflow: Workflow; node: string | null; steps: Step[]; vars: Vars | null };

const BOX = { w: 150, h: 38, small: 64 };

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

/**
 * The workflow drawn from the agent config, using the node positions saved by
 * the ElevenLabs editor. On wide screens it is turned on its side so it reads
 * left to right. The current node, the path taken and each move are live.
 */
export function WorkflowMap({ workflow, node, steps, vars }: Props) {
  const narrow = useNarrow();
  const size = narrow ? { w: 360, h: 330, px: 84, py: 26 } : { w: 720, h: 190, px: 62, py: 36 };

  const xs = workflow.nodes.map(n => n.x);
  const ys = workflow.nodes.map(n => n.y);
  const scale = (v: number, all: number[], from: number, to: number) => {
    const min = Math.min(...all), max = Math.max(...all);
    return max === min ? (from + to) / 2 : from + ((v - min) / (max - min)) * (to - from);
  };
  const at = (n: WorkflowNode) =>
    narrow
      ? { x: scale(n.x, xs, size.px, size.w - size.px), y: scale(n.y, ys, size.py, size.h - size.py) }
      : { x: scale(n.y, ys, size.px, size.w - size.px), y: scale(n.x, xs, size.py, size.h - size.py) };
  const point = new Map(workflow.nodes.map(n => [n.id, at(n)]));

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

      <svg viewBox={`0 0 ${size.w} ${size.h}`} role="img" aria-label="Agent workflow">
        {workflow.edges.map(edge => {
          const a = point.get(edge.source)!, b = point.get(edge.target)!;
          const d = `M${a.x} ${a.y} L${b.x} ${b.y}`;
          const off = closed(edge.gate);
          return (
            <g key={edge.id} className={`wf-edge${walked.has(edge.id) ? " walked" : ""}${off ? " off" : ""}`}>
              <path d={d} />
              {last?.edge === edge.id && <Traveller key={last.id} d={d} back={last.back} />}
            </g>
          );
        })}
        {workflow.nodes.map(n => {
          const p = point.get(n.id)!;
          const w = n.type === "override_agent" ? BOX.w : BOX.small;
          const gate = workflow.edges.find(e => e.target === n.id && e.gate)?.gate ?? null;
          const off = closed(gate);
          const state = n.id === node ? " current" : visited.has(n.id) ? " visited" : "";
          return (
            <g key={n.id} className={`wf-node${state}${off ? " off" : ""}`} transform={`translate(${p.x} ${p.y})`}>
              <title>{off ? `${gate} is false for this business, so the agent can never get here` : n.tools.join(", ")}</title>
              <rect x={-w / 2} y={-BOX.h / 2} width={w} height={BOX.h} rx={BOX.h / 2} />
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
          "Start a call to watch the agent move through its workflow. Dashed stages are switched off for this business."
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
