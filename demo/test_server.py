"""Tests for the demo backend. Run from the repo root: python3 demo/test_server.py"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

DENTAL = server.TENANTS["harbor-dental"]
BAKERY = server.TENANTS["sunrise-bakery"]
NY = ZoneInfo("America/New_York")


class DynamicVariables(unittest.TestCase):
    def test_every_placeholder_the_agent_declares_is_sent(self):
        """A variable the backend forgets silently falls back to the agent's test placeholder."""
        declared = server.AGENT["conversation_config"]["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]
        for tenant in server.TENANTS.values():
            self.assertEqual(set(server.dynamic_variables(tenant)), set(declared), tenant["id"])

    def test_values_are_all_strings(self):
        for value in server.dynamic_variables(DENTAL).values():
            self.assertIsInstance(value, str)

    def test_gates_follow_what_the_business_has(self):
        dental, bakery = server.dynamic_variables(DENTAL), server.dynamic_variables(BAKERY)
        self.assertEqual((dental["transfer_enabled"], dental["scheduling_enabled"]), ("true", "true"))
        self.assertEqual((bakery["transfer_enabled"], bakery["scheduling_enabled"]), ("false", "false"))
        self.assertEqual(bakery["transfer_rules"], "")

    def test_open_and_closed_come_from_the_business_clock(self):
        tuesday_morning = datetime(2026, 6, 23, 10, 0, tzinfo=NY)
        tuesday_night = datetime(2026, 6, 23, 21, 0, tzinfo=NY)
        sunday = datetime(2026, 6, 28, 10, 0, tzinfo=NY)
        self.assertEqual(server.dynamic_variables(DENTAL, tuesday_morning)["within_business_hours"], "true")
        self.assertEqual(server.dynamic_variables(DENTAL, tuesday_night)["within_business_hours"], "false")
        self.assertEqual(server.dynamic_variables(DENTAL, sunday)["within_business_hours"], "false")

    def test_closing_time_itself_is_closed(self):
        self.assertFalse(server.is_open(DENTAL, datetime(2026, 6, 23, 17, 0, tzinfo=NY)))

    def test_datetime_is_written_the_way_the_prompt_expects(self):
        now = datetime(2026, 6, 23, 14, 5, tzinfo=NY)
        self.assertEqual(server.dynamic_variables(DENTAL, now)["current_datetime"], "Tuesday, June 23 2026, 2:05 PM")

    def test_the_ui_label_does_not_leak_into_the_transfer_rules(self):
        for rule in json.loads(server.dynamic_variables(DENTAL)["transfer_rules"]):
            self.assertEqual(set(rule), {"match", "destination", "announce"})


class Tools(unittest.TestCase):
    def setUp(self):
        server.LOG.clear()

    def test_a_booked_slot_is_no_longer_offered(self):
        args = {"calendar_id": "cal_cleanings", "preferred_date": "2026-06-24"}
        before = [s["start"] for s in server.run_tool(DENTAL, "check_availability", args)["slots"]]
        booking = {"calendar_id": "cal_cleanings", "date": "2026-06-24", "start_time": before[0], "caller_name": "Ana"}
        self.assertEqual(server.run_tool(DENTAL, "book_appointment", booking)["status"], "confirmed")
        server.LOG.append({"tenant": DENTAL["id"], "tool": "book_appointment", "params": booking})
        after = [s["start"] for s in server.run_tool(DENTAL, "check_availability", args)["slots"]]
        self.assertEqual(after, before[1:])
        self.assertEqual(server.run_tool(DENTAL, "book_appointment", booking)["status"], "rejected")

    def test_a_booking_in_one_calendar_does_not_take_the_slot_in_another(self):
        server.LOG.append({"tenant": "riverside-yoga", "tool": "book_appointment",
                           "params": {"calendar_id": "cal_private_sessions", "date": "2026-06-24", "start_time": "10:00"}})
        slots = server.run_tool(DENTAL, "check_availability", {"calendar_id": "cal_cleanings", "preferred_date": "2026-06-24"})["slots"]
        self.assertIn("10:00", [s["start"] for s in slots])

    def test_no_slots_at_the_weekend(self):
        saturday = {"calendar_id": "cal_cleanings", "preferred_date": "2026-06-27"}
        self.assertEqual(server.run_tool(DENTAL, "check_availability", saturday)["slots"], [])

    def test_a_calendar_of_another_business_is_refused(self):
        result = server.run_tool(BAKERY, "check_availability", {"calendar_id": "cal_cleanings", "preferred_date": "2026-06-24"})
        self.assertIn("error", result)

    def test_transfer_only_goes_to_a_destination_in_the_rules(self):
        call = {"destination": "+15550198002", "reason": "billing question", "thread_id": "t1"}
        self.assertEqual(server.run_tool(DENTAL, "transfer_call", call), "Transfer initiated.")
        self.assertIn("error", server.run_tool(DENTAL, "transfer_call", {**call, "destination": "+15550000000"}))
        self.assertIn("error", server.run_tool(BAKERY, "transfer_call", call))

    def test_show_item_only_shows_this_business_catalog(self):
        self.assertIn("error", server.run_tool(BAKERY, "show_item", {"item_id": "whitening"}))
        self.assertNotIsInstance(server.run_tool(DENTAL, "show_item", {"item_id": "whitening"}), dict)

    def test_every_client_tool_in_the_repo_is_implemented(self):
        self.assertEqual(set(server.TOOLS), set(server.REQUIRED))

    def test_a_call_with_missing_parameters_is_answered_not_raised(self):
        result = server.run_tool(DENTAL, "book_appointment", {"calendar_id": "cal_cleanings"})
        self.assertEqual(result, {"error": "missing parameters: date, start_time, caller_name"})

    def test_a_malformed_date_is_answered_not_raised(self):
        result = server.run_tool(DENTAL, "check_availability", {"calendar_id": "cal_cleanings", "preferred_date": "tomorrow"})
        self.assertIn("error", result)

    def test_an_unknown_tool_is_answered_not_raised(self):
        self.assertEqual(server.run_tool(DENTAL, "delete_everything", {}), {"error": "unknown tool delete_everything"})


class Workflow(unittest.TestCase):
    def setUp(self):
        self.graph = server.workflow()
        self.nodes = {n["id"]: n for n in self.graph["nodes"]}
        self.edges = {e["id"]: e for e in self.graph["edges"]}

    def test_every_edge_in_an_edge_order_exists(self):
        for node in self.graph["nodes"]:
            for edge_id in node["edge_order"]:
                self.assertIn(edge_id, self.edges, node["id"])

    def test_gated_edges_name_a_variable_the_backend_sends(self):
        sent = set(server.dynamic_variables(DENTAL))
        gates = {e["gate"] for e in self.graph["edges"] if e["gate"]}
        self.assertEqual(gates, {"transfer_enabled", "scheduling_enabled", "display_enabled"})
        self.assertLessEqual(gates, sent)

    def test_each_stage_lists_its_own_tools(self):
        self.assertEqual(self.nodes["node_schedule"]["tools"], ["check_availability", "book_appointment"])
        self.assertEqual(self.nodes["node_transfer"]["tools"], ["transfer_call"])
        self.assertEqual(self.nodes["node_catalog"]["tools"], ["show_item"])
        self.assertIn("end_call", self.nodes["node_triage"]["tools"])

    def test_a_stage_can_go_back_to_triage(self):
        for stage in ("node_transfer", "node_schedule", "node_catalog"):
            edge = self.edges[self.nodes[stage]["edge_order"][0]]
            self.assertEqual((edge["source"], edge["target"]), ("node_triage", stage))
            self.assertTrue(edge["back"], stage)


if __name__ == "__main__":
    unittest.main()
