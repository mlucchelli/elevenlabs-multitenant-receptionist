#!/usr/bin/env python3
"""Demo backend for the multi-tenant receptionist.

Plays the part a real telephony backend plays on every call:

  1. resolve the tenant and turn its settings into the agent's dynamic variables
  2. get a signed URL so the browser can open the conversation without the API key
  3. execute the client tools the agent calls (messages, transfers, bookings)

Standard library only. It also serves the built web app from demo/web/dist.

    ELEVENLABS_API_KEY=... ELEVENLABS_AGENT_ID=agent_... python3 demo/server.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
API = "https://api.elevenlabs.io/v1/convai"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

TENANTS = {t["id"]: t for t in json.loads((HERE / "tenants.json").read_text())}
REPO = HERE.parent
AGENT = json.loads((REPO / "agents" / "receptionist" / "receptionist.json").read_text())

# What the tools wrote during this run. A real backend would use a database.
LOG = []


def workflow():
    """The agent's workflow graph, read from the versioned config so the UI draws what is deployed."""
    tool_names = {
        t["id"]: json.loads((REPO / t["config"]).read_text())["name"]
        for t in json.loads((REPO / "tools.json").read_text())["tools"]
    }
    prompt = AGENT["conversation_config"]["agent"]["prompt"]

    def condition(cond):
        """-> (gate variable or None, the sentence the LLM judges or None)"""
        if not cond or cond["type"] == "unconditional":
            return None, None
        if cond["type"] == "llm":
            return None, cond["condition"]
        gate = text = None
        for child in cond["expression"].get("children", []):
            if child["type"] == "eq_operator":
                gate = child["left"]["name"]
            elif child["type"] == "llm":
                text = child["prompt"]
        return gate, text

    nodes = []
    for node_id, node in AGENT["workflow"]["nodes"].items():
        own = (node.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("built_in_tools") or {})
        tools = [tool_names[i] for i in node.get("additional_tool_ids") or []] + list(own)
        if node_id == "node_triage":
            tools = [tool_names[i] for i in prompt["tool_ids"]] + tools
        nodes.append({
            "id": node_id, "type": node["type"], "label": node.get("label") or node["type"].capitalize(),
            "x": node["position"]["x"], "y": node["position"]["y"],
            "edge_order": node.get("edge_order") or [], "tools": tools,
        })
    edges = []
    for edge_id, edge in AGENT["workflow"]["edges"].items():
        gate, when = condition(edge.get("forward_condition"))
        _, back = condition(edge.get("backward_condition"))
        edges.append({"id": edge_id, "source": edge["source"], "target": edge["target"], "gate": gate, "when": when, "back": back})
    return {"nodes": nodes, "edges": edges}


def clock(hhmm):
    """'09:00' -> '9:00 AM'."""
    t = datetime.strptime(hhmm, "%H:%M")
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def is_open(tenant, now):
    span = tenant["hours"].get(DAYS[now.weekday()])
    return bool(span) and span[0] <= now.strftime("%H:%M") < span[1]


def dynamic_variables(tenant, now=None):
    """Everything the shared agent knows about this business, for this call.

    The agent never computes dates or decides whether the business is open. The
    backend does both here and passes the answer in, so a test can pin them.
    """
    now = now or datetime.now(ZoneInfo(tenant["timezone"]))
    hours = "; ".join(
        f"{name}: {clock(span[0])} to {clock(span[1])}" if span else f"{name}: Closed"
        for name, span in ((n, tenant["hours"].get(d)) for n, d in zip(DAY_NAMES, DAYS))
    )
    return {
        "business_name": tenant["business_name"],
        "first_message": tenant["first_message"],
        "global_context": tenant["global_context"],
        "business_hours": hours,
        "within_business_hours": "true" if is_open(tenant, now) else "false",
        "current_datetime": f"{now:%A}, {now:%B} {now.day} {now.year}, {clock(now.strftime('%H:%M'))}",
        "phone_timezone": tenant["timezone"],
        "caller_number": "+15550100042",
        "transfer_enabled": "true" if tenant["transfer_rules"] else "false",
        # "label" is for the demo UI; the agent only needs match, destination and announce.
        "transfer_rules": json.dumps([{k: r[k] for k in ("match", "destination", "announce")} for r in tenant["transfer_rules"]])
        if tenant["transfer_rules"] else "",
        "scheduling_enabled": "true" if tenant["calendars"] else "false",
        "scheduling_calendars": json.dumps(tenant["calendars"]),
        # The caller is on a web page, so the agent may put catalog items on screen.
        # A phone backend would send display_enabled = "false".
        "display_enabled": "true",
        "catalog": json.dumps([{k: i[k] for k in ("id", "name", "price", "description")} for i in tenant["catalog"]]),
        "thread_id": f"demo-{uuid.uuid4().hex[:10]}",
    }


def signed_url():
    req = urllib.request.Request(
        f"{API}/conversation/get-signed-url?agent_id={os.environ['ELEVENLABS_AGENT_ID']}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["signed_url"]


def slots_for(calendar_id, date):
    """Made-up availability: fewer slots on Fridays, none at the weekend."""
    day = datetime.strptime(date, "%Y-%m-%d").weekday()
    starts = [] if day >= 5 else ["10:00", "14:00"] if day == 4 else ["10:00", "14:00", "16:30"]
    taken = {
        e["params"]["start_time"] for e in LOG
        if e["tool"] == "book_appointment" and (e["params"]["calendar_id"], e["params"]["date"]) == (calendar_id, date)
    }
    return [s for s in starts if s not in taken]


def run_tool(tenant, name, params):
    """Execute one client tool. The return value is what the agent reads."""
    if name == "check_availability":
        calendar = next((c for c in tenant["calendars"] if c["id"] == params.get("calendar_id")), None)
        if not calendar:
            return {"error": "unknown calendar_id"}
        return {
            "slots": [
                {"start": s, "end": (datetime.strptime(s, "%H:%M") + timedelta(minutes=30)).strftime("%H:%M")}
                for s in slots_for(calendar["id"], params["preferred_date"])
            ],
            "timezone": calendar["timezone"],
        }
    if name == "book_appointment":
        if params.get("start_time") not in slots_for(params.get("calendar_id"), params["date"]):
            return {"status": "rejected", "reason": "slot not available"}
        return {"status": "confirmed", "booking_id": f"bk_{uuid.uuid4().hex[:8]}"}
    if name == "take_message":
        return "Message saved."
    if name == "transfer_call":
        known = {r["destination"] for r in tenant["transfer_rules"]}
        if params.get("destination") not in known:
            return {"error": "destination is not in this business's transfer rules"}
        return "Transfer initiated."
    if name == "show_item":
        if params.get("item_id") not in {i["id"] for i in tenant["catalog"]}:
            return {"error": "item_id is not in this business's catalog"}
        return "Shown on the caller's screen."
    return {"error": f"unknown tool {name}"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE / "web" / "dist"), **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("%s\n" % (fmt % args))

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/api/workflow":
            return self.reply(200, workflow())
        if self.path != "/api/tenants":
            return super().do_GET()
        out = []
        for t in TENANTS.values():
            now = datetime.now(ZoneInfo(t["timezone"]))
            out.append({**t, "open": is_open(t, now), "local_time": clock(now.strftime("%H:%M"))})
        self.reply(200, out)

    def do_POST(self):
        try:
            data = self.body()
            tenant = TENANTS.get(data.get("tenant"))
            if not tenant:
                return self.reply(404, {"error": "unknown tenant"})
            if self.path == "/api/preview":
                return self.reply(200, {"dynamic_variables": dynamic_variables(tenant)})
            if self.path == "/api/session":
                return self.reply(200, {"signed_url": signed_url(), "dynamic_variables": dynamic_variables(tenant)})
            if self.path.startswith("/api/tools/"):
                name = self.path.rsplit("/", 1)[1]
                result = run_tool(tenant, name, data.get("params") or {})
                rejected = isinstance(result, dict) and ("error" in result or result.get("status") == "rejected")
                if not rejected:
                    LOG.append({"tenant": tenant["id"], "tool": name, "params": data.get("params") or {}})
                return self.reply(200, {"result": result, "ok": not rejected})
            self.reply(404, {"error": "not found"})
        except urllib.error.HTTPError as e:
            self.reply(502, {"error": f"ElevenLabs answered {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"})
        except (KeyError, ValueError) as e:
            self.reply(400, {"error": f"bad request: {e}"})


if __name__ == "__main__":
    missing = [v for v in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID") if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"set {' and '.join(missing)} first")
    port = int(os.environ.get("PORT", "8787"))
    print(f"http://localhost:{port}")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), Handler).serve_forever()
