# Eval results contract (v1)

The Evals page reads results from CI artifacts. Any workflow listed in `EVAL_WORKFLOWS` (by file name, in `<dir>/src/config.ts`) can feed it, whatever language or harness it uses, by uploading files in this format.

## The artifact

- Upload an artifact whose **name starts with `eval-results`** (`eval-results`, `eval-results-linux`, `eval-results-shard-3`). Other artifacts of the run are never downloaded, so build outputs don't count against the size limit.
- Inside it, put one or more files named exactly **`eval-results.json`**, in any directory.
- Each artifact must be under 48 MB. A bigger one stops that run from being read.
- An `eval-results*` artifact that holds no `eval-results.json` is an error.

## The file

```json
{
  "schema": "oss-dashboard/eval-results@1",
  "suite": "agent-scenarios",
  "tier": "smoke",
  "model": "gpt-x or config name",
  "results": [
    {"id": "login_flow", "category": "auth", "status": "PASS", "score": 0.92,
     "elapsed_s": 31.2, "cost_usd": 0.004, "input_tokens": 1200, "output_tokens": 300, "note": "short reason"}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | Exactly `oss-dashboard/eval-results@1`. |
| `suite` | yes | What was measured. The page has one view per suite, with a selector when there are several. |
| `tier` | no | Size or depth of the run (`smoke`, `full`). |
| `model` | no | Model or configuration under test. |
| `results[].id` | yes | String. Unique within a file per category. |
| `results[].status` | yes | `PASS`, `FAIL`, `TIMEOUT`, `ERROR` or `SKIPPED`, in any case. |
| `category` | no | Groups results for the per-category breakdown and filter. |
| `score` | no | Any number, on the suite's own scale. Averaged, never coloured. |
| `elapsed_s`, `cost_usd`, `input_tokens`, `output_tokens` | no | Numbers. Drive the time and cost charts. |
| `note` | no | Short reason, shown next to the result. Cut to 300 characters. |

Leave out a field, or set it to `null`, when you don't have it. A field that's present with the wrong type is an error. The collector never guesses.

A file that breaks the contract fails the whole run, and the collector log names the file, the result and the bad value: wrong `schema`, missing `suite`, `id` or `status`, an unknown status, a wrong type, or a duplicate result. Nothing from that run is stored. The run is retried every night until it's fixed or its artifacts expire.

The same `id` (and category) can't appear twice for one suite, tier and model in a run, even across files. Give each shard its own ids. When one run tests the same suite against several models, set a different `model` in each file. They show as separate runs.

## How status maps to pass rate

| Status | Pass rate | Shown as |
|---|---|---|
| `PASS` | numerator and denominator | passed |
| `FAIL` | denominator | failed |
| `TIMEOUT` | denominator (counts as a failure) | timeout |
| `ERROR` | **excluded** | error. Means the harness or infrastructure broke, not the thing under test |
| `SKIPPED` | **excluded** | skipped, as a count only |

Pass rate = PASS / (PASS + FAIL + TIMEOUT).

- **Regressions:** a result regressed when it passed the last time it had a PASS, FAIL or TIMEOUT outcome in an earlier run of the same suite, tier and model (and trigger filter), and now fails or times out. Runs with a different model or tier are never compared. Runs with only ERROR or SKIPPED in between are looked past.
- **Cancelled runs** are stored but left out of pass rates.

## Triggers

| Run | Shown as |
|---|---|
| Branch or tag matches `RELEASE_TAG` | Release |
| Also matches `RC_TAG` | Release candidate |
| `schedule` | Scheduled |
| `push` | Push |
| `pull_request` | Pull request |
| `workflow_dispatch` | Manual |

The release check uses the run's `head_branch`, so it works for tag pushes and for runs dispatched on a tag. The default view, "Default branch + releases", shows scheduled and push runs on the default branch plus releases.

## Collection

- Each night the collector reads the finished runs of the last `EVAL_ARTIFACT_DAYS` days (set it to your artifact retention).
- It reads at most 40 new or changed runs per collection, newest first. The status footer says "still catching up" while some are left.
- A re-run (a newer `run_attempt`) is read again and replaces the stored results.
- When a stored run's artifacts expire, its results are kept. That isn't a failure.
- `?only=evals` with full history re-reads every run, the ones read longest ago first.

## GitHub Actions

```yaml
      - name: Run evals
        run: python run_evals.py --out out/eval-results.json
      - name: Upload eval results
        if: ${{ !cancelled() }}           # failed evals still upload their results
        uses: actions/upload-artifact@v4
        with:
          name: eval-results              # must start with "eval-results"; add -${{ matrix.shard }} for shards
          path: out/eval-results.json
          retention-days: 14              # match EVAL_ARTIFACT_DAYS
          if-no-files-found: error
```

The workflow token needs no extra permission. The dashboard's `GITHUB_TOKEN` secret needs Actions read access to download the artifacts.

## Converting from pytest (`pytest-json-report`)

```python
# pytest --json-report --json-report-file=report.json, then: python to_eval_results.py report.json out/eval-results.json
import json, sys

STATUS = {"passed": "PASS", "failed": "FAIL", "error": "ERROR", "skipped": "SKIPPED", "xfailed": "SKIPPED", "xpassed": "PASS"}
report = json.load(open(sys.argv[1]))
results = []
for t in report["tests"]:
    path, _, name = t["nodeid"].partition("::")
    msg = (t.get("call") or t.get("setup") or {}).get("longrepr") or ""
    status = STATUS[t["outcome"]]  # KeyError on an unknown outcome: fail loudly, never guess
    if "Timeout" in msg and status == "FAIL":
        status = "TIMEOUT"           # pytest-timeout reports a failure
    results.append({"id": name or path, "category": path, "status": status,
                    "elapsed_s": sum(t.get(p, {}).get("duration", 0) for p in ("setup", "call", "teardown")),
                    "note": msg.splitlines()[-1][:300] if status != "PASS" and msg else None})
json.dump({"schema": "oss-dashboard/eval-results@1", "suite": "pytest-evals",
           "tier": sys.argv[3] if len(sys.argv) > 3 else None, "model": None, "results": results},
          open(sys.argv[2], "w"), indent=1)
```

## Converting from a custom benchmark (Node)

```js
// node to-eval-results.mjs bench.json out/eval-results.json
// bench.json: [{ name, group, ok, timedOut, crashed, seconds, usd, score, reason }]
import { readFileSync, writeFileSync } from "node:fs";

const [input, output] = process.argv.slice(2);
const runs = JSON.parse(readFileSync(input, "utf8"));
const status = (r) => (r.crashed ? "ERROR" : r.timedOut ? "TIMEOUT" : r.ok ? "PASS" : "FAIL");
const results = runs.map((r) => ({
  id: String(r.name),
  category: r.group ?? null,
  status: status(r),
  score: r.score ?? null,
  elapsed_s: r.seconds ?? null,
  cost_usd: r.usd ?? null,
  note: r.reason ? String(r.reason).slice(0, 300) : null,
}));
writeFileSync(output, JSON.stringify({
  schema: "oss-dashboard/eval-results@1",
  suite: "bench",
  tier: process.env.EVAL_TIER || null,
  model: process.env.EVAL_MODEL || null,
  results,
}, null, 1));
```

Map "the harness crashed" or "a service was down" to `ERROR`, not `FAIL`. That way an outage doesn't read as a drop in quality.
