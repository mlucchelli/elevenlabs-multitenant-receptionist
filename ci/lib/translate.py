"""Translate a config from the reference workspace into another workspace.

The versioned JSON holds the reference workspace's real ids, exactly as
`agents pull` writes them. Pushing to the reference workspace is therefore the
identity function — today's paying workspace is untouched by this project.
Pushing anywhere else substitutes each known id for that workspace's own.
"""

import copy
import json
import re

from .refs import IDENTITY_FIELDS, KEY_FIELDS, VALUE_FIELDS, looks_like_id


class UntranslatedIdentifier(Exception):
    """An identifier survived translation because nothing knew where it lived.

    The lint's shape-based net covers this only until bootstrap runs: once a
    mapping has a target id, the lint calls it mapped and goes quiet, while the
    translator still passes it through untouched at an unenumerated position.
    Catching it here means the guard does not expire.
    """


class KeyCollision(Exception):
    """Two source ids translated to the same target id, silently losing one."""


class MissingCounterpart(Exception):
    """One or more ids in the document have no id in the target workspace.

    Carries every gap, not the first: one run should tell you everything that
    needs provisioning, so bootstrap is a single trip rather than a game of
    fix-one-rerun.
    """

    def __init__(self, target, gaps):
        self.target = target
        self.gaps = gaps
        lines = "\n".join(f"  {kind:10} {sid}  ({name})" for kind, sid, name in gaps)
        super().__init__(
            f"{target}: {len(gaps)} identifier(s) have no counterpart:\n{lines}\n"
            f"Run ci/bootstrap_workspace.py --workspace {target} to provision them."
        )


def load_registry(path="workspaces.json"):
    # Explicit encoding everywhere: the repo is full of "ô" and em-dashes, in
    # filenames as well as content, and the locale default is not ours to assume.
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require_workspace(registry, slug):
    """Reject a workspace name that does not exist.

    Without this a typo reads as "nothing is provisioned": build_map returns
    None for every mapping and the caller reports 39 missing counterparts for a
    workspace that was never declared.
    """
    if slug not in registry["workspaces"]:
        known = ", ".join(sorted(registry["workspaces"]))
        raise SystemExit(f"::error::unknown workspace {slug!r} — known: {known}")
    return slug


def build_map(registry, target):
    """standard id -> (target id or None, kind, name), for every mapping."""
    return {
        m["standard"]: (m.get(target), m["kind"], m["name"])
        for m in registry["mappings"]
    }


def translate(doc, registry, target):
    """Return (translated copy, stats). Never mutates `doc`.

    Raises MissingCounterpart if the document references an id the target
    workspace does not have. A silent pass-through would produce an agent
    wired to tools that do not exist there, which does not fail loudly — it
    just answers calls badly.
    """
    require_workspace(registry, target)
    if target == registry["reference"]:
        return copy.deepcopy(doc), {"substitutions": 0, "by_kind": {}}

    table = build_map(registry, target)
    gaps, by_kind, stowaways = [], {}, []

    def sub(value, kind):
        """Translate one id, recording a gap or a substitution."""
        if not isinstance(value, str) or value not in table:
            return value
        target_id, mapped_kind, name = table[value]
        if target_id is None:
            gap = (mapped_kind or kind, value, name)
            if gap not in gaps:
                gaps.append(gap)
            return value
        by_kind[mapped_kind] = by_kind.get(mapped_kind, 0) + 1
        return target_id

    def walk(node, kind_hint=None, key_kind=None, where="$"):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                new_key = sub(k, key_kind) if key_kind else k
                if new_key in out:
                    raise KeyCollision(
                        f"{k} and another key both translate to {new_key} under "
                        f"{target}; one of them would be silently dropped")
                if k in IDENTITY_FIELDS:
                    # The field's own value is the same string in every
                    # workspace: not translated, and not reported as a stowaway.
                    # Anything nested under it is still walked, so an identity
                    # field cannot become a hiding place for a subtree. Copied
                    # rather than aliased — "never mutates doc" has to be a
                    # property of the result, not just of this function.
                    out[new_key] = (copy.deepcopy(v) if isinstance(v, str)
                                    else walk(v, where=f"{where}.{k}"))
                elif k in KEY_FIELDS:
                    out[new_key] = walk(v, key_kind=KEY_FIELDS[k], where=f"{where}.{k}")
                elif k in VALUE_FIELDS:
                    out[new_key] = walk(v, kind_hint=VALUE_FIELDS[k], where=f"{where}.{k}")
                else:
                    out[new_key] = walk(v, where=f"{where}.{k}")
            return out
        if isinstance(node, list):
            return [walk(v, kind_hint, key_kind, f"{where}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str):
            if kind_hint:
                return sub(node, kind_hint)
            if looks_like_id(node) or node in table:
                # A string that is unmistakably an identifier, at a position the
                # position map does not know about. Passing it through is how a
                # standard-workspace id reaches another workspace's config.
                stowaways.append((node, where))
        return node

    result = walk(doc)
    if gaps:
        raise MissingCounterpart(target, sorted(gaps))
    if stowaways:
        lines = "\n".join(f"  {i}  at {w}" for i, w in sorted(set(stowaways)))
        raise UntranslatedIdentifier(
            f"{target}: {len(set(stowaways))} identifier(s) sit at a position "
            f"ci/lib/refs.py does not know about, so they would ship untranslated:\n"
            f"{lines}\nAdd the position to VALUE_FIELDS or KEY_FIELDS.")
    return result, {"substitutions": sum(by_kind.values()), "by_kind": by_kind}


# An overlay may only reach policy. `conversation_config` and `workflow` are the
# agent's behaviour and are shared across workspaces by design — an overlay that
# could touch them would also bypass translation and the lint, since the overlay
# is applied after both.
OVERLAY_ALLOWED = ("platform_settings.privacy",
                   "platform_settings.analysis_llm",
                   "platform_settings.call_limits")


def _overlay_paths(overlay, prefix=""):
    """Every path the overlay would write. An empty dict is a leaf, not nothing —
    `{"workflow": {"nodes": {}}}` still sets workflow.nodes."""
    for k, v in overlay.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            yield from _overlay_paths(v, path)
        else:
            yield path


# Every instruction about the search_knowledge MCP tool lives under this one
# prompt header. A workspace that omits the MCP server must not be told to
# search: with the tool absent, the Receptionist substituted end_call for it
# and hung up on the caller (3/3 on hipaa, 2026-09-24), and a prompt rule
# against that held only 1/3. Removing the section is deterministic.
SEARCH_SECTION = re.compile(r"\n# Knowledge Search\n.*?(?=\n# )", re.S)


def _strip_search_section(text):
    return SEARCH_SECTION.sub("", text)


def apply_omissions(config, omitted):
    """Strip a dependency this workspace deliberately does without.

    Recorded in workspaces.json by `bootstrap_workspace.py --omit`, and applied
    on every push, not just at provisioning time: the workspace was provisioned
    without these, so pushing a config that still references them would fail
    translation on ids that were never going to exist.
    """
    if not omitted:
        return config
    if "mcp" in omitted:
        for node in (config.get("workflow", {}).get("nodes") or {}).values():
            prompt = ((node.get("conversation_config") or {}).get("agent") or {}).get("prompt")
            if prompt is not None:
                prompt["mcp_server_ids"] = []
            if node.get("additional_prompt"):
                node["additional_prompt"] = _strip_search_section(node["additional_prompt"])
        agent_prompt = ((config.get("conversation_config") or {}).get("agent") or {}).get("prompt")
        if agent_prompt is not None:
            agent_prompt["mcp_server_ids"] = []
            if agent_prompt.get("prompt"):
                agent_prompt["prompt"] = _strip_search_section(agent_prompt["prompt"])
    if "webhook" in omitted:
        config.get("platform_settings", {}).pop("workspace_overrides", None)
    return config


def skipped_tests(registry, target):
    """{test id in `target`: the requirement it needs} for tests this workspace
    cannot run.

    A test declares `requires` in workspaces.json naming the mappings whose
    behaviour it exercises — "mcp/knowledge_base" for one that needs
    search_knowledge. When that dependency is in the workspace's `omitted`
    list, the behaviour the test asserts does not exist here and never will:
    apply_omissions has just stripped it from the config on purpose.

    Running it anyway buys a permanent red that says nothing about the change
    under review, and a permanently red check is one nobody reads. Skipping is
    therefore the honest verdict, not a concession — but it is skipped loudly,
    naming the requirement, so the reason stays visible in the log.
    """
    omitted = set(registry["workspaces"][target].get("omitted") or [])
    by_ref = {f"{m['kind']}/{m['name']}": m for m in registry["mappings"]}
    out = {}
    for m in registry["mappings"]:
        if m["kind"] != "test":
            continue
        for ref in m.get("requires") or []:
            dep = by_ref.get(ref)
            if dep is None:
                # A typo here would silently run the test everywhere, which is
                # the failure this function exists to prevent.
                raise SystemExit(
                    f"::error::test {m['name']!r} requires {ref!r}, which is not a "
                    f"mapping in workspaces.json. Use \"<kind>/<name>\", e.g. "
                    f"\"mcp/knowledge_base\".")
            test_id = m.get(target)
            if dep["kind"] in omitted and test_id:
                out[test_id] = ref
    return out


def apply_overlay(doc, overlay):
    """Deep-merge `overlay` over `doc`. Objects merge; scalars and lists replace.

    Lists replace rather than concatenate on purpose: an overlay that appended to
    attached_tests or supported_voices would change behaviour.

    The allowlist is the enforcement. Saying "the overlay carries policy, never
    behaviour" in a comment is not a constraint — it is a hope, and the overlay
    is applied after translation, so anything it injects is neither translated
    nor linted.
    """
    if not overlay:
        return copy.deepcopy(doc)
    for path in _overlay_paths(overlay):
        if not any(path == a or path.startswith(a + ".") for a in OVERLAY_ALLOWED):
            raise SystemExit(
                f"::error::config_overlay may not set {path!r}. Allowed: "
                + ", ".join(OVERLAY_ALLOWED)
                + ". An overlay carries policy; behaviour is shared across workspaces.")
    return _merge(doc, overlay)


def _merge(doc, overlay):
    out = copy.deepcopy(doc)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
