# gaia-sdk

The GAIA agent loop for your own program. The loop runs in the native `gaiacpp`
engine, started as a child process; your tools, system prompt and conversation
stay in your process. No dependencies.

**Beta.** The engine's host mode has unit tests against a scripted model server and
has not yet been evaluated on a hosted model.

```python
from gaia_sdk import Agent, Budget, ToolRefusal, tool

@tool
def get_order(order_number: str) -> dict:
    """Look up an order by its number."""
    if not verified(order_number):
        raise ToolRefusal("That order has not been confirmed in this chat.")
    return {"status": "shipped"}

with Agent(
    model="my-model",
    base_url="https://api.example.com/v1",
    api_key_env="PROVIDER_API_KEY",   # the variable's name, never the key
    system_prompt="You answer questions about orders.",
    tools=[get_order],
) as agent:
    turn = agent.run("Where is order 1001?", budget=Budget(model_calls=8, seconds=25))
    if turn.ok:
        print(turn.text)
    else:
        print("handing off:", turn.stop_reason)
```

`turn` has `status`, `stop_reason`, `text`, `tool_calls`, `usage`, `seconds`,
`failure` and `model_calls`. Show `text` to an end user only when `turn.ok`.

One agent runs one turn at a time; a second `run` while one is running raises
`AgentBusy`. A tool result JSON cannot hold is reported to the model as a failed call,
and a `seconds` budget cuts off a model request still in flight.
`before_model_call` runs before each model request and can wait or stop the turn, and
`agent.cancel()` stops a turn from outside.

## The engine binary

The SDK looks for `gaiacpp` in this order: the `engine=` argument, the `GAIA_ENGINE`
environment variable, the binary inside a platform wheel, then `PATH`. Builds are
attached to each [GAIA release](https://github.com/amd/gaia/releases).

## A wheel that carries the engine

```bash
python build_wheel.py --engine /path/to/gaiacpp --out dist
pip install dist/gaia_sdk-*.whl      # nothing else is installed
```

The wheel is tagged for the platform the binary was built on. Installed alone, it
runs a turn with no other package and no `GAIA_ENGINE`; `tests/smoke_installed.py`
checks exactly that, and CI runs it on every change.

## Tests

```bash
python -m pytest sdk/python/tests                       # protocol tests, no binary needed
GAIA_ENGINE=/path/to/gaiacpp python -m pytest sdk/python/tests   # plus the real engine
```

Full guide: https://amd-gaia.ai/docs/sdk/gaia-sdk
