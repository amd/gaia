# Agent loop — a per-turn state object

> **Status:** proposed. Three steps that need no state object are done or in review:
> the post-loop wrap-up (`_conclude_query`, #8710), plan validation (`_accept_plan`,
> #8719) and parse-failure recovery (`_recover_from_parse_error`, #8729).

## Why

`Agent._process_query_impl` in `src/gaia/agents/base/agent.py` was one 3,440-line
function. After the three steps above, the step loop is still about 2,700 lines in one
body. A change to how the agent calls a tool, recovers from an error or decides it is
done means reading all of it, and a block cannot be unit-tested without driving a whole
turn.

Every remaining large block both steers the loop (`break` or `continue`) and hands loop
variables back to it, so it cannot move into a method unedited:

| Block | Lines | Loop variables it hands back |
|---|---|---|
| Model call and streaming | 454 | 13 |
| Tool execution | 360 | 17 |
| Native tool-call fan-out | 227 | 20 |
| Single tool call | 249 | 21 |
| Answer and self-check | 858 | 10 |

Returning 13 to 21 values from each method would hide the coupling instead of removing
it.

## Design

One object holds what a turn knows while it runs, and the loop and its helpers share it.

```python
@dataclass
class _TurnState:
    user_input: str
    steps_limit: int
    steps_taken: int = 0
    final_answer: Optional[str] = None
    error_count: int = 0
    parse_failures: int = 0
    markup_retries: int = 0
    messages: List[Dict[str, Any]] = field(default_factory=list)
    # … the rest of the loop's variables, grouped below
```

Group the fields so the object reads as the turn's parts, not as a bag:

- **Budget:** `steps_limit`, `steps_taken`, `start_time`, `time_limit_reached`
- **Conversation:** `messages`, `model_messages`, `conversation`
- **Outcome:** `final_answer`, `answer_repaired`, `completion_gaps`, `self_check_open`
- **Recovery:** `error_count`, `last_error`, `parse_failures`, `markup_retries`
- **Stops:** `cancelled_by_console`, `user_stopped`, `model_call_failed`

Each block becomes a method `def _x(self, turn: _TurnState) -> _Step`, where `_Step` is
`CONTINUE`, `STOP` or `PROCEED`. The loop body shrinks to the sequence of calls and the
three-way branch after each.

## Steps

Each step is its own PR and moves one block, so a regression points at one change.

1. Add `_TurnState` and build it where the locals are set today; read every variable
   through it inside `_process_query_impl`. No block moves. Prove it with a check that
   the set of names read and written is unchanged.
2. Move the model call and streaming block.
3. Move the tool-execution block.
4. Move the native fan-out and single-tool-call blocks together, since they share
   duplicate-call tracking.
5. Move the answer and self-check block.
6. Pass `_TurnState` to `_conclude_query`, `_accept_plan` and
   `_recover_from_parse_error` in place of their keyword arguments.

## How each step is checked

- `tests/unit/agents` and every test that exercises `process_query`.
- A syntax-tree comparison wherever a block moves unchanged apart from its exits.
- An agent eval on the Strix Halo pool before merging steps 2 to 5: `gaia_core`,
  `error_recovery`, `tool_selection` and `gaia_honesty` against the
  `gaia-flagship` baseline. These steps change how the loop decides to end a turn, which
  is what those categories measure.
