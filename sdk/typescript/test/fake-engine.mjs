// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A stand-in for `gaiacpp --engine` that plays a scenario named in the model
// id, so the SDK's handling of the wire can be tested where no engine binary
// is built. It is not a model of the engine's behaviour: those tests use the
// real binary.

import { createInterface } from "node:readline";

const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();

function send(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

async function receive() {
  const next = await lines.next();
  return next.done ? null : JSON.parse(next.value);
}

function final(turn, fields = {}) {
  send({
    type: "final",
    turn,
    status: "success",
    stop_reason: "answered",
    text: "",
    tool_calls: [],
    usage: { model_calls: 1, input_tokens: 100, cached_tokens: 0, output_tokens: 7 },
    seconds: 0.01,
    failure: null,
    ...fields,
  });
}

if (process.argv.slice(2).join(" ") !== "--engine") {
  process.stderr.write(`fake engine started with ${process.argv.slice(2)}\n`);
  process.exit(64);
}

const init = await receive();
const scenario = init.model;
if (scenario === "refuse-init") {
  send({ type: "error", code: "bad_init", message: "refused on purpose" });
  process.exit(2);
} else if (scenario === "old-protocol") {
  send({ type: "ready", protocol: 0, version: "fake" });
} else if (scenario === "die-at-init") {
  process.exit(3);
} else {
  send({ type: "ready", protocol: init.protocol, version: "fake" });
}

for (;;) {
  const message = await receive();
  if (message === null || message.type === "shutdown") process.exit(0);
  const turn = message.id;
  if (scenario === "echo-init") {
    final(turn, { text: JSON.stringify({ init, turn: message }) });
  } else if (scenario === "call-each-tool") {
    const calls = [];
    let index = 0;
    for (const item of init.tools) {
      const args = { order_number: "1001" };
      send({ type: "tool_request", id: `${turn}:${++index}`, turn, name: item.name, arguments: args });
      const answer = await receive();
      calls.push({
        name: item.name,
        arguments: args,
        result: "result" in answer ? answer.result : answer.error,
        failed: "error" in answer,
      });
    }
    final(turn, { text: "done", tool_calls: calls });
  } else if (scenario === "unknown-tool") {
    send({ type: "tool_request", id: `${turn}:1`, turn, name: "no_such_tool", arguments: {} });
    final(turn, { text: JSON.stringify(await receive()) });
  } else if (scenario === "bad-turn") {
    send({ type: "error", code: "bad_turn", message: "refused on purpose" });
  } else if (scenario === "die-mid-turn") {
    process.exit(9);
  } else if (scenario === "new-event") {
    send({ type: "from_the_future", turn });
  } else {
    send({
      type: "model_call",
      turn,
      step: 1,
      seconds: 0.01,
      usage: { input_tokens: 100, cached_tokens: 0, output_tokens: 7 },
    });
    final(turn, { text: "Hello there." });
  }
}
