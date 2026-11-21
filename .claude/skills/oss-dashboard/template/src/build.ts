/**
 * Which workflow runs measure the default branch: push, schedule and merge-queue
 * runs on it (BUILD_EVENTS), minus CI_EXCLUDE_WORKFLOWS. Merge-queue runs carry
 * the branch gh-readonly-queue/<branch>/pr-..., not the branch itself.
 */

import { BUILD_EVENTS, CI_EXCLUDE_WORKFLOWS, PROJECT } from "./config";

const QUEUE_PREFIX = `gh-readonly-queue/${PROJECT.defaultBranch}/`;
const quote = (s: string) => `'${s.replace(/'/g, "''")}'`;

export function isDefaultBranchBuild(r: { branch: string | null; event: string; workflow: string }): boolean {
  if (!BUILD_EVENTS.includes(r.event) || CI_EXCLUDE_WORKFLOWS.includes(r.workflow)) return false;
  return r.branch === PROJECT.defaultBranch || (r.event === "merge_group" && !!r.branch?.startsWith(QUEUE_PREFIX));
}

/** The same rule as a SQL condition on workflow_runs; values come from config.ts, never from a request. */
export const DEFAULT_BRANCH_BUILD_SQL =
  `(event IN (${BUILD_EVENTS.map(quote).join(", ")})` +
  ` AND (branch = ${quote(PROJECT.defaultBranch)} OR (event = 'merge_group' AND substr(branch, 1, ${QUEUE_PREFIX.length}) = ${quote(QUEUE_PREFIX)}))` +
  (CI_EXCLUDE_WORKFLOWS.length ? ` AND workflow NOT IN (${CI_EXCLUDE_WORKFLOWS.map(quote).join(", ")})` : "") +
  ")";
