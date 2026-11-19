/**
 * Repository traffic: views, clones, top referrers and top pages. GitHub keeps
 * only the last 14 days, so each night stores what it reports and the table
 * keeps everything older. Daily counts are upserted because GitHub revises the
 * most recent day; the top lists are stored as that night's snapshot.
 */

import { PROJECT } from "./config";
import { ghHeaders, insertMany, writeAtomic, type CollectEnv, type Fetcher } from "./collector";

interface GhTrafficDay {
  timestamp: string;
  count: number;
  uniques: number;
}

export const TRAFFIC_PERMISSION = 'the repository permission "Administration" set to Read-only';

const isCount = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 0;

async function trafficJson<T>(env: CollectEnv, f: Fetcher, path: string): Promise<T> {
  const url = `https://api.github.com/repos/${PROJECT.repo}/traffic/${path}`;
  const res = await f(url, { headers: ghHeaders(env) }).catch((e: Error) => {
    throw new Error(`GET ${url} failed: ${e.message}`, { cause: e });
  });
  if (res.ok) return (await res.json()) as T;
  if ((res.status === 403 || res.status === 429) && res.headers.get("x-ratelimit-remaining") === "0") {
    throw new Error(`GET ${url} returned HTTP ${res.status}: the GitHub rate limit is used up; traffic is read again on the next run.`);
  }
  if (res.status === 401) throw new Error(`GET ${url} returned HTTP 401: GITHUB_TOKEN is missing, expired or revoked (see README.md).`);
  if (res.status === 403 || res.status === 404) {
    throw new Error(
      `GET ${url} returned HTTP ${res.status}. Repository traffic is shown only to people with push access: GITHUB_TOKEN must belong to ` +
        `someone with push access to ${PROJECT.repo}, and a fine-grained token needs ${TRAFFIC_PERMISSION} (see README.md). ` +
        "If the token cannot have it, set TRAFFIC = false in src/config.ts.",
    );
  }
  throw new Error(`GET ${url} returned HTTP ${res.status}.`);
}

function days(list: unknown, url: string): Map<string, GhTrafficDay> {
  if (!Array.isArray(list)) throw new Error(`GitHub's ${url} response has no daily list.`);
  const out = new Map<string, GhTrafficDay>();
  for (const d of list as GhTrafficDay[]) {
    if (typeof d?.timestamp !== "string" || !isCount(d.count) || !isCount(d.uniques)) {
      throw new Error(`GitHub's ${url} response has a day without a timestamp, count and uniques: ${JSON.stringify(d)}.`);
    }
    out.set(d.timestamp.slice(0, 10), d);
  }
  return out;
}

export async function collectTraffic(env: CollectEnv, f: Fetcher, day: string): Promise<string> {
  const views = await trafficJson<{ views?: unknown }>(env, f, "views?per=day");
  const clones = await trafficJson<{ clones?: unknown }>(env, f, "clones?per=day");
  const referrers = await trafficJson<unknown>(env, f, "popular/referrers");
  const paths = await trafficJson<unknown>(env, f, "popular/paths");
  const v = days(views.views, "traffic/views");
  const c = days(clones.clones, "traffic/clones");
  if (!Array.isArray(referrers) || !Array.isArray(paths)) throw new Error("GitHub's top referrers or top paths response is not a list.");
  const refRows = (referrers as { referrer?: unknown; count?: unknown; uniques?: unknown }[]).map((r) => {
    if (typeof r?.referrer !== "string" || !isCount(r.count) || !isCount(r.uniques)) throw new Error(`Unexpected referrer entry from GitHub: ${JSON.stringify(r)}.`);
    return [day, r.referrer, r.count, r.uniques];
  });
  const pathRows = (paths as { path?: unknown; title?: unknown; count?: unknown; uniques?: unknown }[]).map((p) => {
    if (typeof p?.path !== "string" || !isCount(p.count) || !isCount(p.uniques)) throw new Error(`Unexpected path entry from GitHub: ${JSON.stringify(p)}.`);
    return [day, p.path, typeof p.title === "string" ? p.title : "", p.count, p.uniques];
  });
  // The two lists are read a moment apart and can straddle midnight; a day only one of them has is stored on the next run.
  // Today is still in progress (GitHub sometimes lists it): only complete days are stored, and today's is read tomorrow.
  const both = [...v.keys()].filter((d) => c.has(d) && d < day).sort();
  const daily = both.map((d) => [d, v.get(d)!.count, v.get(d)!.uniques, c.get(d)!.count, c.get(d)!.uniques]);
  await writeAtomic(env.DB, [
    ...insertMany(env.DB, "traffic_daily", ["day", "views", "view_uniques", "clones", "clone_uniques"], daily),
    env.DB.prepare("DELETE FROM traffic_referrers WHERE day = ?").bind(day),
    env.DB.prepare("DELETE FROM traffic_paths WHERE day = ?").bind(day),
    ...insertMany(env.DB, "traffic_referrers", ["day", "referrer", "count", "uniques"], refRows),
    ...insertMany(env.DB, "traffic_paths", ["day", "path", "title", "count", "uniques"], pathRows),
  ]);
  const span = both.length ? ` (${both[0]} to ${both[both.length - 1]})` : "";
  return `${both.length} days of views and clones${span}, ${refRows.length} referrers, ${pathRows.length} pages`;
}
