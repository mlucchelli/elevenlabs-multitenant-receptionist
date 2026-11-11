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
  const cards: Record<CardId, () => ReactNode> = {
    screen: () => <ScreenCard key="screen" tenant={tenant} shown={done.filter(c => c.name === "show_item").map(c => c.params.item_id)} />,
    inbox: () => <InboxCard key="inbox" messages={done.filter(c => c.name === "take_message")} />,
    calendar: () => <CalendarCard key="calendar" tenant={tenant} calls={done} />,
    transfer: () => <TransferCard key="transfer" tenant={tenant} transfer={done.findLast(c => c.name === "transfer_call")!} />,
  };
  return (
    <section className="work" aria-label="What the call did">
      {workflow && <WorkflowMap workflow={workflow} node={node} steps={steps} vars={vars} />}
      <div className="cards" data-count={used.length}>
        {used.map(id => cards[id]())}
        {used.length === 0 && (
          <div className="waiting">
            <span><Icon name="bolt" size={18} /></span>
            <p>Nothing has happened yet. A message, a booking, a transfer or a product appears here when the agent does it.</p>
          </div>
        )}
      </div>
      <aside className="side">
        <ActivityCard calls={calls} />
        <VarsCard vars={vars} sent={varsSent} />
      </aside>
    </section>
  );
}

function Card(p: { icon: IconName; title: string; tool?: string; fresh?: number; children: ReactNode }) {
  return (
    <article className="card tile">
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

function ScreenCard({ tenant, shown }: { tenant: Tenant; shown: string[] }) {
  const spot = shown.at(-1);
  // Newest first; an item the agent showed twice appears once.
  const items = [...new Set(shown.toReversed())].map(id => tenant.catalog.find(i => i.id === id)).filter(i => i !== undefined);
  return (
    <Card icon="screen" title="On screen" tool="show_item" fresh={shown.length}>
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

function InboxCard({ messages }: { messages: ToolCall[] }) {
  return (
    <Card icon="inbox" title="Inbox" tool="take_message" fresh={messages.length}>
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
  const bookings = calls.filter(c => c.name === "book_appointment");
  const check = calls.findLast(c => c.name === "check_availability");
  const date = bookings.at(-1)?.params.date ?? check?.params.preferred_date;
  const calendarId = bookings.at(-1)?.params.calendar_id ?? check?.params.calendar_id ?? tenant.calendars[0].id;
  const calendar = tenant.calendars.find(c => c.id === calendarId) ?? tenant.calendars[0];

  const free = check?.params.preferred_date === date ? (parse<{ slots: { start: string }[] }>(check?.result)?.slots ?? []).map(s => s.start) : [];
  const booked = new Map(bookings.filter(b => b.params.date === date).map(b => [b.params.start_time, b.params.caller_name]));
  const rows = [...new Set([...free, ...booked.keys()])].sort();

  return (
    <Card icon="calendar" title="Calendar" tool={bookings.length ? "book_appointment" : "check_availability"} fresh={bookings.length + (check ? 1 : 0)}>
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

function TransferCard({ tenant, transfer }: { tenant: Tenant; transfer: ToolCall }) {
  return (
    <Card icon="route" title="Transfer" tool="transfer_call" fresh={1}>
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
    <Card icon="bolt" title="Tool calls">
      {calls.length === 0 && <p className="empty">Each call the agent makes is listed here with its arguments and what the backend answered.</p>}
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
    <Card icon="braces" title="Dynamic variables" tool={sent ? "sent" : "preview"}>
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
    </Card>
  );
}
