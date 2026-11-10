#!/usr/bin/env python3
"""Fail the build when a workflow node pins its own LLM or TTS model.

The session backend sends tts.model_id and agent.prompt.llm as per-conversation client
overrides (conversation_config_override). ElevenLabs applies a node's own
conversation_config on top of that override once the workflow reaches the
node, so a node-level value silently wins and the override is ignored. The
only way this stays true is checking every node, on every agent this repo
manages, on every PR.

A missing key and an explicit null are both fine — that is what "inherit the
top-level default, let the override through" looks like. Any other value,
including "", is a violation.

Usage: python3 ci/assert_node_models.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.translate import load_registry


def check_agent(config_path):
    """Return a list of (node_id, field) violations for one agent config."""
    with open(config_path, encoding="utf-8") as f:
        doc = json.load(f)

    violations = []
    nodes = (doc.get("workflow") or {}).get("nodes") or {}
    for node_id, node in nodes.items():
        cc = node.get("conversation_config") or {}
        llm = ((cc.get("agent") or {}).get("prompt") or {}).get("llm")
        if llm is not None:
            violations.append((node_id, "agent.prompt.llm"))
        model_id = (cc.get("tts") or {}).get("model_id")
        if model_id is not None:
            violations.append((node_id, "tts.model_id"))
    return violations


def main():
    registry = load_registry()
    failures = []
    checked = 0

    for agent in registry["agents"]:
        config_path = agent["config"]
        if not os.path.exists(config_path):
            continue
        checked += 1
        for node_id, field in check_agent(config_path):
            failures.append((config_path, node_id, field))

    if failures:
        for config_path, node_id, field in failures:
            print(f"::error::{config_path} node {node_id}: {field} must be null "
                  f"(per-conversation client overrides would be ignored)")
        return 1

    print(f"assert_node_models: {checked} agent config(s) checked, "
          "no node pins its own llm or tts.model_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
