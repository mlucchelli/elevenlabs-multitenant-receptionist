"""Where workspace-scoped identifiers live, and what kind each one is.

Shared by the translator and the lint so the two can never disagree about what
counts as an identifier.

Identifiers are found by POSITION, not by shape. Shape-matching looks tempting
and is wrong here: `agent_response` is a client-event name in
conversation_config.conversation.client_events and matches any sensible
`agent_[A-Za-z0-9]+` pattern. A regex sweep would have rewritten it. Positions
are also the only way to catch the ids that appear as object KEYS, which a
value walker misses entirely.
"""

# JSON key -> kind of identifier its value(s) hold.
# A value may be a bare string or a list of strings; both are handled.
VALUE_FIELDS = {
    "additional_tool_ids": "tool",
    "tool_ids": "tool",
    "mcp_server_ids": "mcp",
    "native_mcp_server_ids": "mcp",
    "referenced_tests_ids": "test",
    "test_id": "test",
    "post_call_webhook_id": "webhook",
    "agent_id": "agent",
    "secret_id": "secret",
    "api_integration_connection_id": "connection",
}

# JSON key -> kind of identifier its child KEYS hold.
# tool_mock_overrides is keyed by tool id. A translator that only rewrites
# string values leaves these pointing at the standard workspace, and the test
# then runs with no mocks at all — passing or failing for the wrong reason.
KEY_FIELDS = {
    "tool_mock_overrides": "tool",
}

# Identifiers that are the same string in every workspace and must be left
# alone. Adding a Voice Library voice to another workspace preserves its
# voice_id, so voices need no mapping and must not be
# reported as unmapped.
IDENTITY_FIELDS = {"voice_id"}

# Safety net for positions not enumerated above. The length floor is what keeps
# `agent_response` out: real ids carry a 20+ character suffix.
PREFIXES = ("agent_", "tool_", "agtbrch_", "agtvrsn_", "test_", "tst_", "icxn_", "phnum_")
MIN_SUFFIX = 20


def looks_like_id(value):
    """True for a string that is unmistakably a workspace-scoped identifier."""
    if not isinstance(value, str):
        return False
    for p in PREFIXES:
        if value.startswith(p) and len(value) - len(p) >= MIN_SUFFIX:
            return value[len(p):].isalnum()
    return False
