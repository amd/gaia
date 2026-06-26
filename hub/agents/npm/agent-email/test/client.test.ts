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
      { description: "Open the dashboard", type: "link", url: "https://example.com/dashboard" },
    ],
    suggested_action: "reply",
    draft: { to: [{ email: "a@b.com" }], subject: "Re: x", body: "" },
    message_id: "m1",
    usage: { prompt_tokens: 120, completion_tokens: 40, total_tokens: 160, tokens_per_second: 32.5 },
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
    expect(res.result.action_items[0]?.due_hint).toBe("by friday");
    expect(res.result.action_items[0]?.type).toBe("text");
    expect(res.result.suggested_action).toBe("reply");
    expect(res.result.usage?.total_tokens).toBe(160);
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

  it("confirm → archive → unarchive round-trip (paths + payloads)", async () => {
    const seen: string[] = [];
    const fetchImpl = vi.fn(async (url, init) => {
      const path = String(url);
      seen.push(path);
      if (path.endsWith("/v1/email/confirm")) {
        const parsed = JSON.parse(String(init?.body));
        expect(parsed.action).toBe("archive");
        expect(parsed.message_id).toBe("m1");
        return jsonResponse({
          schema_version: "2.1",
          confirmation_token: "atok",
          action: "archive",
          message_id: "m1",
        });
      }
      if (path.endsWith("/v1/email/archive")) {
        expect(JSON.parse(String(init?.body)).confirmation_token).toBe("atok");
        return jsonResponse({
          schema_version: "2.1",
          message_id: "m1",
          action_id: "act-1",
          batch_id: "batch-1",
          post_archive_id: "m1",
          undo_window_seconds: 30,
          archived: true,
        });
      }
      // unarchive
      expect(JSON.parse(String(init?.body)).batch_id).toBe("batch-1");
      return jsonResponse({
        schema_version: "2.1",
        batch_id: "batch-1",
        restored: 1,
        messages: [{ message_id: "m1", action_id: "act-1" }],
        failed: [],
        undone: true,
      });
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const c = await client.confirmAction({ action: "archive", message_id: "m1" });
    expect(c.confirmation_token).toBe("atok");
    const a = await client.archive({
      message_id: "m1",
      confirmation_token: c.confirmation_token,
    });
    expect(a.archived).toBe(true);
    expect(a.batch_id).toBe("batch-1");
    expect(a.post_archive_id).toBe("m1");
    const u = await client.unarchive({ batch_id: a.batch_id });
    expect(u.restored).toBe(1);
    expect(u.messages[0]?.message_id).toBe("m1");
    expect(seen).toEqual([
      "http://x/v1/email/confirm",
      "http://x/v1/email/archive",
      "http://x/v1/email/unarchive",
    ]);
  });

  it("quarantine + unquarantine paths and types", async () => {
    let call = 0;
    const fetchImpl = vi.fn(async (url) => {
      call += 1;
      if (String(url).endsWith("/v1/email/quarantine")) {
        return jsonResponse({
          schema_version: "2.1",
          message_id: "m2",
          action_id: "act-9",
          quarantine_label_id: "Label_3",
          prior_labels: ["INBOX"],
          undo_window_seconds: 30,
          quarantined: true,
        });
      }
      return jsonResponse({
        schema_version: "2.1",
        action_id: "act-9",
        message_id: "m2",
        restored: true,
      });
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const q = await client.quarantine({ message_id: "m2", is_phishing: true });
    expect(q.quarantined).toBe(true);
    expect(q.quarantine_label_id).toBe("Label_3");
    const uq = await client.unquarantine({ action_id: q.action_id });
    expect(uq.restored).toBe(true);
    expect(uq.message_id).toBe("m2");
    expect(call).toBe(2);
  });

  it("archive without a token surfaces the 403 gate as HttpError", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "archive rejected: missing confirmation token" }, 403),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(client.archive({ message_id: "m1" })).rejects.toMatchObject({
      status: 403,
    });
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

  it("hits the router-scoped health/version paths", async () => {
    const seen: string[] = [];
    const fetchImpl = vi.fn(async (url) => {
      seen.push(String(url));
      return String(url).endsWith("/version")
        ? jsonResponse({ apiVersion: "2.0", agentVersion: "0.2.0" })
        : jsonResponse({ status: "ok", service: "gaia-agent-email" });
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131", fetchImpl });
    const h = await client.emailHealth();
    const v = await client.emailVersion();
    expect(h.status).toBe("ok");
    expect(v.apiVersion).toBe("2.0");
    expect(seen).toEqual([
      "http://127.0.0.1:8131/v1/email/health",
      "http://127.0.0.1:8131/v1/email/version",
    ]);
  });

  it("fetches the OpenAPI document as a parsed object", async () => {
    const doc = {
      openapi: "3.1.0",
      info: { title: "GAIA Email Agent Sidecar", version: "0.2.0" },
      paths: { "/v1/email/triage": {} },
    };
    const fetchImpl = vi.fn(async (url) => {
      expect(String(url)).toBe("http://x/openapi.json");
      return jsonResponse(doc);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const api = await client.openapi();
    expect(api.openapi).toBe("3.1.0");
    expect(api.paths["/v1/email/triage"]).toBeDefined();
  });

  it("returns the spec page as raw HTML (not JSON-parsed)", async () => {
    const html = "<!doctype html><html><body><h1>Email Triage Agent</h1></body></html>";
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe("http://x/v1/email/spec");
      expect((init?.headers as Record<string, string>)?.accept).toBe("text/html");
      return new Response(html, { status: 200, headers: { "content-type": "text/html" } });
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const out = await client.spec();
    expect(out).toBe(html); // returned verbatim, no JSON.parse
  });

  it("spec() still raises HttpError on a non-2xx", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response("nope", { status: 404 }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(client.spec()).rejects.toMatchObject({ status: 404 });
  });

  it("invokes fetch bound to globalThis (browser 'Illegal invocation' guard)", async () => {
    // Regression: a browser's `fetch` throws "Illegal invocation" when called
    // as a method on any object other than window/globalThis. EmailClient must
    // bind the impl; a non-arrow fn captures its call-time `this`. Node tests
    // pass without the bind (which is why the original bug slipped through), so
    // this asserts the binding directly.
    let calledThis: unknown = "unset";
    const fetchImpl = function (this: unknown) {
      calledThis = this;
      return Promise.resolve(
        jsonResponse({ status: "ok", service: "gaia-agent-email" }),
      );
    } as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await client.health();
    expect(calledThis).toBe(globalThis);
  });
});
