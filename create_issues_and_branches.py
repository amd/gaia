#!/usr/bin/env python3
"""
Create GitHub issues and branches for remaining GAIA pipeline commits.
Processes in MERGE_ORDER dependency order.

FIX: Use subprocess env for GIT_EDITOR (Windows compatible).
"""

import json
import subprocess
import os
import tempfile
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLANS_FILE = os.path.join(BASE_DIR, "plans", "remaining-plans.json")
LOG_FILE = os.path.join(BASE_DIR, "plans", "execution-log-v2.txt")
RESULTS_FILE = os.path.join(BASE_DIR, "plans", "execution-results-v2.json")

os.environ["PYTHONIOENCODING"] = "utf-8"

# Track existing state from previous run
# Issues already created: #50 through whatever was created in v1 run
# Branches already pushed (19 from v1):
BRANCHES_OK_V1 = {
    "pr-pr720-integration-analysis",   # #58
    "pr-agent-ecosystem-design-spec",  # #61
    "pr-mcp-test-isolation",           # #63
    "pr-pipeline-pr-description",      # #64
    "pr-cpp-sse-streaming",            # #73
    "pr-missing-metrics-modules",      # #78
    "pr-release-v0171",                # #83
    "pr-component-framework-loader",   # #93
    "pr-agent-base-tools",             # #95
    "pr-health-monitoring",            # #98
    "pr-resilience-patterns",          # #99
    "pr-data-protection-perf",         # #100
    "pr-core-orchestration-kernel",    # #104
    "pr-workflow-modeler",             # #113
    "pr-loom-builder",                 # #114
    "pr-autonomous-agent-spawning",    # #117
    "pr-pipeline-executor",            # #118
    "pr-supervisor-decision-tests",    # #120
    "pr-orchestration-user-guide",     # #122
    # Pre-existing from earlier (22 total):
    "pr-runtime-artifact-exclusions", "pr-docs-debt-cleanup", "pr-branch-change-matrix",
    "pr-pdf-bundle-generator", "pr-npm-oidc-publish", "pr-remove-claude-from-git",
    "pr-remove-registry-url", "pr-webui-version-bump", "pr-lemonade-v10-compat-fix",
    "pr-gaia-chat-ui", "pr-v0161-release-notes", "pr-lru-eviction-fix",
    "pr-configurable-agent-tool-isolation", "pr-release-v0170", "pr-pipeline-eval-metrics",
    "pr-version-py-proposal", "pr-cpp-runtime-config", "pr-cpp-perf-benchmarks",
    "pr-baibel-master-spec", "pr-kpi-loom-specs", "pr-phase3-closeout-report",
}

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

def run_shell(cmd, cwd=BASE_DIR, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, cwd=cwd,
        encoding='utf-8', errors='replace',
        env=merged_env
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()

def create_issue(plan):
    title = plan["issue_title"]
    body = plan.get("issue_body", "")
    labels = [l.strip() for l in plan.get("labels", "").split(",") if l.strip()]

    deps = plan.get("depends_on", "").strip()
    if deps:
        dep_list = [d.strip() for d in deps.split(",") if d.strip()]
        if dep_list:
            body += f"\n\n**Dependencies:** {', '.join(dep_list)}"

    issue_data = {"title": title, "body": body, "labels": labels}

    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False,
        encoding='utf-8', dir=BASE_DIR
    ) as f:
        json.dump(issue_data, f, ensure_ascii=False)
        tmpfile = f.name

    try:
        rc, stdout, stderr = run_shell(
            f'gh api repos/antmikinka/gaia/issues -X POST --input "{tmpfile}"'
        )
        if rc == 0:
            resp = json.loads(stdout)
            return resp.get("number"), resp.get("html_url", "")
        else:
            log(f"  ISSUE FAILED: {stderr[:300]}")
            return None, None
    except (json.JSONDecodeError, Exception) as e:
        log(f"  Issue error: {e}")
        return None, None
    finally:
        try:
            os.unlink(tmpfile)
        except:
            pass

def cherry_pick_commit(commit_sha):
    """Cherry-pick with robust conflict resolution (Windows-compatible)."""
    rc, full_sha, _ = run_shell(f"git rev-parse {commit_sha}")
    if rc != 0:
        log(f"  SKIP: commit {commit_sha} not found locally")
        return False

    rc, stdout, stderr = run_shell(
        f"git cherry-pick {full_sha} --strategy=recursive -X theirs"
    )
    if rc == 0:
        return True

    rc, status, _ = run_shell("git status --short")

    conflicted = []
    for line in status.split("\n"):
        line = line.strip()
        if len(line) > 3 and line[2] == ' ':
            sc = line[:2]
            if sc in ("UU", "DU", "UD", "AA", "AU", "UA", "DD"):
                conflicted.append(line[3:].strip())

    if not conflicted:
        if "nothing to commit" in status.lower() or "up to date" in status.lower():
            return True
        log(f"  Cherry-pick failed, no conflict state")
        run_shell("git cherry-pick --abort")
        return False

    log(f"  Resolving {len(conflicted)} conflicted file(s)...")
    for filepath in conflicted:
        try:
            rc_show, content, _ = run_shell(f"git show {full_sha}:\"{filepath}\"")
            if rc_show == 0 and content:
                full_path = os.path.join(BASE_DIR, filepath)
                parent = os.path.dirname(full_path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                with open(full_path, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(content)
                run_shell(f'git add "{filepath}"')
            else:
                run_shell(f'git add "{filepath}"')
        except Exception as e:
            log(f"    Error on {filepath}: {e}")
            run_shell(f'git add "{filepath}"')

    # Continue cherry-pick using env parameter (Windows-compatible)
    rc, stdout, stderr = run_shell(
        "git cherry-pick --continue",
        env={"GIT_EDITOR": "echo"}
    )
    if rc == 0:
        return True

    log(f"  --continue failed: {stderr[:200]}")

    # Fallback: patch apply
    log(f"  Trying patch apply fallback...")
    run_shell("git cherry-pick --abort")
    rc, _, _ = run_shell(f"git show {full_sha} | git apply -3 --ignore-whitespace")
    if rc == 0:
        run_shell("git add -A")
        rc2, _, _ = run_shell(f'git commit -m "cherry-pick {commit_sha}"')
        if rc2 == 0:
            return True

    log(f"  Cherry-pick FAILED for {commit_sha}")
    run_shell("git cherry-pick --abort")
    return False

def create_branch(plan):
    branch = plan["branch"]
    commit = plan["commit"]

    if not branch or branch.startswith("N/A") or " " in branch:
        log(f"  SKIP: invalid branch name '{branch}'")
        return False

    # Skip already-pushed branches from v1
    if branch in BRANCHES_OK_V1:
        log(f"  SKIP: branch already exists from v1")
        return "SKIP_OK"

    run_shell("git checkout -f main")

    rc, _, stderr = run_shell(f"git checkout -B {branch} origin/main")
    if rc != 0:
        log(f"  SKIP: cannot create branch: {stderr[:200]}")
        run_shell("git checkout -f main")
        return False

    if not cherry_pick_commit(commit):
        run_shell("git checkout -f main")
        return False

    rc, stdout, stderr = run_shell(f"git push -u origin {branch}")
    if rc != 0:
        log(f"  PUSH FAILED: {stderr[:200]}")
        run_shell("git checkout -f main")
        return False

    log(f"  Branch {branch} pushed OK")
    run_shell("git checkout -f main")
    return True

def main():
    with open(PLANS_FILE, encoding='utf-8') as f:
        plans = json.load(f)

    plans.sort(key=lambda x: x["merge_order"])

    with open(LOG_FILE, "w", encoding='utf-8') as f:
        f.write(f"Execution v2 started: {datetime.now().isoformat()}\n")
        f.write(f"Total commits: {len(plans)}\n")
        f.write(f"Pre-existing branches: {len(BRANCHES_OK_V1)}\n")
        f.write("=" * 70 + "\n")

    log(f"Processing {len(plans)} commits (v2 with Windows GIT_EDITOR fix)")
    log(f"Skipping {len(BRANCHES_OK_V1)} pre-existing branches")
    log("=" * 70)

    results = []
    success_count = 0
    skip_count = 0

    for i, plan in enumerate(plans):
        n = i + 1
        log(f"\n[{n}/{len(plans)}] MO={plan['merge_order']:3d} | {plan['name']}")
        log(f"  Commit: {plan['commit']} | Branch: {plan['branch']}")

        # Skip already-pushed branches
        if plan["branch"] in BRANCHES_OK_V1:
            log(f"  [SKIP] Branch already pushed from v1")
            skip_count += 1
            results.append({
                "merge_order": plan["merge_order"],
                "name": plan["name"],
                "commit": plan["commit"],
                "branch": plan["branch"],
                "issue_num": "SKIP",
                "issue_url": "",
                "branch_ok": True,
                "status": "SKIP"
            })
            continue

        # Create issue
        issue_num, issue_url = create_issue(plan)
        if issue_num:
            log(f"  Issue #{issue_num}: {issue_url}")

        # Create branch
        branch_result = create_branch(plan)

        if branch_result == "SKIP_OK":
            branch_ok = True
        else:
            branch_ok = branch_result

        ok = issue_num and branch_ok
        status = "OK" if ok else ("PARTIAL" if (issue_num or branch_ok) else "FAIL")
        log(f"  [{status}] Issue={issue_num or 'X'} Branch={'OK' if branch_ok else 'X'}")

        if ok:
            success_count += 1

        results.append({
            "merge_order": plan["merge_order"],
            "name": plan["name"],
            "commit": plan["commit"],
            "branch": plan["branch"],
            "issue_num": issue_num,
            "issue_url": issue_url,
            "branch_ok": branch_ok,
            "status": status
        })

        time.sleep(0.3)

    log(f"\n{'=' * 70}")
    log(f"COMPLETE: {success_count} new OK, {skip_count} skipped (pre-existing)")
    log(f"FAILURES: {len([r for r in results if r['status'] == 'FAIL'])}")
    log(f"PARTIAL: {len([r for r in results if r['status'] == 'PARTIAL'])}")

    with open(RESULTS_FILE, "w", encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    failures = [r for r in results if r["status"] in ("FAIL", "PARTIAL")]
    if failures:
        log(f"\nFAILURES/PARTIAL ({len(failures)}):")
        for f in failures:
            log(f"  - {f['branch']}: issue={f['issue_num']}, branch={'OK' if f['branch_ok'] else 'FAIL'}")

    oks = [r for r in results if r["status"] == "OK"]
    if oks:
        log(f"\nNEW SUCCESS ({len(oks)}):")
        for s in oks:
            log(f"  #{s['issue_num']} {s['branch']}")

if __name__ == "__main__":
    main()
