#!/usr/bin/env python3
"""Read the live agents back and assert their privacy block matches the registry.

Set-once-by-hand is exactly how this regresses six months from now: someone
toggles a switch in the UI to debug a call, and a BAA workspace quietly starts
retaining transcripts. Checking on every deploy is the only version of this that
stays true.

The field names are not invented — they were read off a live
GET /v1/convai/agents/{id}: record_voice, retention_days, delete_audio,
delete_transcript_and_pii, zero_retention_mode, apply_to_existing_conversations,
conversation_history_redaction, nested_history_redaction, user_memory.

Usage: python3 ci/assert_privacy.py --workspace hipaa
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.push import call
from lib.translate import load_registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--api-key-env")
    args = ap.parse_args()

    registry = load_registry()
    slug = args.workspace
    ws = registry["workspaces"][slug]
    key = os.environ.get(args.api_key_env or ws["api_key_env"], "")
    if not key:
        raise SystemExit(f"::error::{slug}: no API key in the environment")

    expected = ((ws.get("config_overlay") or {})
                .get("platform_settings", {})
                .get("privacy", {}))
    if not expected:
        print(f"{slug}: no privacy overlay declared — nothing to assert.")
        return 0

    failures, checked = [], 0
    for agent in registry["agents"]:
        if slug not in agent.get("deploy_to", []):
            continue
        ids = agent.get(slug) or {}
        if not ids.get("id"):
            print(f"  {slug}: {agent['name']} not provisioned — skipping")
            continue

        checked += 1
        live = call("GET", f"agents/{ids['id']}", key,
                    query={"branch_id": ids.get("branch_id")})
        actual = (live.get("platform_settings") or {}).get("privacy") or {}

        for field, want in expected.items():
            got = actual.get(field)
            if got != want:
                failures.append((agent["name"], field, want, got))
            else:
                print(f"  {slug}/{agent['name']}: {field} = {got}")

    if failures:
        for name, field, want, got in failures:
            print(f"::error::{slug}/{name}: privacy.{field} is {got!r}, expected {want!r} — "
                  f"the live agent has drifted from workspaces.json")
        return 1

    if not checked:
        # "Nothing failed" and "nothing was checked" must never print the same
        # line. An empty report that reads as a healthy one is how a privacy
        # regression survives a green build.
        print(f"::warning::{slug}: declares a privacy overlay but no agent was checked — "
              f"none are provisioned yet. This is not a pass.")
        return 0

    print(f"{slug}: privacy posture matches the registry ({checked} agent(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
