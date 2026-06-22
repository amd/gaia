// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Client request/response typing + error behavior, against a fake fetch. */

import { describe, expect, it, vi } from "vitest";

import { EmailClient } from "../src/client.js";
import { HttpError } from "../src/errors.js";
import type {
  EmailTriageRequest,
  EmailTriageResponse,
  EmailDraftResponse,
  EmailSendResponse,
} from "../src/types.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const triageResponse: EmailTriageResponse = {
  schema_version: "2.0",
  request_kind: "single",
  result: {
    category: "NEEDS_RESPONSE",
    is_spam: false,
    is_phishing: false,
    summary: "Prod incident follow-up — please review.",
    action_items: [
      { description: "Review the report", due_hint: "by friday", type: "text" },
    ],
    suggested_action: "reply",
    draft: { to: [{ email: "a@b.com" }], subject: "Re: x", body: "" },
    message_id: "m1",
    usage: {
      prompt_tokens: 100,
      completion_tokens: 50,
      total_tokens: 150,
      tokens_per_second: 12.5,
    },
  },
};

describe("EmailClient", () => {
  it("sends a typed triage request and parses the typed response", async () => {
    const fetchImpl = vi.fn(async (_url, init) => {
      expect(init?.method).toBe("POST");
      const parsed = JSON.parse(String(init?.body));
      expect(parsed.payload.kind).toBe("single");
      // wire alias: `from`, not `from_`
      expect(parsed.payload.message.from.email).toBe("sarah@example.com");
      return jsonResponse(triageResponse);
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131/", fetchImpl });
    const req: EmailTriageRequest = {
      payload: {
        kind: "single",
        principal: { email: "me@example.com" },
        message: {
          message_id: "m1",
          from: { name: "Sarah Chen", email: "sarah@example.com" },
          subject: "Prod incident follow-up",
          body: "Please review the report by Friday.",
        },
      },
    };
    const res = await client.triage(req);
    expect(res.request_kind).toBe("single");
    expect(res.result.category).toBe("NEEDS_RESPONSE");
    expect(res.result.suggested_action).toBe("reply");
    expect(res.result.action_items[0]?.due_hint).toBe("by friday");
    expect(res.result.action_items[0]?.type).toBe("text");
    expect(res.result.usage?.total_tokens).toBe(150);
  });

  it("sends a triage request with context (schema 2.0 TriageContext)", async () => {
    const fetchImpl = vi.fn(async (_url, init) => {
      const parsed = JSON.parse(String(init?.body));
      expect(parsed.context.people).toContain("Alice");
      expect(parsed.context.tone).toBe("concise");
      return jsonResponse(triageResponse);
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const req: EmailTriageRequest = {
      payload: {
        kind: "single",
        principal: { email: "me@example.com" },
        message: {
          message_id: "m2",
          from: { email: "alice@example.com" },
          body: "Hi there",
        },
      },
      context: {
        people: ["Alice"],
        projects: ["Q3 launch"],
        tone: "concise",
        self_email: "me@example.com",
      },
    };
    const res = await client.triage(req);
    expect(res.result.category).toBe("NEEDS_RESPONSE");
  });

  it("normalizes a trailing slash in baseUrl", async () => {
    const fetchImpl = vi.fn(async (url) => {
      expect(String(url)).toBe("http://127.0.0.1:8131/health");
      return jsonResponse({ status: "ok", service: "gaia-agent-email" });
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131///", fetchImpl });
    const h = await client.health();
    expect(h.status).toBe("ok");
  });

  it("parses version", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ apiVersion: "2.0", agentVersion: "0.2.0" }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const v = await client.version();
    expect(v.apiVersion).toBe("2.0");
    expect(v.agentVersion).toBe("0.2.0");
  });

  it("draft + send round-trip types", async () => {
    const draftRes: EmailDraftResponse = {
      draft: { to: [{ email: "a@b.com" }], subject: "Re: x", body: "ok" },
      confirmation_token: "tok123",
    };
    const sendRes: EmailSendResponse = {
      sent_id: "sent-1",
      to: [{ email: "a@b.com" }],
      subject: "Re: x",
      sent: true,
    };
    let call = 0;
    const fetchImpl = vi.fn(async () =>
      jsonResponse(call++ === 0 ? draftRes : sendRes),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const d = await client.draft({ to: [{ email: "a@b.com" }], subject: "Re: x", body: "ok" });
    expect(d.confirmation_token).toBe("tok123");
    const s = await client.send({
      to: [{ email: "a@b.com" }],
      subject: "Re: x",
      body: "ok",
      confirmation_token: d.confirmation_token,
    });
    expect(s.sent).toBe(true);
    expect(s.sent_id).toBe("sent-1");
  });

  it("raises HttpError on a non-2xx (no silent fallback)", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "send rejected: missing token" }, 403),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(
      client.send({ to: [{ email: "a@b.com" }], subject: "s", body: "b" }),
    ).rejects.toBeInstanceOf(HttpError);
    await expect(
      client.send({ to: [{ email: "a@b.com" }], subject: "s", body: "b" }),
    ).rejects.toMatchObject({ status: 403 });
  });

  it("raises HttpError on a network failure", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://127.0.0.1:9", fetchImpl });
    await expect(client.health()).rejects.toBeInstanceOf(HttpError);
  });
});
