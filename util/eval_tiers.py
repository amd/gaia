# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the flagship eval tier, build its lane matrix, and keep tiers honest.

    python util/eval_tiers.py decide       >> "$GITHUB_OUTPUT"   # eval-gate job
    python util/eval_tiers.py matrix       >> "$GITHUB_OUTPUT"   # lanes job
    python util/eval_tiers.py check                              # coverage report
    python util/eval_tiers.py scenarios --tier smoke             # comma-separated ids
    python util/eval_tiers.py verify-final                       # final-tag job

Tiers (``eval/ci_tiers.json`` holds the lists and why each scenario is there):

- ``smoke``: every RC tag, and a dispatch's default.
- ``full``: an RC nominated for promotion, by ``[full eval]`` in its annotated
  tag message or a dispatch with ``tier=full``. Every lane, minus the weekly
  backlog's ``leaves_full`` scenarios.
- ``backlog``: the weekly schedule. Skipped, with a notice, while another run
  of the workflow holds the pool.
- ``verify``: a final ``vX.Y.Z`` tag. Nothing re-runs; ``verify-final`` checks a
  full sweep measured this commit or one of its RCs.
- ``targeted``: a dispatch naming ``categories`` or ``scenarios`` (#5513).

Standard library only: the jobs that call this run before any venv exists.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_lane_matrix as lanes  # noqa: E402
from release_tag import FINAL_TAG_RE, RC_TAG_RE  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
TIERS_FILE = "eval/ci_tiers.json"
LANES_FILE = "eval/ci_lanes.json"
WORKFLOW_FILE = "eval_flagship.yml"

#: Never pruned, never moved to the weekly lane (#5493 section 6.6).
PROTECTED_CATEGORIES = frozenset(
    {"gaia_honesty", "adversarial", "error_recovery", "gaia_resilience"}
)
FULL_EVAL_RE = re.compile(r"\[full eval\]", re.IGNORECASE)
DISPATCH_TIERS = ("smoke", "full", "backlog")
#: Uploaded by a full sweep's aggregate job; verify-final looks for it.
FULL_MARKER = "flagship-tier-full"


class TierError(ValueError):
    """The tier file or a request is unusable; the message is an ``::error::`` body."""


# --- the suite ----------------------------------------------------------------


def is_protected(sid: str, meta: dict) -> bool:
    return (
        meta["category"] in PROTECTED_CATEGORIES
        or "security" in meta["tags"]
        or "injection" in sid
    )


def suite(lanes_doc: dict, index: dict) -> tuple[dict, dict]:
    """``(runnable, not_in_ci)``: id -> category, and id -> why it never runs."""
    claimed = {c for lane in lanes_doc["lanes"] for c in lane["categories"]}
    excluded = lanes_doc.get("excluded") or {}
    runnable, not_in_ci = {}, {}
    for sid, meta in index.items():
        category = meta["category"]
        if category not in claimed:
            not_in_ci[sid] = excluded.get(category, f"no lane runs '{category}'")
            continue
        hit = sorted(set(meta["tags"]) & set(lanes.GAIA_LANE_EXCLUDE_TAGS))
        if category.startswith(lanes.GAIA_PREFIX) and hit:
            not_in_ci[sid] = f"tagged {hit}, which the gaia_* lanes exclude"
            continue
        runnable[sid] = category
    return runnable, not_in_ci


def tier_entries(doc: dict, tier: str) -> list[tuple[str, dict]]:
    """``(machine, entry)`` for every scenario a tier lists, in file order."""
    machines = ((doc.get("tiers") or {}).get(tier) or {}).get("machines") or []
    return [(m["machine"], e) for m in machines for e in m.get("scenarios") or []]


def leaves_full(doc: dict) -> set[str]:
    return {e["id"] for _, e in tier_entries(doc, "backlog") if e.get("leaves_full")}


# --- the coverage check -------------------------------------------------------


@dataclass
class Coverage:
    runnable: dict
    not_in_ci: dict
    smoke: set = field(default_factory=set)
    full: set = field(default_factory=set)
    backlog: set = field(default_factory=set)
    leaves_full: set = field(default_factory=set)

    def report(self) -> str:
        union = self.smoke | self.full | self.backlog
        lines = [
            f"Suite: {len(self.runnable) + len(self.not_in_ci)} scenarios, "
            f"{len(self.runnable)} runnable in CI.",
            f"  smoke   {len(self.smoke):4d}",
            f"  full    {len(self.full):4d}  (runnable minus {len(self.leaves_full)} "
            "that moved to the weekly backlog)",
            f"  backlog {len(self.backlog):4d}",
            f"  union   {len(union):4d} of {len(self.runnable)} runnable: "
            + ("every one covered" if union == set(self.runnable) else "GAPS"),
        ]
        if self.not_in_ci:
            lines.append(f"Never run in CI, with the reason ({len(self.not_in_ci)}):")
            lines += [f"  {sid}: {why}" for sid, why in sorted(self.not_in_ci.items())]
        return "\n".join(lines)


def covered(include: list[dict], runnable: dict) -> set[str]:
    """The scenario ids a matrix actually runs.

    A category with a ``scenarios`` pick runs those ids; one without runs every
    runnable scenario in it, as the run step does.
    """
    ids = set()
    for entry in include:
        picks = dict(
            pair.split("=", 1)
            for pair in (entry.get("scenarios") or "").split()
            if pair
        )
        for category in entry["categories"].split():
            if category in picks:
                ids |= set(picks[category].split(","))
            else:
                ids |= {s for s, c in runnable.items() if c == category}
    return ids


def check(
    doc: dict,
    lanes_doc: dict,
    index: dict,
    scenarios_dir: Path | None = None,
    repo_root: Path = REPO_ROOT,
):
    """Validate the tier file against the suite. Raises TierError listing every problem.

    Coverage is computed from the matrices the workflow would run, not from the
    tier lists, so a bug in how a matrix is built shows up as a gap here.
    """
    scenarios_dir = scenarios_dir or repo_root / "eval" / "scenarios"
    runnable, not_in_ci = suite(lanes_doc, index)
    cov = Coverage(runnable=runnable, not_in_ci=not_in_ci)
    problems = []
    limit = doc.get("max_machines")
    if not isinstance(limit, int) or limit < 1:
        problems.append("max_machines must be a positive integer")
        limit = 0

    lane_names = {lane["lane"] for lane in lanes_doc["lanes"]}
    seen_machines = set()
    for tier in ("smoke", "backlog"):
        machines = ((doc.get("tiers") or {}).get(tier) or {}).get("machines") or []
        if not machines:
            problems.append(f"tier '{tier}' lists no machines, so it would run nothing")
        if limit and len(machines) > limit:
            problems.append(
                f"tier '{tier}' needs {len(machines)} machines; the limit is {limit}"
            )
        ids = set()
        for machine in machines:
            name = machine.get("machine")
            if not name or name in seen_machines or name in lane_names:
                problems.append(
                    f"machine name {name!r} is missing or reused; each names its own "
                    "artifact (eval-<machine>), so it must be unique across lanes and tiers"
                )
            seen_machines.add(name)
            categories = set()
            for entry in machine.get("scenarios") or []:
                sid = entry.get("id")
                if not entry.get("why"):
                    problems.append(f"{tier}/{name}: '{sid}' has no `why`")
                if sid in ids:
                    problems.append(f"{tier}: '{sid}' is listed twice")
                ids.add(sid)
                if sid not in index:
                    problems.append(f"{tier}/{name}: '{sid}' is not a scenario id")
                    continue
                if sid not in runnable:
                    problems.append(
                        f"{tier}/{name}: '{sid}' never runs in CI ({not_in_ci[sid]})"
                    )
                    continue
                categories.add(runnable[sid])
                if "leaves_full" in entry and tier != "backlog":
                    problems.append(f"{tier}: '{sid}' sets leaves_full outside backlog")
                if entry.get("leaves_full") and is_protected(sid, index[sid]):
                    problems.append(
                        f"'{sid}' is protected and may never leave the full sweep "
                        "(security, honesty, injection, adversarial, error recovery, "
                        "resilience). Drop its leaves_full."
                    )
            gaia = {c for c in categories if c.startswith(lanes.GAIA_PREFIX)}
            if gaia and gaia != categories:
                problems.append(
                    f"{tier}/{name} mixes gaia_* and other categories "
                    f"({sorted(categories)}). gaia_* need fixtures and auto-approval "
                    "the others must not run with; give them separate machines."
                )
    cov.leaves_full = leaves_full(doc) & set(runnable)
    if not problems:
        full_lanes = lanes.build_matrix(
            lanes_doc, lanes.categories_on_disk(scenarios_dir), LANES_FILE
        )
        cov.full = covered(full_matrix(doc, lanes_doc, index, full_lanes), runnable)
        for tier in ("smoke", "backlog"):
            setattr(cov, tier, covered(tier_matrix(doc, index, tier), runnable))
        if cov.full & cov.leaves_full:
            problems.append(
                f"the full sweep still runs {sorted(cov.full & cov.leaves_full)}, "
                "which left it for the weekly backlog"
            )
        gaps = set(runnable) - (cov.smoke | cov.full | cov.backlog)
        if gaps:
            problems.append(
                f"no tier runs {sorted(gaps)}: they would stop being measured with "
                "every lane green"
            )

    baselines = doc.get("baselines") or {}
    if doc.get("halo_model") not in baselines:
        problems.append(
            f"halo_model {doc.get('halo_model')!r} has no entry in baselines"
        )
    for model, rel in baselines.items():
        meta_path = repo_root / rel / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"baseline for {model}: cannot read {meta_path} ({exc})")
            continue
        if meta.get("model") != model:
            problems.append(
                f"baseline for {model} is {rel}, but its meta.json names "
                f"{meta.get('model')!r}. A run would be compared with another model."
            )
        if not meta.get("eval_model"):
            problems.append(f"{meta_path} has no eval_model")

    if problems:
        raise TierError(f"{TIERS_FILE} is not usable: " + " | ".join(problems))
    return cov


# --- the matrix ---------------------------------------------------------------


def machine_env(categories: list[str]) -> dict:
    """Job-level env for a machine; memory alone gets the live store when mixed."""
    if "memory" in categories and len(categories) > 1:
        if any(c.startswith(lanes.GAIA_PREFIX) for c in categories):
            raise TierError(f"memory cannot share a machine with gaia_*: {categories}")
        others = [c for c in categories if c != "memory"]
        return {**lanes.lane_env(others), "memory_categories": "memory"}
    return {**lanes.lane_env(categories), "memory_categories": ""}


def tier_matrix(doc: dict, index: dict, tier: str) -> list[dict]:
    include = []
    for machine in doc["tiers"][tier]["machines"]:
        picked: dict[str, list[str]] = {}
        for entry in machine["scenarios"]:
            picked.setdefault(index[entry["id"]]["category"], []).append(entry["id"])
        categories = list(picked)
        include.append(
            {
                "lane": machine["machine"],
                "categories": " ".join(categories),
                "scenarios": " ".join(f"{c}={','.join(picked[c])}" for c in categories),
                **machine_env(categories),
            }
        )
    return include


def full_matrix(doc: dict, lanes_doc: dict, index: dict, full_lanes: list[dict]):
    """The lane matrix with the backlog's leaves_full scenarios taken out."""
    runnable, _ = suite(lanes_doc, index)
    gone = leaves_full(doc)
    include = []
    for lane in full_lanes:
        keep, picks = [], []
        for category in lane["categories"].split():
            ids = sorted(s for s, c in runnable.items() if c == category)
            left = [s for s in ids if s not in gone]
            if not left:
                continue
            keep.append(category)
            if len(left) < len(ids):
                picks.append(f"{category}={','.join(left)}")
        if keep:
            include.append(
                {
                    **lane,
                    "categories": " ".join(keep),
                    "scenarios": " ".join(picks),
                    "memory_categories": "",
                }
            )
    return include


def resolve_model(doc: dict, requested: str) -> tuple[str, str]:
    model = requested.strip() or doc["halo_model"]
    baselines = doc["baselines"]
    if model not in baselines:
        raise TierError(
            f"model '{model}' has no baseline directory in {TIERS_FILE}, so its "
            f"results could only be compared with another model's. Valid: "
            f"{', '.join(sorted(baselines))}. To add one, give it a baseline "
            "directory whose meta.json names it."
        )
    return model, baselines[model]


def build(env: dict, doc: dict, lanes_doc: dict, index: dict, scenarios_dir: Path):
    """Everything the lanes job emits, for the tier in ``env``."""
    tier = env.get("EVAL_TIER", "").strip()
    model, baseline = resolve_model(doc, env.get("EVAL_AGENT_MODEL", ""))
    out = {"tier": tier, "agent_model": model, "baseline_dir": baseline}
    full_lanes = lanes.build_matrix(
        lanes_doc, lanes.categories_on_disk(scenarios_dir), LANES_FILE
    )
    if tier in ("verify", "none"):
        what = (
            "final tag: nothing re-runs, verify-final checks a full sweep measured it"
            if tier == "verify"
            else "skipped: another run holds the pool"
        )
        return {
            **out,
            "include": [],
            "targeted": False,
            "outcomes": False,
            "selection": what,
        }
    if tier == "targeted":
        chosen = lanes.select(
            full_lanes,
            index,
            lanes.parse_list(env.get("EVAL_SELECT_CATEGORIES")),
            lanes.parse_list(env.get("EVAL_SELECT_SCENARIOS")),
            lanes_doc.get("excluded") or {},
        )
        include = [{**e, "memory_categories": ""} for e in chosen["include"]]
        return {
            **out,
            "include": include,
            "targeted": chosen["targeted"],
            "outcomes": chosen["outcomes"],
            "selection": chosen["description"],
        }
    if tier == "full":
        include = full_matrix(doc, lanes_doc, index, full_lanes)
        moved = len(leaves_full(doc))
        return {
            **out,
            "include": include,
            "targeted": False,
            "outcomes": True,
            "selection": (
                f"FULL SWEEP on {model}: every lane, except the {moved} weekly-backlog "
                "scenarios that left it (eval/ci_tiers.json)"
            ),
        }
    if tier in ("smoke", "backlog"):
        include = tier_matrix(doc, index, tier)
        ids = [e["id"] for _, e in tier_entries(doc, tier)]
        protected = sum(is_protected(s, index[s]) for s in ids)
        label = "RC SMOKE tier" if tier == "smoke" else "WEEKLY BACKLOG tier"
        return {
            **out,
            "include": include,
            "targeted": True,
            "outcomes": False,
            "selection": (
                f"{label} on {model}: {len(ids)} scenarios ({protected} protected) "
                f"from eval/ci_tiers.json on {len(include)} machines; outcomes job skipped"
            ),
        }
    raise TierError(
        f"unknown tier {tier!r}; expected one of smoke, full, backlog, targeted, verify, none"
    )


# --- which tier ---------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    tier: str
    notice: str = ""


def decide(
    event: str,
    ref_type: str,
    tag: str,
    tag_object_type: str = "",
    tag_message: str = "",
    dispatch_tier: str = "",
    targeted: bool = False,
    busy: list[str] | None = None,
) -> Decision:
    """Pure decision: which tier this trigger runs."""
    if event == "workflow_dispatch":
        if targeted:
            return Decision("targeted")
        tier = dispatch_tier.strip() or "smoke"
        if tier not in DISPATCH_TIERS:
            raise TierError(
                f"tier input {tier!r}; expected one of {', '.join(DISPATCH_TIERS)}"
            )
        return Decision(tier)
    if event == "schedule":
        if busy:
            return Decision(
                "none",
                "Weekly backlog skipped: another eval_flagship run holds the Strix Halo "
                f"pool ({', '.join(busy)}). Release evals come first; the next weekly "
                "run measures the backlog.",
            )
        return Decision("backlog")
    if event == "push" and ref_type == "tag":
        nominated = bool(FULL_EVAL_RE.search(tag_message)) and tag_object_type == "tag"
        if FINAL_TAG_RE.match(tag):
            note = (
                f"{tag} is a final release: the eval is not re-run. A full sweep of this "
                "commit or one of its RCs must already exist; verify-final checks."
            )
            if nominated:
                note += " Its [full eval] marker is ignored."
            return Decision("verify", note)
        if RC_TAG_RE.match(tag) and nominated:
            return Decision(
                "full",
                f"{tag} is nominated for promotion ([full eval] in its tag message): "
                "running the full sweep.",
            )
        return Decision(
            "smoke",
            f"{tag} runs the RC smoke tier. To run the full sweep, tag the RC you "
            "will promote with [full eval] in its message, or dispatch with tier=full.",
        )
    raise TierError(
        f"eval_flagship.yml has no tier for event '{event}' (ref type '{ref_type}'). "
        "Add one to util/eval_tiers.py::decide before adding the trigger."
    )


# --- GitHub plumbing ----------------------------------------------------------


def gh_api(path: str) -> dict:
    """``gh api <path>`` as JSON. Raises with gh's own error on failure."""
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh api {path}` failed (exit {result.returncode}): {result.stderr.strip()}. "
            "Check the job's GH_TOKEN and its `actions: read` permission."
        )
    return json.loads(result.stdout)


def busy_runs(repo: str, current_run: str, api=gh_api) -> list[str]:
    """Other runs of this workflow that are queued or running, except schedules."""
    busy = []
    for status in ("in_progress", "queued"):
        runs = api(
            f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?status={status}&per_page=50"
        ).get("workflow_runs", [])
        for run in runs:
            if str(run["id"]) == str(current_run) or run.get("event") == "schedule":
                continue
            busy.append(
                f"run {run['id']} ({run.get('event')}, {run.get('head_branch')})"
            )
    return busy


def rc_tag_shas(final_tag: str, ls_remote: str) -> dict:
    """``{rc_tag: commit}`` for the final's RCs, from ``git ls-remote --tags`` output."""
    base = FINAL_TAG_RE.match(final_tag)
    if not base:
        raise TierError(f"{final_tag} is not a final release tag")
    shas: dict[str, str] = {}
    for line in ls_remote.splitlines():
        sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        m = RC_TAG_RE.match(name)
        if not m or m.group("base") != final_tag[1:]:
            continue
        if peeled or name not in shas:
            shas[name] = sha
    return dict(
        sorted(shas.items(), key=lambda kv: int(RC_TAG_RE.match(kv[0]).group("rc")))
    )


@dataclass
class Verification:
    ok: bool
    lines: list[str]


def verify_final(
    repo: str, tag: str, sha: str, rc_shas: dict, current_run: str, api=gh_api
) -> Verification:
    """Find a full-sweep run of this commit, or of one of its RCs' commits."""
    candidates = [(tag, sha)] + [(rc, s) for rc, s in rc_shas.items()]
    lines = [f"## Final tag {tag}: was this release measured by a full sweep?", ""]
    found = []
    measured_final = False
    looked = set()
    for name, commit in candidates:
        if commit in looked:
            continue
        looked.add(commit)
        runs = api(
            f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?head_sha={commit}&per_page=100"
        ).get("workflow_runs", [])
        for run in runs:
            if str(run["id"]) == str(current_run) or run.get("status") != "completed":
                continue
            artifacts = api(
                f"repos/{repo}/actions/runs/{run['id']}/artifacts?name={FULL_MARKER}"
            ).get("artifacts", [])
            if artifacts:
                measured_final = measured_final or commit == sha
                same = (
                    "same commit as the final"
                    if commit == sha
                    else f"commit {commit[:9]}"
                )
                found.append(
                    f"- {run['html_url']} ({run.get('event')}, {same}, via {name}, "
                    f"conclusion `{run.get('conclusion')}`)"
                )
    if found:
        lines.append("Yes. Full-sweep runs found:")
        lines += found
        if not measured_final:
            lines += [
                "",
                f"None of them measured {sha[:9]} itself. The release train promotes the "
                "RC's commit unchanged; if this final was built from a different commit, "
                "its diff from the measured RC was never evaluated.",
            ]
        return Verification(True, lines)
    tried = ", ".join(f"{n} ({c[:9]})" for n, c in candidates)
    lines += [
        f"**No.** No completed eval_flagship.yml run of {tried} uploaded the "
        f"`{FULL_MARKER}` marker, so no full sweep measured this release.",
        "",
        "The RC smoke tier is 30 scenarios, not a release verdict. Dispatch the full "
        f"sweep on the commit you are releasing: `gh workflow run {WORKFLOW_FILE} "
        f"--ref {tag} -f tier=full`, and nominate the next release's RC with "
        "`[full eval]` in its tag message.",
    ]
    return Verification(False, lines)


# --- entry points -------------------------------------------------------------


def _load(rel: str) -> dict:
    path = REPO_ROOT / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TierError(f"cannot read {path}: {exc}. Fix the JSON.") from exc


def _context():
    doc, lanes_doc = _load(TIERS_FILE), _load(LANES_FILE)
    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    index = lanes.scenario_index(scenarios_dir)
    return doc, lanes_doc, index, scenarios_dir


def _append(var: str, text: str) -> None:
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def cmd_decide() -> int:
    import eval_tag_gate

    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    otype, message = "", ""
    if event == "push" and ref_type == "tag":
        otype, message = eval_tag_gate.read_tag(tag)
    busy = None
    if event == "schedule":
        busy = busy_runs(
            os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_RUN_ID", "")
        )
    targeted = bool(
        os.environ.get("EVAL_SELECT_CATEGORIES", "").strip()
        or os.environ.get("EVAL_SELECT_SCENARIOS", "").strip()
    )
    decision = decide(
        event,
        ref_type,
        tag,
        otype,
        message,
        os.environ.get("EVAL_DISPATCH_TIER", ""),
        targeted,
        busy,
    )
    print(f"tier={decision.tier}")
    if decision.notice:
        print(f"::notice title=Eval tier::{decision.notice}", file=sys.stderr)
        _append(
            "GITHUB_STEP_SUMMARY",
            f"**Eval tier `{decision.tier}`:** {decision.notice}\n",
        )
    return 0


def cmd_matrix() -> int:
    doc, lanes_doc, index, scenarios_dir = _context()
    check(doc, lanes_doc, index, scenarios_dir)
    result = build(os.environ, doc, lanes_doc, index, scenarios_dir)
    print(f"matrix={json.dumps({'include': result['include']})}")
    print(f"has_lanes={'true' if result['include'] else 'false'}")
    print(f"targeted={'true' if result['targeted'] else 'false'}")
    print(f"outcomes={'true' if result['outcomes'] else 'false'}")
    print(f"selection={result['selection']}")
    print(f"tier={result['tier']}")
    print(f"agent_model={result['agent_model']}")
    print(f"baseline_dir={result['baseline_dir']}")
    names = ", ".join(e["lane"] for e in result["include"]) or "none"
    print(
        f"Tier {result['tier']}, model {result['agent_model']}: lanes {names}. "
        f"{result['selection']}",
        file=sys.stderr,
    )
    return 0


def cmd_check() -> int:
    doc, lanes_doc, index, scenarios_dir = _context()
    print(check(doc, lanes_doc, index, scenarios_dir).report())
    return 0


def cmd_scenarios(tier: str) -> int:
    doc, lanes_doc, index, scenarios_dir = _context()
    check(doc, lanes_doc, index, scenarios_dir)
    print(",".join(e["id"] for _, e in tier_entries(doc, tier)))
    return 0


def cmd_verify_final() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    tag = os.environ["GITHUB_REF_NAME"]
    sha = os.environ["GITHUB_SHA"]
    ls = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}-rc*"],
        capture_output=True,
        text=True,
        check=False,
    )
    if ls.returncode != 0:
        raise RuntimeError(f"git ls-remote failed: {ls.stderr.strip()}")
    result = verify_final(
        repo, tag, sha, rc_tag_shas(tag, ls.stdout), os.environ.get("GITHUB_RUN_ID", "")
    )
    text = "\n".join(result.lines) + "\n"
    print(text)
    _append("GITHUB_STEP_SUMMARY", text)
    if not result.ok:
        print(
            f"::error title=No full sweep for {tag}::No eval_flagship.yml full sweep "
            f"measured {tag} or any of its RCs. The release was not evaluated beyond the "
            "RC smoke tier. See this job's summary for how to run one."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "action", choices=["decide", "matrix", "check", "scenarios", "verify-final"]
    )
    parser.add_argument("--tier", choices=["smoke", "backlog"], default="smoke")
    args = parser.parse_args(argv)
    try:
        if args.action == "decide":
            return cmd_decide()
        if args.action == "matrix":
            return cmd_matrix()
        if args.action == "check":
            return cmd_check()
        if args.action == "scenarios":
            return cmd_scenarios(args.tier)
        return cmd_verify_final()
    except (TierError, lanes.LaneMapError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
