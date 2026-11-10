# elevenlabs-multitenant-receptionist

Version-controlled configuration for a **multi-tenant ElevenLabs Conversational AI
receptionist**: one shared agent serves many businesses, each getting its own behavior
through dynamic variables injected at call time (`business_name`, `transfer_rules`,
`scheduling_enabled`, `scheduling_calendars`, `global_context`, …).

All changes to prompts, tools, tests and agent JSON go through this repo — **never directly
in the ElevenLabs UI**. The UI is read-only for this project.

Every ElevenLabs id in this repo is a **placeholder**. The pipeline is real and fully
exercised by the unit tests and lints; pointing it at live workspaces means provisioning
your own resources and pulling the real ids back (see "Running it yourself" in README.md).

## Two workspaces

This repo deploys to more than one ElevenLabs workspace. `workspaces.json` is the registry —
see [ci/README.md](./ci/README.md) for the full model. Three rules:

1. **The versioned JSON holds the `standard` workspace's ids.** Pushing to standard rewrites
   nothing; pushing elsewhere substitutes each id from the mapping table. Never hand-edit an
   id in `agents/` to point at another workspace.
2. **Pull from `standard` only.** Pulling from another workspace overwrites the config with
   that workspace's ids and inverts the next standard deploy. CI fails the build if it sees
   one.
3. **`workspaces.json` may name an environment variable, never hold a key.**

When you add a tool, test or MCP server, add it to `workspaces.json` too — `ci/lint_ids.py`
fails until every id has a counterpart in every workspace the agent ships to. Null means
*not provisioned yet* (a lint error); a kind listed in a workspace's `omitted` means
*deliberately absent* (stripped on push; tests that declare it in `requires` are skipped, with the test id and the reason in the log).

## The agent

One config, `agents/receptionist/receptionist.json`: a shared base prompt plus a 5-node
workflow — `start → Reception & Triage`, which routes to `Call Transfer` and
`Schedule Appointment` nodes and to an `end` node. Each `override_agent` node carries
numbered SOP-style **procedures** in its `additional_prompt` and scopes its own tools via
`additional_tool_ids` (the base prompt holds `take_message` and `show_item`). `end_call` is declared only on the triage node, so the transfer and scheduling stages cannot
hang up; their only way out is the `backward_condition` on the edge that brought the call in,
which returns it to triage. Loops are always one edge with a forward and a backward
condition, never two edges. Forward conditions out of triage are
`expression` conditions: a deterministic equality on the tenant's gate variable
(`transfer_enabled` / `scheduling_enabled` == `"true"`) AND-ed with a model-signaled intent
condition — so a tenant with a capability disabled can never enter that stage, no matter
what the model thinks the caller wants. Committed model defaults are `claude-haiku-5-5`
(LLM) and `eleven_v4_turbo` (TTS; v4 has exactly two voice controls, `stability` and
`similarity_boost` — no speed, style or SSML).

Things in it that are load-bearing and easy to break:

- **The `# Knowledge Search` prompt section is strippable by exact heading.**
  `apply_omissions` removes `\n# Knowledge Search\n…` up to the next `\n# ` heading when a
  workspace omits `mcp` — a workspace without the search tool must never be told to search
  (an agent instructed to use a missing tool substitutes another one and misbehaves). Do not
  rename that heading, fold it into another section, or mention `search_knowledge` outside
  it. `ci/tests/test_translate.py` guards this.
- **No node or config may out-pin the per-conversation overrides.** The backend sends
  `tts.model_id` and `agent.prompt.llm` via `conversation_config_override`; a node-level
  value applies after the override and silently beats it. `ci/assert_node_models.py`
  enforces this on every PR. The committed `model_id`/`llm` values are defaults only.
- **Capabilities are hard gates.** `transfer_enabled` / `scheduling_enabled` /
  `display_enabled` arrive as strings per tenant; the prompt treats a disabled capability as nonexistent. Keep new
  capabilities on the same pattern: a gate variable, a rules/config variable, and a decline
  path that explains itself in the same reply.
- **`current_datetime` is pinned by the caller of the session, never read from a wall
  clock.** Tests depend on this; an agent reading real time passes or fails on what weekday
  CI runs.

Multi-tenant discipline: tests inject tenant-specific `dynamic_variables` to simulate
different business types. A test failure means the shared platform broke for that tenant
*type* — never "fix" it by special-casing one tenant's scenario, and never loosen a success
condition without reading the judge's rationale in the results artifact first.

## BEFORE OPENING A PR — mandatory steps

1. **Validate JSON locally:**
   ```bash
   find agents/ tools/ tests/ -name "*.json" | xargs -I{} jq . {} > /dev/null && echo "JSON valid"
   ```

2. **Run the unit tests** (CI runs them before pushing anything):
   ```bash
   python3 ci/tests/test_translate.py
   python3 ci/lint_ids.py --workspace hipaa
   python3 ci/assert_node_models.py
   ```

3. **Optionally run the pipeline locally** against a real workspace before opening the PR:
   ```bash
   ELEVENLABS_API_KEY=... python3 ci/push_workspace.py \
     --workspace standard --mode ci --branch-name "$(git rev-parse --abbrev-ref HEAD)"
   ```
   Run it from the repo root — it reads `workspaces.json`, `tools.json` and `tests/*.json`
   by relative path. It is the same driver CI runs: it pushes tools and tests, resolves or
   creates the ElevenLabs branch named after your git branch, pushes the agent config to it,
   then runs the attached simulation suite. In `--mode ci` it writes
   `test-results-<workspace>.json` (gitignored).

4. **Open the PR.** CI runs on every commit: validate JSON, then per workspace — lint ids,
   assert node models, push tools and tests, push the agent config to a per-PR ElevenLabs
   branch, run the simulation suite, post a check. Only the **agent config** is
   branch-scoped: tools and tests are workspace-global, so a tool or test edit on a PR
   commit reaches that workspace's production agents immediately. **Never create the
   ElevenLabs branch by hand** — a manual one would only ever exist in the standard
   workspace.

## After merge

`deploy.yml` runs when the repository variable `ELEVENLABS_ENABLED` is `true` (off by default,
since the repo ships placeholder ids). For each workspace in `deploy_order` — **hipaa first,
then standard** — it pushes tools, tests, then the agent config to that workspace's Main
branch, then reads the agent back and asserts the live privacy block
(`ci/assert_privacy.py`). It stops on the first failure, so a hipaa failure leaves
production untouched.

PR branches on ElevenLabs are **not** actually deleted, despite what `cleanup.yml` attempts:
the `DELETE /v1/convai/agents/{id}/branches/{id}` endpoint returns 405 and removes nothing.
Read the comments in `cleanup.yml` before touching it — the obvious "fix" deletes the whole
agent.

## Reading a red CI check

These are LLM simulations judged by an LLM — noisy by construction. A test fails the build
only after 4 real `failed` verdicts; `nonverdict` outcomes (judge timeout, API error,
inconclusive) are re-rolled free. Triage order:

1. Read the `── evaluation detail ──` block in the log — it names the failing condition and
   the judge's reasoning.
2. Ask whether the branch touches the behavior under test. A scheduling PR turning a
   transfer test red is suspicious; a transfer PR doing it is the suite working.
3. Download the `test-results-<workspace>-<run_id>` artifact **before re-running** — a
   re-run replaces the log, so the artifact is the only durable evidence.

Never fix a flaky test by loosening its success conditions until the artifact shows which
condition is actually failing — the likely mistake is deleting the one condition doing the
work.

## Adding or changing things

- **New test:** add a JSON file in `tests/` (omit `id` — CI creates it; pull the id back),
  add its mapping to `workspaces.json`, and attach it via
  `platform_settings.testing.attached_tests` in the agent config. If it exercises a feature
  some workspace omits, declare it with `requires: ["<kind>/<name>"]` on its mapping.
- **Existing test:** keep the `id` field — CI PUTs to update it.
- **Per-test tool mocks:** `tool_mock_overrides` (keyed **by tool id** — one of the reasons
  ids are translated by position, not shape) holds mocks that apply only inside that test,
  ahead of the tool's shared `response_mocks`.
- **New tool:** JSON in `tools/` with `response_mocks` (mocks are returned during simulation
  only, never production), an entry in `tools.json`, and a mapping in `workspaces.json`.

## Known CLI limitations (push only)

| Command | Issue | What we use instead |
|---|---|---|
| `agents push` | wraps LLM AST nodes in `{value:{...}}` → 422 | `PATCH /v1/convai/agents/{id}?branch_id=` |
| `tools push` | drops `response_mocks` | Python REST loop over `tools.json` |
| `agents test` | no `--branch` flag — always tests Main | `POST /run-tests` with `branch_id` |

**Pull commands work fine** (`agents pull`, `tools pull`, `tests pull`) and are how the repo
syncs from the reference workspace.

## The demo

`demo/` calls the agent from a browser. It implements the same five client tools the agent
config declares, so a tool added to `tools/` needs three more things to work there: a branch
in `run_tool` in `demo/server.py`, a `useConversationClientTool` line in `demo/web/src/App.tsx`,
and a case in `ToolEvent` in `demo/web/src/Call.tsx`. `server.py` sends `display_enabled = "true"`
because the caller is on a web page; a phone backend sends `"false"`. After changing the front
end run `npm run build` in `demo/web` (it type-checks first).

## Repo structure

```
workspaces.json                  # workspace registry + id translation table (placeholder ids)
agents.json                      # ElevenLabs CLI index for `agents pull` — not read by CI
agents/receptionist/
  receptionist.json              # the multi-tenant agent: prompt, variables, overrides, attached tests
tools/
  tools.json                     # index: tool id → config file
  *.json                         # tool definitions; include response_mocks for simulation
tests/
  *.json                         # simulation tests — one fictional tenant each; id field for updates
ci/
  README.md                      # pipeline internals + REST API reference
  push_workspace.py              # the driver CI and deploy both run, one workspace at a time
  lib/                           # refs.py (where ids live) / translate.py / push.py (REST)
demo/
  server.py                      # demo backend: dynamic variables, signed URL, runs the client tools
  tenants.json                   # the four fictional businesses the demo can call
  web/                           # React + @elevenlabs/react front end (npm run build → web/dist)
.github/workflows/
  ci.yml                         # PR: validate → per workspace: push to a PR branch → test → check
  deploy.yml                     # merge: per workspace in deploy_order: push to Main → assert privacy
  cleanup.yml                    # tries to delete the PR branch; ElevenLabs returns 405 (read its comments)
```
