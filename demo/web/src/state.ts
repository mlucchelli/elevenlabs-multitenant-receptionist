import type { FeedItem, Step, ToolCall, Vars, Workflow } from "./types";

export type State = {
  feed: FeedItem[];
  calls: ToolCall[];
  vars: Vars | null;
  /** true once the variables shown are the ones a real session was started with */
  varsSent: boolean;
  workflow: Workflow | null;
  /** id of the workflow node the agent is in */
  node: string | null;
  steps: Step[];
  /** text mode only: the agent is streaming a reply */
  replying: boolean;
};

export type Action =
  | { type: "reset" }
  | { type: "workflow"; workflow: Workflow }
  | { type: "vars"; vars: Vars; sent: boolean }
  | { type: "say"; kind: "agent" | "user" | "sys"; text: string }
  | { type: "delta"; text: string }
  | { type: "replying"; on: boolean }
  | { type: "enter" }
  | { type: "condition"; index: number }
  | { type: "ended" }
  | { type: "callStart"; call: ToolCall }
  | { type: "callEnd"; id: number; result: string; ok: boolean; ms: number };

export const initial: State = { feed: [], calls: [], vars: null, varsSent: false, workflow: null, node: null, steps: [], replying: false };

let seq = 0;
export const nextId = () => ++seq;

function move(state: State, edgeId: string | undefined, announce: boolean): State {
  const edge = state.workflow?.edges.find(e => e.id === edgeId);
  if (!edge || !state.node) return state;
  const back = edge.target === state.node;
  const step: Step = { id: nextId(), from: state.node, to: back ? edge.source : edge.target, edge: edge.id, back };
  return {
    ...state,
    node: step.to,
    steps: [...state.steps, step],
    feed: announce ? [...state.feed, { id: nextId(), kind: "step", stepId: step.id }] : state.feed,
  };
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return { ...initial, vars: state.vars, workflow: state.workflow };
    case "workflow":
      return { ...state, workflow: action.workflow };
    case "vars":
      return { ...state, vars: action.vars, varsSent: action.sent };
    case "say": {
      // A final agent message replaces the bubble that was being streamed.
      const last = state.feed.at(-1);
      if (action.kind === "agent" && last?.kind === "agent" && last.live) {
        return { ...state, feed: [...state.feed.slice(0, -1), { ...last, text: action.text, live: false }] };
      }
      return { ...state, feed: [...state.feed, { id: nextId(), kind: action.kind, text: action.text }] };
    }
    case "delta": {
      const last = state.feed.at(-1);
      if (last?.kind === "agent" && last.live) {
        return { ...state, feed: [...state.feed.slice(0, -1), { ...last, text: last.text + action.text }] };
      }
      return { ...state, feed: [...state.feed, { id: nextId(), kind: "agent", text: action.text, live: true }] };
    }
    case "replying":
      return { ...state, replying: action.on };
    case "enter": {
      // The call starts on the start node and takes its unconditional edge straight away.
      const start = state.workflow?.nodes.find(n => n.type === "start");
      if (!start) return state;
      return move({ ...state, node: start.id, steps: [] }, start.edge_order[0], false);
    }
    case "condition": {
      // ElevenLabs reports a workflow move as the tool notify_condition_<N>_met,
      // where N counts the current node's edges in edge_order, starting at 1.
      const current = state.workflow?.nodes.find(n => n.id === state.node);
      return move(state, current?.edge_order[action.index - 1], true);
    }
    case "ended": {
      // Normally the move to the end node has already been reported. This covers a hang-up without one.
      const end = state.workflow?.nodes.find(n => n.type === "end");
      const current = state.workflow?.nodes.find(n => n.id === state.node);
      if (!end || !current || current.id === end.id) return state;
      return move(state, current.edge_order.find(id => state.workflow!.edges.some(e => e.id === id && e.target === end.id)), false);
    }
    case "callStart":
      return {
        ...state,
        calls: [...state.calls, action.call],
        feed: [...state.feed, { id: nextId(), kind: "tool", callId: action.call.id }],
      };
    case "callEnd":
      return {
        ...state,
        calls: state.calls.map(c => (c.id === action.id ? { ...c, result: action.result, ok: action.ok, ms: action.ms } : c)),
      };
  }
}
