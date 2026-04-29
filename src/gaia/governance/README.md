# gaia.governance

Optional governance layer for GAIA agents. Opt-in. Off by default.

## Quick start (5 minutes)

```python
from gaia import Agent, tool
from gaia.governance import GaiaGovernanceAdapter, GovernedAgentMixin, govern


@tool
@govern(risk="blocked", reason="destructive")
def wipe_disk() -> dict:
    return {"status": "ok"}


class MyAgent(GovernedAgentMixin, Agent):
    ...


agent = MyAgent(
    governance_adapter=GaiaGovernanceAdapter.default(),
)
```

That's it. When the model calls `wipe_disk`, governance short-circuits
the call, issues a signed receipt to `receipts.jsonl`, and returns a
denied result to the agent loop.

## How decisions work

| Decision | Effect |
|---|---|
| `ALLOW` | Tool runs as usual. |
| `BLOCK` | Tool is refused. A receipt is written to the audit log with the full evidence envelope (action, policy version, constitution hash, timestamp). |
| `REVIEW` | A checkpoint is opened. The mixin asks your `governance_reviewer` callback (or fails closed if none). On `APPROVE` the tool runs; on `REJECT` it is refused. Either way a receipt is written. |

Decisions are produced by a `PolicyEngine`. The shipped
`RuleBasedPolicyEngine` reads tags from `@govern(risk=...)` and/or a
`governance_risk_tags` dict on the agent. Swap in any
`PolicyEngine`-shaped object (ACGS-lite, your own rules, an LLM judge,
etc.) without touching agent code.

## Two tagging styles

**Decorator — colocates policy with the tool (recommended):**

```python
@tool
@govern(risk="review", reason="sends money")
def transfer(amount: float): ...
```

**Dict — centralizes policy on the agent:**

```python
agent = MyAgent(
    governance_adapter=GaiaGovernanceAdapter.default(),
    governance_risk_tags={"transfer": ["review"]},
)
```

Both work together. Tags are **additive** (union, deduplicated): decorator
tags come first, then dict tags are appended. Neither side overrides the
other, so a tool declared `"review"` in a decorator and `"blocked"` in
the dict will carry both tags.

## Configuration

Two equivalent styles. Pick whichever reads better:

```python
# Structured config object
from gaia.governance import GovernanceConfig

agent = MyAgent(governance=GovernanceConfig(
    adapter=adapter,
    actor_id="alice",
    workflow_id="session-42",
    risk_tags={"delete_record": ["blocked"]},
    reviewer=my_reviewer,
))

# Individual kwargs (also supported)
agent = MyAgent(
    governance_adapter=adapter,
    governance_actor_id="alice",
    governance_risk_tags={"delete_record": ["blocked"]},
    governance_reviewer=my_reviewer,
)
```

## Reviewers

When a `REVIEW` decision fires, the mixin calls your
`governance_reviewer` callback — nothing else. There is no "fall back to
the console" path, because GAIA's default `AgentConsole.confirm_tool_execution`
returns `True`, and a silent auto-approve would defeat the decision.

```python
def my_reviewer(tool_name, tool_args, decision) -> bool:
    # UI, Slack, a web form, whatever you like
    return input(f"approve {tool_name}? [y/N]: ") == "y"

agent = MyAgent(
    governance_adapter=GaiaGovernanceAdapter.default(),
    governance_reviewer=my_reviewer,
)
```

If no reviewer is set, REVIEW decisions **fail closed** (tool denied).

## Security properties

- **Canonical name resolution:** governance resolves the registered
  tool name before checking risk tags, so an LLM cannot bypass a tag
  on `mcp_time_get_current_time` by calling the unprefixed alias
  `get_current_time`.
- **Envelope-bound receipts:** each receipt's `payload_hash` covers
  the full evidence envelope (action, decision, policy version,
  constitution hash, actor, timestamp) with strict canonical JSON. Any
  field tampered in the log changes the hash.
- **Workflow-bound checkpoint resolution:** the adapter refuses to
  resolve a checkpoint under a workflow_id that differs from the one
  recorded when the checkpoint was opened.
- **Atomic checkpoint resolution:** `InMemoryCheckpointBridge` uses a
  lock so a race between two concurrent resolutions cannot produce two
  terminal outcomes.

## Extension points

| Interface                   | Shipped reference                                 | Swap with                                  |
|-----------------------------|---------------------------------------------------|--------------------------------------------|
| `PolicyEngine`              | `RuleBasedPolicyEngine`                           | ACGS-lite engine, LLM judge, OPA, etc.     |
| `CheckpointRuntime`         | `InMemoryCheckpointBridge`                        | constitutional-swarm checkpoint service    |
| `ReceiptServiceProtocol`    | `InMemoryReceiptService` / `JsonlReceiptService`  | DB, log forwarder, chain anchor            |
| `PolicyBindingProtocol`     | `StaticPolicyBindingService`                      | constitutional-swarm policy control plane  |

All four are `@runtime_checkable` Protocols — no inheritance required.

## What's not here (yet)

- Policy control plane; `PolicyBindingProtocol` is static in PR 1.
- Attestation / trust routing.
- Precedent memory or validator marketplace.
- Plan-step / multi-agent workflow transitions. The mixin only intercepts
  tool calls today; broader workflow events will arrive in a follow-up PR
  along with the mapper that turns them into `WorkflowTransition`s.
