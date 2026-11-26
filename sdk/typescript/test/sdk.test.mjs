// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The SDK against a stand-in engine (always), and against the real
// `gaiacpp --engine` with a scripted model server (when GAIA_ENGINE is set).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  Agent,
  AgentBusy,
  EngineError,
  EngineNotFound,
  PROTOCOL,
  StopTurn,
  ToolRefusal,
  tool,
} from "../dist/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const FAKE = [process.execPath, join(here, "fake-engine.mjs")];
const FIXTURE = JSON.parse(
  readFileSync(join(here, "..", "..", "..", "tests", "fixtures", "stdio", "gaia_host_wire.json"), "utf8"),
);
const REAL = process.env.GAIA_ENGINE?.trim();

const orderSchema = {
  type: "object",
  properties: { order_number: { type: "string", description: "The order number." } },
  required: ["order_number"],
};

function tools(ran = []) {
  return {
    getOrder: tool({
      name: "get_order",
      description: "Look up an order by its number.",
      parameters: orderSchema,
      run: async ({ order_number }) => {
        ran.push(order_number);
        if (order_number === "2002") {
          throw new ToolRefusal("That order has not been confirmed in this chat.", { order: order_number });
        }
        return { status: "shipped", carrier: "UPS" };
      },
    }),
    brokenOrder: tool({
      name: "broken_order",
      description: "Look up an order in an archive that is down.",
      parameters: orderSchema,
      run: () => {
        throw new Error("archive offline");
      },
    }),
  };
}

async function withAgent(options, body) {
  const agent = await Agent.start({ baseUrl: "http://127.0.0.1:9/v1", engine: FAKE, ...options });
  try {
    return await body(agent);
  } finally {
    await agent.close();
  }
}

// ---- the wire, against the stand-in --------------------------------------

test("the SDK speaks the fixture's protocol version", () => {
  assert.equal(PROTOCOL, FIXTURE.protocol);
});

test("init and turn carry only fields the wire allows", async () => {
  const sent = await withAgent(
    {
      model: "echo-init",
      apiKeyEnv: "PROVIDER_KEY",
      systemPrompt: "You answer questions.",
      tools: [tools().getOrder],
      maxSteps: 8,
      maxOutputTokens: 1200,
      contextSize: 64000,
      temperature: 0.1,
      budget: { modelCalls: 8, outputTokens: 3600, seconds: 25 },
    },
    async (agent) => JSON.parse((await agent.run("hi")).text),
  );

  const initSpec = FIXTURE.host_to_engine.init;
  const allowed = new Set([...initSpec.required, ...initSpec.optional]);
  for (const key of initSpec.required) assert.ok(key in sent.init, `init lacks ${key}`);
  for (const key of Object.keys(sent.init)) assert.ok(allowed.has(key), `init has ${key}`);
  const turnSpec = FIXTURE.host_to_engine.turn;
  assert.deepEqual(Object.keys(sent.turn).sort(), [...turnSpec.required, ...turnSpec.optional].sort());
  assert.deepEqual(Object.keys(sent.turn.budget).sort(), FIXTURE.budget.filter((k) => k !== "usd").sort());
  assert.deepEqual(Object.keys(sent.init.tools[0]).sort(), ["description", "name", "parameters"]);
  assert.equal(sent.init.api_key_env, "PROVIDER_KEY");
  assert.deepEqual(sent.turn.messages, [{ role: "user", content: "hi" }]);
});

test("tool results, refusals and failures go back as the wire says", async () => {
  const { getOrder, brokenOrder } = tools();
  const refuse = tool({
    name: "refuse_order",
    description: "Look up an order that is never confirmed.",
    parameters: orderSchema,
    run: () => {
      throw new ToolRefusal("That order has not been confirmed.", { order: "1001" });
    },
  });

  const turn = await withAgent({ model: "call-each-tool", tools: [getOrder, refuse, brokenOrder] }, (agent) =>
    agent.run("where is it?"),
  );

  const [ok, refused, broken] = turn.toolCalls;
  assert.deepEqual(ok, {
    name: "get_order",
    arguments: { order_number: "1001" },
    result: { status: "shipped", carrier: "UPS" },
    failed: false,
  });
  assert.equal(refused.failed, false);
  assert.deepEqual(refused.result, {
    order: "1001",
    status: "refused",
    message: "That order has not been confirmed.",
  });
  assert.equal(broken.failed, true);
  assert.equal(broken.result, "Error: archive offline");
});

test("a request for a tool that is not registered is answered with an error", async () => {
  const answer = await withAgent({ model: "unknown-tool", tools: [tools().getOrder] }, async (agent) =>
    JSON.parse((await agent.run("hi")).text),
  );

  assert.equal(answer.type, "tool_result");
  assert.match(answer.error, /no_such_tool/);
  assert.ok(!("result" in answer));
});

test("a turn reports each model call", async () => {
  const seen = [];
  const turn = await withAgent({ model: "answer", onModelCall: (call) => seen.push(call) }, (agent) =>
    agent.run("hi"),
  );

  assert.equal(turn.ok, true);
  assert.equal(turn.text, "Hello there.");
  assert.deepEqual(turn.modelCalls, seen);
  assert.deepEqual(seen[0].usage, { inputTokens: 100, cachedTokens: 0, outputTokens: 7 });
  assert.deepEqual(turn.usage, { modelCalls: 1, inputTokens: 100, cachedTokens: 0, outputTokens: 7 });
});

test("turn ids count up", async () => {
  const ids = await withAgent({ model: "echo-init" }, async (agent) => [
    JSON.parse((await agent.run("one")).text).turn.id,
    JSON.parse((await agent.run("two")).text).turn.id,
  ]);

  assert.deepEqual(ids, ["t1", "t2"]);
});

test("a refused init rejects with the engine's code", async () => {
  await assert.rejects(
    () => Agent.start({ model: "refuse-init", baseUrl: "http://x/v1", engine: FAKE }),
    (error) => error instanceof EngineError && error.code === "bad_init" && /refused on purpose/.test(error.message),
  );
});

test("an engine on another protocol is refused", async () => {
  await assert.rejects(
    () => Agent.start({ model: "old-protocol", baseUrl: "http://x/v1", engine: FAKE }),
    /speaks protocol 1/,
  );
});

test("an engine that exits at start is reported", async () => {
  await assert.rejects(
    () => Agent.start({ model: "die-at-init", baseUrl: "http://x/v1", engine: FAKE }),
    /exit code 3/,
  );
});

test("a refused turn rejects and the agent can be closed", async () => {
  await withAgent({ model: "bad-turn" }, async (agent) => {
    await assert.rejects(
      () => agent.run("hi"),
      (error) => error instanceof EngineError && error.code === "bad_turn",
    );
  });
});

test("an engine that dies mid-turn is reported, not hung", async () => {
  await withAgent({ model: "die-mid-turn" }, async (agent) => {
    await assert.rejects(() => agent.run("hi"), /exit code 9/);
  });
});

test("an event from a newer engine is named", async () => {
  await withAgent({ model: "new-event" }, async (agent) => {
    await assert.rejects(() => agent.run("hi"), /from_the_future/);
  });
});

test("an engine path that is not a file is named", async () => {
  await assert.rejects(
    () => Agent.start({ model: "m", baseUrl: "http://x/v1", engine: join(here, "missing-binary") }),
    (error) => error instanceof EngineNotFound && /is not a file/.test(error.message),
  );
});

test("a tool the model cannot be told about is refused", () => {
  assert.throws(() => tool({ name: "t", description: "", parameters: orderSchema, run: () => 1 }), /no description/);
  assert.throws(() => tool({ name: "t", description: "d", parameters: {}, run: () => 1 }), /JSON Schema object/);
  assert.throws(() => tool({ name: "t", description: "d", parameters: orderSchema }), /run function/);
});

test("two tools with one name are refused", async () => {
  const { getOrder } = tools();
  await assert.rejects(
    () => Agent.start({ model: "answer", baseUrl: "http://x/v1", engine: FAKE, tools: [getOrder, getOrder] }),
    /Two tools are named/,
  );
});

test("a budget limit that is not positive is refused", async () => {
  for (const budget of [{ modelCalls: 0 }, { outputTokens: 1.5 }, { seconds: -1 }]) {
    await assert.rejects(
      () => Agent.start({ model: "answer", baseUrl: "http://x/v1", engine: FAKE, budget }),
      RangeError,
    );
  }
});

// ---- the real engine -----------------------------------------------------

/** A scripted OpenAI-compatible model server. */
async function modelServer() {
  const replies = [];
  const requests = [];
  const authorization = [];
  // Milliseconds to wait before the next reply; plays a slow provider.
  const state = { delayMs: 0 };
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      requests.push(JSON.parse(body));
      authorization.push(req.headers.authorization ?? "");
      const [status, reply] = replies.shift() ?? [500, {}];
      const payload = JSON.stringify(reply);
      const wait = state.delayMs;
      state.delayMs = 0;
      setTimeout(() => {
        // The engine may have given up on a slow reply and closed the socket.
        if (res.destroyed) return;
        res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) });
        res.end(payload);
      }, wait);
    });
  });
  server.on("clientError", () => {});
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const push = (message) =>
    replies.push([
      200,
      { choices: [{ message }], usage: { prompt_tokens: 100, completion_tokens: 7, total_tokens: 107 } },
    ]);
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
    requests,
    authorization,
    replies,
    slow: (ms) => (state.delayMs = ms),
    say: (text) => push({ role: "assistant", content: text }),
    call: (name, args) =>
      push({
        role: "assistant",
        content: null,
        tool_calls: [{ id: "c1", type: "function", function: { name, arguments: JSON.stringify(args) } }],
      }),
    fail: (status = 503) => replies.push([status, { error: { message: "upstream overloaded" } }]),
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections?.();
        server.close(resolve);
      }),
  };
}

const real = { skip: REAL ? false : "no gaiacpp engine: set GAIA_ENGINE to a built gaiacpp binary" };

async function withRealAgent(options, body) {
  const model = await modelServer();
  const ran = [];
  const agent = await Agent.start({
    model: "host-model",
    baseUrl: model.baseUrl,
    systemPrompt: "You answer questions about orders.",
    tools: [tools(ran).getOrder],
    engine: REAL,
    ...options,
  });
  try {
    return await body(agent, model, ran);
  } finally {
    await agent.close();
    await model.close();
  }
}

test("real engine: an answer in one model call", real, async () => {
  await withRealAgent({}, async (agent, model) => {
    model.say("Hello there.");

    const turn = await agent.run("hi");

    assert.equal(turn.ok, true);
    assert.equal(turn.stopReason, "answered");
    assert.equal(turn.text, "Hello there.");
    assert.deepEqual(turn.usage, { modelCalls: 1, inputTokens: 100, cachedTokens: 0, outputTokens: 7 });
    assert.equal(turn.failure, null);
  });
});

test("real engine: the model gets the prompt, the tools and the key by name only", real, async () => {
  process.env.SDK_TEST_PROVIDER_KEY = "sk-test-123";
  await withRealAgent({ apiKeyEnv: "SDK_TEST_PROVIDER_KEY" }, async (agent, model) => {
    model.say("Hello there.");

    await agent.run("hi");

    const request = model.requests[0];
    assert.equal(request.model, "host-model");
    assert.equal(request.messages[0].content.trim(), "You answer questions about orders.");
    assert.deepEqual(request.messages.at(-1), { role: "user", content: "hi" });
    const names = request.tools.map((t) => t.function.name);
    assert.equal(names[0], "get_order");
    for (const name of names) assert.ok(["get_order", "read_tool_output"].includes(name), name);
    assert.equal(model.authorization[0], "Bearer sk-test-123");
  });
});

test("real engine: a tool runs in the host and its result reaches the model", real, async () => {
  await withRealAgent({}, async (agent, model, ran) => {
    model.call("get_order", { order_number: "1001" });
    model.say("It has shipped with UPS.");

    const turn = await agent.run("Where is order 1001?");

    assert.deepEqual(ran, ["1001"]);
    assert.equal(turn.text, "It has shipped with UPS.");
    assert.deepEqual(turn.toolCalls, [
      {
        name: "get_order",
        arguments: { order_number: "1001" },
        result: { status: "shipped", carrier: "UPS" },
        failed: false,
      },
    ]);
    assert.equal(turn.usage.modelCalls, 2);
    assert.match(JSON.stringify(model.requests[1].messages), /UPS/);
  });
});

test("real engine: a refusal is an answer for the model, not a failed call", real, async () => {
  await withRealAgent({}, async (agent, model) => {
    model.call("get_order", { order_number: "2002" });
    model.say("Please confirm that order first.");

    const turn = await agent.run("Where is order 2002?");

    assert.equal(turn.ok, true);
    assert.equal(turn.toolCalls[0].failed, false);
    assert.equal(turn.toolCalls[0].result.status, "refused");
  });
});

test("real engine: a model-call budget stops the turn and the tool is not run", real, async () => {
  await withRealAgent({}, async (agent, model, ran) => {
    model.call("get_order", { order_number: "1001" });
    model.say("never asked for");

    const turn = await agent.run("Where is order 1001?", { budget: { modelCalls: 1 } });

    assert.equal(turn.ok, false);
    assert.equal(turn.status, "incomplete");
    assert.equal(turn.stopReason, "model_calls");
    assert.equal(turn.text, "");
    assert.deepEqual(ran, []);
    assert.equal(model.requests.length, 1);
  });
});

test("real engine: a provider failure is a typed turn, and the next turn still runs", real, async () => {
  await withRealAgent({}, async (agent, model) => {
    model.fail(503);
    model.say("Hello again.");

    const first = await agent.run("hi");
    const second = await agent.run("hi again");

    assert.equal(first.status, "failed");
    assert.equal(first.stopReason, "model_call");
    assert.equal(first.failure.kind, "model_call");
    assert.equal(second.ok, true);
    assert.equal(second.text, "Hello again.");
  });
});

// ---- limits and checks ---------------------------------------------------

function limitedTools(ran) {
  return [
    tool({
      name: "get_order",
      description: "Look up an order by its number.",
      parameters: orderSchema,
      maxCallsPerTurn: 1,
      run: ({ order_number }) => {
        ran.push(["get_order", order_number]);
        return { status: "shipped" };
      },
    }),
    tool({
      name: "handoff_to_human",
      description: "Pass the conversation to a person.",
      parameters: { type: "object", properties: { summary: { type: "string" } }, required: ["summary"] },
      terminal: true,
      run: ({ summary }) => {
        ran.push(["handoff_to_human", summary]);
        return { ticket: "T-17" };
      },
    }),
  ];
}

function handoffWasReal(answer, toolCalls) {
  const said = answer.toLowerCase().includes("passed this to the team");
  const did = toolCalls.some((c) => c.name === "handoff_to_human" && !c.failed);
  return said && !did ? "You said this was passed to the team, but handoff_to_human was not called." : null;
}

test("tool limits and prices go out under the wire's names", async () => {
  const sent = await withAgent(
    {
      model: "echo-init",
      tools: limitedTools([]),
      prices: { input: 0.3, cachedInput: 0.006, output: 1.2 },
      budget: { usd: 0.03 },
    },
    async (agent) => JSON.parse((await agent.run("hi")).text),
  );

  const allowed = new Set([...FIXTURE.tool.required, ...FIXTURE.tool.optional]);
  for (const declared of sent.init.tools) {
    for (const key of Object.keys(declared)) assert.ok(allowed.has(key), `tool has ${key}`);
  }
  assert.equal(sent.init.tools[0].max_calls_per_turn, 1);
  assert.equal(sent.init.tools[1].terminal, true);
  assert.deepEqual(Object.keys(sent.init.prices).sort(), [...FIXTURE.prices].sort());
  assert.deepEqual(sent.turn.budget, { usd: 0.03 });
});

test("a dollar budget without prices is refused before anything is sent", async () => {
  await assert.rejects(
    () => Agent.start({ model: "answer", baseUrl: "http://x/v1", engine: FAKE, budget: { usd: 0.03 } }),
    /needs the agent's prices/,
  );
  await withAgent({ model: "answer" }, async (agent) => {
    await assert.rejects(() => agent.run("hi", { budget: { usd: 0.03 } }), /needs the agent's prices/);
  });
});

test("bad tool limits are refused", () => {
  const base = { name: "t", description: "d", parameters: orderSchema, run: () => 1 };
  assert.throws(() => tool({ ...base, maxCallsPerTurn: 0 }), /positive whole number/);
  assert.throws(() => tool({ ...base, terminal: "yes" }), /true or false/);
});

async function withLimitedAgent(options, body) {
  const model = await modelServer();
  const ran = [];
  const agent = await Agent.start({
    model: "host-model",
    baseUrl: model.baseUrl,
    systemPrompt: "You answer questions about orders.",
    tools: limitedTools(ran),
    engine: REAL,
    ...options,
  });
  try {
    return await body(agent, model, ran);
  } finally {
    await agent.close();
    await model.close();
  }
}

test("real engine: a tool past its per-turn limit is not run", real, async () => {
  await withLimitedAgent({}, async (agent, model, ran) => {
    model.call("get_order", { order_number: "1001" });
    model.call("get_order", { order_number: "2002" });
    model.say("Order 1001 has shipped.");

    const turn = await agent.run("Where are my orders?");

    assert.deepEqual(ran, [["get_order", "1001"]]);
    assert.equal(turn.text, "Order 1001 has shipped.");
    assert.equal(turn.toolCalls[1].result.status, "refused");
  });
});

test("real engine: after a terminal tool no other tool runs", real, async () => {
  await withLimitedAgent({}, async (agent, model, ran) => {
    model.call("handoff_to_human", { summary: "empty box" });
    model.call("get_order", { order_number: "1001" });
    model.say("A colleague will email you today.");

    const turn = await agent.run("my box was empty");

    assert.deepEqual(ran, [["handoff_to_human", "empty box"]]);
    assert.equal(turn.text, "A colleague will email you today.");
  });
});

test("real engine: a dollar budget stops the turn at the agent's prices", real, async () => {
  await withLimitedAgent({ prices: { input: 1000, cachedInput: 0, output: 10000 } }, async (agent, model, ran) => {
    model.call("get_order", { order_number: "1001" });
    model.say("never asked for");

    const turn = await agent.run("Where is order 1001?", { budget: { usd: 0.1 } });

    assert.equal(turn.stopReason, "cost");
    assert.ok(Math.abs(turn.costUsd - 0.17) < 1e-9);
    assert.deepEqual(ran, []);
  });
});

test("real engine: a claim without the call is sent back once, then backed by a real call", real, async () => {
  await withLimitedAgent({ answerChecks: [handoffWasReal] }, async (agent, model, ran) => {
    model.say("I've passed this to the team.");
    model.call("handoff_to_human", { summary: "empty box" });
    model.say("I've passed this to the team.");

    const turn = await agent.run("my box was empty");

    assert.deepEqual(ran, [["handoff_to_human", "empty box"]]);
    assert.equal(turn.ok, true);
    assert.deepEqual(turn.unresolvedChecks, []);
    assert.equal(turn.usage.modelCalls, 3);
    assert.equal(turn.costUsd, null);
  });
});

test("real engine: a claim the model never backs is returned for the host to withhold", real, async () => {
  await withLimitedAgent({ answerChecks: [handoffWasReal] }, async (agent, model) => {
    model.say("I've passed this to the team.");
    model.say("I've passed this to the team.");

    const turn = await agent.run("my box was empty");

    assert.equal(turn.ok, false);
    assert.equal(turn.status, "incomplete");
    assert.equal(turn.stopReason, "unresolved_checks");
    assert.equal(turn.unresolvedChecks.length, 1);
    assert.equal(model.requests.length, 2);
  });
});

// ---- tools, hosts and providers that misbehave ----------------------------

function oddTools(gate) {
  const circular = {};
  circular.self = circular;
  const results = {
    circular: () => circular,
    bigint: () => ({ total: 10n }),
    thrower: () => ({
      toJSON() {
        throw new Error("cannot serialise");
      },
    }),
    undefined: () => undefined,
    function: () => () => 1,
    symbol: () => Symbol("x"),
    nojson: () => ({
      toJSON() {
        return undefined;
      },
    }),
    nan: () => ({ total: Number.NaN }),
    text: () => "plain text",
    huge: () => ({ rows: Array.from({ length: 2000 }, () => "x".repeat(1000)) }),
  };
  return [
    tool({
      name: "lookup",
      description: "Look something up in a system that answers oddly.",
      parameters: { type: "object", properties: { kind: { type: "string" } }, required: ["kind"] },
      run: async ({ kind }) => {
        if (gate) await gate();
        if (kind === "raise") throw new Error("archive offline");
        return results[kind]();
      },
    }),
  ];
}

async function withOddAgent(options, body) {
  const model = await modelServer();
  const { gate, ...rest } = options;
  const agent = await Agent.start({
    model: "host-model",
    baseUrl: model.baseUrl,
    systemPrompt: "You answer questions about orders.",
    tools: oddTools(gate),
    engine: REAL,
    ...rest,
  });
  try {
    return await body(agent, model);
  } finally {
    await agent.close();
    await model.close();
  }
}

for (const kind of ["circular", "bigint", "thrower", "function", "symbol", "nojson"]) {
  test(`real engine: a tool result JSON cannot hold (${kind}) is a tool error the model is told of`, real, async () => {
    await withOddAgent({}, async (agent, model) => {
      model.call("lookup", { kind });
      model.say("I could not read that.");
      model.say("Second answer.");

      const turn = await agent.run("look it up");
      const after = await agent.run("and again");

      assert.equal(turn.text, "I could not read that.");
      assert.equal(turn.toolCalls[0].failed, true);
      assert.match(turn.toolCalls[0].result.error, /cannot be sent to the model as JSON/);
      assert.match(JSON.stringify(model.requests[1].messages), /cannot be sent to the model as JSON/);
      assert.equal(after.text, "Second answer.");
    });
  });
}

for (const kind of ["undefined", "nan", "text", "huge"]) {
  test(`real engine: a tool result of any JSON shape (${kind}) reaches the model`, real, async () => {
    await withOddAgent({}, async (agent, model) => {
      model.call("lookup", { kind });
      model.say("Done.");

      const turn = await agent.run("look it up");

      assert.equal(turn.text, "Done.");
      assert.equal(turn.toolCalls[0].failed, false);
    });
  });
}

test("real engine: a callback that throws does not hand its answer to the next turn", real, async () => {
  let seen = 0;
  const onModelCall = () => {
    if (++seen === 1) throw new Error("metrics sink down");
  };
  await withOddAgent({ onModelCall }, async (agent, model) => {
    model.say("First answer.");
    model.say("Second answer.");

    await assert.rejects(() => agent.run("one"), /metrics sink down/);
    const second = await agent.run("two");

    assert.equal(second.text, "Second answer.");
    assert.equal(model.requests.length, 2);
  });
});

test("real engine: a second run before the first resolves is told the agent is busy", real, async () => {
  let release;
  let entered;
  const inTool = new Promise((resolve) => (entered = resolve));
  const gate = () => {
    entered();
    return new Promise((resolve) => (release = resolve));
  };
  await withOddAgent({ gate }, async (agent, model) => {
    model.call("lookup", { kind: "text" });
    model.say("First answer.");
    model.say("Second answer.");

    const first = agent.run("one");
    await inTool;
    await assert.rejects(() => agent.run("two"), AgentBusy);
    release();

    assert.equal((await first).text, "First answer.");
    assert.equal((await agent.run("two")).text, "Second answer.");
  });
});

test("real engine: a closed agent says so", real, async () => {
  const model = await modelServer();
  const agent = await Agent.start({ model: "m", baseUrl: model.baseUrl, engine: REAL });
  await agent.close();

  await assert.rejects(() => agent.run("hi"), /This agent was closed/);
  await model.close();
});

test("real engine: a turn the engine refuses leaves the agent usable", real, async () => {
  await withOddAgent({}, async (agent, model) => {
    model.say("Hello.");

    await assert.rejects(
      () => agent.run([{ role: "assistant", content: "I spoke last" }]),
      (error) => error instanceof EngineError && error.code === "bad_turn",
    );
    await assert.rejects(() => agent.run(["hi"]), TypeError);

    assert.equal((await agent.run("hi")).text, "Hello.");
  });
});

test("real engine: a request still in flight at the deadline is cut off", real, async () => {
  await withOddAgent({}, async (agent, model) => {
    model.say("far too late");
    model.slow(15000);
    model.say("On time.");

    const started = Date.now();
    const turn = await agent.run("hi", { budget: { seconds: 1 } });
    const waited = (Date.now() - started) / 1000;
    const after = await agent.run("hi");

    assert.equal(turn.status, "incomplete");
    assert.equal(turn.stopReason, "deadline");
    assert.equal(turn.text, "");
    assert.ok(waited < 8, `waited ${waited.toFixed(1)}s for a 1s deadline`);
    assert.equal(after.text, "On time.");
  });
});

test("real engine: a long run of mixed turns keeps each answer with its own turn", real, async () => {
  await withOddAgent({}, async (agent, model) => {
    for (let round = 0; round < 30; round++) {
      const label = `answer ${round}`;
      const kind = round % 4;
      if (kind === 0) {
        model.say(label);
        assert.equal((await agent.run("hi")).text, label);
      } else if (kind === 1) {
        model.call("lookup", { kind: "circular" });
        model.say(label);
        const turn = await agent.run("hi");
        assert.equal(turn.text, label);
        assert.equal(turn.toolCalls[0].failed, true);
      } else if (kind === 2) {
        model.fail(503);
        const turn = await agent.run("hi");
        assert.equal(turn.stopReason, "model_call");
      } else {
        model.call("lookup", { kind: "text" });
        const turn = await agent.run("hi", { budget: { modelCalls: 1 } });
        assert.equal(turn.stopReason, "model_calls");
      }
      assert.equal(model.replies.length, 0, `round ${round} left a reply unread`);
    }
  });
});

test("real engine: the hook is told what the turn has spent before each request", real, async () => {
  const seen = [];
  await withOddAgent({ beforeModelCall: (request) => void seen.push(request) }, async (agent, model) => {
    model.call("lookup", { kind: "text" });
    model.say("Done.");

    const turn = await agent.run("look it up", { budget: { seconds: 60 } });

    assert.equal(turn.text, "Done.");
    assert.deepEqual(
      seen.map((r) => [r.modelCalls, r.outputTokens]),
      [
        [0, 0],
        [1, 7],
      ],
    );
    assert.ok(seen[1].secondsLeft > 0 && seen[1].secondsLeft <= 60);
    assert.equal(turn.hostStop, null);
  });
});

test("real engine: StopTurn ends the turn without another request", real, async () => {
  let stop = true;
  const beforeModelCall = async (request) => {
    if (stop && request.modelCalls === 1) throw new StopTurn("account is over its rate limit");
  };
  await withOddAgent({ beforeModelCall }, async (agent, model) => {
    model.call("lookup", { kind: "text" });
    model.say("Next turn.");

    const turn = await agent.run("look it up");
    const sent = model.requests.length;
    stop = false;
    const after = await agent.run("hi");

    assert.equal(turn.status, "incomplete");
    assert.equal(turn.stopReason, "host_stop");
    assert.equal(turn.hostStop, "account is over its rate limit");
    assert.equal(turn.text, "");
    assert.equal(sent, 1);
    assert.equal(after.text, "Next turn.");
  });
});

test("real engine: a hook that throws ends the turn and rejects after it", real, async () => {
  let calls = 0;
  const beforeModelCall = () => {
    if (++calls === 1) throw new Error("limiter unreachable");
  };
  await withOddAgent({ beforeModelCall }, async (agent, model) => {
    model.say("Second answer.");

    await assert.rejects(() => agent.run("one"), /limiter unreachable/);
    assert.equal(model.requests.length, 0);

    assert.equal((await agent.run("two")).text, "Second answer.");
  });
});

test("real engine: cancel during a tool call ends the turn cancelled", real, async () => {
  let release;
  let entered;
  const inTool = new Promise((resolve) => (entered = resolve));
  const gate = () => {
    entered();
    return new Promise((resolve) => (release = resolve));
  };
  await withOddAgent({ gate }, async (agent, model) => {
    model.call("lookup", { kind: "text" });
    model.say("Next turn.");

    const first = agent.run("one");
    await inTool;
    agent.cancel();
    release();
    const turn = await first;

    assert.equal(turn.status, "cancelled");
    assert.equal(turn.stopReason, "cancelled");
    assert.equal(turn.text, "");
    assert.equal(model.requests.length, 1);
  });
});

test("real engine: cancel with no turn running does not stop the next one", real, async () => {
  await withOddAgent({}, async (agent, model) => {
    model.say("Hello.");

    agent.cancel();

    assert.equal((await agent.run("hi")).text, "Hello.");
  });
});
