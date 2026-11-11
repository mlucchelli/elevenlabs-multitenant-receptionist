import { useCallback, useEffect, useReducer, useState, type CSSProperties, type Dispatch } from "react";
import {
  ConversationProvider,
  useConversationClientTool,
  useConversationControls,
  useConversationMode,
  useConversationStatus,
} from "@elevenlabs/react";
import { api } from "./api";
import { CallPanel } from "./Call";
import { Workspace } from "./Workspace";
import type { OrbState } from "./Orb";
import { initial, nextId, reducer, type Action, type State } from "./state";
import type { Params, Tenant, ToolCall, ToolName } from "./types";

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  return (
    <ConversationProvider
      onConnect={() => dispatch({ type: "enter" })}
      // Workflow moves arrive as tool events: the agent config asks for agent_tool_response in client_events.
      onAgentToolResponse={event => {
        const moved = "tool_type" in event && event.tool_type === "workflow" && !event.is_error && /^notify_condition_(\d+)_met$/.exec(event.tool_name);
        if (moved) dispatch({ type: "condition", index: Number(moved[1]) });
      }}
      onMessage={({ message, source }) => dispatch({ type: "heard", from: source, text: message })}
      // Text mode streams the reply in parts.
      onAgentChatResponsePart={part => {
        if (part.type === "start") dispatch({ type: "replying", on: true });
        if (part.type === "delta" && part.text) dispatch({ type: "delta", text: part.text });
        if (part.type === "stop") dispatch({ type: "replying", on: false });
      }}
      onError={message => dispatch({ type: "say", kind: "sys", text: String(message) })}
      onDisconnect={details => dispatch({ type: "disconnected", reason: details.reason, message: "message" in details ? details.message : undefined })}
    >
      <Demo state={state} dispatch={dispatch} />
    </ConversationProvider>
  );
}

/** How long the agent must have been quiet before a transferred call is closed. */
const HANDOVER_QUIET_MS = 3500;

function Demo({ state, dispatch }: { state: State; dispatch: Dispatch<Action> }) {
  const { startSession, endSession, sendUserMessage, getInputVolume, getOutputVolume } = useConversationControls();
  const { status, message: statusMessage } = useConversationStatus();
  const { isSpeaking } = useConversationMode();

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const { textMode } = state;
  const tenant = tenants.find(t => t.id === tenantId) ?? null;
  const live = status === "connected";
  const busy = live || starting || status === "connecting";

  const talking = textMode ? state.replying : isSpeaking;

  useEffect(() => {
    api.workflow().then(workflow => dispatch({ type: "workflow", workflow })).catch(() => {});
    api.tenants().then(list => {
      setTenants(list);
      setTenantId(list[0]?.id ?? null);
    }).catch(() => setProblem("The demo backend is not reachable. Start demo/server.py."));
  }, [dispatch]);

  // On a phone the businesses are a carousel: keep the selected one in view.
  useEffect(() => {
    document.querySelector('.tenant[aria-pressed="true"]')?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [tenantId]);

  // Preview what the backend would send for this business, before any call.
  useEffect(() => {
    if (!tenantId) return;
    let stale = false;
    dispatch({ type: "reset" });
    api.preview(tenantId).then(r => !stale && dispatch({ type: "vars", vars: r.dynamic_variables, sent: false })).catch(() => {});
    return () => { stale = true; };
  }, [tenantId, dispatch]);

  // A transfer ends the agent's part of the call: on a phone line the backend
  // bridges the caller to the destination. Here the session is closed once the
  // agent has finished announcing it.
  useEffect(() => {
    if (!state.handedTo || !live || talking) return;
    const timer = setTimeout(endSession, HANDOVER_QUIET_MS);
    return () => clearTimeout(timer);
  }, [state.handedTo, live, talking, endSession]);

  // Client tools: ElevenLabs sends the call to this page, the page forwards it
  // to the backend, and the backend's answer goes back to the agent. The SDK
  // always invokes the latest handler, so this can read the current business.
  async function run(name: ToolName, params: Params): Promise<string> {
    if (!tenant) return JSON.stringify({ error: "no business selected" });
    const call: ToolCall = { id: nextId(), name, params };
    const t0 = performance.now();
    dispatch({ type: "callStart", call });
    const finish = (result: string, ok: boolean) =>
      dispatch({ type: "callEnd", id: call.id, result, ok, ms: Math.round(performance.now() - t0) });
    try {
      const { result, ok } = await api.tool(tenant.id, name, params);
      const text = typeof result === "string" ? result : JSON.stringify(result);
      finish(text, ok);
      if (ok && name === "transfer_call") {
        const rule = tenant.transfer_rules.find(r => r.destination === params.destination);
        dispatch({ type: "handover", to: rule?.label ?? params.destination });
      }
      return text;
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      finish(text, false);
      return JSON.stringify({ error: text });
    }
  }

  useConversationClientTool("take_message", p => run("take_message", p as Params));
  useConversationClientTool("transfer_call", p => run("transfer_call", p as Params));
  useConversationClientTool("check_availability", p => run("check_availability", p as Params));
  useConversationClientTool("book_appointment", p => run("book_appointment", p as Params));
  useConversationClientTool("show_item", p => run("show_item", p as Params));

  async function start() {
    if (!tenant) return;
    setProblem(null);
    setStarting(true);
    dispatch({ type: "reset" });
    if (window.matchMedia("(max-width: 1099px)").matches) {
      document.getElementById("call")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    try {
      const session = await api.session(tenant.id);
      dispatch({ type: "vars", vars: session.dynamic_variables, sent: true });
      startSession({
        signedUrl: session.signed_url,
        connectionType: "websocket",
        dynamicVariables: session.dynamic_variables,
        ...(textMode ? { textOnly: true, overrides: { conversation: { textOnly: true } } } : {}),
      });
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  function send(text: string) {
    dispatch({ type: "say", kind: "user", text });
    sendUserMessage(text);
  }

  const level = useCallback(() => {
    if (!live || textMode) return 0;
    try {
      return isSpeaking ? getOutputVolume() : getInputVolume();
    } catch {
      return 0; // the audio graph is not ready for the first few frames
    }
  }, [live, textMode, isSpeaking, getInputVolume, getOutputVolume]);

  const orb: OrbState = live ? (talking ? "speaking" : "listening") : busy ? "connecting" : "idle";
  const error = problem ?? (status === "error" ? statusMessage ?? "Connection error" : null);
  // During a call the line under the name is the workflow stage: on a phone the map is below the fold.
  const stage = state.workflow?.nodes.find(n => n.id === state.node)?.label;
  const sub = error
    ? error
    : live
      ? stage ?? "Connected"
      : busy
        ? "Connecting…"
        : state.feed.length
          ? "Call ended"
          : tenant ? `${tenant.kind} · ${tenant.open ? "open now" : "closed right now"}, ${tenant.local_time} there` : "";

  return (
    <div className="app" style={{ "--h": tenant?.hue ?? 270 } as CSSProperties}>
      <div className="backdrop" aria-hidden="true" />

      <header className="top">
        <div className="brand">
          <span className="brand-mark" />
          <span>Receptionist</span>
          <em>One agent, four businesses</em>
        </div>
        <nav className="tenants" aria-label="Businesses">
          {tenants.map(t => (
            <button
              key={t.id}
              type="button"
              className="tenant"
              style={{ "--th": t.hue } as CSSProperties}
              aria-pressed={t.id === tenantId}
              disabled={busy}
              onClick={() => setTenantId(t.id)}
            >
              <span className="tenant-art">{t.emoji}</span>
              <span className="tenant-text">
                <strong>{t.business_name}</strong>
                <span className={t.open ? "open yes" : "open"}>{t.open ? "open" : "closed"}</span>
              </span>
            </button>
          ))}
        </nav>
        <span className="pill" data-state={error ? "error" : live ? "live" : "idle"}>
          {error ? "Error" : live ? "On call" : busy ? "Connecting" : "Idle"}
        </span>
      </header>

      {tenant && (
        <main className="layout">
          <CallPanel
            tenant={tenant}
            textMode={textMode}
            onMode={text => dispatch({ type: "mode", text })}
            busy={busy}
            live={live}
            orb={orb}
            level={level}
            sub={sub}
            feed={state.feed}
            calls={state.calls}
            steps={state.steps}
            workflow={state.workflow}
            onStart={start}
            onHangUp={endSession}
            onSend={send}
          />
          <Workspace tenant={tenant} calls={state.calls} workflow={state.workflow} node={state.node} steps={state.steps} vars={state.vars} varsSent={state.varsSent} />
        </main>
      )}
    </div>
  );
}
