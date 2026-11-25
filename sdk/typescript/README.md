# @amd/gaia-sdk

The GAIA agent loop for your own program. The loop runs in the native `gaiacpp`
engine, started as a child process; your tools, system prompt and conversation stay
in your process. No runtime dependencies.

**Beta.** The engine's host mode has unit tests against a scripted model server and
has not yet been evaluated on a hosted model.

```ts
import { Agent, ToolRefusal, tool } from "@amd/gaia-sdk";

const getOrder = tool({
  name: "get_order",
  description: "Look up an order by its number.",
  parameters: {
    type: "object",
    properties: { order_number: { type: "string" } },
    required: ["order_number"],
  },
  run: async ({ order_number }: { order_number: string }) => {
    if (!(await verified(order_number))) {
      throw new ToolRefusal("That order has not been confirmed in this chat.");
    }
    return { status: "shipped" };
  },
});

const agent = await Agent.start({
  model: "my-model",
  baseUrl: "https://api.example.com/v1",
  apiKeyEnv: "PROVIDER_API_KEY", // the variable's name, never the key
  systemPrompt: "You answer questions about orders.",
  tools: [getOrder],
});
try {
  const turn = await agent.run("Where is order 1001?", {
    budget: { modelCalls: 8, seconds: 25 },
  });
  console.log(turn.ok ? turn.text : `handing off: ${turn.stopReason}`);
} finally {
  await agent.close();
}
```

`turn` has `ok`, `status`, `stopReason`, `text`, `toolCalls`, `usage`, `seconds`,
`failure` and `modelCalls`. Show `text` to an end user only when `turn.ok`.

## The engine binary

The SDK looks for `gaiacpp` in this order: the `engine` option, the `GAIA_ENGINE`
environment variable, a `bin/` folder beside the package, then `PATH`. Builds are
attached to each [GAIA release](https://github.com/amd/gaia/releases).

## A package that carries the engine

Put the binary in `bin/` before `npm pack` and the tarball is self-contained:
installed alone, it runs a turn with no `GAIA_ENGINE`. `test/smoke-installed.mjs`
checks exactly that, and CI runs it on every change.

## Tests

```bash
npm install
npm test                                   # protocol tests, no binary needed
GAIA_ENGINE=/path/to/gaiacpp npm test      # plus the real engine
```

Full guide: https://amd-gaia.ai/docs/sdk/gaia-sdk
