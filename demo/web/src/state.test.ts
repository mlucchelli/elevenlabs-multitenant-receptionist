import { describe, expect, it } from "vitest";
import { initial, reducer, type Action, type State } from "./state";
import type { Workflow } from "./types";

// Same shape as the real agent: triage fans out to three gated stages and the end node.
const workflow: Workflow = {
  nodes: [
    { id: "start", type: "start", label: "Start", x: 0, y: 0, edge_order: ["e_start"], tools: [] },
    { id: "triage", type: "override_agent", label: "Triage", x: 0, y: 0, edge_order: ["e_transfer", "e_schedule", "e_end"], tools: [] },
    { id: "transfer", type: "override_agent", label: "Transfer", x: 0, y: 0, edge_order: ["e_transfer"], tools: [] },
    { id: "schedule", type: "override_agent", label: "Schedule", x: 0, y: 0, edge_order: ["e_schedule"], tools: [] },
    { id: "end", type: "end", label: "End", x: 0, y: 0, edge_order: [], tools: [] },
  ],
  edges: [
    { id: "e_start", source: "start", target: "triage", gate: null, when: null, back: null },
    { id: "e_transfer", source: "triage", target: "transfer", gate: "transfer_enabled", when: "wants a person", back: "transfer over" },
    { id: "e_schedule", source: "triage", target: "schedule", gate: "scheduling_enabled", when: "wants to book", back: "booking over" },
    { id: "e_end", source: "triage", target: "end", gate: null, when: "goodbye", back: null },
  ],
};

const run = (...actions: Action[]): State => actions.reduce(reducer, { ...initial, workflow });
const texts = (state: State) => state.feed.map(item => ("text" in item ? item.text : item.kind));

describe("workflow moves", () => {
  it("enters triage through the start node's edge without announcing it", () => {
    const state = run({ type: "enter" });
    expect(state.node).toBe("triage");
    expect(state.steps).toMatchObject([{ from: "start", to: "triage", back: false }]);
    expect(state.feed).toEqual([]);
  });

  it("reads notify_condition_N as the Nth edge of the current node", () => {
    const state = run({ type: "enter" }, { type: "condition", index: 2 });
    expect(state.node).toBe("schedule");
    expect(state.feed.at(-1)).toMatchObject({ kind: "step" });
  });

  it("follows an edge backwards when the current node is its target", () => {
    const state = run({ type: "enter" }, { type: "condition", index: 2 }, { type: "condition", index: 1 });
    expect(state.node).toBe("triage");
    expect(state.steps.at(-1)).toMatchObject({ from: "schedule", to: "triage", edge: "e_schedule", back: true });
    expect(state.feed.filter(item => item.kind === "step")).toHaveLength(1); // the way back is not announced
  });

  it("ignores a condition number the current node does not have", () => {
    const state = run({ type: "enter" }, { type: "condition", index: 9 });
    expect(state.node).toBe("triage");
    expect(state.steps).toHaveLength(1);
  });

  it("moves to the end node when the agent hangs up without reporting the move", () => {
    const state = run({ type: "enter" }, { type: "disconnected", reason: "agent" });
    expect(state.node).toBe("end");
    expect(texts(state)).toEqual(["The agent ended the call."]);
  });
});

describe("conversation", () => {
  it("replaces a streamed reply with the final message instead of adding a second bubble", () => {
    const state = run({ type: "delta", text: "Hel" }, { type: "delta", text: "lo" }, { type: "heard", from: "ai", text: "Hello." });
    expect(texts(state)).toEqual(["Hello."]);
  });

  it("does not repeat a typed message when the server echoes it", () => {
    const state = run({ type: "mode", text: true }, { type: "say", kind: "user", text: "hi" }, { type: "heard", from: "user", text: "hi" });
    expect(texts(state)).toEqual(["hi"]);
  });

  it("shows what the caller said in voice mode", () => {
    expect(texts(run({ type: "heard", from: "user", text: "hi" }))).toEqual(["hi"]);
  });

  it("says who took the call when it ends after a transfer", () => {
    const state = run({ type: "enter" }, { type: "handover", to: "Billing desk" }, { type: "disconnected", reason: "user" });
    expect(texts(state)).toEqual(["Call handed over to Billing desk."]);
    expect(state.node).toBe("triage");
  });

  it("keeps the text mode choice and the workflow across a reset", () => {
    const state = run({ type: "mode", text: true }, { type: "say", kind: "user", text: "hi" }, { type: "reset" });
    expect(state).toMatchObject({ textMode: true, workflow, feed: [] });
  });
});

describe("tool calls", () => {
  it("records the result against the call that started", () => {
    const state = run(
      { type: "callStart", call: { id: 7, name: "take_message", params: { message: "hi" } } },
      { type: "callEnd", id: 7, result: "Message saved.", ok: true, ms: 12 },
    );
    expect(state.calls).toEqual([{ id: 7, name: "take_message", params: { message: "hi" }, result: "Message saved.", ok: true, ms: 12 }]);
    expect(state.feed).toMatchObject([{ kind: "tool", callId: 7 }]);
  });
});
