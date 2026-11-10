import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { day, parse, phone, time } from "./format";
import type { Tenant, ToolCall, Vars, WorkflowNode } from "./types";

type Props = { tenant: Tenant; calls: ToolCall[]; node: WorkflowNode | null; vars: Vars | null; varsSent: boolean };

/** The business's side of the call: what each tool call left behind. */
export function Workspace({ tenant, calls, node, vars, varsSent }: Props) {
  const done = calls.filter(c => c.ok);
  return (
    <section className="work" aria-label="What the call did">
      <ScreenCard tenant={tenant} shown={done.filter(c => c.name === "show_item").map(c => c.params.item_id)} />
      <InboxCard messages={done.filter(c => c.name === "take_message")} />
      <CalendarCard tenant={tenant} calls={done} />
      <TransferCard tenant={tenant} transfer={calls.findLast(c => c.name === "transfer_call" && c.ok !== false)} />
      <ActivityCard calls={calls} node={node} tenant={tenant} />
      <VarsCard vars={vars} sent={varsSent} />
    </section>
  );
}

function Card(p: { id: string; icon: IconName; title: string; tool?: string; wide?: boolean; fresh?: number; children: ReactNode }) {
  return (
    <article className={`card tile${p.wide ? " wide" : ""}`} id={p.id}>
      <header>
        <span className="tile-icon"><Icon name={p.icon} size={16} /></span>
        <h3>{p.title}</h3>
        {p.tool && <code>{p.tool}</code>}
      </header>
      {/* The key restarts the highlight animation each time something new lands in the card. */}
      <div className="tile-body" key={p.fresh ?? 0} data-fresh={p.fresh ? "" : undefined}>
        {p.children}
      </div>
    </article>
  );
}

function Locked({ flag, children }: { flag: string; children: ReactNode }) {
  return (
    <div className="locked">
      <Icon name="lock" size={16} />
      <p>{children}</p>
      <code>{flag} = "false"</code>
    </div>
  );
}

function ScreenCard({ tenant, shown }: { tenant: Tenant; shown: string[] }) {
  const spot = shown.at(-1);
  return (
    <Card id="card-screen" icon="screen" title="On the caller's screen" tool="show_item" wide fresh={shown.length}>
      <div className="catalog">
        {tenant.catalog.map(item => (
          <div key={item.id} className={`product${item.id === spot ? " spot" : shown.includes(item.id) ? " seen" : ""}`}>
            <span className="item-art">{item.emoji}</span>
            <div>
              <strong>{item.name}</strong>
              <p>{item.description}</p>
            </div>
            <b>{item.price}</b>
            {item.id === spot && <em>The agent is showing this</em>}
          </div>
        ))}
      </div>
      <p className="hint">Ask about one of these. The agent calls a client tool and the page brings it forward.</p>
    </Card>
  );
}

function InboxCard({ messages }: { messages: ToolCall[] }) {
  return (
    <Card id="card-inbox" icon="inbox" title="Inbox" tool="take_message" fresh={messages.length}>
      {messages.length === 0 && <p className="empty">Messages the agent takes land here.</p>}
      {messages.toReversed().map(m => (
        <div key={m.id} className="note">
          <span className="avatar">{m.params.caller_name?.[0] ?? "?"}</span>
          <div>
            <strong>{m.params.caller_name}</strong>
            <span>just now</span>
            <p>{m.params.message}</p>
            {m.params.contact_number && <small>Call back: {phone(m.params.contact_number)}</small>}
          </div>
        </div>
      ))}
    </Card>
  );
}

function CalendarCard({ tenant, calls }: { tenant: Tenant; calls: ToolCall[] }) {
  if (tenant.calendars.length === 0) {
    return (
      <Card id="card-calendar" icon="calendar" title="Calendar" tool="book_appointment">
        <Locked flag="scheduling_enabled">Scheduling is off for this business, so the workflow never opens that stage. The agent offers to take a message.</Locked>
      </Card>
    );
  }
  const bookings = calls.filter(c => c.name === "book_appointment");
  const check = calls.findLast(c => c.name === "check_availability");
  const date = bookings.at(-1)?.params.date ?? check?.params.preferred_date;
  const calendarId = bookings.at(-1)?.params.calendar_id ?? check?.params.calendar_id ?? tenant.calendars[0].id;
  const calendar = tenant.calendars.find(c => c.id === calendarId) ?? tenant.calendars[0];

  const free = check?.params.preferred_date === date ? (parse<{ slots: { start: string }[] }>(check?.result)?.slots ?? []).map(s => s.start) : [];
  const booked = new Map(bookings.filter(b => b.params.date === date).map(b => [b.params.start_time, b.params.caller_name]));
  const rows = [...new Set([...free, ...booked.keys()])].sort();

  return (
    <Card id="card-calendar" icon="calendar" title="Calendar" tool="book_appointment" fresh={bookings.length + (check ? 1 : 0)}>
      <p className="cal-head">
        <strong>{calendar.name}</strong>
        <span>{date ? day(date) : calendar.description}</span>
      </p>
      {rows.length === 0 && <p className="empty">{date ? "No openings that day." : "The agent checks this calendar before it offers a time."}</p>}
      <div className="cal-rows">
        {rows.map(start => (
          <div key={start} className={booked.has(start) ? "slot booked" : "slot"}>
            <span>{time(start)}</span>
            {booked.has(start) ? (
              <strong><span className="tick"><Icon name="check" size={12} /></span> {booked.get(start)}</strong>
            ) : (
              <em>open</em>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function TransferCard({ tenant, transfer }: { tenant: Tenant; transfer?: ToolCall }) {
  if (tenant.transfer_rules.length === 0) {
    return (
      <Card id="card-transfer" icon="route" title="Transfers" tool="transfer_call">
        <Locked flag="transfer_enabled">Transfers are off for this business. The agent says why it cannot connect the caller and takes a message.</Locked>
      </Card>
    );
  }
  return (
    <Card id="card-transfer" icon="route" title="Transfers" tool="transfer_call" fresh={transfer ? 1 : 0}>
      <div className="routes">
        {tenant.transfer_rules.map(rule => {
          const active = transfer?.params.destination === rule.destination;
          return (
            <div key={rule.destination} className={active ? "route active" : "route"}>
              <span className="avatar">{rule.label[0]}</span>
              <div>
                <strong>{rule.label}</strong>
                <p>{active ? transfer.params.reason : `When: ${rule.match}`}</p>
              </div>
              {active ? <span className="ringing"><span /><span /><span /></span> : <small>{phone(rule.destination)}</small>}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

const NODES: { id: WorkflowNode; name: string; gate?: "transfer_rules" | "calendars" }[] = [
  { id: "triage", name: "Reception & Triage" },
  { id: "transfer", name: "Call Transfer", gate: "transfer_rules" },
  { id: "schedule", name: "Schedule Appointment", gate: "calendars" },
  { id: "end", name: "End" },
];

function ActivityCard({ calls, node, tenant }: { calls: ToolCall[]; node: WorkflowNode | null; tenant: Tenant }) {
  return (
    <Card id="card-activity" icon="bolt" title="Agent activity">
      <ol className="flow">
        {NODES.map(n => {
          const off = n.gate && tenant[n.gate].length === 0;
          return (
            <li key={n.id} className={`${n.id === node ? "active" : ""}${off ? " off" : ""}`} title={off ? "The gate variable is false for this business" : undefined}>
              {off && <Icon name="lock" size={12} />}
              {n.name}
            </li>
          );
        })}
      </ol>
      {calls.length === 0 && <p className="empty">Every tool call appears here with its arguments and what the backend answered.</p>}
      <div className="log">
        {calls.toReversed().map(c => (
          <div key={c.id} className={c.ok === false ? "call-row bad" : "call-row"}>
            <p>
              <code>{c.name}</code>
              <span>{c.result === undefined ? "running…" : `${c.ms} ms`}</span>
            </p>
            <dl>
              {Object.entries(c.params).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              ))}
            </dl>
            {c.result !== undefined && <pre>→ {c.result}</pre>}
          </div>
        ))}
      </div>
      <p className="hint">ElevenLabs does not tell the client which workflow node is active, so the highlight follows the tool calls.</p>
    </Card>
  );
}

function VarsCard({ vars, sent }: { vars: Vars | null; sent: boolean }) {
  return (
    <article className="card tile wide" id="card-vars">
      <details>
        <summary>
          <span className="tile-icon"><Icon name="braces" size={16} /></span>
          <h3>Dynamic variables</h3>
          <span className="sum-note">{sent ? "sent when this call started" : "what the backend would send now"}</span>
          <Icon name="down" size={16} />
        </summary>
        <dl className="vars">
          {Object.entries(vars ?? {}).map(([k, v]) => {
            const json = /^[[{]/.test(v) ? parse<unknown>(v) : null;
            return (
              <div key={k}>
                <dt>{k}</dt>
                <dd className={v === "true" || v === "false" ? `flag ${v}` : undefined}>
                  {json ? JSON.stringify(json, null, 2) : v === "" ? '""' : v}
                </dd>
              </div>
            );
          })}
        </dl>
      </details>
    </article>
  );
}
