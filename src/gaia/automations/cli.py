# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia automation``: list, add, edit, pause, resume, run and remove.

The agent drives this through the ``automations`` skill; every change asks the
user first, and listing never does.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from typing import Any, Callable, Optional

from gaia.automations import checks, files, store
from gaia.automations.timing import (
    TimingError,
    format_interval,
    parse_days,
    parse_interval,
    parse_quiet,
    parse_when,
)

Emit = Callable[[str], None]

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _delivery_note() -> str:
    """Say so when nothing would deliver what was just set up."""
    try:
        from gaia.messaging.discord import state

        config = state.load_config()
    except (ImportError, files.StateFileError):
        return ""
    if config.paired:
        return " It arrives in your Discord DM."
    return (
        " Nothing delivers it yet: automations reach you through Discord, so "
        "connect it under Settings → Messaging in the GAIA app."
    )


def _when(item: store.Automation) -> str:
    moment = datetime.fromtimestamp(item.next_at).astimezone()
    today = datetime.now().astimezone().date()
    day = "" if moment.date() == today else moment.strftime("%a %d %b ")
    return f"{day}{moment.strftime('%H:%M')}"


def _watches() -> list:
    """The Discord bridge's GitHub watches, so one list shows everything."""
    try:
        from gaia.messaging.discord import github_watch

        watches = github_watch.load()
    except ImportError:
        return []
    except files.StateFileError as e:
        return [
            {"kind": "watch", "name": "GitHub watches", "status": f"unreadable: {e}"}
        ]
    return [
        {
            "id": f"watch:{w.repo}",
            "name": f"GitHub activity on {w.repo}",
            "kind": "watch",
            "status": "active",
            "does": "DMs each new PR, issue or comment right away, except from "
            + (", ".join(w.own_accounts) if w.own_accounts else "nobody")
            + " and bots",
            "change_with": f"gaia discord watch --repo {w.repo} --mine <accounts>",
            "remove_with": f"gaia discord unwatch --repo {w.repo}",
        }
        for w in watches
    ]


def _waiting() -> list:
    """What already ran and waits for the Discord bridge to deliver it."""
    from gaia.automations import outbox

    return [
        {
            "id": f"waiting:{d.id}",
            "name": d.name,
            "kind": "waiting",
            "status": "ran "
            + time.strftime("%a %H:%M", time.localtime(d.due_at))
            + "; reaches your Discord DM when the Discord bridge runs",
        }
        for d in outbox.pending()
    ]


def run_list(*, as_json: bool = True, emit: Emit = print) -> int:
    items = [store.describe(a) for a in store.load()] + _watches() + _waiting()
    if as_json:
        emit(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    if not items:
        emit("No automations.")
        return 0
    for a in items:
        if a["kind"] == "watch":
            emit(f"{a['id']}  {a['name']} — instant alerts ({a['status']})")
            continue
        if a["kind"] == "waiting":
            emit(f"{a['id']}  {a['name']} — {a['status']}")
            continue
        every = f"every {a['every']}" if "every" in a else "once"
        emit(
            f"{a['id']}  {a['name']} — {a['kind']}, {every}, next {a['next']} "
            f"({a['status']})"
        )
    return 0


def run_add(
    *,
    name: str,
    prompt: Optional[str] = None,
    say: Optional[str] = None,
    every: Optional[str] = None,
    at: Optional[str] = None,
    check: Optional[str] = None,
    alert: bool = False,
    quiet: Optional[str] = None,
    days: Optional[str] = None,
    emit: Emit = print,
) -> int:
    now = time.time()
    try:
        item = _build(name, prompt, say, every, at, check, alert, quiet, now)
        if days:
            if not item.repeats:
                raise store.AutomationError("--days is for a repeating automation.")
            item.days = parse_days(days)
        baseline = None
        if item.check:
            # Tried now, so a check that cannot work fails here, not silently later.
            ok, baseline = checks.run_check(item.check)
            if not ok:
                raise store.AutomationError(f"The check failed when tried: {baseline}")
            item.last_output, item.checked = baseline, True
        item = store.add(item)
    except (store.AutomationError, TimingError, checks.CheckError) as e:
        emit(f"❌ {e}")
        return 2
    emit(_confirmation(item, baseline) + _delivery_note())
    return 0


def _build(name, prompt, say, every, at, check, alert, quiet, now) -> store.Automation:
    name = (name or "").strip()
    prompt, say, check = (
        (prompt or "").strip(),
        (say or "").strip(),
        (check or "").strip(),
    )
    if not name:
        raise store.AutomationError("--name is empty; give it a short name.")
    if not (every or at):
        raise store.AutomationError(
            "Say when: --at for once (10m, 17:30, tomorrow 9am), --every to repeat, "
            "or both for a repeat that starts at a set time (--every 1d --at 8am)."
        )
    if quiet:
        parse_quiet(quiet)
    if at and not every:
        if check or alert:
            raise store.AutomationError("A trigger or alert repeats; use --every.")
        if not (prompt or say):
            raise store.AutomationError("A reminder needs --say or --prompt.")
        fire = parse_when(at, datetime.now().astimezone())
        return store.Automation(
            "",
            name,
            store.KIND_REMINDER,
            prompt=prompt,
            say=say,
            next_at=fire.timestamp(),
        )
    seconds = parse_interval(every)
    if say:
        raise store.AutomationError("--say is for a one-time reminder (--at).")
    if not prompt:
        raise store.AutomationError("--prompt is empty; say what GAIA should do.")
    if check and alert:
        raise store.AutomationError("Use --check or --alert, not both.")
    if check:
        checks.check_argv(check)
        kind = store.KIND_TRIGGER
    else:
        kind = store.KIND_ALERT if alert else store.KIND_SUMMARY
    if at:
        # "Every morning at 8": the first run sets the clock time for all of them.
        first = parse_when(at, datetime.now().astimezone()).timestamp()
    else:
        # An alert looks soon after it is set; a summary waits for its first slot.
        first = now + (60 if kind == store.KIND_ALERT else seconds)
    return store.Automation(
        "",
        name,
        kind,
        prompt=prompt,
        every_seconds=seconds,
        next_at=first,
        check=check,
        quiet=(quiet or "").strip(),
    )


def _confirmation(item: store.Automation, baseline: Optional[str]) -> str:
    quiet = f", quiet {item.quiet}" if item.quiet else ""
    if item.kind == store.KIND_REMINDER:
        return f"Reminder {item.name!r} (id {item.id}) set for {_when(item)}."
    every = format_interval(item.every_seconds)
    if item.kind == store.KIND_TRIGGER:
        return (
            f"Trigger {item.name!r} (id {item.id}): every {every}{quiet} GAIA runs "
            f"`{item.check}` and messages you only when its output changes. Right "
            f"now it prints:\n{baseline}"
        )
    if item.kind == store.KIND_ALERT:
        return (
            f"Alert {item.name!r} (id {item.id}): every {every}{quiet} GAIA checks "
            "and messages you only when something needs your attention."
        )
    return (
        f"Summary {item.name!r} (id {item.id}): every {every}{quiet}, first at "
        f"{_when(item)}."
    )


def run_edit(
    *,
    ref: str,
    rename: Optional[str] = None,
    prompt: Optional[str] = None,
    say: Optional[str] = None,
    every: Optional[str] = None,
    at: Optional[str] = None,
    check: Optional[str] = None,
    quiet: Optional[str] = None,
    days: Optional[str] = None,
    emit: Emit = print,
) -> int:
    changes: dict = {}
    try:
        items = store.load()
        item = store.find(items, ref)
        if item is None:
            raise store.AutomationError(f"No automation is called or numbered {ref!r}.")
        if rename is not None:
            if not rename.strip():
                raise store.AutomationError("--rename is empty; give the new name.")
            changes["name"] = rename.strip()
        if prompt is not None:
            if not prompt.strip() and not item.say:
                raise store.AutomationError(
                    "--prompt is empty; say what GAIA should do."
                )
            changes["prompt"] = prompt.strip()
        if say is not None:
            if item.repeats or (not say.strip() and not item.prompt):
                raise store.AutomationError("--say is a one-time reminder's text.")
            changes["say"] = say.strip()
        if every:
            if not item.repeats:
                raise store.AutomationError(
                    "A one-time reminder takes --at, not --every."
                )
            changes["every_seconds"] = parse_interval(every)
            # Keep its time of day: the next run stays where it was, if still ahead.
            if item.next_at <= time.time():
                changes["next_at"] = time.time() + changes["every_seconds"]
        if at:
            # Once: when it fires. Repeating: when the next run is, which sets the
            # time of day for the rest.
            changes["next_at"] = parse_when(at, datetime.now().astimezone()).timestamp()
        if check is not None:
            if item.kind != store.KIND_TRIGGER:
                raise store.AutomationError("Only a trigger has a --check.")
            ok, output = checks.run_check(check.strip())
            if not ok:
                raise store.AutomationError(f"The check failed when tried: {output}")
            changes.update(
                check=check.strip(), last_output=output, checked=True, failures=0
            )
        if quiet is not None:
            if quiet.strip():
                parse_quiet(quiet)
            changes["quiet"] = quiet.strip()
        if days is not None:
            if not item.repeats:
                raise store.AutomationError("--days is for a repeating automation.")
            changes["days"] = parse_days(days) if days.strip() else ""
        if not changes:
            raise store.AutomationError("Nothing to change; pass what should change.")
        item = store.update(item.id, **changes)
    except (store.AutomationError, TimingError, checks.CheckError) as e:
        emit(f"❌ {e}")
        return 2
    emit(
        f"Updated {item.name!r}: "
        + json.dumps(store.describe(item), ensure_ascii=False)
    )
    return 0


def _remove(ref: str) -> str:
    """Remove an automation, or a message that already ran and still waits."""
    from gaia.automations import outbox

    if ref.startswith("waiting:"):
        dropped = outbox.remove(ref.split(":", 1)[1])
        if dropped is None:
            raise store.AutomationError(
                f"Nothing is waiting as {ref!r}; it was delivered or removed. "
                "List again to see what is left."
            )
        return f"Dropped the waiting message from {dropped.name!r}."
    try:
        return f"Removed {store.remove(ref).name!r}."
    except store.AutomationError:
        # A reminder that already ran is gone from the store; only its message waits.
        waiting = outbox.drop_named(ref)
        if not waiting:
            raise
        return f"Dropped the waiting message from {waiting[0].name!r}."


def run_set(*, ref: str, action: str, emit: Emit = print) -> int:
    """pause, resume, run (now) or remove one automation."""
    try:
        if action == "remove":
            emit(_remove(ref))
            return 0
        changes: Any = {
            "pause": {"paused": True},
            "resume": {"paused": False},
            "run": {"next_at": time.time()},
        }[action]
        if action == "run":
            current = store.find(store.load(), ref)
            if current is not None and current.paused:
                raise store.AutomationError(
                    f"{current.name!r} is paused; resume it to run it."
                )
        item = store.update(ref, **changes)
    except store.AutomationError as e:
        emit(f"❌ {e}")
        return 2
    verb = {
        "pause": "Paused",
        "resume": "Resumed",
        "run": "Running within a minute (after quiet hours, if it has them)",
    }[action]
    emit(f"{verb}: {item.name!r}.")
    return 0


def run_remove_all(*, emit: Emit = print) -> int:
    emit(f"Removed {store.clear()} automation(s).")
    return 0


def run_ci_status(*, repo: str, branch: str, emit: Emit = print) -> int:
    """One stable line: what is failing on *branch*. Built to be a trigger's check."""
    from gaia.automations import github

    repo = repo.strip().strip("/")
    if not _REPO.fullmatch(repo):
        emit(f"❌ {repo!r} is not a repository; write it as OWNER/REPO, like amd/gaia.")
        return 2
    try:
        failing = github.ci_failures(repo, branch, github.github_token())
    except Exception as e:  # noqa: BLE001 - httpx errors, reported as a failed check
        emit(f"❌ Could not read CI for {repo}@{branch}: {e}")
        return 1
    if failing:
        emit(f"{repo}@{branch}: failing — {', '.join(failing)}")
    else:
        emit(f"{repo}@{branch}: all workflows passing")
    return 0


def main(args) -> int:
    """Dispatch one ``gaia automation <action>``."""
    action = getattr(args, "automation_action", None)
    try:
        if action == "list":
            return run_list(as_json=not getattr(args, "plain", False))
        if action == "add":
            return run_add(
                name=args.name,
                prompt=args.prompt,
                say=args.say,
                every=args.every,
                at=args.at,
                check=args.check,
                alert=args.alert,
                quiet=args.quiet,
                days=getattr(args, "days", None),
            )
        if action == "edit":
            return run_edit(
                ref=args.ref,
                rename=args.rename,
                prompt=args.prompt,
                say=args.say,
                every=args.every,
                at=args.at,
                check=args.check,
                quiet=args.quiet,
                days=getattr(args, "days", None),
            )
        if action in ("pause", "resume", "run"):
            return run_set(ref=args.ref, action=action)
        if action == "remove":
            if getattr(args, "all", False):
                return run_remove_all()
            if not getattr(args, "ref", None):
                print(
                    "❌ Name the automation to remove, or pass --all.", file=sys.stderr
                )
                return 2
            return run_set(ref=args.ref, action="remove")
        if action == "ci-status":
            return run_ci_status(repo=args.repo, branch=args.branch)
    except files.StateFileError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(
        "No automation action given. Use: gaia automation "
        "list|add|edit|pause|resume|run|remove|ci-status",
        file=sys.stderr,
    )
    return 2
