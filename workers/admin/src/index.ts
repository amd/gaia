// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { handleApi } from "./api";
import { authenticate, AuthError, type AuthEnv } from "./auth";
import { collectAll, type CollectEnv } from "./collector";
import { renderEvalsPage } from "./evalspage";
import { parseMaintainers, renderCiPage, renderDevelopersPage, renderUsersPage } from "./page";

type Env = CollectEnv & AuthEnv & { MAINTAINER_LOGINS?: string };

const PAGES: Record<string, (env: Env) => string> = {
  "/": renderUsersPage,
  "/developers": (env) => renderDevelopersPage(parseMaintainers(env.MAINTAINER_LOGINS)),
  "/ci": renderCiPage,
  "/evals": renderEvalsPage,
};

const text = (body: string, status: number, type = "text/plain"): Response =>
  new Response(body, { status, headers: { "content-type": `${type}; charset=utf-8`, "cache-control": "no-store" } });

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname !== "/admin" && !url.pathname.startsWith("/admin/")) return text("Not found", 404);
    try {
      await authenticate(req, env);
    } catch (e) {
      if (e instanceof AuthError) return text(e.message, e.status);
      throw e;
    }

    const path = url.pathname.slice("/admin".length) || "/";
    if (req.method === "GET") {
      const page = PAGES[path];
      if (page) return text(page(env), 200, "text/html");
    }

    if (path === "/api/collect" && req.method === "POST") {
      // Must be a deliberate call (curl or a script), never a cross-site form riding a signed-in browser.
      const origin = req.headers.get("origin");
      if (req.headers.get("x-admin-action") !== "collect" || (origin && new URL(origin).host !== url.host)) {
        return text("Refused: send the header 'x-admin-action: collect' from the same site (see README).", 403);
      }
      const days = Number(url.searchParams.get("days") ?? "3");
      const skip = Number(url.searchParams.get("skip") ?? "0");
      if (!Number.isInteger(days) || days < 1 || days > 95 || !Number.isInteger(skip) || skip < 0 || skip > 95) {
        return text("days must be 1-95 and skip 0-95 (whole numbers).", 400);
      }
      try {
        const only = url.searchParams.get("only")?.split(",").map((x) => x.trim()).filter(Boolean);
        await collectAll(env, { runDays: days, skipDays: skip, fullHistory: url.searchParams.get("full") === "1", only });
      } catch (e) {
        return text(`Collection finished with errors:\n${(e as Error).message}`, 502);
      }
      return text("ok", 200);
    }

    if (req.method === "GET" && path.startsWith("/api/")) return handleApi(env.DB, path, url);
    return text("Not found", 404);
  },

  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    await collectAll(env, { runDays: 3 });
  },
};
