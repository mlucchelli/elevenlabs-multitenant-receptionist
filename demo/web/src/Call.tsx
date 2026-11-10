import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "./Icon";
import { Orb, type OrbState } from "./Orb";
import { day, parse, phone, time } from "./format";
import type { FeedItem, Tenant, ToolCall } from "./types";

type Props = {
  tenant: Tenant;
  textMode: boolean;
  onMode: (text: boolean) => void;
  busy: boolean;
  live: boolean;
  orb: OrbState;
  level: () => number;
  sub: string;
  feed: FeedItem[];
  calls: ToolCall[];
  onStart: () => void;
  onHangUp: () => void;
  onSend: (text: string) => void;
};

export function CallPanel(p: Props) {
  const canType = p.textMode && p.live;
  return (
    <section className="call card" data-live={p.busy} id="call">
      <div className="call-head">
        <Orb state={p.orb} level={p.level} />
        <div className="call-who">
          <h2>{p.tenant.business_name}</h2>
          <p>{p.sub}</p>
        </div>
        <div className="call-controls">
          <div className="seg" role="group" aria-label="How to talk to the agent">
            <button type="button" className={p.textMode ? "" : "on"} disabled={p.busy} onClick={() => p.onMode(false)}>
              <Icon name="mic" size={16} /> Voice
            </button>
            <button type="button" className={p.textMode ? "on" : ""} disabled={p.busy} onClick={() => p.onMode(true)}>
              <Icon name="chat" size={16} /> Text
            </button>
          </div>
          {p.live ? (
            <button type="button" className="cta hangup" onClick={p.onHangUp}>
              <Icon name="phone" /> Hang up
            </button>
          ) : (
            <button type="button" className="cta" disabled={p.busy} onClick={p.onStart}>
              <Icon name={p.textMode ? "chat" : "phone"} />
              {p.busy ? "Connecting…" : p.textMode ? "Start chat" : `Call ${p.tenant.business_name}`}
            </button>
          )}
        </div>
      </div>

      <Feed feed={p.feed} calls={p.calls} tenant={p.tenant} />

      <div className="suggest">
        {p.tenant.try_saying.map(line => (
          <button key={line} type="button" disabled={!canType} onClick={() => p.onSend(line)} title={canType ? undefined : "Say it out loud, or use Text mode to tap it"}>
            {line}
          </button>
        ))}
      </div>
      {canType && <Composer onSend={p.onSend} />}
    </section>
  );
}

function Composer({ onSend }: { onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    onSend(text.trim());
    setText("");
  };
  return (
    <form className="composer" onSubmit={submit}>
      <input value={text} onChange={e => setText(e.target.value)} placeholder="Type what the caller says" autoComplete="off" enterKeyHint="send" />
      <button type="submit" aria-label="Send">
        <Icon name="send" />
      </button>
    </form>
  );
}

function Feed({ feed, calls, tenant }: { feed: FeedItem[]; calls: ToolCall[]; tenant: Tenant }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [feed, calls]);

  return (
    <div className="feed" ref={box} aria-live="polite">
      {feed.length === 0 && (
        <p className="feed-empty">
          The conversation shows up here, together with every tool the agent calls.
        </p>
      )}
      {feed.map(item => {
        if (item.kind !== "tool") return <p key={item.id} className={`msg ${item.kind}`}>{item.text}</p>;
        const call = calls.find(c => c.id === item.callId);
        return call ? <ToolEvent key={item.id} call={call} tenant={tenant} /> : null;
      })}
    </div>
  );
}

const DOING: Record<ToolCall["name"], string> = {
  take_message: "Saving the message",
  check_availability: "Checking the calendar",
  book_appointment: "Booking the slot",
  transfer_call: "Starting the transfer",
  show_item: "Putting it on screen",
};

/** A tool call drawn inside the conversation, at the moment it happened. */
function ToolEvent({ call, tenant }: { call: ToolCall; tenant: Tenant }) {
  const tag = (
    <footer>
      <Icon name="bolt" size={12} />
      <code>{call.name}</code>
      {call.ms != null && <span>{call.ms} ms</span>}
    </footer>
  );

  if (call.result === undefined) {
    return (
      <div className="ev pending">
        <span className="spinner" /> {DOING[call.name]}…{tag}
      </div>
    );
  }
  if (!call.ok) {
    return (
      <div className="ev bad">
        <strong>The backend refused this call</strong>
        <p>{call.result}</p>
        {tag}
      </div>
    );
  }

  const p = call.params;
  const calendar = tenant.calendars.find(c => c.id === p.calendar_id)?.name ?? p.calendar_id;

  switch (call.name) {
    case "take_message":
      return (
        <div className="ev">
          <strong><Icon name="inbox" size={15} /> Message saved to the inbox</strong>
          <blockquote>{p.message}</blockquote>
          <p className="ev-meta">from {p.caller_name}{p.contact_number ? ` · call back on ${phone(p.contact_number)}` : ""}</p>
          {tag}
        </div>
      );
    case "check_availability": {
      const slots = parse<{ slots: { start: string }[] }>(call.result)?.slots ?? [];
      return (
        <div className="ev">
          <strong><Icon name="calendar" size={15} /> {calendar} · {day(p.preferred_date)}</strong>
          <div className="slots">
            {slots.length ? slots.map(s => <span key={s.start}>{time(s.start)}</span>) : <em>No openings that day</em>}
          </div>
          {tag}
        </div>
      );
    }
    case "book_appointment":
      return (
        <div className="ev pop">
          <strong><span className="tick"><Icon name="check" size={13} /></span> {day(p.date)} · {time(p.start_time)}</strong>
          <p>{calendar} with {p.caller_name}</p>
          <p className="ev-meta">Booking {parse<{ booking_id: string }>(call.result)?.booking_id}</p>
          {tag}
        </div>
      );
    case "transfer_call": {
      const rule = tenant.transfer_rules.find(r => r.destination === p.destination);
      return (
        <div className="ev">
          <strong><Icon name="route" size={15} /> Transferring to {rule?.label ?? phone(p.destination)}</strong>
          <p className="ringing"><span /><span /><span /> {phone(p.destination)}</p>
          <p className="ev-meta">{p.reason}</p>
          {tag}
        </div>
      );
    }
    case "show_item": {
      const item = tenant.catalog.find(i => i.id === p.item_id);
      return (
        <div className="ev pop item">
          <span className="item-art">{item?.emoji}</span>
          <div>
            <strong>{item?.name ?? p.item_id}</strong>
            <p>{item?.description}</p>
          </div>
          <b>{item?.price}</b>
          {tag}
        </div>
      );
    }
  }
}
