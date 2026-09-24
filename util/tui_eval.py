"""Drive gaia_* eval scenarios through the live TUI control API.

Local no-Lemonade validation runner (plan §7): reads the same scenario YAMLs as
``gaia eval agent``, sends each turn's ``user_message`` through the TUI, and
checks deterministic ``ground_truth.expected_answer`` values. Turns without an
explicit expected answer are captured as NEEDS_JUDGE transcripts for a
separate judging pass — a captured transcript is evidence, not a verdict.

The TUI is restarted between scenarios: ``/clear`` on the subprocess transport
resets only the view, not the agent's ``conversation_history`` (recorded as a
product finding in the validation report), and scenario isolation must be real.

Usage (from the repo root, one process per batch):
  python util/tui_eval.py --launcher <ps1> --control-json <path> \
      --category gaia_core [--category ...] [--scenario id] \
      [--exclude-tag local_blocked_no_embedder --exclude-tag live] \
      --out eval/results/gaia-local-tui-validation
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

TURN_TIMEOUT_S = int(os.environ.get("TUI_EVAL_TURN_TIMEOUT_S", "240"))
LAUNCH_TIMEOUT_S = 150  # cold TUI+agent spawn measured ~60s on the dev box


class Control:
    """Minimal control-API client (one HTTP connection, ~3ms per call)."""

    def __init__(self, control_json: Path):
        info = json.loads(control_json.read_text(encoding="utf-8"))
        self.conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=30)
        self.headers = {
            "Authorization": "Bearer " + info["token"],
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = json.dumps(body) if body is not None else None
        self.conn.request(method, f"/control/v1/{path}", payload, self.headers)
        resp = self.conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        if resp.status >= 400:
            raise RuntimeError(f"control {path} -> {resp.status}: {data[:200]}")
        return json.loads(data) if data else {}

    def wait(self, timeout_ms: int, **cond) -> dict:
        return self.call("POST", "wait", {"timeout_ms": timeout_ms, **cond})

    def screen(self) -> str:
        return self.call("GET", "screen?format=plain").get("screen", "")

    def status(self) -> dict:
        return self.call("GET", "status")


def kill_tuis() -> None:
    subprocess.run(
        ["taskkill", "/IM", "gaia-drive.exe", "/F"],
        capture_output=True,
        check=False,
    )
    time.sleep(1.0)


def launch(launcher: Path, control_json: Path) -> Control:
    """Kill any TUI, launch fresh, wait for a NEW control file + chat view."""
    kill_tuis()
    try:
        control_json.unlink()
    except FileNotFoundError:
        pass
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
        ],
        check=True,
        capture_output=True,
    )
    deadline = time.time() + LAUNCH_TIMEOUT_S
    while time.time() < deadline:
        if control_json.is_file():
            try:
                ctl = Control(control_json)
                st = ctl.status().get("state", {})
                if st.get("view") == "chat":
                    return ctl
            except (OSError, RuntimeError, json.JSONDecodeError):
                pass
        time.sleep(1.0)
    raise RuntimeError(f"TUI did not come up within {LAUNCH_TIMEOUT_S}s")


def answer_region(screen_text: str) -> str:
    """Text after the last '▶ You:' line — the current turn's answer."""
    marker = screen_text.rfind("▶ You:")
    tail = screen_text[marker:] if marker >= 0 else screen_text
    lines = tail.splitlines()[1:]
    return "\n".join(line.rstrip() for line in lines).strip()


def run_turn(ctl: Control, message: str) -> str:
    send_line(ctl, message)
    ctl.wait(30_000, state={"streaming": True})
    ctl.wait(TURN_TIMEOUT_S * 1000, state={"streaming": False})
    ctl.call("POST", "keys", {"keys": ["end"], "delay_ms": 40})
    time.sleep(0.6)
    return answer_region(ctl.screen())


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check_expected(answer: str, expected) -> bool:
    if expected is None:
        return False
    return norm(str(expected)) in norm(answer)


def send_line(ctl: Control, text: str) -> None:
    """Type a line and submit it — the text endpoint does not submit itself."""
    ctl.call("POST", "text", {"text": text, "delay_ms": 0})
    ctl.call("POST", "keys", {"keys": ["enter"], "delay_ms": 40})


def clear_conversation(ctl: Control) -> None:
    """Start a fresh conversation via /clear (view AND agent history —
    the subprocess transport implements TranscriptResetter as of this branch)."""
    send_line(ctl, "/clear")
    time.sleep(0.8)
    screen_text = ctl.screen()
    if "▶ You:" in screen_text:
        raise RuntimeError("/clear did not empty the transcript view")


def run_scenario(scenario: dict, ctl: Control, out: Path) -> dict:
    sid = scenario["id"]
    clear_conversation(ctl)
    transcript: list[dict] = []
    verdict = "PASS"
    checked = 0
    for turn in scenario.get("turns", []):
        msg = turn.get("user_message")
        if not msg:
            transcript.append(
                {"turn": turn.get("turn"), "skipped": "no user_message (judge-only)"}
            )
            continue
        started = time.time()
        try:
            answer = run_turn(ctl, msg)
        except Exception as exc:  # timeout/API failure is a scenario ERROR, loudly
            transcript.append({"turn": turn.get("turn"), "error": str(exc)})
            verdict = "ERROR"
            break
        entry = {
            "turn": turn.get("turn"),
            "user": msg,
            "answer": answer,
            "elapsed_s": round(time.time() - started, 1),
        }
        gt = turn.get("ground_truth") or {}
        if "expected_answer" in gt and gt["expected_answer"] is not None:
            ok = check_expected(answer, gt["expected_answer"])
            entry["expected"] = gt["expected_answer"]
            entry["match"] = ok
            checked += 1
            if not ok:
                # expected_answer is SEMANTIC ground truth (judge harness
                # vocabulary) — a containment miss is judge-undecided, not a
                # deterministic FAIL: correct answers phrase things differently.
                verdict = "NEEDS_JUDGE"
        transcript.append(entry)
    if verdict == "PASS" and checked == 0:
        verdict = "NEEDS_JUDGE"
    result = {
        "scenario": sid,
        "category": scenario.get("category"),
        "tags": scenario.get("tags", []),
        "verdict": verdict,
        "deterministic_checks": checked,
        "transcript": transcript,
    }
    (out / f"{sid}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--launcher", required=True, type=Path)
    ap.add_argument("--control-json", required=True, type=Path)
    ap.add_argument("--category", action="append", default=[])
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--exclude-tag", action="append", default=[])
    ap.add_argument(
        "--restart-per-scenario",
        action="store_true",
        help="Full TUI restart between scenarios — needed when a scenario "
        "mutates state /clear does not reset (loaded skills persist).",
    )
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    from gaia.eval.runner import find_scenarios

    found: list[tuple] = []
    if args.scenario:
        found = find_scenarios(scenario_id=args.scenario)
    else:
        for cat in args.category:
            found += find_scenarios(
                category=cat, exclude_tags=args.exclude_tag or None
            )
    if not found:
        print("No scenarios matched.", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    ctl = launch(args.launcher, args.control_json)
    results = []
    for _path, data in found:
        print(f"[RUN ] {data['id']}", flush=True)
        if args.restart_per_scenario:
            ctl = launch(args.launcher, args.control_json)
        try:
            res = run_scenario(data, ctl, args.out)
        except Exception as exc:
            # A wedged TUI poisons every later scenario — relaunch and retry once.
            print(f"[RETRY] {data['id']}: {exc}", flush=True)
            ctl = launch(args.launcher, args.control_json)
            res = run_scenario(data, ctl, args.out)
        print(f"[{res['verdict']:<5}] {data['id']}", flush=True)
        results.append(res)
        if res["verdict"] == "ERROR":
            ctl = launch(args.launcher, args.control_json)

    summary = {
        "total": len(results),
        "pass": sum(r["verdict"] == "PASS" for r in results),
        "fail": sum(r["verdict"] == "FAIL" for r in results),
        "error": sum(r["verdict"] == "ERROR" for r in results),
        "needs_judge": sum(r["verdict"] == "NEEDS_JUDGE" for r in results),
        "results": [
            {k: r[k] for k in ("scenario", "category", "verdict")} for r in results
        ],
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["fail"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
