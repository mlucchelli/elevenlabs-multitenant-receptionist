import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { day, parse, phone, time } from "./format";
import { WorkflowMap } from "./WorkflowMap";
import type { Step, Tenant, ToolCall, Vars, Workflow } from "./types";

type Props = { tenant: Tenant; calls: ToolCall[]; workflow: Workflow | null; node: string | null; steps: Step[]; vars: Vars | null; varsSent: boolean };

type CardId = "screen" | "inbox" | "calendar" | "transfer";
const CARD_OF: Record<ToolCall["name"], CardId> = {
  show_item: "screen",
  take_message: "inbox",
  check_availability: "calendar",
  book_appointment: "calendar",
  transfer_call: "transfer",
};

/**
 * The business's side of the call. Nothing is drawn until the agent uses it:
 * a card appears the first time one of its tools succeeds, in the order used.
 */
export function Workspace({ tenant, calls, workflow, node, steps, vars, varsSent }: Props) {
  const done = calls.filter(c => c.ok);
  const used = [...new Set(done.map(c => CARD_OF[c.name]))];
  const cards: Record<CardId, (wide: boolean) => ReactNode> = {
    screen: wide => <ScreenCard key="screen" wide={wide} tenant={tenant} shown={done.filter(c => c.name === "show_item").map(c => c.params.item_id)} />,
    inbox: wide => <InboxCard key="inbox" wide={wide} messages={done.filter(c => c.name === "take_message")} />,
    calendar: wide => <CalendarCard key="calendar" wide={wide} tenant={tenant} calls={done} />,
    transfer: wide => <TransferCard key="transfer" wide={wide} tenant={tenant} transfer={done.findLast(c => c.name === "transfer_call")!} />,
  };
  return (
    <section className="work" aria-label="What the call did">
      {workflow && <WorkflowMap workflow={workflow} node={node} steps={steps} vars={vars} />}
      {/* With an odd number of cards the first one takes the full row, so the grid never has a hole. */}
      {used.map((id, i) => cards[id](used.length % 2 === 1 && i === 0))}
      {used.length === 0 && (
        <div className="waiting">
          <span><Icon name="bolt" size={18} /></span>
          <p>Nothing has happened yet. Each thing the agent does shows up here when it does it.</p>
        </div>
      )}
      {calls.length > 0 && <ActivityCard calls={calls} />}
      <VarsCard vars={vars} sent={varsSent} />
    </section>
  );
}

function Card(p: { id: string; icon: IconName; title: string; tool?: string; wide?: boolean; fresh?: number; children: ReactNode }) {
  return (
    <article className={`card tile appear${p.wide ? " wide" : ""}`} id={p.id}>
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

function ScreenCard({ tenant, shown, wide }: { tenant: Tenant; shown: string[]; wide: boolean }) {
  const spot = shown.at(-1);
  // Newest first; an item the agent showed twice appears once.
  const items = [...new Set(shown.toReversed())].map(id => tenant.catalog.find(i => i.id === id)).filter(i => i !== undefined);
  return (
    <Card id="card-screen" icon="screen" title="On the caller's screen" tool="show_item" wide={wide} fresh={shown.length}>
      <div className="catalog">
        {items.map(item => (
          <div key={item.id} className={item.id === spot ? "product spot" : "product"}>
            <span className="item-art">{item.emoji}</span>
            <div>
              <strong>{item.name}</strong>
              <p>{item.description}</p>
            </div>
            <b>{item.price}</b>
          </div>
        ))}
      </div>
    </Card>
  );
}

function InboxCard({ messages, wide }: { messages: ToolCall[]; wide: boolean }) {
  return (
    <Card id="card-inbox" icon="inbox" title="Inbox" tool="take_message" wide={wide} fresh={messages.length}>
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

function CalendarCard({ tenant, calls, wide }: { tenant: Tenant; calls: ToolCall[]; wide: boolean }) {
  const bookings = calls.filter(c => c.name === "book_appointment");
  const check = calls.findLast(c => c.name === "check_availability");
  const date = bookings.at(-1)?.params.date ?? check?.params.preferred_date;
  const calendarId = bookings.at(-1)?.params.calendar_id ?? check?.params.calendar_id ?? tenant.calendars[0].id;
  const calendar = tenant.calendars.find(c => c.id === calendarId) ?? tenant.calendars[0];

  const free = check?.params.preferred_date === date ? (parse<{ slots: { start: string }[] }>(check?.result)?.slots ?? []).map(s => s.start) : [];
  const booked = new Map(bookings.filter(b => b.params.date === date).map(b => [b.params.start_time, b.params.caller_name]));
  const rows = [...new Set([...free, ...booked.keys()])].sort();

  return (
    <Card id="card-calendar" icon="calendar" title="Calendar" tool={bookings.length ? "book_appointment" : "check_availability"} wide={wide} fresh={bookings.length + (check ? 1 : 0)}>
      <p className="cal-head">
        <strong>{calendar.name}</strong>
        <span>{date ? day(date) : calendar.description}</span>
      </p>
      {rows.length === 0 && <p className="empty">No openings that day.</p>}
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

function TransferCard({ tenant, transfer, wide }: { tenant: Tenant; transfer: ToolCall; wide: boolean }) {
  return (
    <Card id="card-transfer" icon="route" title="Transfer" tool="transfer_call" wide={wide} fresh={1}>
      <div className="routes">
        {tenant.transfer_rules.map(rule => {
          const active = transfer.params.destination === rule.destination;
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

function ActivityCard({ calls }: { calls: ToolCall[] }) {
  return (
    <Card id="card-activity" icon="bolt" title="Tool calls" wide>
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
