#!/usr/bin/env python3
"""Unit tests for the workspace translator.

Run: python3 ci/tests/test_translate.py   (from the repo root)
"""

import copy
import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib.refs import looks_like_id
from lib.translate import (KeyCollision, MissingCounterpart, UntranslatedIdentifier,
                           apply_omissions, apply_overlay, load_registry, skipped_tests, translate)
from lib import push as push_lib


HIPAA_PREFIX = "hipaa~"


def provisioned(registry):
    """A copy of the registry as it will look once provisioned: every gap filled.

    The fake ids are the real ones behind a fixed prefix. An earlier version
    derived them with a substring replace, which silently mapped some ids to
    themselves and made a genuinely broken translation look correct — the
    fixture has to guarantee every target id differs from its source.
    """
    r = copy.deepcopy(registry)
    for m in r["mappings"]:
        m["hipaa"] = HIPAA_PREFIX + m["standard"]
    assert all(m["hipaa"] != m["standard"] for m in r["mappings"])
    return r


def unprovisioned(registry):
    """A copy with every counterpart cleared.

    Tests about missing counterparts must not read the live registry: it
    fills in as the workspace gets provisioned, so a test that assumed
    "nothing is mapped yet" starts passing vacuously and then fails outright.
    """
    r = copy.deepcopy(registry)
    for m in r["mappings"]:
        m["hipaa"] = None
    return r


class TestIdentity(unittest.TestCase):
    """Pushing to the reference workspace must change nothing at all.

    This is the property the whole design rests on: the workspace carrying paid
    traffic has zero blast radius from this project.
    """

    def test_every_in_scope_file_is_unchanged(self):
        r = load_registry()
        paths = [a["config"] for a in r["agents"]] + sorted(glob.glob("tests/*.json"))
        self.assertGreater(len(paths), 5, "expected the real repo files")
        for p in paths:
            with self.subTest(file=p):
                with open(p) as f:
                    original = json.load(f)
                out, stats = translate(original, r, "standard")
                self.assertEqual(json.dumps(out, sort_keys=True),
                                 json.dumps(original, sort_keys=True))
                self.assertEqual(stats["substitutions"], 0)

    def test_input_is_never_mutated(self):
        r = provisioned(load_registry())
        with open(r["agents"][0]["config"]) as f:
            doc = json.load(f)
        before = json.dumps(doc, sort_keys=True)
        translate(doc, r, "hipaa")
        self.assertEqual(json.dumps(doc, sort_keys=True), before)


class TestKeyPosition(unittest.TestCase):

    @staticmethod
    def _find_by_id(test_id):
        for path in glob.glob("tests/*.json"):
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("id") == test_id:
                    return path
        return None

    """tool_mock_overrides is keyed BY tool id.

    A translator that only rewrites string values passes every other test and
    fails this one. It exists to catch exactly that regression: the mocks would
    silently keep pointing at the reference workspace and the test would run
    with none, passing or failing for the wrong reason.
    """

    def test_object_keys_are_translated(self):
        r = provisioned(load_registry())
        tool = next(m for m in r["mappings"] if m["kind"] == "tool")
        doc = {"tool_mock_overrides": {tool["standard"]: {"mock": "value"}}}
        out, stats = translate(doc, r, "hipaa")
        self.assertIn(tool["hipaa"], out["tool_mock_overrides"])
        self.assertNotIn(tool["standard"], out["tool_mock_overrides"])
        self.assertEqual(out["tool_mock_overrides"][tool["hipaa"]], {"mock": "value"})
        self.assertEqual(stats["substitutions"], 1)

    def test_the_real_mock_override_test_file(self):
        r = provisioned(load_registry())
        # Located by test id, not by filename: the file name is already stale
        # relative to the registry, and skipping on a rename turns the one test
        # that exercises a real key-position document into a silent pass.
        p = self._find_by_id("test_1au0oorcvubyl3kabgd20qnlhgz8")
        self.assertIsNotNone(p, "the knowledge-test key-position fixture is gone — "
                                "find its replacement, do not delete this test")
        from lib.push import TEST_READONLY
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        # Same shape the driver pushes: the read-only envelope, including the
        # test's own id, is stripped before translation.
        body = {k: v for k, v in raw.items() if k not in TEST_READONLY}
        out, _ = translate(body, r, "hipaa")
        self.assertTrue(out.get("tool_mock_overrides"), "fixture lost its mocks")
        for key in out.get("tool_mock_overrides", {}):
            self.assertTrue(key.startswith(HIPAA_PREFIX), f"{key} was not translated")


class TestMissingCounterpart(unittest.TestCase):
    def test_reports_every_gap_not_just_the_first(self):
        r = unprovisioned(load_registry())
        with open(r["agents"][0]["config"]) as f:
            doc = json.load(f)
        with self.assertRaises(MissingCounterpart) as ctx:
            translate(doc, r, "hipaa")
        self.assertGreater(len(ctx.exception.gaps), 5)
        kinds = {k for k, _, _ in ctx.exception.gaps}
        self.assertIn("tool", kinds)
        self.assertIn("test", kinds)
        self.assertIn("mcp", kinds)

    def test_message_says_where_to_add_the_ids(self):
        r = unprovisioned(load_registry())
        with self.assertRaises(MissingCounterpart) as ctx:
            translate({"additional_tool_ids": [r["mappings"][0]["standard"]]}, r, "hipaa")
        self.assertIn("workspaces.json", str(ctx.exception))


class TestOverlay(unittest.TestCase):
    def test_policy_changes_but_behaviour_does_not(self):
        """An overlay may carry policy. It must never be able to carry behaviour."""
        r = provisioned(load_registry())
        with open(r["agents"][0]["config"]) as f:
            doc = json.load(f)
        translated, _ = translate(doc, r, "hipaa")
        overlay = r["workspaces"]["hipaa"]["config_overlay"]
        out = apply_overlay(translated, overlay)

        self.assertTrue(out["platform_settings"]["privacy"]["zero_retention_mode"])
        self.assertFalse(out["platform_settings"]["privacy"]["record_voice"])
        self.assertEqual(json.dumps(out["conversation_config"], sort_keys=True),
                         json.dumps(translated["conversation_config"], sort_keys=True))
        self.assertEqual(json.dumps(out["workflow"], sort_keys=True),
                         json.dumps(translated["workflow"], sort_keys=True))

    def test_untouched_privacy_fields_survive_the_merge(self):
        doc = {"platform_settings": {"privacy": {"record_voice": True, "retention_days": -1,
                                                 "conversation_history_redaction": {"enabled": False}}}}
        out = apply_overlay(doc, {"platform_settings": {"privacy": {"record_voice": False}}})
        self.assertFalse(out["platform_settings"]["privacy"]["record_voice"])
        self.assertEqual(out["platform_settings"]["privacy"]["retention_days"], -1)
        self.assertIn("conversation_history_redaction", out["platform_settings"]["privacy"])

    def test_lists_replace_rather_than_append(self):
        doc = {"platform_settings": {"privacy": {"entities": [1, 2, 3]}}}
        out = apply_overlay(doc, {"platform_settings": {"privacy": {"entities": [9]}}})
        self.assertEqual(out["platform_settings"]["privacy"]["entities"], [9])


class TestIdShape(unittest.TestCase):
    def test_agent_response_is_not_an_id(self):
        """The false positive that motivated position-aware scanning.

        `agent_response` is a client-event name in
        conversation_config.conversation.client_events. A regex sweep for
        agent_[A-Za-z0-9]+ matches it and would have rewritten it.
        """
        self.assertFalse(looks_like_id("agent_response"))
        self.assertFalse(looks_like_id("agent_tool_response"))
        self.assertTrue(looks_like_id("agent_b02mlsj27d8tlz0zb5lqdmgmwfey"))
        self.assertTrue(looks_like_id("tool_01jxf7s8v7efcvx95acnqycz56"))

    def test_voice_ids_are_left_alone(self):
        """Voices are identity across workspaces.

        The value is a MAPPED id on purpose. With a real voice id the assertion
        held whether or not the identity guard existed, because an unmapped
        string is returned unchanged either way — the test could never fail.
        A mapped id makes the guard the only reason it survives.
        """
        r = provisioned(load_registry())
        mapped = r["mappings"][0]["standard"]
        out, _ = translate({"tts": {"voice_id": mapped}}, r, "hipaa")
        self.assertEqual(out["tts"]["voice_id"], mapped)

    def test_an_identity_field_cannot_hide_a_subtree(self):
        r = provisioned(load_registry())
        tool = next(m for m in r["mappings"] if m["kind"] == "tool")
        out, _ = translate({"voice_id": {"nested": {"tool_ids": [tool["standard"]]}}}, r, "hipaa")
        self.assertEqual(out["voice_id"]["nested"]["tool_ids"], [tool["hipaa"]])

    def test_identity_subtree_is_copied_not_aliased(self):
        r = provisioned(load_registry())
        src = {"voice_id": {"x": [1, 2, 3]}}
        out, _ = translate(src, r, "hipaa")
        out["voice_id"]["x"].append(999)
        self.assertEqual(src["voice_id"]["x"], [1, 2, 3])


class TestSafetyNet(unittest.TestCase):
    """An id at a position refs.py does not know about must not ship."""

    def test_unknown_position_raises(self):
        r = provisioned(load_registry())
        t = next(m for m in r["mappings"] if m["kind"] == "test")
        doc = {"platform_settings": {"testing": {"simulation_library": {
            "test_ids": [t["standard"]]}}}}
        with self.assertRaises(UntranslatedIdentifier) as ctx:
            translate(doc, r, "hipaa")
        self.assertIn("simulation_library", str(ctx.exception))

    def test_the_net_does_not_expire_once_provisioned(self):
        """The lint's shape net goes quiet once provisioned; this one must not.

        Once a mapping has a target id the lint calls it mapped and says nothing,
        so if the translator also stayed silent the guard would protect exactly
        the phase in which nothing is deployed.
        """
        r = provisioned(load_registry())
        t = next(m for m in r["mappings"] if m["kind"] == "test")
        self.assertIsNotNone(t["hipaa"], "fixture must be provisioned")
        with self.assertRaises(UntranslatedIdentifier):
            translate({"some_new_field": t["standard"]}, r, "hipaa")

    def test_key_collision_is_not_silent(self):
        r = provisioned(load_registry())
        a, b = [m for m in r["mappings"] if m["kind"] == "tool"][:2]
        b["hipaa"] = a["hipaa"]
        with self.assertRaises(KeyCollision):
            translate({"tool_mock_overrides": {a["standard"]: 1, b["standard"]: 2}}, r, "hipaa")


class TestOverlayIsConstrained(unittest.TestCase):
    """The allowlist is the enforcement; a comment is not a constraint."""

    def test_behaviour_cannot_be_overlaid(self):
        for path in ({"conversation_config": {"agent": {"prompt": {"prompt": "x"}}}},
                     {"workflow": {"nodes": {}}},
                     {"platform_settings": {"testing": {"attached_tests": []}}}):
            with self.subTest(overlay=path):
                with self.assertRaises(SystemExit):
                    apply_overlay({}, path)

    def test_the_real_overlay_is_allowed(self):
        r = load_registry()
        apply_overlay({}, r["workspaces"]["hipaa"]["config_overlay"])


class TestUnknownWorkspace(unittest.TestCase):
    def test_a_typo_is_not_reported_as_missing_counterparts(self):
        r = load_registry()
        with self.assertRaises(SystemExit) as ctx:
            translate({}, r, "hippa")
        self.assertIn("unknown workspace", str(ctx.exception))


class TestSkippedTests(unittest.TestCase):
    """A test is skipped exactly where its dependency was omitted on purpose.

    The rule is deliberately keyed on `omitted` and not on "the mapping is
    null". Null means not provisioned yet, and reading it as a skip would make
    a half-provisioned workspace report green by running nothing at all.
    """

    def test_nothing_is_skipped_in_the_reference_workspace(self):
        self.assertEqual(skipped_tests(load_registry(), "standard"), {})

    def test_an_mcp_dependent_test_is_skipped_where_mcp_is_omitted(self):
        r = load_registry()
        self.assertIn("mcp", r["workspaces"]["hipaa"]["omitted"])
        m = next(m for m in r["mappings"] if m["kind"] == "test")
        m["requires"] = ["mcp/knowledge_base"]
        self.assertEqual(skipped_tests(r, "hipaa").get(m["hipaa"]), "mcp/knowledge_base")

    def test_the_skip_keys_are_ids_the_target_workspace_uses(self):
        """attached_tests is already translated when the filter runs, so a skip
        keyed by the reference id would match nothing and quietly do nothing."""
        r = load_registry()
        for test_id in skipped_tests(r, "hipaa"):
            self.assertTrue(any(m.get("hipaa") == test_id for m in r["mappings"]),
                            f"{test_id} is not a hipaa id")

    def test_nothing_is_skipped_when_the_workspace_omits_nothing(self):
        r = load_registry()
        r["workspaces"]["hipaa"]["omitted"] = []
        self.assertEqual(skipped_tests(r, "hipaa"), {})

    def test_a_requirement_naming_no_mapping_is_rejected(self):
        r = load_registry()
        m = next(m for m in r["mappings"] if m["kind"] == "test")
        m["requires"] = ["mcp/typo"]
        with self.assertRaises(SystemExit) as ctx:
            skipped_tests(r, "hipaa")
        self.assertIn("not a mapping", str(ctx.exception))

    def test_every_declared_requirement_resolves(self):
        """The real registry, not a fixture: a typo here disables a test silently."""
        r = load_registry()
        for slug in r["workspaces"]:
            skipped_tests(r, slug)


class TestOmissionStripsSearchPrompt(unittest.TestCase):
    """A workspace without the MCP server is never told to search.

    With the tool absent, an agent instructed to search substitutes another tool
    for it and misbehaves (observed in production: it hung up on the caller); a
    prompt rule against that held far less reliably than removing the section. So the omission strips the whole section, and this test is
    what fails if a search instruction ever escapes it.
    """

    def _receptionist(self, registry):
        agent = next(a for a in registry["agents"] if a["name"] == "receptionist")
        with open(agent["config"], encoding="utf-8") as f:
            return json.load(f)

    def test_the_reference_config_carries_the_section(self):
        cfg = self._receptionist(load_registry())
        flat = json.dumps(cfg)
        self.assertIn("# Knowledge Search", flat)
        self.assertIn("search_knowledge", flat)

    def test_a_workspace_without_mcp_never_hears_of_the_search_tool(self):
        r = load_registry()
        self.assertIn("mcp", r["workspaces"]["hipaa"]["omitted"])
        cfg = apply_omissions(self._receptionist(r), r["workspaces"]["hipaa"]["omitted"])
        flat = json.dumps(cfg)
        for needle in ("search_knowledge", "Knowledge Search"):
            self.assertNotIn(needle, flat)
        # Stripping must take exactly the one section, not its neighbours.
        prompt = cfg["conversation_config"]["agent"]["prompt"]["prompt"]
        self.assertIn("# Conduct", prompt)
        self.assertIn("# Capabilities", prompt)

    def test_omitting_nothing_strips_nothing(self):
        r = load_registry()
        cfg = apply_omissions(self._receptionist(r), [])
        self.assertIn("# Knowledge Search", json.dumps(cfg))


class TestTransientRetry(unittest.TestCase):
    """A dropped connection is not a verdict.

    A CI run once lost a whole leg to an SSL handshake reset during
    preflight — before one test had run.
    """

    def _patch(self, responses):
        """Feed call() a scripted sequence of outcomes; count the attempts."""
        self.attempts = 0

        def fake_urlopen(req, *a, **kw):
            i, self.attempts = self.attempts, self.attempts + 1
            outcome = responses[min(i, len(responses) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            class R:
                def read(self_): return outcome
                def __enter__(self_): return self_
                def __exit__(self_, *e): return False
            return R()

        self._orig = push_lib.urllib.request.urlopen
        self._sleep = push_lib.time.sleep
        push_lib.urllib.request.urlopen = fake_urlopen
        push_lib.time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(push_lib.urllib.request, "urlopen", self._orig))
        self.addCleanup(lambda: setattr(push_lib.time, "sleep", self._sleep))

    def test_a_reset_connection_is_retried_and_succeeds(self):
        self._patch([ConnectionResetError(104, "reset"), b'{"ok": true}'])
        self.assertEqual(push_lib.call("GET", "agents", "k"), {"ok": True})
        self.assertEqual(self.attempts, 2)

    def test_a_reset_that_never_clears_exits_with_a_named_error(self):
        self._patch([ConnectionResetError(104, "reset")])
        with self.assertRaises(SystemExit) as ctx:
            push_lib.call("GET", "agents", "k")
        self.assertIn("after", str(ctx.exception))
        self.assertEqual(self.attempts, push_lib.NET_RETRIES)

    def test_a_post_still_retries_a_dropped_connection(self):
        """No response means the API never answered, whatever the method."""
        self._patch([ConnectionResetError(104, "reset"), b"{}"])
        push_lib.call("POST", "agents/x/run-tests", "k", {})
        self.assertEqual(self.attempts, 2)

    def test_a_4xx_is_not_retried(self):
        err = push_lib.urllib.error.HTTPError("u", 422, "bad", {}, None)
        err.read = lambda: b"bad mock"
        self._patch([err])
        with self.assertRaises(push_lib.ApiError):
            push_lib.call("PATCH", "tools/x", "k", {})
        self.assertEqual(self.attempts, 1)

    def test_a_5xx_post_is_not_replayed(self):
        """The API may have accepted it; a second create leaves a stray test."""
        err = push_lib.urllib.error.HTTPError("u", 503, "nope", {}, None)
        err.read = lambda: b"unavailable"
        self._patch([err])
        with self.assertRaises(push_lib.ApiError):
            push_lib.call("POST", "agent-testing/create", "k", {})
        self.assertEqual(self.attempts, 1)

    def test_a_5xx_get_is_retried(self):
        err = push_lib.urllib.error.HTTPError("u", 502, "gw", {}, None)
        err.read = lambda: b"bad gateway"
        self._patch([err, b'{"ok": 1}'])
        self.assertEqual(push_lib.call("GET", "agents", "k"), {"ok": 1})
        self.assertEqual(self.attempts, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
