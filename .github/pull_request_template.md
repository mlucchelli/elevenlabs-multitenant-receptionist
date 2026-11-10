**Problem solved**:
Detail the specific problem or gap that this change addresses.

**Importance**:
Explain why this change matters and how it affects the agent's behavior.

# Key changes
- List the key changes introduced by this PR.

  - *Example*: "Updated system prompt to handle scheduling requests before transferring."
  - *Example*: "Added transfer rule for support department requests."
  - *Example*: "Adjusted simulation test to cover the new booking flow edge case."

# Why is this the best solution?

Explain why this approach was chosen over alternatives.

> ⚠️ **New environment variables / secrets** (if any):
```bash
ELEVENLABS_API_KEY=
```

# Test results

CI pushes this PR to an ElevenLabs agent branch and runs the simulation suite there — see the
per-workspace checks. To inspect locally first:

```bash
ELEVENLABS_API_KEY=... python3 ci/push_workspace.py \
  --workspace standard --mode ci --branch-name "$(git rev-parse --abbrev-ref HEAD)"
```

<details>
<summary>Local test output (optional)</summary>

```
# paste output here
```

</details>

# Review checklist
- [ ] Simulation tests ran against the ElevenLabs PR branch (not production) — see CI check
- [ ] All existing test scenarios still pass
- [ ] New test scenario added if this introduces a new call flow
- [ ] No direct changes made in the ElevenLabs UI (config pulled via CLI only)
- [ ] `ci/README.md` updated if pipeline behavior changed
