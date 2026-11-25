// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// One turn through an installed @amd/gaia-sdk package, with nothing else
// installed. Run it from a folder whose node_modules holds only the packed
// tarball:
//
//   node smoke-installed.mjs
//
// It fails unless the SDK resolved from node_modules and the engine came from
// inside the package.

import { createServer } from "node:http";
import { createRequire } from "node:module";
import { dirname, sep } from "node:path";

delete process.env.GAIA_ENGINE;

const { Agent, findEngine, tool } = await import("@amd/gaia-sdk");

const replies = [
  {
    role: "assistant",
    content: null,
    tool_calls: [
      { id: "c1", type: "function", function: { name: "get_order", arguments: '{"order_number":"1001"}' } },
    ],
  },
  { role: "assistant", content: "It has shipped." },
];

const installed = dirname(dirname(createRequire(import.meta.url).resolve("@amd/gaia-sdk")));
if (!installed.includes(`node_modules${sep}@amd${sep}gaia-sdk`)) {
  console.error(`FAIL: the SDK resolved to ${installed}, not an installed package`);
  process.exit(1);
}
const [engine] = findEngine();
if (!engine.startsWith(installed)) {
  console.error(`FAIL: the engine is ${engine}, not the one inside the package`);
  process.exit(1);
}

const server = createServer((req, res) => {
  req.resume();
  req.on("end", () => {
    const payload = JSON.stringify({
      choices: [{ message: replies.shift() }],
      usage: { prompt_tokens: 100, completion_tokens: 7 },
    });
    res.writeHead(200, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) });
    res.end(payload);
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

const agent = await Agent.start({
  model: "host-model",
  baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
  systemPrompt: "You answer questions about orders.",
  tools: [
    tool({
      name: "get_order",
      description: "Look up an order by its number.",
      parameters: { type: "object", properties: { order_number: { type: "string" } }, required: ["order_number"] },
      run: ({ order_number }) => ({ status: "shipped", order: order_number }),
    }),
  ],
});
let turn;
try {
  turn = await agent.run("Where is order 1001?");
} finally {
  await agent.close();
  await new Promise((resolve) => server.close(resolve));
}
if (!(turn.ok && turn.text === "It has shipped." && turn.toolCalls.length === 1)) {
  console.error(`FAIL: unexpected turn ${JSON.stringify(turn)}`);
  process.exit(1);
}
console.log(`OK: one turn through ${engine} (engine ${agent.engineVersion})`);
