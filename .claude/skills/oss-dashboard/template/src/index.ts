import { handleApi } from "./api";
import { authenticate, AuthError, type AuthEnv } from "./auth";
import { collectAll, CollectError, SOURCES, unknownSources, type CollectEnv } from "./collector";
import { renderEvalsPage } from "./evalspage";
import { renderGrowthPage } from "./growthpage";
import { renderCiPage, renderDevelopersPage, renderUsersPage } from "./page";
import { EVAL_WORKFLOWS, PROJECT } from "./config";
import { coreTeam, formatPeople, parseCoreTeam, saveCoreTeam } from "./settings";

type Env = CollectEnv & AuthEnv & { MAINTAINER_LOGINS?: string };

const PAGES: Record<string, (env: Env) => string | Promise<string>> = {
  "/": renderUsersPage,
  "/growth": async (env) => renderGrowthPage((await coreTeam(env.DB, env.MAINTAINER_LOGINS)).people),
  "/developers": async (env) => renderDevelopersPage(await coreTeam(env.DB, env.MAINTAINER_LOGINS)),
  "/ci": renderCiPage,
  ...(EVAL_WORKFLOWS.length ? { "/evals": renderEvalsPage } : {}),
};

/** A deliberate call from this site (a script, or the page's own fetch), never a cross-site form riding a signed-in browser. */
function deliberate(req: Request, url: URL, action: string): boolean {
  const origin = req.headers.get("origin");
  const sameSite = (() => {
    if (!origin) return true;
    try {
      const o = new URL(origin);
      if (o.protocol !== url.protocol || o.hostname !== url.hostname) return false;
      return o.port === url.port || url.hostname === "localhost"; // wrangler dev rewrites Origin without the port
    } catch {
      return false; // "null" (sandboxed frames, file:) or anything unparseable is not this site
    }
  })();
  return req.headers.get("x-admin-action") === action && sameSite;
}

/** Largest core-team save accepted; a real team is a few hundred bytes. */
const MAX_TEAM_BYTES = 8192;

// Pages carry their scripts and styles inline and talk only to this Worker; nothing may load from or send to elsewhere.
const CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";

const text = (body: string, status: number, type = "text/plain"): Response =>
  new Response(body, {
    status,
    headers: {
      "content-type": `${type}; charset=utf-8`,
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "content-security-policy": CSP,
      "referrer-policy": "no-referrer",
    },
  });

export default {
  async fetch(req: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname !== PROJECT.basePath && !url.pathname.startsWith(`${PROJECT.basePath}/`)) return text("Not found", 404);
    let who: string;
    try {
      who = await authenticate(req, env, undefined, undefined, ctx?.access);
    } catch (e) {
      if (e instanceof AuthError) return text(e.message, e.status);
      throw e;
    }

    const path = url.pathname.slice(PROJECT.basePath.length) || "/";
    if (req.method === "GET") {
      const page = PAGES[path];
      if (page) return text(await page(env), 200, "text/html");
    }

    if (path === "/api/core-team" && req.method === "POST") {
      if (!deliberate(req, url, "core-team")) {
        return text("Refused: send the header 'x-admin-action: core-team' from the same site.", 403);
      }
      if (!who.includes("@")) return text("Refused: sign in as a person to change the core team; a service token cannot.", 403);
      if (Number(req.headers.get("content-length") ?? 0) > MAX_TEAM_BYTES) return text(`Refused: a core-team save is at most ${MAX_TEAM_BYTES} bytes.`, 413);
      const body = await req.text();
      if (new TextEncoder().encode(body).length > MAX_TEAM_BYTES) return text(`Refused: a core-team save is at most ${MAX_TEAM_BYTES} bytes.`, 413);
      let raw: unknown;
      try {
        raw = (JSON.parse(body) as { maintainers?: unknown } | null)?.maintainers;
      } catch {
        return text('Send JSON: {"maintainers": "alice+alice-work,bob"}.', 400);
      }
      if (typeof raw !== "string") return text('Send JSON: {"maintainers": "alice+alice-work,bob"}.', 400);
      let people: string[][];
      try {
        people = parseCoreTeam(raw);
      } catch (e) {
        return text((e as Error).message, 400);
      }
      await saveCoreTeam(env.DB, people, who);
      console.log(JSON.stringify({ event: "core-team-saved", by: who, maintainers: formatPeople(people) }));
      return text(JSON.stringify({ maintainers: formatPeople(people) }), 200, "application/json");
    }

    if (path === "/api/collect" && req.method === "POST") {
      if (!deliberate(req, url, "collect")) {
        return text("Refused: send the header 'x-admin-action: collect' from the same site (see README).", 403);
      }
      const days = Number(url.searchParams.get("days") ?? "3");
      const skip = Number(url.searchParams.get("skip") ?? "0");
      if (!Number.isInteger(days) || days < 1 || days > 95 || !Number.isInteger(skip) || skip < 0 || skip > 95) {
        return text("days must be 1-95 and skip 0-95 (whole numbers).", 400);
      }
      const only = url.searchParams.get("only")?.split(",").map((x) => x.trim()).filter(Boolean);
      if (only && only.length === 0) return text(`only= names no source. Known: ${SOURCES.join(", ")}.`, 400);
      const unknown = unknownSources(only);
      if (unknown.length) return text(`Unknown source(s): ${unknown.join(", ")}. Known: ${SOURCES.join(", ")}.`, 400);
      try {
        await collectAll(env, { runDays: days, skipDays: skip, fullHistory: url.searchParams.get("full") === "1", only });
      } catch (e) {
        if (!(e instanceof CollectError)) return text(`Collection stopped: ${(e as Error).message}`, 502);
        const stored = e.attempted.filter((s) => !e.failed.includes(s));
        const head = stored.length
          ? `Failed: ${e.failed.join(", ")}. The other sources (${stored.join(", ")}) were collected and stored.`
          : `Failed: ${e.failed.join(", ")}. Nothing was collected.`;
        return text(`${head} Re-run just the failed ones with ?only=${e.failed.map(encodeURIComponent).join(",")} once the cause below is fixed.\n\n${e.message}`, 502);
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
