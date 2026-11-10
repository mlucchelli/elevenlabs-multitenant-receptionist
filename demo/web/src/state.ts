import type { FeedItem, ToolCall, Vars, WorkflowNode } from "./types";

export type State = {
  feed: FeedItem[];
  calls: ToolCall[];
  vars: Vars | null;
  /** true once the variables shown are the ones a real session was started with */
  varsSent: boolean;
  node: WorkflowNode | null;
  /** text mode only: the agent is streaming a reply */
  replying: boolean;
};

export type Action =
  | { type: "reset" }
  | { type: "vars"; vars: Vars; sent: boolean }
  | { type: "say"; kind: "agent" | "user" | "sys"; text: string }
  | { type: "delta"; text: string }
  | { type: "replying"; on: boolean }
  | { type: "node"; node: WorkflowNode | null }
  | { type: "callStart"; call: ToolCall }
  | { type: "callEnd"; id: number; result: string; ok: boolean; ms: number };

export const initial: State = { feed: [], calls: [], vars: null, varsSent: false, node: null, replying: false };

let seq = 0;
export const nextId = () => ++seq;

// The workflow node is not reported to the client, so it is inferred from the tool that ran.
const NODE_OF: Record<ToolCall["name"], WorkflowNode | null> = {
  take_message: "triage",
  show_item: null,
  transfer_call: "transfer",
  check_availability: "schedule",
  book_appointment: "schedule",
};

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return { ...initial, vars: state.vars, varsSent: false };
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
    case "node":
      return { ...state, node: action.node };
    case "callStart":
      return {
        ...state,
        calls: [...state.calls, action.call],
        feed: [...state.feed, { id: nextId(), kind: "tool", callId: action.call.id }],
        node: NODE_OF[action.call.name] ?? state.node,
      };
    case "callEnd":
      return {
        ...state,
        calls: state.calls.map(c => (c.id === action.id ? { ...c, result: action.result, ok: action.ok, ms: action.ms } : c)),
      };
  }
}
