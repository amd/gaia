#!/usr/bin/env python
"""Single-process driver for the GAIA TUI control API.

Point ``CJ`` at the ``control.json`` inside your ``GAIA_TUI_HOME``, then:

    python util/tui_driver.py status
    python util/tui_driver.py ask "What is 17 times 23?"
    python util/tui_driver.py ladder          # the full capability ladder
    python util/tui_driver.py gate            # gh command-policy tiers (no LLM, instant)

Why one process: spawning a process costs 0.7-2.0s on a Windows/MSYS box with AV
(``curl --version`` alone measures ~2s), while the control API answers in 3ms. A
shell driver that spawns curl+python per step spends all its time in process
creation. Everything here runs in one process over one kept-alive connection.

Always run with PYTHONIOENCODING=utf-8 or captures die on the spinner glyphs.
"""

import http.client
import json
import sys
import time

# --- point this at YOUR GAIA_TUI_HOME ---------------------------------------
CJ = r"C:/Users/kovtchar/AppData/Local/Temp/gaia-tui-solo/control.json"

_conn = None
_HDRS = {}


def _connect():
    """Attach to the running TUI, or say exactly what is missing.

    Deferred rather than done at import: `gate` drives no TUI at all, and
    reading control.json up front made the one check that needs no TUI the one
    check you could not run without one.
    """
    global _conn, _HDRS
    if _conn is not None:
        return _conn
    try:
        info = json.load(open(CJ))
    except FileNotFoundError:
        raise SystemExit(
            f"No TUI control file at {CJ}.\n"
            "Launch the TUI with --control-port and set CJ (top of this file) to "
            "the control.json inside that launch's GAIA_TUI_HOME. "
            "'gate' needs no TUI and does not reach this path."
        )
    _conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=900)
    _HDRS = {
        "Authorization": "Bearer " + info["token"],
        "Content-Type": "application/json",
    }
    return _conn


def call(method, path, body=None):
    """One control-API call, reconnecting once if the connection went stale."""
    _connect()
    payload = json.dumps(body) if body is not None else None
    for attempt in (1, 2):
        try:
            _conn.request(method, "/control/v1/" + path, payload, _HDRS)
            raw = _conn.getresponse().read().decode("utf-8", "replace")
            break
        except (http.client.HTTPException, OSError):
            if attempt == 2:
                raise
            _conn.close()
            _conn.connect()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def screen():
    return call("GET", "screen?format=plain").get("screen", "")


def answer_region():
    """Just the current turn: lines after the last '> You:' marker."""
    body = [ln.rstrip() for ln in screen().split("\n") if ln.strip()]
    idx = max(
        (i for i, ln in enumerate(body) if ln.strip().startswith("▶ You:")),
        default=0,
    )
    return [
        ln
        for ln in body[idx:]
        if not ln.strip().startswith("┃")
        and "GAIA connected" not in ln
        and set(ln.strip()) != {"─"}
    ]


def turn(query, limit=600, poll=False):
    """Send a query and block until the turn settles. Returns (seconds, samples).

    Waits for streaming to START before waiting for it to stop -- otherwise the
    idle-wait matches the pre-turn idle state and returns in 0.0s.
    """
    call("POST", "text", {"text": query, "delay_ms": 0})
    t0 = time.time()
    call("POST", "keys", {"keys": ["enter"], "delay_ms": 40})
    try:
        call("POST", "wait", {"state": {"streaming": True}, "timeout_ms": 20000})
    except Exception:
        pass
    samples = []
    while time.time() - t0 < limit:
        streaming = call("GET", "status")["state"]["streaming"]
        if poll:
            chars = sum(len(ln) for ln in answer_region())
            samples.append((round(time.time() - t0, 1), chars))
        if not streaming:
            break
        time.sleep(1.0 if poll else 1.5)
    elapsed = round(time.time() - t0, 1)
    call("POST", "keys", {"keys": ["end"], "delay_ms": 40})  # else stale scrollback
    time.sleep(0.6)
    return elapsed, samples


def show(label, query, poll=False):
    elapsed, samples = turn(query, poll=poll)
    print(f"===== {label} | {elapsed}s =====")
    for ln in answer_region():
        print("  ", ln.strip()[:112])
    if poll:
        print("   --- answer chars over time (rising == streaming) ---")
        print("  ", " ".join(f"{t}s:{c}" for t, c in samples[:16]))
    print()
    return elapsed


LADDER = [
    ("L1 arithmetic", "What is 17 times 23? Answer with just the number."),
    ("L2 store memory", "Remember that my favourite colour is teal. Just acknowledge."),
    ("L3 recall", "What is my favourite colour? One word."),
    (
        "L4 shell tool",
        "Use your shell tool to run pwd and tell me the directory it printed.",
    ),
    ("L5 load skill", "Load the github-triage skill."),
    ("L6 skill persists", "Which skills do you currently have loaded? Name them."),
    (
        "L7 real triage",
        "Using the github-triage skill, list the 3 most recently opened "
        "issues in the amd/gaia repo with their numbers and titles.",
    ),
]

# One case per tier boundary that has ever been got wrong. Expected verdicts
# are in the tuple so the output is checkable at a glance instead of read and
# nodded at -- a gate check nobody can grade is not a check.
GATE_CASES = [
    ("gh issue list --repo amd/gaia", "allow"),
    ("gh auth status", "allow"),
    ("gh api repos/amd/gaia/issues", "allow"),
    # The last mile: a write the user is offered, not refused.
    ("gh issue create --title x", "confirm"),
    ("gh issue comment 1 --body hi", "confirm"),
    ("gh issue edit 1 --add-label bug", "confirm"),
    # Escalations. These must never reach a prompt -- a gate the user learns to
    # approve is a gate that approves the credential print too.
    ("gh auth token", "refuse"),
    ("gh api -X POST /repos", "refuse"),
    ("gh alias set x !sh", "refuse"),
    ("gh extension install evil", "refuse"),
    ("gh issue close 1", "refuse"),
    ("gh pr merge 1", "refuse"),
    # A write carrying an escalating flag is refused, not asked about.
    ("gh issue create --body-file /etc/passwd", "refuse"),
]


def run_gate():
    """Command-policy tier check. No LLM, no TUI -- instant."""
    from gaia.skills.binaries import BINARY_POLICIES, classify_invocation

    policy = BINARY_POLICIES["gh"]
    bad = 0
    for cmd, expected in GATE_CASES:
        decision = classify_invocation(policy, cmd.split())
        ok = decision.outcome == expected
        bad += not ok
        mark = "ok  " if ok else "WRONG"
        print(
            f"{mark} {decision.outcome.upper():8} {cmd:38} "
            f"{'' if ok else f'(expected {expected})'}"
        )
    print(f"\n{len(GATE_CASES) - bad}/{len(GATE_CASES)} as expected")
    return bad


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        s = call("GET", "status")
        st = s["state"]
        print(
            f"{s['cols']}x{s['rows']} view={st['view']} agent={st.get('agent')} "
            f"streaming={st['streaming']} frame={s['frame_seq']}"
        )
    elif cmd == "screen":
        print(screen())
    elif cmd == "keys":
        call("POST", "keys", {"keys": sys.argv[2:], "delay_ms": 40})
        print(screen())
    elif cmd == "ask":
        show("ask", " ".join(sys.argv[2:]))
    elif cmd == "stream":
        show("stream", " ".join(sys.argv[2:]), poll=True)
    elif cmd == "ladder":
        for label, query in LADDER:
            show(label, query)
    elif cmd == "gate":
        # Exit non-zero on a wrong tier: a check that always passes is not one.
        sys.exit(1 if run_gate() else 0)
    else:
        print(__doc__)
