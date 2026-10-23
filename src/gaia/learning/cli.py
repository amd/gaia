# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia learn`` — nightly on-device learning."""

import argparse
import json
import sys
from typing import Any, Dict

from gaia.learning.errors import LearningError


def add_learn_parser(subparsers, parent_parser) -> None:
    p = subparsers.add_parser(
        "learn",
        help="Nightly on-device learning: train a LoRA from your own history "
        "(setup|status|enable|disable|config|run|stop|analyze|list|show|"
        "activate|deactivate|delete|items|forget|runs)",
        parents=[parent_parser],
    )
    sub = p.add_subparsers(dest="learn_action", help="learn action to perform")

    s = sub.add_parser(
        "setup", help="Install the training toolchain (ROCm PyTorch, PEFT, converter)"
    )
    s.add_argument(
        "--backend",
        choices=["peft", "unsloth"],
        default=None,
        help="Trainer to install (default: the configured one)",
    )
    s.add_argument(
        "--check", action="store_true", help="Only check the installed toolchain"
    )

    st = sub.add_parser(
        "status", help="Settings, toolchain, active adapter and the latest run"
    )
    st.add_argument("--json", action="store_true")

    sub.add_parser("enable", help="Turn nightly learning on")
    sub.add_parser("disable", help="Turn nightly learning off")

    c = sub.add_parser("config", help="Show or change learning settings")
    c.add_argument("--window", help="Local time window, e.g. 01:00-06:00")
    c.add_argument(
        "--idle-minutes", type=int, help="Minutes of inactivity before training starts"
    )
    c.add_argument("--base-model", help="Chat model to personalise")
    c.add_argument("--backend", choices=["peft", "unsloth"])
    c.add_argument(
        "--allow-battery", action="store_true", help="Train on battery power too"
    )
    c.add_argument(
        "--require-ac", action="store_true", help="Train only on mains power"
    )
    c.add_argument("--rank", type=int)
    c.add_argument("--epochs", type=int)
    c.add_argument("--max-steps", type=int)
    c.add_argument("--lookback-days", type=int)
    c.add_argument(
        "--keep",
        type=int,
        dest="keep_adapters",
        help="Adapters to keep besides the active one",
    )

    r = sub.add_parser("run", help="Run a learning cycle now")
    r.add_argument(
        "--trigger",
        choices=["manual", "scheduled"],
        default="manual",
        help="scheduled applies the nightly gates",
    )
    r.add_argument("--background", action="store_true", help="Start it and return")

    sub.add_parser("stop", help="Stop the running cycle")

    a = sub.add_parser(
        "analyze", help="Show what the next cycle would learn, without training"
    )
    a.add_argument(
        "--days",
        type=int,
        default=None,
        help="History to read (default: lookback_days)",
    )
    a.add_argument("--json", action="store_true")

    sub.add_parser("list", help="List trained adapters")
    sh = sub.add_parser("show", help="Show one adapter's scores and what it learned")
    sh.add_argument("adapter_id")
    ac = sub.add_parser(
        "activate", help="Use an adapter (or roll back to an older one)"
    )
    ac.add_argument("adapter_id")
    sub.add_parser("deactivate", help="Serve the base model with no adapter")
    d = sub.add_parser("delete", help="Delete an adapter")
    d.add_argument("adapter_id")

    sub.add_parser("items", help="List everything learned so far")
    f = sub.add_parser(
        "forget", help="Remove a learned item; the next cycle retrains without it"
    )
    f.add_argument("item_id")

    rs = sub.add_parser("runs", help="Recent learning cycles")
    rs.add_argument("--limit", type=int, default=10)

    p.set_defaults(action="learn")


def _model() -> str:
    from gaia.learning.config import load_config
    from gaia.llm.lemonade_client import resolve_default_chat_model

    return load_config().base_model or resolve_default_chat_model()


def _reload(model: str) -> None:
    from gaia.learning.serving import reload_with_adapter
    from gaia.llm.lemonade_client import LemonadeClient

    reload_with_adapter(LemonadeClient(verbose=False), model)


def _scores_line(m: Dict[str, Any]) -> str:
    sc = m.get("scores") or {}
    cand, base = sc.get("candidate") or {}, sc.get("base") or {}
    if not cand:
        return ""
    return (
        f"learned probes {cand.get('learned_passed')}/{cand.get('learned_total')} "
        f"(base {base.get('learned_passed')}), general "
        f"{cand.get('regression_passed')}/{cand.get('regression_total')}"
    )


def _print_status(as_json: bool) -> None:
    from gaia.learning.config import load_config
    from gaia.learning.environment import cached_status
    from gaia.learning.registry import AdapterRegistry
    from gaia.learning.runs import list_runs

    cfg = load_config()
    model = cfg.base_model or _model()
    reg = AdapterRegistry()
    runs = list_runs(limit=1)
    data = {
        "config": cfg.to_dict(),
        "base_model": model,
        "toolchain": cached_status(),
        "active_adapter": reg.active(model),
        "latest_run": runs[0] if runs else None,
    }
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    tc = data["toolchain"]
    print(
        f"Nightly learning: {'on' if cfg.enabled else 'off'}  "
        f"(window {cfg.window_start}-{cfg.window_end}, idle {cfg.min_idle_minutes} min, "
        f"{'mains power only' if cfg.require_ac_power else 'battery allowed'})"
    )
    print(f"Base model: {model}")
    if tc is None:
        print("Toolchain: not checked — run `gaia learn setup`")
    else:
        print(
            f"Toolchain: {'ready' if tc.get('ready') else 'NOT ready'} "
            f"(torch {tc.get('torch')}, GPU {tc.get('gpu')})"
        )
        for prob in tc.get("problems") or []:
            print(f"  - {prob}")
    act = data["active_adapter"]
    print(
        f"Active adapter: {act['id'] + ' — ' + _scores_line(act) if act else 'none (base model)'}"
    )
    run = data["latest_run"]
    if run:
        print(f"Latest run: {run['id']} {run['status']} ({run.get('phase')})")
        prog = run.get("progress") or {}
        if run["status"] == "running" and prog.get("total"):
            print(
                f"  training step {prog.get('step')}/{prog.get('total')}, loss {prog.get('loss')}"
            )
        if run.get("summary"):
            print(f"  {run['summary']}")


def _print_report(report: Dict[str, Any]) -> None:
    counts = report["counts"]
    print(
        f"Read {report['turns_read']} turns, {report['facts_read']} memories, "
        f"{report['tool_calls_read']} tool calls."
    )
    print("Found: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    for o in report["opportunities"]:
        mark = "!" if o["error"] else "+"
        print(
            f" {mark} [{o['kind']}] {o['title']}  ({len(o['examples'])} examples, "
            f"{len(o['probes'])} probes)"
        )
        if o["error"]:
            print(f"     {o['error']}")
    if report.get("skipped"):
        print("Skipped: " + ", ".join(f"{v} {k}" for k, v in report["skipped"].items()))


def handle_learn(args: argparse.Namespace) -> int:
    try:
        return _dispatch(args)
    except LearningError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:  # noqa: C901
    from gaia.learning import config as lconfig
    from gaia.learning.registry import AdapterRegistry

    action = getattr(args, "learn_action", None)

    if action == "setup":
        return _setup(args)
    if action in (None, "status"):
        _print_status(getattr(args, "json", False))
        return 0
    if action == "enable":
        from gaia.learning.launcher import register_nightly_job

        cfg = lconfig.update_config(enabled=True)
        register_nightly_job()
        print(
            f"✅ Nightly learning is on: it runs between {cfg.window_start} and "
            f"{cfg.window_end} once the agent has been idle {cfg.min_idle_minutes} min. "
            "The GAIA daemon must be running (`gaia daemon start`)."
        )
        return 0
    if action == "disable":
        lconfig.update_config(enabled=False)
        print(
            "Nightly learning is off. The active adapter stays in use; "
            "`gaia learn deactivate` removes it."
        )
        return 0
    if action == "config":
        return _config(args)
    if action == "run":
        if args.background:
            from gaia.learning.launcher import spawn_cycle

            print(
                f"Started a learning cycle (pid {spawn_cycle(args.trigger)}). "
                "`gaia learn status` shows progress."
            )
            return 0
        from gaia.learning.cycle import run_cycle

        run = run_cycle(trigger=args.trigger)
        print(f"{run['status']}: {run.get('summary', '')}")
        return 0 if run["status"] != "failed" else 1
    if action == "stop":
        from gaia.learning.cycle import request_stop

        request_stop()
        print("Asked the running cycle to stop; it stops within a training step.")
        return 0
    if action == "analyze":
        return _analyze(args)

    reg = AdapterRegistry()
    if action == "list":
        adapters = reg.list()
        if not adapters:
            print("No adapters yet. `gaia learn run` trains the first one.")
        for m in adapters:
            print(f"{m['id']}  {m['status']:<9} {m['base_model']}  {_scores_line(m)}")
        return 0
    if action == "show":
        print(json.dumps(reg.get(args.adapter_id), indent=2))
        return 0
    if action == "activate":
        m = reg.activate(args.adapter_id)
        _reload(m["base_model"])
        print(f"✅ {m['id']} is active for {m['base_model']} and loaded.")
        return 0
    if action == "deactivate":
        model = _model()
        prev = reg.deactivate(model)
        _reload(model)
        print(
            f"{model} now runs without an adapter"
            + (f" ({prev} archived)." if prev else ".")
        )
        return 0
    if action == "delete":
        reg.delete(args.adapter_id)
        print(f"Deleted {args.adapter_id}.")
        return 0
    if action == "items":
        from gaia.learning.dataset import Corpus

        items = Corpus().items()
        if not items:
            print("Nothing learned yet.")
        for i in items:
            print(
                f"{i['id']:<28} [{i['kind']}] {i['title']}  ({len(i['examples'])} examples)"
            )
        return 0
    if action == "forget":
        from gaia.learning.dataset import Corpus

        Corpus().remove(args.item_id)
        model = _model()
        retired = reg.retire_holding(model, [args.item_id])
        if retired:
            _reload(model)
        note = (
            f" Adapter {retired}, which had learned it, is switched off."
            if retired
            else ""
        )
        print(
            f"Removed {args.item_id}.{note} The next cycle trains an adapter without it."
        )
        return 0
    if action == "runs":
        from gaia.learning.runs import list_runs

        for run in list_runs(limit=args.limit):
            print(
                f"{run['id']}  {run['status']:<16} {run.get('summary') or run.get('error') or ''}"
            )
        return 0
    raise LearningError(f"Unknown learn action {action!r}. See `gaia learn -h`.")


def _setup(args) -> int:
    from gaia.learning import environment
    from gaia.learning.config import load_config
    from gaia.llm.lemonade_client import LemonadeClient

    cfg = load_config()
    backend = args.backend or cfg.backend
    client = LemonadeClient(verbose=False)
    commit = environment.llama_server_commit(
        environment.llama_server_binary(client.health_check())
    )
    if args.check:
        st = environment.check(server_commit=commit, backend=backend)
    else:
        arch = environment.gpu_arch(client.get_system_info(timeout=30))
        print(
            f"Installing the training toolchain for {arch} ({backend}) into "
            f"{environment.train_venv_python().parent.parent} — several GB, one time."
        )
        st = environment.setup(
            arch=arch, server_commit=commit, backend=backend, log_fn=print
        )
        if not st.ready:
            st = environment.check(server_commit=commit, backend=backend)
    print(f"torch {st.torch} on {st.gpu}; converter llama.cpp {st.converter_commit}")
    for prob in st.problems:
        print(f"  ❌ {prob}")
    if st.ready:
        print("✅ Training toolchain ready.")
    return 0 if st.ready else 1


def _config(args) -> int:
    from gaia.learning import config as lconfig

    changes: Dict[str, Any] = {}
    if args.window:
        start, sep, end = args.window.partition("-")
        if not sep:
            raise LearningError("--window takes START-END, e.g. 01:00-06:00.")
        changes.update(window_start=start.strip(), window_end=end.strip())
    if args.allow_battery and args.require_ac:
        raise LearningError("Pass only one of --allow-battery and --require-ac.")
    if args.allow_battery:
        changes["require_ac_power"] = False
    if args.require_ac:
        changes["require_ac_power"] = True
    for name, key in (
        ("idle_minutes", "min_idle_minutes"),
        ("base_model", "base_model"),
        ("backend", "backend"),
        ("rank", "rank"),
        ("epochs", "epochs"),
        ("max_steps", "max_steps"),
        ("lookback_days", "lookback_days"),
        ("keep_adapters", "keep_adapters"),
    ):
        value = getattr(args, name)
        if value is not None:
            changes[key] = value
    cfg = lconfig.update_config(**changes) if changes else lconfig.load_config()
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def _analyze(args) -> int:
    from datetime import datetime, timedelta, timezone

    from gaia.learning.analyze import Analyzer
    from gaia.learning.config import load_config
    from gaia.learning.cycle import Deps
    from gaia.learning.sources import read_history

    cfg = load_config()
    deps = Deps()
    days = args.days or cfg.lookback_days
    history = read_history(
        deps.chat_db(),
        deps.memory_db(),
        datetime.now(timezone.utc) - timedelta(days=days),
    )
    model = _model()
    teacher = deps.teacher_factory(deps.client_factory(), model)
    report = (
        Analyzer(teacher).analyze(history, max_examples=cfg.max_new_examples).to_dict()
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0
