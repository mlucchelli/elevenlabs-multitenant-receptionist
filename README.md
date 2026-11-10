# ElevenLabs Multi-Tenant Receptionist — Agents as Code

**One ElevenLabs Conversational AI agent, serving many businesses, with its entire lifecycle
— prompts, tools, tests, deploys — managed from a git repository instead of the ElevenLabs UI.**

This repo distills the architecture I designed and operated for a commercial AI receptionist
platform (thousands of real phone calls across many tenant businesses), rebuilt here with
generic content and placeholder ids so the pattern itself is the artifact.

## Highlights

| Problem | Solution in this repo |
|---|---|
| Hundreds of businesses, one agent | Tenant behavior injected at call time via `dynamic_variables`; capabilities are hard gates per tenant |
| Prompt changes are risky | Every PR gets its own **ElevenLabs agent branch**; an LLM-judged simulation suite runs against it before merge |
| LLM-judged tests are flaky | Verdicts split into `passed` / `failed` / `nonverdict`; free re-rolls; a **measured** 4-strike threshold instead of a guessed one |
| HIPAA customers need zero retention | Same config ships to a second workspace with id translation, a privacy overlay asserted post-deploy, and incompatible features stripped — with their tests skipped loudly, never silently |
| The official CLI breaks pushes | All writes go through the REST API; the CLI is pull-only (its push bugs are documented below) |
| Call flow must be explicit, not emergent | A 5-node **workflow** (triage → transfer / scheduling → end) whose edges gate on tenant variables AND model-signaled conditions; each node carries SOP-style procedures and only the tools that stage needs |

## Architecture

```mermaid
flowchart LR
    caller(("Caller"))
    subgraph backend ["Session backend (not in this repo)"]
        resolve["Resolve called number → tenant"]
        mint["Mint session:<br/>dynamic_variables (tenant behavior)<br/>+ conversation_config_override<br/>(llm, tts model, stability)"]
        handlers["Client-tool handlers:<br/>transfer_call · take_message<br/>check_availability · book_appointment"]
    end
    subgraph el ["ElevenLabs Conversational AI"]
        agent["Shared multi-tenant agent<br/>(this repo: prompt, workflow, tools, tests)"]
        kb[("Knowledge base<br/>via MCP server")]
        wh["Post-call webhook<br/>(transcript)"]
    end
    git[("This repo<br/>agents/ tools/ tests/")]
    ci["GitHub Actions<br/>per-PR agent branch + simulation suite<br/>merge → Main of each workspace"]

    caller -->|"PSTN / SIP"| resolve --> mint -->|"startSession"| agent
    agent <-->|"client tool calls"| handlers
    agent --- kb
    agent -->|"call ended"| wh --> backend
    git --> ci -->|"REST API"| el
```

The backend and telephony layer are intentionally out of scope: every integration point they
need (which variables to send, which client tools to implement, which overrides are enabled)
is defined by the JSON in this repo.

## How multi-tenancy works

The agent config is tenant-agnostic. At session start, the telephony backend resolves the
called number to a business and opens the conversation with that tenant's variables:

```jsonc
{
  "dynamic_variables": {
    "business_name": "Harbor Dental",
    "first_message": "Hi, you've reached Harbor Dental. How can I help you today?",
    "business_hours": "Monday: 9:00 AM to 5:00 PM; ...",
    "within_business_hours": "true",
    "current_datetime": "Tuesday, June 23 2026, 2:00 PM",   // pinned — the agent never reads a wall clock
    "transfer_enabled": "true",
    "transfer_rules": "[{\"match\": \"...\", \"destination\": \"...\", \"announce\": \"...\"}]",
    "scheduling_enabled": "false",
    "scheduling_calendars": "[]",
    "global_context": "Family dental clinic. Dr. Chen sees patients Mon-Fri."
  }
}
```

The prompt (`agents/receptionist/receptionist.json`) treats each capability as a **hard
gate**: a disabled capability does not exist on that call. On top of the shared base prompt
sits a **workflow**: a triage node routes to dedicated transfer and scheduling nodes, each
carrying numbered, SOP-style procedures in its `additional_prompt` and only the tools that
stage needs (`additional_tool_ids`). Edges combine a deterministic check on the tenant's
gate variable (`transfer_enabled == "true"`) with a model-signaled intent condition, so a
tenant with scheduling disabled can never even enter the booking stage. The simulation
tests exercise the same mechanism — each test file injects a different fictional tenant (a dental clinic, a yoga
studio, a law firm, a bakery) and asserts the platform behaves correctly *for that tenant
type*. A test failure means the shared platform broke for a class of tenant, not that one
business's config is wrong.

The committed defaults are `claude-haiku-5-5` for the LLM and `eleven_v4_turbo` for TTS
(Eleven v4 exposes exactly two voice controls — `stability` and `similarity_boost` — no
speed, style or SSML). Per-conversation **client overrides** (`tts.model_id`,
`agent.prompt.llm`, `tts.stability`, `tts.similarity_boost`) are enabled in
`platform_settings.overrides`, so the backend can run model experiments per call without a
redeploy — e.g. an A/B arm on a new LLM. A rule this
enables: no workflow node may pin its own `llm` or `tts.model_id`, because a node-level value
silently beats the override (`ci/assert_node_models.py` enforces it on every PR).

## Pipeline

```mermaid
flowchart TD
    pr[/"Commit on a PR"/]

    subgraph ci ["CI — every PR commit, fan-out per workspace"]
        lint["Lint ids: every workspace-scoped id\nhas a counterpart (ci/lint_ids.py)"]
        models["Assert no node pins its own model\n(ci/assert_node_models.py)"]
        tools["Push tools (REST, keeps response_mocks)"]
        tests["Push test definitions (before the suite runs)"]
        branch["Resolve or create an ElevenLabs agent branch\nnamed after the git branch"]
        agent["PATCH agent config onto that branch"]
        suite["Run attached simulation tests vs the branch\nstrike policy + nonverdict re-rolls"]
    end

    subgraph deploy ["Deploy — on merge, workspaces walked in deploy_order"]
        hipaa["hipaa first: translate ids, apply privacy\noverlay, strip omitted features, push to Main"]
        std["then standard: identity push to Main"]
        assert["Read agents back, assert live privacy block\n(ci/assert_privacy.py)"]
    end

    pr --> lint --> models --> tools --> tests --> branch --> agent --> suite
    suite -->|green + review| hipaa --> std --> assert
```

HIPAA deploys **first** on purpose: a failure there leaves production untouched, while a
standard-first failure would leave the HIPAA workspace stale after production had already
moved.

## The workspace registry

`workspaces.json` is the single source of truth: which workspaces exist, which agents ship to
each (`deploy_to`), the deploy order, and a translation table mapping every workspace-scoped
id (agents, tools, tests, MCP servers, webhooks) from the reference workspace to its
counterpart elsewhere. Three invariants:

1. The versioned JSON always holds the **reference workspace's** ids. Pushing there is the
   identity function — the repo cannot corrupt the workspace carrying real traffic.
2. Ids are located **by position, never by shape**. A regex sweep for `agent_[a-z0-9]+`
   would match `agent_response` — a client-event name — and corrupt it; and only a
   position walk can reach ids that appear as object *keys* (`tool_mock_overrides`).
3. The registry may name an environment variable; it may **never hold a key value**
   (linted on every run).

## Testing philosophy

Simulation tests are LLM conversations judged by an LLM. One run proves nothing, so the
pipeline encodes what a red check is allowed to mean:

| Verdict | Meaning | Counts as a strike? |
|---|---|---|
| `passed` | every success condition met | — |
| `failed` | the judge evaluated and the agent got it wrong | yes |
| `nonverdict` | the judge produced no answer (timeout, API error, inconclusive) | no — re-rolled free |

A test fails the build after **4 real `failed` verdicts** — a measured threshold, not a
taste: at the suite's observed ~10% per-test round-0 failure rate, 2 strikes produce a false
red roughly one run in five, 4 strikes one in five hundred, while every genuine defect
observed failed on *every* attempt and still strikes out. `ci/test_stability.py` re-measures
the noise floor across recent runs before anyone tunes the threshold.

Tests that exercise a feature a workspace deliberately omits (e.g. knowledge-base search via
MCP in the zero-retention workspace) declare it with `requires` in the registry and are
skipped there — logged by name and reason, because a silent skip and a permanently red test
are both ways a check stops meaning anything.

## Why REST instead of the official CLI (push direction)

| CLI command | Issue | Used instead |
|---|---|---|
| `agents push` | serialization bug wraps LLM AST nodes → 422 | `PATCH /v1/convai/agents/{id}?branch_id=` |
| `tools push` | silently drops `response_mocks` | REST loop over `tools.json` |
| `agents test` | no `--branch` flag — always tests Main | `POST /run-tests` with `branch_id` |

Pull commands are fine and are how the repo syncs back from the reference workspace.

## Repo layout

```
workspaces.json        # workspace registry + id translation table (ids here are placeholders)
agents/receptionist/   # the multi-tenant agent config: prompt, variables, overrides, attached tests
tools/                 # tool definitions, each with response_mocks for simulation
tests/                 # simulation tests — one fictional tenant each
ci/
  push_workspace.py    # the driver CI and deploy both run, one workspace at a time
  lib/                 # refs (where ids live) / translate (id substitution + overlay) / push (REST)
  lint_ids.py          # every id has a counterpart in every workspace it ships to
  assert_node_models.py# no node may out-pin the per-conversation model overrides
  assert_privacy.py    # post-deploy read-back of the live privacy block
  test_stability.py    # measures suite flakiness across CI runs
  tests/               # unit tests for the translator (run first in CI)
.github/workflows/     # ci.yml (PR) / deploy.yml (merge) / cleanup.yml (see its comments)
```

## Running it yourself

All ids in this repo are placeholders. To point it at real workspaces:

1. Create an agent, the four tools, and the tests in your ElevenLabs workspace (the configs
   here are complete payloads — `POST /v1/convai/agents/create`, `/tools/create`,
   `/agent-testing/create`).
2. Write the resulting ids into `workspaces.json`, `tools.json` and the test files
   (`agents pull` / `tools pull` / `tests pull` from the official CLI do this for you).
3. Set the `ELEVENLABS_API_KEY` (and optionally `ELEVENLABS_API_KEY_HIPAA`) secrets, and the
   `DEPLOY_ENABLED=true` repository variable.
4. Open a PR — CI pushes everything to a per-PR agent branch and runs the suite:

```bash
# or locally, same driver CI runs:
ELEVENLABS_API_KEY=... python3 ci/push_workspace.py \
  --workspace standard --mode ci --branch-name "$(git rev-parse --abbrev-ref HEAD)"
```

## Operational lessons baked into the code

- **Branch deletion is left broken on purpose.** The obvious "fix" for the 405 on
  `DELETE .../branches/{id}` — scoping the agent delete with `?branch_id=` the way `PATCH`
  scopes — ignores the query param and deletes the whole agent. `cleanup.yml`'s comments
  tell the story.
- **Tests are pushed before the suite runs**, so a PR that changes a success condition is
  evaluated against the condition it just changed, not the previous revision.
- **A workspace that can't hold a feature never hears of it.** Stripping the MCP server
  alone made the agent substitute `end_call` for the search tool and hang up on callers;
  the omission therefore strips the whole prompt section that mentions searching.
- **`current_datetime` is pinned, never read from a wall clock** — otherwise a test passes
  or fails depending on what weekday CI happens to run.

`ci/README.md` documents the pipeline internals, the REST endpoints used, and the required
GitHub configuration.

---

MIT — see [LICENSE](LICENSE).
