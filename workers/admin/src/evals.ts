// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Agent-eval results from CI. Every finished run of the eval workflows is
 * read once: its artifacts are downloaded, the scorecards inside are parsed,
 * and one row per scenario is stored, tied to the run's commit, branch or tag,
 * and trigger. Eval artifacts expire after 14 days, so collecting nightly is
 * what preserves the history.
 *
 * Two scorecard formats exist:
 *  - `gaia eval agent`: {summary, scenarios[]} per category directory.
 *  - `gaia eval tasks` (SWE-bench and other task suites): {suite, tasks[]}.
 */

import { getJson, ghHeaders, githubDownload, insertMany, writeAtomic, type CollectEnv, type Fetcher } from "./collector";
import { listZip, readText } from "./zip";

/** Workflows whose artifacts carry eval scorecards, by workflow file. */
export const EVAL_WORKFLOWS = ["eval_flagship.yml", "test_gaia_agent_eval.yml", "eval_swebench.yml"];

const WANTED = /(^|\/)(scorecard\.json|lane-status\.json|lane-timing\.json|tier\.json)$/;
// Well under the Worker's 128 MB memory limit: the zip and its inflated files are both held in memory.
const MAX_ARTIFACT_BYTES = 48 * 1024 * 1024;

/** Release tags (vX.Y.Z, vX.Y.Z-rcN) and agent package tags, whatever event started the run. */
export const TAG_REF = /^(v\d+(\.\d+){2,3}(-rc\d+)?|agent-pkg-.+)$/;

export interface ScenarioRow {
  category: string;
  scenario_id: string;
  lane: string | null;
  status: string;
  score: number | null;
  elapsed_s: number | null;
  cost_usd: number | null;
  tps: number | null;
  ttft_s: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tool_calls: number | null;
  turns: number | null;
  root_cause: string | null;
  kind: "scenario" | "task";
}

export interface ParsedArtifact {
  scenarios: ScenarioRow[];
  tier: string | null;
  agentModel: string | null;
  judgeModel: string | null;
  ref: string | null;
  laneMinutes: number | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
// Scorecards sometimes carry the literal string "null" where a value is absent.
const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() && !/^(null|none|undefined)$/i.test(v.trim()) ? v.trim() : null;
const short = (v: unknown, n = 300): string | null => {
  const s = str(v);
  return s ? (s.length > n ? s.slice(0, n - 1) + "…" : s) : null;
};

interface AgentScorecard {
  config?: { model?: string; agent_model?: string };
  scenarios?: Record<string, unknown>[];
}
interface TasksScorecard {
  suite?: string;
  model?: string;
  tasks?: Record<string, unknown>[];
}

/** Rows from one `gaia eval agent` scorecard. */
export function parseAgentScorecard(sc: AgentScorecard, dirCategory: string, lane: string | null): ScenarioRow[] {
  return (sc.scenarios ?? []).map((s) => {
    const perf = (s.performance_summary ?? {}) as Record<string, unknown>;
    const tools = (s.tool_usage ?? {}) as Record<string, unknown>;
    const cost = (s.cost_estimate ?? {}) as Record<string, unknown>;
    return {
      category: str(s.category) ?? dirCategory,
      scenario_id: String(s.scenario_id),
      lane,
      status: str(s.status) ?? "UNKNOWN",
      score: num(s.overall_score),
      elapsed_s: num(s.elapsed_s),
      cost_usd: num(cost.estimated_usd),
      tps: num(perf.avg_tokens_per_second),
      ttft_s: num(perf.avg_time_to_first_token),
      input_tokens: num(perf.total_input_tokens),
      output_tokens: num(perf.total_output_tokens),
      tool_calls: num(perf.total_tool_calls) ?? num(tools.total_tool_calls),
      turns: Array.isArray(s.turns) ? s.turns.length : null,
      root_cause: short(s.root_cause),
      kind: "scenario",
    };
  });
}

/**
 * Rows from one `gaia eval tasks` scorecard. A task that never reached the
 * model (the backend refused or was missing) is an infrastructure error, not a
 * failure of the agent, so it is not counted against the pass rate.
 */
export function parseTasksScorecard(sc: TasksScorecard): ScenarioRow[] {
  const suite = str(sc.suite) ?? "tasks";
  return (sc.tasks ?? []).map((t) => {
    const error = str(t.error);
    const infra = !!error && /backend failed|unavailable|HTTP \d{3}|connection|rate limit/i.test(error) && (num(t.output_tokens) ?? 0) === 0;
    const status = t.passed === true ? "PASS" : t.timed_out === true ? "TIMEOUT" : infra ? "INFRA_ERROR" : "FAIL";
    return {
      category: suite,
      scenario_id: String(t.id),
      lane: null,
      status,
      score: null,
      elapsed_s: num(t.wall_seconds),
      cost_usd: num(t.cost_usd),
      tps: null,
      ttft_s: null,
      input_tokens: num(t.input_tokens),
      output_tokens: num(t.output_tokens),
      tool_calls: num(t.tool_calls),
      turns: num(t.steps),
      root_cause: short(error ?? t.why),
      kind: "task",
    };
  });
}

/** Parse every scorecard and lane file inside one artifact zip. */
export async function parseArtifact(zip: Uint8Array): Promise<ParsedArtifact> {
  const out: ParsedArtifact = { scenarios: [], tier: null, agentModel: null, judgeModel: null, ref: null, laneMinutes: null };
  const entries = listZip(zip).filter((e) => WANTED.test(e.name));
  let lane: string | null = null;
  const json = async (name: string) => JSON.parse(await readText(zip, entries.find((e) => e.name === name)!)) as Record<string, unknown>;
  for (const e of entries) {
    if (e.name.endsWith("lane-status.json")) {
      const s = await json(e.name);
      lane = str(s.lane);
      out.tier ??= str(s.tier);
      out.agentModel ??= str(s.agent_model);
    } else if (e.name.endsWith("lane-timing.json")) {
      out.laneMinutes = num((await json(e.name)).total_minutes);
    } else if (e.name.endsWith("tier.json")) {
      const t = await json(e.name);
      out.tier = str(t.tier) ?? out.tier;
      out.agentModel ??= str(t.agent_model);
      out.ref = str(t.ref);
    }
  }
  for (const e of entries.filter((x) => x.name.endsWith("scorecard.json"))) {
    const sc = (await json(e.name)) as AgentScorecard & TasksScorecard;
    if (Array.isArray(sc.scenarios)) {
      const dir = e.name.split("/").slice(-2, -1)[0] ?? "uncategorised";
      out.scenarios.push(...parseAgentScorecard(sc, dir, lane));
      out.judgeModel ??= str(sc.config?.model);
      out.agentModel ??= str(sc.config?.agent_model);
    } else if (Array.isArray(sc.tasks)) {
      out.scenarios.push(...parseTasksScorecard(sc));
      out.agentModel ??= str(sc.model);
    }
  }
  return out;
}

interface GhRun {
  id: number;
  event: string;
  head_branch: string | null;
  head_sha: string;
  created_at: string;
  updated_at: string;
  run_started_at?: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  name: string;
}

export async function collectEvals(env: CollectEnv, f: Fetcher, days: number, today: Date, refresh = false): Promise<string> {
  const since = new Date(today.getTime() - days * 86_400_000).toISOString().slice(0, 10);
  const known = new Set(
    (await env.DB.prepare("SELECT run_id FROM eval_runs").all<{ run_id: number }>()).results.map((r) => r.run_id),
  );
  let runsRead = 0;
  let scenarios = 0;
  let noArtifacts = 0;
  const failures: string[] = [];
  for (const wf of EVAL_WORKFLOWS) {
    const runs: GhRun[] = [];
    for (let page = 1; page <= 10; page++) {
      const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${wf}/runs?status=completed&per_page=100&page=${page}&created=%3E%3D${since}`;
      const res = await getJson<{ workflow_runs: GhRun[] }>(f, url, { headers: ghHeaders(env) });
      runs.push(...res.workflow_runs);
      if (res.workflow_runs.length < 100) break;
    }
    for (const run of runs) {
      if (run.conclusion === "skipped" || (known.has(run.id) && !refresh)) continue;
      try {
      const arts = await getJson<{ artifacts: { id: number; name: string; size_in_bytes: number; expired: boolean }[] }>(
        f,
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/runs/${run.id}/artifacts?per_page=100`,
        { headers: ghHeaders(env) },
      );
      const unusable = arts.artifacts.filter((a) => a.expired || a.size_in_bytes > MAX_ARTIFACT_BYTES);
      if (unusable.length) {
        // A run stored without some of its artifacts would show a wrong pass rate forever.
        throw new Error(
          `Run ${run.id} has artifacts that cannot be read (${unusable.map((a) => `${a.name}: ${a.expired ? "expired" : `${Math.round(a.size_in_bytes / 1e6)} MB`}`).join(", ")}). ` +
            `Expired artifacts cannot be recovered; raise MAX_ARTIFACT_BYTES in src/evals.ts only if the Worker's memory allows it.`,
        );
      }
      const parsed: ParsedArtifact[] = [];
      for (const a of arts.artifacts) {
        const res = await githubDownload(env, f, `https://api.github.com/repos/${env.GITHUB_REPO}/actions/artifacts/${a.id}/zip`);
        if (!res.ok) throw new Error(`Downloading artifact "${a.name}" of run ${run.id} returned HTTP ${res.status}.`);
        try {
          parsed.push(await parseArtifact(new Uint8Array(await res.arrayBuffer())));
        } catch (e) {
          throw new Error(`Artifact "${a.name}" of run ${run.id} could not be read: ${(e as Error).message}`, { cause: e });
        }
      }
      const rows = parsed.flatMap((p) => p.scenarios);
      if (rows.length === 0) {
        // Remembered as having no scorecards, so it is not downloaded again every night.
        await env.DB.prepare(
          "INSERT OR REPLACE INTO eval_runs (run_id, workflow, event, ref, is_tag, sha, created_at, conclusion, tier, agent_model, judge_model, minutes, lanes, html_url, collected_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?, ?)",
        ).bind(run.id, run.name, run.event, run.head_branch, run.head_sha, run.created_at, run.conclusion, run.html_url, new Date().toISOString()).run();
        noArtifacts++;
        continue;
      }
      const pick = <K extends keyof ParsedArtifact>(k: K) => parsed.map((p) => p[k]).find((v) => v != null) ?? null;
      const tagRef = pick("ref") as string | null;
      const ref = tagRef ? tagRef.replace(/^refs\/(tags|heads)\//, "") : run.head_branch;
      // A dispatch on a tag (how RCs are often re-measured) is still a release eval.
      const isTag = tagRef?.startsWith("refs/tags/") || TAG_REF.test(ref ?? "");
      const tier = (pick("tier") as string | null) ?? (run.event === "pull_request" ? "pr" : null);
      const minutes = Math.max(0, ...parsed.map((p) => p.laneMinutes ?? 0)) || null;
      // One transaction, run row included: a run is either fully stored or not at all (and retried next night).
      await writeAtomic(env.DB, [
        env.DB.prepare("DELETE FROM eval_scenarios WHERE run_id = ?").bind(run.id),
        ...insertMany(env.DB, "eval_scenarios",
          ["run_id", "category", "scenario_id", "lane", "status", "score", "elapsed_s", "cost_usd", "tps", "ttft_s", "input_tokens", "output_tokens", "tool_calls", "turns", "root_cause", "kind"],
          rows.map((r) => [run.id, r.category, r.scenario_id, r.lane, r.status, r.score, r.elapsed_s, r.cost_usd, r.tps, r.ttft_s, r.input_tokens, r.output_tokens, r.tool_calls, r.turns, r.root_cause, r.kind])),
        env.DB.prepare(
          "INSERT OR REPLACE INTO eval_runs (run_id, workflow, event, ref, is_tag, sha, created_at, conclusion, tier, agent_model, judge_model, minutes, lanes, html_url, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ).bind(
          run.id, run.name, run.event, ref, isTag ? 1 : 0, run.head_sha, run.created_at, run.conclusion, tier,
          pick("agentModel"), pick("judgeModel"), minutes, parsed.length, run.html_url, new Date().toISOString(),
        ),
      ]);
      runsRead++;
      scenarios += rows.length;
      } catch (e) {
        // One unreadable run must not stop the others; it is retried next night and reported below.
        failures.push((e as Error).message);
      }
    }
  }
  const summary = `${runsRead} eval runs, ${scenarios} scenario results${noArtifacts ? `; ${noArtifacts} finished runs had no scorecards` : ""}`;
  if (failures.length) throw new Error(`${summary}; ${failures.length} run(s) could not be read: ${failures.join(" | ")}`);
  return summary;
}
