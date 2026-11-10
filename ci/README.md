# CI/CD internals

All push operations use the ElevenLabs REST API directly — no CLI in CI or deploy.
See the root `README.md` for the why and for pull commands.

## The two-workspace model

`workspaces.json` is the registry: which workspaces exist, which agents ship to each, and a
translation table mapping every workspace-scoped id from the reference workspace to its
counterpart elsewhere.

**The versioned JSON always holds the reference (`standard`) workspace's ids**, exactly as
`agents pull` writes them. Pushing to standard is the identity function — nothing is
rewritten. Pushing anywhere else substitutes each id.

Pull from `standard` only. Pulling from another workspace would overwrite the config with
that workspace's ids and invert the next standard deploy; `ci/lint_ids.py` fails the build if
it sees one.

Identifiers are located by position (`ci/lib/refs.py`), never by shape. A regex sweep for
`agent_[A-Za-z0-9]+` matches `agent_response`, which is a client-event name, and would
corrupt it. Position-scanning is also the only way to reach ids that appear as object *keys*
— `tool_mock_overrides` is keyed by tool id.

| File | Does what |
|---|---|
| `workspaces.json` | The registry. Names env vars, never holds a key value |
| `ci/lib/refs.py` | Where identifiers live and what kind each one is |
| `ci/lib/translate.py` | Translation + `config_overlay` deep-merge |
| `ci/lib/push.py` | REST operations against one workspace |
| `ci/push_workspace.py` | Driver: push tools, agents, tests; run the suite |
| `ci/lint_ids.py` | Blocking lint: every id has a counterpart |
| `ci/assert_node_models.py` | No workflow node may pin its own `llm`/`tts.model_id` |
| `ci/assert_privacy.py` | Post-deploy read-back of the privacy block |
| `ci/test_stability.py` | Per-test pass/fail across recent CI runs |
| `ci/tests/test_translate.py` | Unit tests. Run by CI before anything is pushed |

## Multi-tenant context

The agent is a shared platform. The same config runs for every tenant; tests inject
tenant-specific `dynamic_variables` (business name, transfer rules, scheduling config) to
simulate different business scenarios. A test failure means the platform behavior broke for
that tenant *type* — not that the test scenario is wrong. Never loosen a success condition to
fix a tenant-specific failure without reading the judge's rationale first.

## CI workflow — `ci.yml`

Triggers on `pull_request` to `main`. Concurrency cancels superseded runs per branch.

Three jobs: `discover` reads the workspace list out of `workspaces.json` (adding a workspace
is a data change — no YAML moves), `validate` runs `jq` plus the unit tests once, and
`test-agent` fans out over the workspaces with **`fail-fast: false`** so one workspace's
failure cannot hide another's. Each leg binds only its own credential: GitHub Actions can
build an env var's *value* from an expression but not its *name*, hence the fixed
`WORKSPACE_API_KEY` plus `--api-key-env`.

Steps per workspace leg:

1. **Lint identifiers** — fires when someone added a tool, test or MCP server in the
   reference workspace's UI and this workspace is silently behind. Runs before any write.
2. **Assert node models** — a node-level `llm` or `tts.model_id` would silently beat the
   per-conversation client override.
3. **Push tools** — `PATCH /v1/convai/tools/{id}` with `{tool_config, response_mocks}`.
   Tools are workspace-global: a tool edit on a PR commit reaches that workspace's
   production agents immediately.
4. **Push tests** — `PUT /v1/convai/agent-testing/{id}` (or `POST .../create` for new ones),
   **before** the suite runs, so a PR that changes a success condition is evaluated against
   the condition it just changed.
5. **Resolve or create the agent branch** — branches are agent-scoped; the branch is named
   after the git head ref. **Never create one by hand** — it would exist only in the
   reference workspace.
6. **Push the agent config to the branch** — `PATCH /v1/convai/agents/{id}?branch_id=…`
   (`branch_id` is a query param, not body). The only branch-scoped write.
7. **Run the simulation suite** — `POST /run-tests` with `branch_id`, poll
   `/test-invocations/{id}`. Results are uploaded as a 90-day artifact on success *and*
   failure — re-running a job replaces the log, so the log cannot be the record.

## Reading the suite — one run proves nothing

These are LLM simulations judged by an LLM, so the driver distinguishes three outcomes:

| Verdict | Meaning | Strike? |
|---|---|---|
| `passed` | every success condition met | — |
| `failed` | the judge evaluated and the agent got it wrong | **yes** |
| `nonverdict` | no answer: inconclusive, timeout, API error | no — re-rolled free |

A test fails the build after `STRIKE_LIMIT` (4) real `failed` verdicts. The arithmetic
behind the number is in `ci/lib/push.py`; re-measure with `ci/test_stability.py` before
changing it. If a test never resolves within `MAX_ROUNDS`, the run warns and stays green — a
test that never evaluated is not evidence of a regression.

Triaging a red leg: read the `── evaluation detail ──` block (it names the failing condition
and the judge's reasoning), ask whether the branch touches the behavior under test, and
download the results artifact before re-running. Do **not** fix a flaky test by loosening
its conditions until the artifact shows which condition actually failed — the likely mistake
is deleting the one condition doing the work.

## Tests a workspace cannot run

A workspace may deliberately do without a dependency — here, `hipaa` omits `mcp` and
`webhook`, because a zero-retention workspace cannot hold an MCP server (a product
constraint of force-disabled logging, not a permission). `apply_omissions` strips those
references from the config on push, so any test asserting that behavior would be red there
by construction, forever.

A test declares what it exercises on its own mapping in `workspaces.json`:

```json
{
  "kind": "test",
  "name": "Knowledge — Answers From The Knowledge Base",
  "standard": "test_…",
  "hipaa": "test_…",
  "requires": ["mcp/knowledge_base"]
}
```

The test is dropped from the suite in any workspace whose `omitted` list covers that kind,
logged by name and reason. A `requires` naming no mapping is a hard error — a typo would
otherwise silently re-enable the test everywhere. The rule keys on `omitted`, not on "the
mapping is null": null means *not provisioned yet*, and reading that as a skip would let a
half-bootstrapped workspace run nothing and report green.

## Deploy workflow — `deploy.yml`

Triggers on `push` to `main`. Deploys serialize and never cancel. One job loops over
`deploy_order` — **not a matrix**, because a matrix does not promise an execution order and
the order is the point: the zero-retention workspace deploys first, so a failure there
leaves production untouched. Per workspace: push tools, tests, agent configs to Main, then
`ci/assert_privacy.py` reads the agents back and fails the deploy if the live privacy block
drifted from the registry — a privacy block set once by hand is how a BAA workspace quietly
starts retaining transcripts again.

## REST endpoints used

| Operation | Method | URL |
|---|---|---|
| List branches | GET | `/v1/convai/agents/{id}/branches` |
| Create branch | POST | `/v1/convai/agents/{id}/branches` |
| Push agent config | PATCH | `/v1/convai/agents/{id}?branch_id=<id>` |
| Push tool | PATCH | `/v1/convai/tools/{id}` |
| Create tool | POST | `/v1/convai/tools/create` |
| Update test | PUT | `/v1/convai/agent-testing/{id}` |
| Create test | POST | `/v1/convai/agent-testing/create` |
| Trigger test run | POST | `/v1/convai/agents/{id}/run-tests` |
| Poll test results | GET | `/v1/convai/test-invocations/{id}` |

## Branch deletion is broken — leave it broken

`cleanup.yml`'s `DELETE /v1/convai/agents/{id}/branches/{branch_id}` returns `405` and
deletes nothing — PR branches pile up and need manual cleanup. The tempting "fix",
`DELETE /v1/convai/agents/{id}?branch_id={branch_id}`, mirrors how `PATCH` scopes to a
branch — but it ignores `branch_id` and **deletes the entire agent**. Confirmed in
production, once. Verify the correct branch-scoped call with ElevenLabs support before
touching this, and test against a throwaway agent first.

## Required GitHub configuration

| Name | Kind | Purpose |
|---|---|---|
| `ELEVENLABS_API_KEY` | secret | reference workspace |
| `ELEVENLABS_API_KEY_HIPAA` | secret | zero-retention workspace (named by its `api_key_env`) |
| `DEPLOY_ENABLED` | variable | set `true` to activate `deploy.yml` once real ids are in place |

A missing secret resolves to an empty string rather than an error, so the driver's
`preflight` is what reports it, naming the registry variable to fix.
