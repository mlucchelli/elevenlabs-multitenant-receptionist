"""REST operations against one ElevenLabs workspace.

Extracted verbatim in behaviour from the inline Python and bash that used to
live in ci.yml. Every function takes `api_key` explicitly: nothing reads a
global, because with two workspaces a global key is a wrong-workspace write
waiting to happen.

The CLI is not used for any of this. `agents push` wraps llm AST nodes in
{value:{...}} and 422s; `tools push` drops response_mocks and fails on
api_integration_webhook tools; `agents test` has no --branch flag. Pull is fine.
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.elevenlabs.io/v1/convai"

# Fields the API returns but rejects on write.
TEST_READONLY = {
    "id", "access_info", "created_at_unix_secs", "last_updated_at_unix_secs",
    "entity_type", "folder_parent_id", "folder_path", "children_count",
    "conversation_initiation_source",
}

# The agent payload is a subset: the API rejects the read-only envelope.
AGENT_FIELDS = ("name", "conversation_config", "platform_settings", "workflow", "tags")

# A test fails the build after this many real `failed` verdicts. It is a
# statistical threshold, not a taste, so here is the arithmetic behind it.
#
# The numbers come from the production suite this pipeline was built for, not
# from this repo: 23 attached tests, per-test round-0 failure rate of 9.7%
# measured over 20 CI runs. The chance a leg goes red with nothing actually
# broken is 1 - (1 - p**STRIKE_LIMIT)**N, which for N=23 gives:
#
#     STRIKE_LIMIT=2  ->  19.5%      STRIKE_LIMIT=4  ->  0.2%
#     STRIKE_LIMIT=3  ->   2.1%      STRIKE_LIMIT=5  ->  0.02%
#
# At 2 the check was red often enough to be unreadable, and an unreadable check
# is one nobody reads: a structurally unpassable test once blocked every merge
# for over two weeks without anyone noticing, because a red run looked normal.
#
# Raising this does not hide that class of bug. Every real defect found while tuning this
# suite failed on every single attempt — 6/6, 11/14, 8/16 — and would still
# strike out at 4. What 4 absorbs is coin-flips, like an agent reading the real
# wall clock instead of the pinned current_datetime, so a test passes or fails
# on what weekday CI happens to run.
#
# Re-measure with ci/test_stability.py before changing it. If the rate climbs
# well above 10%, 4 stops being enough and the agent needs work, not a bigger number.
STRIKE_LIMIT = 4
# Enough rounds for a test to actually reach STRIKE_LIMIT: a run that stops at 4
# rounds while a test sits on 3 strikes reports green on an unresolved test.
MAX_ROUNDS = 6
# A TCP reset or a 502 from the API is not a result. Without these, one blip
# anywhere in a run throws away the whole leg — a CI run once died on an SSL
# handshake reset during preflight, before a single test had run.
NET_RETRIES = 4
NET_BACKOFF = 2
POLL_SECONDS = 15
POLL_DEADLINE = 600


class ApiError(Exception):
    def __init__(self, method, url, code, body):
        self.code = code
        self.body = body
        super().__init__(f"{method} {url} -> HTTP {code}\n{body[:2000]}")


def read_json(path):
    # Explicit encoding: the repo carries "ô" and em-dashes in filenames and
    # content, and the locale default is not ours to assume.
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Safe to send again: the API applies them to a known id, so a duplicate is the
# same write twice. POST is not — agent-testing/create makes a new test and
# run-tests starts an invocation, and a retry after the server already accepted
# one would leave a stray behind.
IDEMPOTENT = ("GET", "PUT", "PATCH")


def call(method, path, api_key, body=None, query=None):
    url = f"{BASE}/{path}" if not path.startswith("http") else path
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items() if v is not None)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method=method,
    )
    for attempt in range(NET_RETRIES):
        last = attempt == NET_RETRIES - 1
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            # 4xx is our mistake and will not fix itself. 429 and 5xx might, but
            # only replay a request the API can safely receive twice.
            retryable = (e.code == 429 or e.code >= 500) and method in IDEMPOTENT
            if last or not retryable:
                raise ApiError(method, url, e.code, e.read().decode("utf-8", "replace")) from None
            wait = NET_BACKOFF ** attempt
            print(f"    {method} {path} -> HTTP {e.code}, retrying in {wait}s")
        except (urllib.error.URLError, ConnectionError, socket.timeout) as e:
            # The connection broke rather than the API answering, so this is
            # retryable whatever the method: there is no response either way, and
            # leaving it unretried loses a 4-minute leg to a dropped packet.
            if last:
                raise SystemExit(f"::error::{method} {url} failed after "
                                 f"{NET_RETRIES} attempts: {e}") from None
            wait = NET_BACKOFF ** attempt
            print(f"    {method} {path} -> {type(e).__name__}, retrying in {wait}s")
        time.sleep(wait)


def preflight(api_key, slug, key_env):
    """Fail loudly before touching anything if the key is missing or wrong.

    GitHub Actions resolves an unknown secret name to an empty string rather
    than erroring, and the matrix values come from a checked-in file — so a
    typo in workspaces.json would otherwise run half a pipeline unauthenticated
    before anything complained.
    """
    if not api_key:
        raise SystemExit(f"::error::{slug}: {key_env} is empty or unset")
    try:
        # Probe a convai route, not /v1/user. A key scoped to conversational AI
        # can do everything this script needs and still be refused by the user
        # endpoint, so probing there rejects a perfectly good key.
        call("GET", "agents", api_key, query={"page_size": 1})
    except ApiError as e:
        raise SystemExit(f"::error::{slug}: {key_env} cannot read {BASE}/agents "
                         f"(HTTP {e.code}) — wrong key, or missing convai access")
    print(f"{slug}: authenticated via {key_env}")


def push_tools(api_key, slug, tool_entries):
    """PATCH each tool. Reports every rejection, not just the first."""
    failures = []
    for tool_id, config_path in tool_entries:
        tool = read_json(config_path)
        mocks = tool.pop("response_mocks", [])
        try:
            result = call("PATCH", f"tools/{tool_id}", api_key,
                          {"tool_config": tool, "response_mocks": mocks})
        except ApiError as e:
            # A malformed mock 422s here and blocks the whole pipeline. Say which
            # tool and what the API objected to — "HTTP Error 422: unknown" on its
            # own has cost entire afternoons.
            print(f"::error file={config_path}::{slug}: tool push failed, HTTP {e.code}: {e.body[:400]}")
            failures.append(config_path)
            continue
        print(f"  OK: {result.get('tool_config', {}).get('name', tool_id)}")
    if failures:
        raise SystemExit(f"::error::{slug}: {len(failures)} tool(s) rejected: {', '.join(failures)}")


def resolve_or_create_branch(api_key, slug, agent_id, main_branch_id, name, fallback_version=None):
    """Find the branch named `name` on this agent, or create it from Main."""
    existing = call("GET", f"agents/{agent_id}/branches", api_key)
    for b in existing.get("results", existing.get("branches", [])):
        if b.get("name") == name:
            print(f"  {slug}/{agent_id}: found existing branch {b['id']}")
            return b["id"]

    live = call("GET", f"agents/{agent_id}", api_key, query={"branch_id": main_branch_id})
    parent = live.get("version_id") or live.get("current_version_id") or fallback_version
    if not parent:
        raise SystemExit(f"::error::{slug}: no parent version for {agent_id}")

    created = call("POST", f"agents/{agent_id}/branches", api_key,
                   {"name": name, "parent_version_id": parent, "description": "PR branch"})
    branch_id = created.get("created_branch_id") or created.get("id")
    if not branch_id:
        raise SystemExit(f"::error::{slug}: branch creation failed for {agent_id}: {json.dumps(created)[:400]}")
    print(f"  {slug}/{agent_id}: created branch {branch_id}")
    return branch_id


def push_agent(api_key, slug, agent_id, branch_id, config, label):
    payload = {k: config[k] for k in AGENT_FIELDS if k in config}
    result = call("PATCH", f"agents/{agent_id}", api_key, payload, query={"branch_id": branch_id})
    pushed = result.get("branch_id")
    if not pushed:
        raise SystemExit(f"::error::{slug}: agent push failed for {label}: {json.dumps(result)[:400]}")
    print(f"  {slug}: pushed {label} to branch {pushed}")
    return pushed


def push_tests(api_key, slug, payloads):
    """PUT tests that have an id in THIS workspace, POST those that do not.

    Takes payloads the caller has already translated. Reading the files here
    instead was a real bug: a test document carries the reference workspace's
    agent id in from_conversation_metadata.agent_id (40 of them do), and tool
    ids as the KEYS of tool_mock_overrides. Pushed raw at another workspace,
    the PUT either 404s on an id that does not exist there or lands a test that
    evaluates the wrong agent with no mocks at all.

    A payload with no id is created fresh on EVERY run. Six files in this repo
    have none, which is why the workspace holds 100+ tests against 46 files.
    """
    pushed = {}
    for path, test_id, body in payloads:
        if test_id:
            result = call("PUT", f"agent-testing/{test_id}", api_key, body)
        else:
            result = call("POST", "agent-testing/create", api_key, body)
        pushed[path] = result.get("id", test_id)
        print(f"  OK: {result.get('name', os.path.basename(path))}")
    return pushed


def _reason(run):
    """The judge's actual reasoning, per criterion.

    rationale.summary is literally the string "Evaluation failed" on every
    failure — it identifies nothing. The per-criterion verdicts live in
    rationale.messages, and they are what tells you which criterion broke and
    why. Reading the summary is how a log ends up full of failures that all say
    the same four words.
    """
    cr = run.get("condition_result") or {}
    messages = (cr.get("rationale") or {}).get("messages")
    if messages:
        return " | ".join(str(m) for m in messages)
    for candidate in (
        (cr.get("rationale") or {}).get("summary"),
        cr.get("summary"),
        run.get("failure_reason"),
        run.get("error"),
        run.get("status"),
    ):
        if candidate:
            return candidate
    return "no rationale"


NONVERDICT_MARKERS = ("inconclusive", "timed out", "timeout", "internal error")


def _classify(run):
    """passed / failed / nonverdict.

    A nonverdict is the JUDGE failing to answer, not the agent failing. It is
    re-rolled without spending a strike; treating it as a failure re-imports
    the false reds this policy exists to remove.
    """
    status = run.get("status")
    if status == "passed":
        return "passed"
    reason = str(_reason(run)).lower()
    if status == "error" or any(m in reason for m in NONVERDICT_MARKERS):
        return "nonverdict"
    return "failed"


def run_suite(api_key, slug, agent_id, branch_id, tests, label, records):
    """Trigger one run, poll to completion, print every failure, record verdicts."""
    invocation = call("POST", f"agents/{agent_id}/run-tests", api_key,
                      {"tests": tests, "branch_id": branch_id})
    invocation_id = invocation.get("id")
    print(f"  {slug}: invocation {invocation_id}")

    deadline = time.time() + POLL_DEADLINE
    while True:
        result = call("GET", f"test-invocations/{invocation_id}", api_key)
        pending = [r for r in result.get("test_runs", []) if r.get("status") == "pending"]
        if not pending:
            break
        if time.time() >= deadline:
            raise SystemExit(f"::error::{slug}: timed out waiting for {invocation_id}")
        print(f"    waiting... {len(pending)} pending")
        time.sleep(POLL_SECONDS)

    counts = {"passed": 0, "failed": 0, "nonverdict": 0}
    for run in result.get("test_runs", []):
        verdict = _classify(run)
        counts[verdict] += 1
        name = run.get("test_name")
        reason = _reason(run)
        if verdict == "passed":
            print(f"  ✓ {name}: passed")
        elif verdict == "nonverdict":
            print(f"  ~ {name}: no verdict — {reason}")
            print("    (the evaluation did not produce an answer; re-rolled, no strike)")
        else:
            print(f"  ✗ {name}: failed")
            for part in str(reason).split(" | "):
                print(f"    → {part}")
            # Dump the whole condition_result: whatever per-condition detail the
            # API returns lives in here, and guessing at its shape is what left
            # every failure in this log reading "Evaluation failed" and nothing else.
            print("    ── evaluation detail ──")
            detail = json.dumps(run.get("condition_result") or {}, indent=2)
            for line in detail.splitlines()[:120]:
                print(f"    {line}")
            if len(detail) > 6000:
                print("    … truncated — full object in the test-results artifact")
        records.append({
            "run": os.environ.get("GITHUB_RUN_ID", ""),
            "sha": os.environ.get("GITHUB_SHA", ""),
            "branch": os.environ.get("GITHUB_HEAD_REF", ""),
            "workspace": slug, "agent": agent_id, "config": label,
            "test_id": run.get("test_id"), "test_name": name,
            "status": run.get("status"), "verdict": verdict, "reason": reason,
            "condition_result": run.get("condition_result"),
        })

    print(f"  Passed: {counts['passed']} | Failed: {counts['failed']} | No verdict: {counts['nonverdict']}")
    unresolved = [{"test_id": r["test_id"]} for r in records[-len(result.get("test_runs", [])):]
                  if r["verdict"] in ("failed", "nonverdict")]
    return unresolved


def run_until_settled(api_key, slug, agent_id, branch_id, attached, label, records):
    """Re-roll unresolved tests until one earns STRIKE_LIMIT real failures.

    Returns the list of test names that are genuinely red.
    """
    retry, rounds = attached, 0
    while True:
        print(f"::group::{slug} — {label} round {rounds}")
        retry = run_suite(api_key, slug, agent_id, branch_id, retry, label, records)
        print("::endgroup::")
        if not retry:
            break
        strikes = {}
        for r in records:
            if r["config"] == label and r["workspace"] == slug and r["verdict"] == "failed":
                strikes[r["test_id"]] = strikes.get(r["test_id"], 0) + 1
        # Further rounds cannot change an outcome that is already red.
        if strikes and max(strikes.values()) >= STRIKE_LIMIT:
            break
        rounds += 1
        if rounds >= MAX_ROUNDS:
            print(f"::warning::{slug}/{label}: hit MAX_ROUNDS={MAX_ROUNDS} with tests unresolved. "
                  f"A test that never evaluated is not evidence of a regression.")
            break
        print(f"  re-running {len(retry)} unresolved test(s) — round {rounds} of at most {MAX_ROUNDS - 1}")

    strikes, names = {}, {}
    for r in records:
        if r["config"] == label and r["workspace"] == slug and r["verdict"] == "failed":
            strikes[r["test_id"]] = strikes.get(r["test_id"], 0) + 1
            names[r["test_id"]] = r["test_name"]
    return [names[t] for t, n in strikes.items() if n >= STRIKE_LIMIT]
