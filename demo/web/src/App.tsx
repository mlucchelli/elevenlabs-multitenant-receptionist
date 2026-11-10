import { useCallback, useEffect, useReducer, useRef, useState, type CSSProperties, type Dispatch, type RefObject } from "react";
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
  // Read inside SDK callbacks, which outlive the render that created them.
  const textMode = useRef(false);
  const handedTo = useRef<string | null>(null);

  return (
    <ConversationProvider
      onConnect={() => dispatch({ type: "node", node: "triage" })}
      onMessage={({ message, source }) => {
        if (source === "user" && textMode.current) return; // typed messages are shown when they are sent
        dispatch({ type: "say", kind: source === "user" ? "user" : "agent", text: message });
      }}
      // Text mode streams the reply in parts.
      onAgentChatResponsePart={part => {
        if (part.type === "start") dispatch({ type: "replying", on: true });
        if (part.type === "delta" && part.text) dispatch({ type: "delta", text: part.text });
        if (part.type === "stop") dispatch({ type: "replying", on: false });
      }}
      onError={message => dispatch({ type: "say", kind: "sys", text: String(message) })}
      onDisconnect={details => {
        dispatch({ type: "replying", on: false });
        if (handedTo.current) {
          dispatch({ type: "say", kind: "sys", text: `Call handed over to ${handedTo.current}.` });
        } else if (details.reason === "agent") {
          dispatch({ type: "node", node: "end" });
          dispatch({ type: "say", kind: "sys", text: "The agent ended the call." });
        } else if (details.reason === "error") {
          dispatch({ type: "say", kind: "sys", text: `Disconnected: ${details.message}` });
        } else {
          dispatch({ type: "say", kind: "sys", text: "Call ended." });
        }
      }}
    >
      <Demo state={state} dispatch={dispatch} textModeRef={textMode} handedTo={handedTo} />
    </ConversationProvider>
  );
}

type DemoProps = {
  state: State;
  dispatch: Dispatch<Action>;
  textModeRef: RefObject<boolean>;
  handedTo: RefObject<string | null>;
};

function Demo({ state, dispatch, textModeRef, handedTo }: DemoProps) {
  const { startSession, endSession, sendUserMessage, getInputVolume, getOutputVolume } = useConversationControls();
  const { status, message: statusMessage } = useConversationStatus();
  const { isSpeaking } = useConversationMode();

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [textMode, setTextMode] = useState(false);
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [toast, setToast] = useState<{ id: number; text: string; target: string } | null>(null);

  const tenant = tenants.find(t => t.id === tenantId) ?? null;
  const live = status === "connected";
  const busy = live || starting || status === "connecting";

  const tenantRef = useRef(tenant);
  tenantRef.current = tenant;
  const speaking = useRef(isSpeaking);
  speaking.current = isSpeaking;
  textModeRef.current = textMode;

  useEffect(() => {
    api.tenants().then(list => {
      setTenants(list);
      setTenantId(list[0]?.id ?? null);
    }).catch(() => setProblem("The demo backend is not reachable. Start demo/server.py."));
  }, []);

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
  // bridges the caller to the destination. Here the session is simply closed
  // once the agent has finished announcing it.
  const handOver = useCallback((label: string) => {
    handedTo.current = label;
    const started = Date.now();
    const timer = setInterval(() => {
      const waited = Date.now() - started;
      if (waited > 3500 && (!speaking.current || waited > 14000)) {
        clearInterval(timer);
        endSession();
      }
    }, 300);
  }, [endSession, handedTo]);

  // Client tools: ElevenLabs sends the call to this page, the page forwards it
  // to the backend, and the backend's answer goes back to the agent.
  const run = useCallback(async (name: ToolName, params: Params): Promise<string> => {
    const business = tenantRef.current!;
    const call: ToolCall = { id: nextId(), name, params };
    const t0 = performance.now();
    dispatch({ type: "callStart", call });
    const finish = (result: string, ok: boolean) =>
      dispatch({ type: "callEnd", id: call.id, result, ok, ms: Math.round(performance.now() - t0) });
    try {
      const { result, ok } = await api.tool(business.id, name, params);
      const text = typeof result === "string" ? result : JSON.stringify(result);
      finish(text, ok);
      if (ok) {
        setToast({ id: call.id, ...TOAST[name] });
        if (name === "transfer_call") {
          handOver(business.transfer_rules.find(r => r.destination === params.destination)?.label ?? params.destination);
        }
      }
      return text;
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      finish(text, false);
      return JSON.stringify({ error: text });
    }
  }, [dispatch, handOver]);

  useConversationClientTool("take_message", p => run("take_message", p as Params));
  useConversationClientTool("transfer_call", p => run("transfer_call", p as Params));
  useConversationClientTool("check_availability", p => run("check_availability", p as Params));
  useConversationClientTool("book_appointment", p => run("book_appointment", p as Params));
  useConversationClientTool("show_item", p => run("show_item", p as Params));

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  async function start() {
    if (!tenant) return;
    setProblem(null);
    setStarting(true);
    handedTo.current = null;
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
      return speaking.current ? getOutputVolume() : getInputVolume();
    } catch {
      return 0;
    }
  }, [live, textMode, getInputVolume, getOutputVolume]);

  const talking = textMode ? state.replying : isSpeaking;
  const orb: OrbState = live ? (talking ? "speaking" : "listening") : busy ? "connecting" : "idle";
  const error = problem ?? (status === "error" ? statusMessage ?? "Connection error" : null);
  const sub = error
    ? error
    : live
      ? talking ? (textMode ? "Replying" : "Speaking") : textMode ? "Connected" : "Listening"
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
          Receptionist <em>demo</em>
        </div>
        <span className="pill" data-state={error ? "error" : live ? "live" : "idle"}>
          {error ? "Error" : live ? "On call" : busy ? "Connecting" : "Idle"}
        </span>
      </header>

      <section className="intro">
        <h1>One agent, four businesses</h1>
        <p>
          Pick a business and call it. The ElevenLabs agent, its prompt and its workflow are the same every time. Only the
          dynamic variables the backend sends at the start of the call change.
        </p>
      </section>

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
              <span className={t.open ? "open yes" : "open"}>{t.kind} · {t.open ? "open" : "closed"}</span>
            </span>
            <span className="caps">
              <i className="on">Messages</i>
              <i className={t.transfer_rules.length ? "on" : ""}>Transfers</i>
              <i className={t.calendars.length ? "on" : ""}>Scheduling</i>
            </span>
          </button>
        ))}
      </nav>

      {tenant && (
        <main className="layout">
          <CallPanel
            tenant={tenant}
            textMode={textMode}
            onMode={setTextMode}
            busy={busy}
            live={live}
            orb={orb}
            level={level}
            sub={sub}
            feed={state.feed}
            calls={state.calls}
            onStart={start}
            onHangUp={endSession}
            onSend={send}
          />
          <Workspace tenant={tenant} calls={state.calls} node={state.node} vars={state.vars} varsSent={state.varsSent} />
        </main>
      )}

      {toast && (
        <button
          key={toast.id}
          type="button"
          className="toast"
          onClick={() => {
            document.getElementById(toast.target)?.scrollIntoView({ behavior: "smooth", block: "center" });
            setToast(null);
          }}
        >
          {toast.text} <span>View</span>
        </button>
      )}
    </div>
  );
}

const TOAST: Record<ToolName, { text: string; target: string }> = {
  take_message: { text: "New message in the inbox", target: "card-inbox" },
  check_availability: { text: "Calendar checked", target: "card-calendar" },
  book_appointment: { text: "Appointment booked", target: "card-calendar" },
  transfer_call: { text: "Transfer started", target: "card-transfer" },
  show_item: { text: "Item shown on screen", target: "card-screen" },
};
