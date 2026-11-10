#!/usr/bin/env python3
"""Fail the build when a workspace-scoped id has no counterpart in a workspace.

This lint replaces the obvious-but-wrong alternative, "no raw ids in agents/".
That version fires on `agents pull`, which is a normal and correct operation —
a lint that punishes correct behaviour gets disabled. This one fires on the
event we actually care about: someone added a tool, test or MCP server in the
reference workspace's UI and the other workspace is now silently behind.

Usage:  python3 ci/lint_ids.py [--workspace hipaa]
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.refs import IDENTITY_FIELDS, KEY_FIELDS, VALUE_FIELDS, looks_like_id
from lib.translate import build_map, load_registry, require_workspace

# Never pushed by ci.yml or deploy.yml: reachable only through
# agents.json .branches.<id>.config, which nothing reads. It carries a stale
# tts model and a voice override that exists nowhere else. Linting it would
# force us to maintain config that never ships.
EXCLUDE = re.compile(r"\.agtbrch_[A-Za-z0-9]+\.json$")

KEY_SHAPE = re.compile(r"sk_[0-9a-f]{40,}")


def scan(path, found):
    """Collect (kind, id, json path) for every identifier in one file."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)

    def walk(node, where, kind_hint=None, key_kind=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if key_kind and looks_like_id(k):
                    found.append((key_kind, k, f"{where}.<key {k}>"))
                if k in IDENTITY_FIELDS:
                    continue
                if k in KEY_FIELDS:
                    walk(v, f"{where}.{k}", key_kind=KEY_FIELDS[k])
                elif k in VALUE_FIELDS:
                    walk(v, f"{where}.{k}", kind_hint=VALUE_FIELDS[k])
                else:
                    walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]", kind_hint, key_kind)
        elif isinstance(node, str):
            if kind_hint:
                found.append((kind_hint, node, where))
            elif looks_like_id(node):
                # Safety net for a position we did not enumerate.
                found.append(("unknown", node, where))

    walk(doc, os.path.basename(path))


def in_scope_files(registry, target):
    """Every file that ships to a second workspace, and only those.

    Scope is the transitive closure of the agents in workspaces.json, not the
    contents of the directories. Out-of-scope tools are where the hard
    dependencies tend to live — third-party OAuth connections, workspace
    secrets — and the tests that no in-scope agent attaches are dead
    weight. Linting either would demand counterparts for identifiers nobody
    intends to provision, and a lint that reports work you will never do is a
    lint people learn to ignore.
    """
    agents = [a["config"] for a in registry["agents"] if target in a.get("deploy_to", [])]

    wanted_tools = {m["standard"] for m in registry["mappings"] if m["kind"] == "tool"}
    tools = [t["config"] for t in json.load(open("tools.json"))["tools"]
             if t["id"] in wanted_tools]

    wanted_tests = {m["standard"] for m in registry["mappings"] if m["kind"] == "test"}
    tests = []
    for path in sorted(glob.glob("tests/*.json")):
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("id") in wanted_tests:
                tests.append(path)

    return [p for p in agents + tools + tests if not EXCLUDE.search(p)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="hipaa")
    args = ap.parse_args()

    registry = load_registry()
    target = require_workspace(registry, args.workspace)
    errors = []

    # 1. workspaces.json is committed to git. It may name an environment
    #    variable; it may never hold a value.
    with open("workspaces.json", encoding="utf-8") as f:
        raw = f.read()
    if KEY_SHAPE.search(raw):
        errors.append("workspaces.json contains something shaped like an API key — "
                      "use api_key_env with the variable name, never the value")
    for slug, ws in registry["workspaces"].items():
        if "api_key" in ws:
            errors.append(f"workspaces.json: workspaces.{slug}.api_key must be api_key_env")

    if target == registry["reference"]:
        print(f"{target} is the reference workspace — nothing to map.")
        return 0 if not errors else fail(errors)

    # A dependency this workspace deliberately does without — listed in
    # workspaces.<slug>.omitted — is not a gap waiting to be filled. Reporting it forever
    # is how a red check stops meaning anything.
    omitted = set(registry["workspaces"][target].get("omitted") or [])
    table = build_map(registry, target)
    reverse = {m[target] for m in registry["mappings"] if m.get(target)}

    files = in_scope_files(registry, target)

    found = []
    for p in files:
        scan(p, found)

    unmapped, unknown, wrong_side = [], [], []
    for kind, ident, where in found:
        if ident in reverse:
            wrong_side.append((ident, where))
        elif ident in table:
            if table[ident][0] is None and table[ident][1] not in omitted:
                unmapped.append((kind, ident, where))
        elif kind == "unknown" or looks_like_id(ident):
            unknown.append((kind, ident, where))

    def dedupe(rows, n):
        seen, out = set(), []
        for r in rows:
            if r[n] not in seen:
                seen.add(r[n])
                out.append(r)
        return out

    for kind, ident, where in dedupe(unmapped, 1):
        errors.append(f"{target}: {kind} {ident} has no counterpart  ({where})")
    for kind, ident, where in dedupe(unknown, 1):
        errors.append(f"{target}: {ident} is not in workspaces.json at all  ({where}) — "
                      f"someone added it in the {registry['reference']} UI")
    for ident, where in dedupe(wrong_side, 0):
        errors.append(f"{ident} belongs to {target}, not to {registry['reference']}  ({where}) — "
                      f"pull from {registry['reference']} only")

    if errors:
        return fail(errors)
    if omitted:
        print(f"::warning::{target} deliberately omits: {', '.join(sorted(omitted))}. "
              f"Those references ship unresolved by design.")
    print(f"{target}: {len(found)} identifier references, all mapped.")
    return 0


def fail(errors):
    for e in errors:
        print(f"::error::{e}")
    print(f"\n{len(errors)} problem(s). See ci/README.md.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
