// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Client request/response typing + error behavior, against a fake fetch. */

import { describe, expect, it, vi } from "vitest";

import { EmailClient } from "../src/client.js";
import { HttpError } from "../src/errors.js";
import type {
  BatchTriageRequest,
  BatchTriageResponse,
  EmailTriageRequest,
  EmailTriageResponse,
  EmailDraftResponse,
  EmailSearchResponse,
  EmailSendResponse,
  CalendarEventsResponse,
  CalendarEventPreviewResponse,
  CalendarEventResponse,
  CalendarRespondResponse,
  EmailPreScanResponse,
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

  it("sends a typed inbox-search request and parses the typed response", async () => {
    const searchResponse: EmailSearchResponse = {
      schema_version: "2.2",
      query: "is:unread",
      count: 1,
      messages: [
        {
          id: "m1",
          thread_id: "t-m1",
          subject: "Prod incident",
          from: "Sarah Chen <sarah@example.com>",
          to: "me@example.com",
          date: "Mon, 01 Jan 2026 10:00:00 +0000",
          snippet: "please review",
          label_ids: ["INBOX", "UNREAD"],
        },
      ],
      next_page_token: null,
    };
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe("http://127.0.0.1:8131/v1/email/search");
      expect(init?.method).toBe("POST");
      const parsed = JSON.parse(String(init?.body));
      expect(parsed.query).toBe("is:unread");
      expect(parsed.max_results).toBe(10);
      return jsonResponse(searchResponse);
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131", fetchImpl });
    const res = await client.search({ query: "is:unread", max_results: 10 });
    expect(res.count).toBe(1);
    expect(res.messages[0]?.id).toBe("m1");
    // Wire alias: the sender is `from`, not `from_`.
    expect(res.messages[0]?.from).toBe("Sarah Chen <sarah@example.com>");
    expect(res.messages[0]?.label_ids).toEqual(["INBOX", "UNREAD"]);
    expect(res.next_page_token).toBeNull();
  });

  it("surfaces a 503 from search (no mailbox connected) as HttpError", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "No mailbox connected" }, 503),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(client.search({ query: "x" })).rejects.toMatchObject({
      status: 503,
    });
  });

  it("sends a typed batch triage request to /triage/batch and parses results", async () => {
    const batchResponse: BatchTriageResponse = {
      schema_version: "2.0",
      results: [
        { index: 0, result: triageResponse.result },
        { index: 1, error: { message: "injected failure" } },
      ],
    };
    const fetchImpl = vi.fn(async (url, init) => {
      // The batch method must POST to the batch path with an `items` array.
      expect(String(url)).toContain("/v1/email/triage/batch");
      expect(init?.method).toBe("POST");
      const parsed = JSON.parse(String(init?.body));
      expect(Array.isArray(parsed.items)).toBe(true);
      expect(parsed.items).toHaveLength(2);
      expect(parsed.items[0].kind).toBe("single");
      return jsonResponse(batchResponse);
    }) as unknown as typeof fetch;

    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131", fetchImpl });
    const req: BatchTriageRequest = {
      items: [
        {
          kind: "single",
          principal: { email: "me@example.com" },
          message: {
            message_id: "m1",
            from: { email: "sarah@example.com" },
            subject: "Prod incident follow-up",
            body: "Please review by Friday.",
          },
        },
        {
          kind: "single",
          principal: { email: "me@example.com" },
          message: {
            message_id: "m2",
            from: { email: "promo@shop.example" },
            subject: "Sale",
            body: "Shop now.",
          },
        },
      ],
    };
    const res = await client.triageBatch(req);
    expect(res.results).toHaveLength(2);
    expect(res.results[0]?.index).toBe(0);
    expect(res.results[0]?.result?.category).toBe("NEEDS_RESPONSE");
    expect(res.results[1]?.error?.message).toBe("injected failure");
    expect(res.results[1]?.result ?? null).toBeNull();
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

  it("init() parses the ready (200) readiness preflight", async () => {
    const ready = {
      ready: true,
      lemonade: {
        reachable: true,
        base_url: "http://localhost:8000/api/v1",
        version: "8.1.0",
        min_version: "8.0.0",
        compatible: true,
      },
      model: { id: "Gemma-4-E4B-it-GGUF", present: true, loadable: null },
      hint: null,
    };
    const fetchImpl = vi.fn(async (url) => {
      expect(String(url)).toBe("http://x/v1/email/init");
      return jsonResponse(ready, 200);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const r = await client.init();
    expect(r.ready).toBe(true);
    expect(r.model.present).toBe(true);
    expect(r.hint).toBeNull();
  });

  it("init() returns the not-ready body on 503 instead of throwing", async () => {
    const notReady = {
      ready: false,
      lemonade: {
        reachable: false,
        base_url: "http://localhost:8000/api/v1",
        version: null,
        min_version: "8.0.0",
        compatible: null,
      },
      model: { id: "Gemma-4-E4B-it-GGUF", present: false, loadable: null },
      hint: "Lemonade Server not reachable — run `lemonade-server serve`.",
    };
    const fetchImpl = vi.fn(async () =>
      jsonResponse(notReady, 503),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const r = await client.init();
    expect(r.ready).toBe(false);
    expect(r.lemonade.reachable).toBe(false);
    expect(r.hint).toMatch(/lemonade-server serve/);
  });

  it("init() still throws HttpError on a non-503 failure", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response("upstream boom", { status: 500 }),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(client.init()).rejects.toBeInstanceOf(HttpError);
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
          schema_version: "2.2",
          confirmation_token: "atok",
          action: "archive",
          message_id: "m1",
        });
      }
      if (path.endsWith("/v1/email/archive")) {
        expect(JSON.parse(String(init?.body)).confirmation_token).toBe("atok");
        return jsonResponse({
          schema_version: "2.2",
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
        schema_version: "2.2",
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
          schema_version: "2.2",
          message_id: "m2",
          action_id: "act-9",
          quarantine_label_id: "Label_3",
          prior_labels: ["INBOX"],
          undo_window_seconds: 30,
          quarantined: true,
        });
      }
      return jsonResponse({
        schema_version: "2.2",
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

  it("lists calendar events with optional query params (GET)", async () => {
    const calResponse: CalendarEventsResponse = {
      schema_version: "2.2",
      events: [
        {
          id: "evt-1",
          summary: "Standup",
          start: "2026-07-01T09:00:00Z",
          end: "2026-07-01T09:15:00Z",
          location: "Zoom",
          organizer: "lead@example.com",
        },
      ],
    };
    let seenUrl = "";
    const fetchImpl = vi.fn(async (url, init) => {
      seenUrl = String(url);
      expect(init?.method).toBe("GET");
      return jsonResponse(calResponse);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131", fetchImpl });
    const res = await client.listCalendarEvents({
      timeMin: "2026-07-01T00:00:00Z",
      timeMax: "2026-07-02T00:00:00Z",
      provider: "google",
    });
    expect(res.events[0]?.id).toBe("evt-1");
    expect(seenUrl).toContain("/v1/email/calendar/events?");
    expect(seenUrl).toContain("time_min=2026-07-01T00%3A00%3A00Z");
    expect(seenUrl).toContain("time_max=2026-07-02T00%3A00%3A00Z");
    expect(seenUrl).toContain("provider=google");
  });

  it("omits the query string when no calendar filters are given", async () => {
    let seenUrl = "";
    const fetchImpl = vi.fn(async (url) => {
      seenUrl = String(url);
      return jsonResponse({ schema_version: "2.2", events: [] });
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await client.listCalendarEvents();
    expect(seenUrl).toBe("http://x/v1/email/calendar/events");
  });

  it("calendar preview → create round-trip (confirmation token)", async () => {
    const previewRes: CalendarEventPreviewResponse = {
      schema_version: "2.2",
      summary: "Project sync",
      start: { date_time: "2026-07-01T14:00:00Z" },
      end: { date_time: "2026-07-01T15:00:00Z" },
      attendees: ["alice@example.com"],
      confirmation_token: "cal-tok-1",
    };
    const createRes: CalendarEventResponse = {
      schema_version: "2.2",
      event_id: "evt-created-1",
      summary: "Project sync",
      created: true,
    };
    const seenPaths: string[] = [];
    let call = 0;
    const fetchImpl = vi.fn(async (url, init) => {
      seenPaths.push(String(url));
      expect(init?.method).toBe("POST");
      return jsonResponse(call++ === 0 ? previewRes : createRes);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const preview = await client.previewCalendarEvent({
      summary: "Project sync",
      start: { date_time: "2026-07-01T14:00:00Z" },
      end: { date_time: "2026-07-01T15:00:00Z" },
      attendees: ["alice@example.com"],
    });
    expect(preview.confirmation_token).toBe("cal-tok-1");
    const created = await client.createCalendarEvent({
      summary: "Project sync",
      start: { date_time: "2026-07-01T14:00:00Z" },
      end: { date_time: "2026-07-01T15:00:00Z" },
      attendees: ["alice@example.com"],
      confirmation_token: preview.confirmation_token,
    });
    expect(created.event_id).toBe("evt-created-1");
    expect(created.created).toBe(true);
    expect(seenPaths).toEqual([
      "http://x/v1/email/calendar/events/preview",
      "http://x/v1/email/calendar/events",
    ]);
  });

  it("create without a token raises HttpError 403 (no silent fallback)", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "Create rejected: missing confirmation_token" }, 403),
    ) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    await expect(
      client.createCalendarEvent({
        summary: "x",
        start: { date_time: "2026-07-01T14:00:00Z" },
        end: { date_time: "2026-07-01T15:00:00Z" },
      }),
    ).rejects.toMatchObject({ status: 403 });
  });

  it("responds to a calendar invite (RSVP)", async () => {
    const respondRes: CalendarRespondResponse = {
      schema_version: "2.2",
      event_id: "evt-1",
      status: "accepted",
      responded: true,
    };
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe("http://x/v1/email/calendar/events/respond");
      const parsed = JSON.parse(String(init?.body));
      expect(parsed.status).toBe("accepted");
      expect(parsed.attendee_email).toBe("me@example.com");
      return jsonResponse(respondRes);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const res = await client.respondToCalendarEvent({
      event_id: "evt-1",
      status: "accepted",
      attendee_email: "me@example.com",
    });
    expect(res.responded).toBe(true);
    expect(res.status).toBe("accepted");
  });

  it("pre-scans the inbox and parses the card envelope", async () => {
    const preScanRes: EmailPreScanResponse = {
      schema_version: "2.2",
      result: {
        kind: "email_pre_scan",
        urgent: [
          { message_id: "u1", sender: "boss@corp.com", subject: "Prod down", why: "urgent label" },
        ],
        actionable: [],
        informational_count: 3,
        suggested_archives: [
          { message_id: "a1", thread_id: "t-a1", sender: "deals@shop.com", subject: "Sale", reason: "promotional" },
        ],
        suggested_drafts: [],
        preferences_applied: { priority_senders: [], low_priority_senders: [], category_defaults: {} },
        totals: { urgent: 1, actionable: 0, informational: 3, suggested_archives: 1 },
      },
    };
    const fetchImpl = vi.fn(async (url, init) => {
      expect(String(url)).toBe("http://x/v1/email/prescan");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body)).max_messages).toBe(10);
      return jsonResponse(preScanRes);
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const res = await client.prescan({ max_messages: 10 });
    expect(res.schema_version).toBe("2.2");
    expect(res.result.kind).toBe("email_pre_scan");
    expect(res.result.urgent[0]?.message_id).toBe("u1");
    expect(res.result.informational_count).toBe(3);
    expect(res.result.suggested_archives[0]?.reason).toBe("promotional");
    expect(res.result.totals?.suggested_archives).toBe(1);
  });

  it("pre-scans with an empty body (server defaults max_messages)", async () => {
    const fetchImpl = vi.fn(async (_url, init) => {
      expect(JSON.parse(String(init?.body))).toEqual({});
      return jsonResponse({
        schema_version: "2.2",
        result: {
          kind: "email_pre_scan",
          urgent: [],
          actionable: [],
          informational_count: 0,
          suggested_archives: [],
          suggested_drafts: [],
        },
      });
    }) as unknown as typeof fetch;
    const client = new EmailClient({ baseUrl: "http://x", fetchImpl });
    const res = await client.prescan();
    expect(res.result.kind).toBe("email_pre_scan");
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
