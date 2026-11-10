#!/usr/bin/env python3
"""Push tools, agent configs and tests to one ElevenLabs workspace.

Replaces five inline steps that used to live in ci.yml as a mix of bash and
heredoc Python. Behaviour is unchanged for the reference workspace; the point
of the extraction is that the same code can now be pointed at a second one.

  ci mode      push everything to a per-PR branch, then run the simulation suite
  deploy mode  push everything to the workspace's Main branch, run nothing

Usage:
  python3 ci/push_workspace.py --workspace standard --mode ci --branch-name my-branch
  python3 ci/push_workspace.py --workspace standard --mode deploy
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.push import (preflight, push_agent, push_tests, push_tools,
                      resolve_or_create_branch, run_until_settled)
from lib.push import read_json
from lib.translate import (apply_omissions, apply_overlay, load_registry,
                           require_workspace, skipped_tests, translate)


def tool_entries(registry, slug):
    """(tool id in THIS workspace, config path) for the tools it carries.

    The id must be translated too. tools.json is an index of the reference
    workspace, so pushing its ids at another workspace would PATCH tools that do
    not exist there — a 404 at best, and at worst a write against whatever else
    happens to hold that id.

    The reference workspace keeps pushing all of tools.json; that is existing
    behaviour. Every other workspace gets only the tools its agents reference:
    unmapped ones may carry third-party OAuth connection ids or
    workspace secrets, which cannot be copied into a second workspace.
    """
    index = read_json("tools.json")["tools"]
    if slug == registry["reference"]:
        return [(t["id"], t["config"]) for t in index]

    table = {m["standard"]: m.get(slug) for m in registry["mappings"] if m["kind"] == "tool"}
    out, missing = [], []
    for t in index:
        if t["id"] not in table:
            continue
        if table[t["id"]] is None:
            missing.append(f"{t['config']} ({t['id']})")
        else:
            out.append((table[t["id"]], t["config"]))
    if missing:
        raise SystemExit(f"::error::{slug}: {len(missing)} tool(s) have no counterpart: "
                         + ", ".join(missing))
    return out


def test_payloads(registry, slug):
    """(path, test id IN THIS WORKSPACE, translated body) for each test to push.

    The body has to be translated like an agent config: 40 of the 46 test files
    carry the reference workspace's agent id in from_conversation_metadata, and
    the UI Command test keys tool_mock_overrides by tool id.
    """
    from lib.push import TEST_READONLY
    table = {m["standard"]: m.get(slug) for m in registry["mappings"] if m["kind"] == "test"}
    is_ref = slug == registry["reference"]

    out = []
    for path in sorted(glob.glob("tests/*.json")):
        data = read_json(path)
        standard_id = data.get("id")
        if not is_ref and standard_id not in table:
            continue
        body = {k: v for k, v in data.items() if k not in TEST_READONLY}
        body, _ = translate(body, registry, slug)
        out.append((path, standard_id if is_ref else table[standard_id], body))
    return out


def check_agent_ids_agree(registry, slug):
    """An agent id lives in two places; nothing else notices when they disagree.

    agents[].<slug>.id drives the push, mappings[kind=agent] drives translation.
    Fill only the mapping and the driver prints "not provisioned here — skipping"
    and exits 0: a deploy that pushes nothing and reports green.
    """
    by_name = {m["name"]: m for m in registry["mappings"] if m["kind"] == "agent"}
    for agent in registry["agents"]:
        if slug not in agent.get("deploy_to", []):
            continue
        if agent["name"] not in by_name:
            # No mapping means the id never needs translating: nothing else
            # references it. That is correct for an agent that ships only to the
            # reference workspace, and wrong for one that ships anywhere else.
            elsewhere = [w for w in agent.get("deploy_to", []) if w != registry["reference"]]
            if elsewhere:
                raise SystemExit(
                    f"::error::{agent['name']} ships to {', '.join(elsewhere)} but has no "
                    f"agent mapping in workspaces.json, so its id cannot be translated.")
            continue
        theirs = (agent.get(slug) or {}).get("id")
        ours = by_name[agent["name"]].get(slug)
        if theirs != ours:
            raise SystemExit(
                f"::error::workspaces.json disagrees with itself for {agent['name']} in "
                f"{slug}: agents[].{slug}.id is {theirs!r} but the agent mapping says "
                f"{ours!r}. Both must be filled, or both left null.")


def prepare(config_path, registry, slug):
    """Load an agent config and make it ready for this workspace."""
    config = apply_omissions(read_json(config_path),
                             registry["workspaces"][slug].get("omitted"))
    translated, stats = translate(config, registry, slug)
    overlay = registry["workspaces"][slug].get("config_overlay") or {}
    if stats["substitutions"]:
        print(f"  {slug}: {stats['substitutions']} id(s) translated {stats['by_kind']}")
    return apply_overlay(translated, overlay)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--mode", choices=("ci", "deploy"), required=True)
    ap.add_argument("--branch-name", help="git head ref; required in ci mode")
    ap.add_argument("--api-key-env",
                    help="read the key from this variable instead of the one named in "
                         "workspaces.json. CI uses it so each leg sees only its own "
                         "credential: GitHub Actions cannot build an env var NAME from "
                         "an expression, only its value.")
    args = ap.parse_args()

    registry = load_registry()
    slug = require_workspace(registry, args.workspace)
    check_agent_ids_agree(registry, slug)
    ws = registry["workspaces"][slug]
    key_env = ws["api_key_env"]
    api_key = os.environ.get(args.api_key_env or key_env, "")

    # Errors name the registry variable, not the CI plumbing: the reader has to
    # know which secret to go fix.
    preflight(api_key, slug, key_env)

    # Translate everything before writing anything. A config that cannot be
    # translated must not leave half-pushed tools behind in the workspace, and
    # MissingCounterpart reports every gap at once so provisioning is one trip.
    planned = []
    for agent in registry["agents"]:
        if slug not in agent.get("deploy_to", []):
            print(f"  {slug}: {agent['name']} does not ship here — skipping")
            continue
        ids = agent.get(slug) or {}
        if not ids.get("id"):
            print(f"  {slug}: {agent['name']} not provisioned here — skipping")
            continue
        planned.append((agent, ids, prepare(agent["config"], registry, slug)))

    tools = tool_entries(registry, slug)
    tests = test_payloads(registry, slug)

    print(f"::group::{slug}: tools")
    push_tools(api_key, slug, tools)
    print("::endgroup::")

    # Before the suite runs, not after. A PR that changes a success condition
    # must be evaluated against the condition it just changed; pushing tests
    # afterwards validates the previous revision and goes green.
    print(f"::group::{slug}: tests")
    push_tests(api_key, slug, tests)
    print("::endgroup::")

    records, red = [], []
    for agent, ids, config in planned:
        if args.mode == "ci":
            branch_id = resolve_or_create_branch(
                api_key, slug, ids["id"], ids["branch_id"], args.branch_name,
                fallback_version=ids.get("version_id"))
        else:
            branch_id = ids["branch_id"]

        push_agent(api_key, slug, ids["id"], branch_id, config, agent["name"])

        if args.mode == "ci":
            attached = (config.get("platform_settings", {})
                              .get("testing", {})
                              .get("attached_tests", []))
            # A test whose dependency this workspace deliberately does without
            # cannot pass here, so it is not evidence about the change under
            # review. Dropped from the suite by name and reason, never silently.
            skip = skipped_tests(registry, slug)
            for test in attached:
                test_id = test.get("test_id") if isinstance(test, dict) else test
                if test_id in skip:
                    print(f"  {slug}: skipping test {test_id} — "
                          f"requires {skip[test_id]}, omitted in this workspace")
            attached = [t for t in attached
                        if (t.get("test_id") if isinstance(t, dict) else t) not in skip]
            if not attached:
                print(f"  {slug}: {agent['name']} has no attached tests — skipping suite")
                continue
            red += [(agent["name"], t) for t in run_until_settled(
                api_key, slug, ids["id"], branch_id, attached, agent["name"], records)]

    if args.mode == "ci":
        out = f"test-results-{slug}.json"
        with open(out, "w") as f:
            json.dump(records, f, indent=2)
        print(f"Wrote {out} ({len(records)} records).")
        write_summary(slug, records)

    if red:
        for label, name in red:
            print(f'::error::{slug}/{label}: "{name}" failed evaluation twice — '
                  f"treating as a real failure.")
        return 1
    print(f"{slug}: green.")
    return 0


def write_summary(slug, records):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    by_test = {}
    for r in records:
        by_test.setdefault(r["test_name"], []).append(r["verdict"])
    with open(path, "a") as f:
        f.write(f"\n### Simulation tests — {slug}\n\n| Test | Rounds | Verdicts |\n|---|---|---|\n")
        for name, verdicts in sorted(by_test.items()):
            f.write(f"| {name} | {len(verdicts)} | {', '.join(verdicts)} |\n")


if __name__ == "__main__":
    sys.exit(main())
