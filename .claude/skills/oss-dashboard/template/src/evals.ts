/**
 * Eval and benchmark results from CI. Every finished run of the workflows in
 * EVAL_WORKFLOWS is read once: its artifacts whose name starts with
 * "eval-results" are downloaded, every `eval-results.json` inside them is
 * parsed, and one row per result is stored, tied to the run's commit, branch
 * or tag, and trigger. Artifacts expire, so collecting nightly is what
 * preserves the history. The file format is references/evals-contract.md.
 */

import { getJson, ghHeaders, githubDownload, insertMany, writeAtomic, type CollectEnv, type Fetcher } from "./collector";
import { EVAL_WORKFLOWS, PROJECT } from "./config";
import { listZip, readText } from "./zip";

export const SCHEMA = "oss-dashboard/eval-results@1";
export const STATUSES = ["PASS", "FAIL", "TIMEOUT", "ERROR", "SKIPPED"] as const;
export type Status = (typeof STATUSES)[number];

/** Only artifacts named like this are downloaded, so build outputs of the same workflow are never fetched. */
export const ARTIFACT_PREFIX = "eval-results";
const RESULTS_FILE = /(^|\/)eval-results\.json$/;
// Well under the Worker's 128 MB memory limit: the zip and its inflated files are both held in memory.
const MAX_ARTIFACT_BYTES = 48 * 1024 * 1024;
const MAX_NOTE = 300;

export interface ResultRow {
  suite: string;
  tier: string | null;
  model: string | null;
  category: string | null;
  id: string;
  status: Status;
  score: number | null;
  elapsed_s: number | null;
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  note: string | null;
}

/**
 * Validate one eval-results.json and return its rows. `file` names the file in
 * every error, so a broken producer is found from the collector log alone.
 */
export function parseResultsFile(text: string, file: string): ResultRow[] {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    throw new Error(`${file} is not valid JSON: ${(e as Error).message}`, { cause: e });
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw new Error(`${file} must be a JSON object.`);
  const d = doc as Record<string, unknown>;
  if (d.schema !== SCHEMA) {
    throw new Error(`${file} has schema ${JSON.stringify(d.schema ?? null)}; expected "${SCHEMA}". See references/evals-contract.md.`);
  }
  const text_ = (where: string, field: string, v: unknown, required = false): string | null => {
    if (v == null) {
      if (required) throw new Error(`${file}: ${where}"${field}" is required.`);
      return null;
    }
    if (typeof v !== "string") throw new Error(`${file}: ${where}"${field}" must be a string, got ${JSON.stringify(v)}.`);
    const s = v.trim();
    if (!s && required) throw new Error(`${file}: ${where}"${field}" must not be empty.`);
    return s || null;
  };
  const num = (where: string, field: string, v: unknown): number | null => {
    if (v == null) return null;
    if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`${file}: ${where}"${field}" must be a number, got ${JSON.stringify(v)}.`);
    return v;
  };
  const suite = text_("", "suite", d.suite, true)!;
  const tier = text_("", "tier", d.tier);
  const model = text_("", "model", d.model);
  if (!Array.isArray(d.results)) throw new Error(`${file}: "results" must be an array.`);
  const seen = new Set<string>();
  return d.results.map((raw, i) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`${file}: results[${i}] must be an object.`);
    const r = raw as Record<string, unknown>;
    const id = text_(`results[${i}].`, "id", r.id, true)!;
    const where = `result "${id}" `;
    const rawStatus = text_(where, "status", r.status, true)!;
    const status = rawStatus.toUpperCase() as Status;
    if (!STATUSES.includes(status)) {
      throw new Error(`${file}: ${where}has status ${JSON.stringify(r.status)}; expected one of ${STATUSES.join(", ")} (any case).`);
    }
    const category = text_(where, "category", r.category);
    const key = `${category ?? ""}\u0000${id}`;
    if (seen.has(key)) throw new Error(`${file}: result "${id}"${category ? ` in category "${category}"` : ""} appears more than once.`);
    seen.add(key);
    const note = text_(where, "note", r.note);
    return {
      suite,
      tier,
      model,
      category,
      id,
      status,
      score: num(where, "score", r.score),
      elapsed_s: num(where, "elapsed_s", r.elapsed_s),
      cost_usd: num(where, "cost_usd", r.cost_usd),
      input_tokens: num(where, "input_tokens", r.input_tokens),
      output_tokens: num(where, "output_tokens", r.output_tokens),
      note: note && note.length > MAX_NOTE ? note.slice(0, MAX_NOTE - 1) + "…" : note,
    };
  });
}

/** Every eval-results.json inside one artifact zip; an eval-results artifact without one is an error. */
export async function parseArtifact(zip: Uint8Array, artifact: string): Promise<{ file: string; rows: ResultRow[] }[]> {
  const entries = listZip(zip).filter((e) => RESULTS_FILE.test(e.name));
  if (!entries.length) throw new Error(`Artifact "${artifact}" contains no eval-results.json.`);
  const out: { file: string; rows: ResultRow[] }[] = [];
  for (const e of entries) {
    const file = `${artifact}/${e.name}`;
    out.push({ file, rows: parseResultsFile(await readText(zip, e), file) });
  }
  return out;
}

/** Rows of every file of one run; the same result twice in one suite, tier and model is an error naming both files. */
export function mergeFiles(files: { file: string; rows: ResultRow[] }[]): ResultRow[] {
  const where = new Map<string, string>();
  const rows: ResultRow[] = [];
  for (const f of files) {
    for (const r of f.rows) {
      const key = [r.suite, r.tier ?? "", r.model ?? "", r.category ?? "", r.id].join("\u0000");
      const first = where.get(key);
      if (first && first !== f.file) {
        throw new Error(`Result "${r.id}" of suite "${r.suite}" appears in both ${first} and ${f.file}; give each shard its own ids, tier or model.`);
      }
      where.set(key, f.file);
      rows.push(r);
    }
  }
  return rows;
}

interface GhRun {
  id: number;
  event: string;
  head_branch: string | null;
  head_sha: string;
  created_at: string;
  conclusion: string | null;
  html_url: string;
  name: string;
  run_attempt: number;
  /** Where the code ran from; a pull request from a fork names the fork. */
  head_repository?: { full_name: string } | null;
}
interface GhArtifact {
  id: number;
  name: string;
  size_in_bytes: number;
  expired: boolean;
}

/** New or changed runs downloaded per invocation; the rest wait for the next collection. */
export const MAX_RUNS_PER_INVOCATION = 40;

const RUN_COLS = ["run_id", "run_attempt", "workflow", "event", "ref", "sha", "created_at", "conclusion", "html_url", "files", "collected_at"];
const RESULT_COLS = ["run_id", "suite", "tier", "model", "category", "result_id", "status", "score", "elapsed_s", "cost_usd", "input_tokens", "output_tokens", "note"];

async function listArtifacts(env: CollectEnv, f: Fetcher, runId: number): Promise<GhArtifact[]> {
  const out: GhArtifact[] = [];
  for (let page = 1; page <= 10; page++) {
    const res = await getJson<{ total_count?: number; artifacts: GhArtifact[] }>(
      f,
      `https://api.github.com/repos/${PROJECT.repo}/actions/runs/${runId}/artifacts?per_page=100&page=${page}`,
      { headers: ghHeaders(env) },
    );
    out.push(...res.artifacts);
    if (res.artifacts.length < 100 || out.length >= (res.total_count ?? out.length)) return out;
  }
  throw new Error(`Run ${runId} has more than 1,000 artifacts; the listing would be cut off.`);
}

/**
 * Read the eval runs of the last `days` days. With `refresh`, every run is read
 * again, those read longest ago first, so repeated refreshes work through all.
 */
export async function collectEvals(env: CollectEnv, f: Fetcher, days: number, today: Date, refresh = false): Promise<string> {
  const since = new Date(today.getTime() - days * 86_400_000).toISOString().slice(0, 10);
  const stored = (await env.DB.prepare("SELECT run_id, run_attempt, collected_at FROM eval_runs").all<{ run_id: number; run_attempt: number; collected_at: string }>()).results;
  const known = new Map(stored.map((r) => [r.run_id, r.run_attempt]));
  const readAt = new Map(stored.map((r) => [r.run_id, r.collected_at]));
  let deferred = 0;
  let attempted = 0;
  let runsRead = 0;
  let results = 0;
  let noResults = 0;
  let kept = 0;
  const failures: string[] = [];
  const todo: GhRun[] = [];
  for (const wf of EVAL_WORKFLOWS) {
    const runs: GhRun[] = [];
    for (let page = 1; page <= 10; page++) {
      const url = `https://api.github.com/repos/${PROJECT.repo}/actions/workflows/${encodeURIComponent(wf)}/runs?status=completed&per_page=100&page=${page}&created=%3E%3D${since}`;
      const res = await getJson<{ workflow_runs: GhRun[] }>(f, url, { headers: ghHeaders(env) });
      runs.push(...res.workflow_runs);
      if (res.workflow_runs.length < 100) break;
      if (page === 10) throw new Error(`More than 1,000 finished runs of ${wf} since ${since}; the listing would be cut off.`);
    }
    // Runs of a fork's code are not this project's results, and a malformed artifact there must not turn the source red.
    const own = runs.filter((r) => !r.head_repository || r.head_repository.full_name.toLowerCase() === PROJECT.repo.toLowerCase());
    todo.push(...own.filter((r) => r.conclusion !== "skipped" && (refresh || known.get(r.id) !== r.run_attempt)));
  }
  // Newest first across all workflows, so the cap never holds recent results back for old ones.
  todo.sort((a, b) => (refresh ? (readAt.get(a.id) ?? "").localeCompare(readAt.get(b.id) ?? "") : 0) || b.created_at.localeCompare(a.created_at));
  for (const run of todo) {
    if (attempted >= MAX_RUNS_PER_INVOCATION) {
      deferred++;
      continue;
    }
    attempted++;
    try {
      const arts = (await listArtifacts(env, f, run.id)).filter((a) => a.name.startsWith(ARTIFACT_PREFIX));
      const unusable = arts.filter((a) => a.expired || a.size_in_bytes > MAX_ARTIFACT_BYTES);
      if (unusable.length && known.get(run.id) === run.run_attempt) {
        // Stored when its artifacts were readable; a refresh keeps that data.
        kept++;
        continue;
      }
      if (unusable.length) {
        // A run stored without some of its results would show a wrong pass rate forever.
        throw new Error(
          `Run ${run.id} has eval artifacts that cannot be read (${unusable.map((a) => `${a.name}: ${a.expired ? "expired" : `${Math.round(a.size_in_bytes / 1e6)} MB`}`).join(", ")}). ` +
            `Expired artifacts cannot be recovered; raise MAX_ARTIFACT_BYTES in src/evals.ts only if the Worker's memory allows it.`,
        );
      }
      const files: { file: string; rows: ResultRow[] }[] = [];
      for (const a of arts) {
        const res = await githubDownload(env, f, `https://api.github.com/repos/${PROJECT.repo}/actions/artifacts/${a.id}/zip`);
        if (!res.ok) throw new Error(`Downloading artifact "${a.name}" of run ${run.id} returned HTTP ${res.status}.`);
        try {
          files.push(...(await parseArtifact(new Uint8Array(await res.arrayBuffer()), a.name)));
        } catch (e) {
          throw new Error(`Run ${run.id}: ${(e as Error).message}`, { cause: e });
        }
      }
      const rows = mergeFiles(files);
      const runRow = [run.id, run.run_attempt, run.name, run.event, run.head_branch, run.head_sha, run.created_at, run.conclusion, run.html_url, files.length, new Date().toISOString()];
      // One transaction, run row included: a run is either fully stored or not at all (and retried next night).
      await writeAtomic(env.DB, [
        env.DB.prepare("DELETE FROM eval_results WHERE run_id = ?").bind(run.id),
        ...insertMany(env.DB, "eval_results", RESULT_COLS,
          rows.map((r) => [run.id, r.suite, r.tier ?? "", r.model ?? "", r.category ?? "", r.id, r.status, r.score, r.elapsed_s, r.cost_usd, r.input_tokens, r.output_tokens, r.note])),
        env.DB.prepare(`INSERT OR REPLACE INTO eval_runs (${RUN_COLS.join(", ")}) VALUES (${RUN_COLS.map(() => "?").join(", ")})`).bind(...runRow),
      ]);
      if (files.length) {
        runsRead++;
        results += rows.length;
      } else noResults++;
    } catch (e) {
      // One unreadable run must not stop the others; it is retried next night and reported below.
      failures.push((e as Error).message);
    }
  }
  const summary =
    `${runsRead} eval runs, ${results} results` +
    (noResults ? `; ${noResults} finished runs had no ${ARTIFACT_PREFIX}* artifact` : "") +
    (kept ? `; ${kept} stored runs kept as stored (their artifacts have expired)` : "") +
    (deferred ? `; ${deferred} more runs left for the next collection (at most ${MAX_RUNS_PER_INVOCATION} per run)` : "");
  if (failures.length) throw new Error(`${summary}; ${failures.length} run(s) could not be read: ${failures.join(" | ")}`);
  return summary;
}
