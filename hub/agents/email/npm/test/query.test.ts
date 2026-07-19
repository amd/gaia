// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * EmailClient.query() / cancelQuery() unit tests against a fake fetch — the
 * typed client surface of the frozen /query SSE contract (schema 2.4, #2097).
 * Asserts the OUTGOING call shape (path, headers, bearer, body) as well as the
 * parsed event stream, so a contract-violating request can't hide behind a
 * stubbed 200.
 */

import { describe, expect, it, vi } from "vitest";

import { EmailClient } from "../src/client.js";
import { HttpError, QueryStreamError } from "../src/errors.js";
import type { QueryCancelResponse, QueryEvent } from "../src/types.js";

const RUN_ID = "0f9c2b6e-2c4a-4b1e-9d6a-1e2f3a4b5c6d";

function sseResponse(text: string, status = 200): Response {
  return new Response(text, {
    status,
    headers: { "content-type": "text/event-stream" },
  });
}

function sse(events: Array<Record<string, unknown>>): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

async function collect(iter: AsyncIterable<QueryEvent>): Promise<QueryEvent[]> {
  const out: QueryEvent[] = [];
  for await (const ev of iter) out.push(ev);
  return out;
}

const HAPPY_WIRE = sse([
  { type: "status", message: "Processing..." },
  { type: "tool_call", tool: "triage_inbox", args: { max_messages: 10 } },
  { type: "tool_result", tool: "triage_inbox", data: { ok: true, count: 5 } },
  { type: "final", answer: "Triaged 5 emails.", usage: { steps: 1 } },
]);

describe("EmailClient.query", () => {
  it("sends the spec-shaped request (path, SSE accept, bearer, body) and yields the typed sequence", async () => {
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe("http://127.0.0.1:8131/v1/email/query");
      expect(init?.method).toBe("POST");
      const headers = init?.headers as Record<string, string>;
      expect(headers.accept).toBe("text/event-stream");
      expect(headers["content-type"]).toBe("application/json");
      expect(headers.authorization).toBe("Bearer session-token");
      const body = JSON.parse(String(init?.body));
      expect(body).toEqual({
        query: "Triage my inbox",
        run_id: RUN_ID,
        context: [{ role: "user", content: "earlier turn" }],
        max_steps: 5,
      });
      return sseResponse(HAPPY_WIRE);
    }) as unknown as typeof fetch;

    const client = new EmailClient({
      baseUrl: "http://127.0.0.1:8131",
      fetchImpl,
      authToken: "session-token",
    });
    const events = await collect(
      client.query({
        query: "Triage my inbox",
        run_id: RUN_ID,
        context: [{ role: "user", content: "earlier turn" }],
        max_steps: 5,
      }),
    );

    expect(events.map((e) => e.type)).toEqual([
      "status",
      "tool_call",
      "tool_result",
      "final",
    ]);
    expect(events[0]).toEqual({ type: "status", message: "Processing..." });
    const call = events[1];
    if (call.type !== "tool_call") throw new Error("expected tool_call");
    expect(call.tool).toBe("triage_inbox");
    expect(call.args).toEqual({ max_messages: 10 });
    const final = events[3];
    if (final.type !== "final") throw new Error("expected final");
    expect(final.answer).toBe("Triaged 5 emails.");
    expect(final.usage?.steps).toBe(1);
  });

  it("reassembles events split across arbitrary transport chunks", async () => {
    // Deliver the happy wire in 7-byte chunks — framing must not matter.
    const bytes = new TextEncoder().encode(HAPPY_WIRE);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let i = 0; i < bytes.length; i += 7) {
          controller.enqueue(bytes.slice(i, i + 7));
        }
        controller.close();
      },
    });
    const fetchImpl = vi.fn(
      async () =>
        new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    ) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const events = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    );
    expect(events.map((e) => e.type)).toEqual([
      "status",
      "tool_call",
      "tool_result",
      "final",
    ]);
  });

  it("reassembles a multibyte character split across chunk boundaries", async () => {
    const wire = sse([
      { type: "token", delta: "café ☕" },
      { type: "final", answer: "done ✓" },
    ]);
    const bytes = new TextEncoder().encode(wire);
    // 3-byte chunks guarantee the multibyte sequences are split mid-character.
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let i = 0; i < bytes.length; i += 3) {
          controller.enqueue(bytes.slice(i, i + 3));
        }
        controller.close();
      },
    });
    const fetchImpl = vi.fn(
      async () =>
        new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    ) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const events = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    );
    expect(events[0]).toEqual({ type: "token", delta: "café ☕" });
    expect(events[1]).toEqual({ type: "final", answer: "done ✓" });
  });

  it("surfaces an unknown event type as {type:'unknown'} instead of dropping it", async () => {
    const wire = sse([
      { type: "status", message: "working" },
      { type: "reasoning_trace", text: "a future additive event" },
      { type: "final", answer: "done" },
    ]);
    const fetchImpl = vi.fn(async () => sseResponse(wire)) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    const events = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    );
    expect(events.map((e) => e.type)).toEqual(["status", "unknown", "final"]);
    const unknown = events[1];
    if (unknown.type !== "unknown") throw new Error("expected unknown");
    expect(unknown.eventType).toBe("reasoning_trace");
    expect(unknown.raw).toEqual({
      type: "reasoning_trace",
      text: "a future additive event",
    });
  });

  it("yields a terminal error event (surfaced verbatim, not thrown) and ends the stream", async () => {
    const wire = sse([
      { type: "status", message: "working" },
      { type: "error", detail: "Lemonade Server is not reachable", status: 502 },
    ]);
    const fetchImpl = vi.fn(async () => sseResponse(wire)) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    const events = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    );
    expect(events.map((e) => e.type)).toEqual(["status", "error"]);
    const err = events[1];
    if (err.type !== "error") throw new Error("expected error");
    expect(err.detail).toContain("not reachable");
    expect(err.status).toBe(502);
  });

  it("stops iterating after the terminal event even if more data follows", async () => {
    const wire = sse([
      { type: "final", answer: "done" },
      { type: "status", message: "should never be seen" },
    ]);
    const fetchImpl = vi.fn(async () => sseResponse(wire)) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    const events = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    );
    expect(events.map((e) => e.type)).toEqual(["final"]);
  });

  it("throws QueryStreamError when the stream closes without a terminal event", async () => {
    const wire = sse([{ type: "status", message: "working" }]);
    const fetchImpl = vi.fn(async () => sseResponse(wire)) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    await expect(
      collect(client.query({ query: "q", run_id: RUN_ID, context: [] })),
    ).rejects.toThrow(QueryStreamError);
  });

  it("throws QueryStreamError on a malformed (non-JSON) event payload", async () => {
    const fetchImpl = vi.fn(async () =>
      sseResponse("data: not-json{\n\n"),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    await expect(
      collect(client.query({ query: "q", run_id: RUN_ID, context: [] })),
    ).rejects.toThrow(QueryStreamError);
  });

  it("throws QueryStreamError on an event missing its type discriminator", async () => {
    const fetchImpl = vi.fn(async () =>
      sseResponse('data: {"message":"no type"}\n\n'),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    await expect(
      collect(client.query({ query: "q", run_id: RUN_ID, context: [] })),
    ).rejects.toThrow(QueryStreamError);
  });

  it("throws HttpError (with the body) on a non-2xx response", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "run_id 'x' is already in flight." }), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    const err = await collect(
      client.query({ query: "q", run_id: RUN_ID, context: [] }),
    ).catch((e) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(409);
    expect((err as HttpError).bodyText).toContain("already in flight");
  });

  it("throws QueryStreamError when the response is not text/event-stream", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    await expect(
      collect(client.query({ query: "q", run_id: RUN_ID, context: [] })),
    ).rejects.toThrow(QueryStreamError);
  });

  it("cancels the transport when the consumer breaks out early", async () => {
    const cancelled = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('data: {"type":"status","message":"1"}\n\n'),
        );
        // Stream intentionally left open — only a consumer break releases it.
      },
      cancel: cancelled,
    });
    const fetchImpl = vi.fn(
      async () =>
        new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    for await (const ev of client.query({ query: "q", run_id: RUN_ID, context: [] })) {
      expect(ev.type).toBe("status");
      break; // consumer walks away mid-run
    }
    expect(cancelled).toHaveBeenCalled();
  });
});

describe("EmailClient.cancelQuery", () => {
  it("POSTs to /v1/email/query/{run_id}/cancel with the bearer and parses the result", async () => {
    const cancelResponse: QueryCancelResponse = {
      run_id: RUN_ID,
      cancelled: true,
      status: "ok",
    };
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe(
        `http://127.0.0.1:8131/v1/email/query/${RUN_ID}/cancel`,
      );
      expect(init?.method).toBe("POST");
      const headers = init?.headers as Record<string, string>;
      expect(headers.authorization).toBe("Bearer session-token");
      return new Response(JSON.stringify(cancelResponse), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const client = new EmailClient({
      baseUrl: "http://127.0.0.1:8131",
      fetchImpl,
      authToken: "session-token",
    });
    const res = await client.cancelQuery(RUN_ID);
    expect(res.cancelled).toBe(true);
    expect(res.run_id).toBe(RUN_ID);
  });

  it("throws HttpError 404 for a run that is not in flight", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "No in-flight run" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });

    const err = await client.cancelQuery(RUN_ID).catch((e) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
  });

  it("refuses an empty run_id loudly", async () => {
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl: vi.fn() as never });
    await expect(client.cancelQuery("")).rejects.toThrow(TypeError);
  });
});
