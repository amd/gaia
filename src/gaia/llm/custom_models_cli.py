# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI for ``gaia model {check|add|remove|list}``: models added from a Hugging Face link.

``--json`` prints one JSON object on stdout for scripts and the TUI, which
shells out here rather than repeating the checks in Go. Exit codes: ``0`` ok,
``1`` the link or model was refused (the reason is in the output), ``2`` the
model server or config could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


def add_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "model",
        help="Add or remove local GGUF models from a Hugging Face link",
    )
    actions = parser.add_subparsers(dest="model_action", help="Model action")

    def with_link(name: str, help_text: str) -> argparse.ArgumentParser:
        p = actions.add_parser(name, help=help_text)
        p.add_argument("url", help="Hugging Face link, or owner/repo[:QUANT]")
        p.add_argument(
            "--quant",
            help="Quantization to use, e.g. Q4_K_M (default: one that fits this PC)",
        )
        p.add_argument("--json", action="store_true", help="Print JSON")
        return p

    with_link("check", "Check a link without saving or downloading anything")
    with_link("add", "Check a link and add the model to the model list")
    remove = actions.add_parser("remove", help="Remove an added model and its download")
    remove.add_argument("model_id", help="The model's id, as `gaia model list` shows")
    remove.add_argument("--json", action="store_true", help="Print JSON")
    listing = actions.add_parser("list", help="List the models you added")
    listing.add_argument("--json", action="store_true", help="Print JSON")
    parser.set_defaults(action="model")


class _ServerError(Exception):
    pass


def _client():
    from gaia.llm.lemonade_client import LemonadeClient

    return LemonadeClient(verbose=False)


def _system_info() -> Dict[str, Any]:
    from gaia.llm.lemonade_client import LemonadeClientError

    try:
        return _client().get_system_info()
    except (LemonadeClientError, OSError) as e:
        raise _ServerError(
            f"The local model server did not answer ({e}). Start it with "
            "`gaia daemon start`, then try again."
        ) from e


def _catalog(client) -> list:
    from gaia.llm.lemonade_client import LemonadeClientError

    try:
        return client.list_models(show_all=True).get("data") or []
    except (LemonadeClientError, OSError) as e:
        raise _ServerError(
            f"The local model server did not answer ({e}). Start it with "
            "`gaia daemon start`, then try again."
        ) from e


def _delete_from_lemonade(entry: Dict[str, Any]) -> None:
    from gaia.llm.custom_models import listed_entry
    from gaia.llm.lemonade_client import LemonadeClientError

    client = _client()
    name = listed_entry(_catalog(client), entry)
    if name is not None:
        try:
            client.delete_model(name)
        except LemonadeClientError as e:
            raise _ServerError(
                f"{e}. {entry['display_name']} is still in your list; try again "
                "once nothing is using it."
            ) from e


def _emit(as_json: bool, payload: Dict[str, Any], text: str) -> None:
    print(json.dumps(payload, indent=2) if as_json else text)


def _describe(report: Dict[str, Any]) -> str:
    lines = []
    for c in report["checks"]:
        lines.append(f"  {'✅' if c['ok'] else '❌'} {c['label']}: {c['detail']}")
    for w in report.get("warnings") or []:
        lines.append(f"  ⚠️  {w}")
    if report.get("ok"):
        lines.append(
            f"  {report['display_name']} — {report['size_gb']} GB, id {report['model_id']}"
        )
    return "\n".join(lines)


def handle(args) -> int:
    from gaia.config import GaiaConfigError
    from gaia.llm.custom_models import (
        CustomModelError,
        add_custom_model,
        inspect_link,
        list_custom_models,
        remove_custom_model,
    )

    action = getattr(args, "model_action", None)
    as_json = bool(getattr(args, "json", False))
    try:
        if action == "list":
            models = list_custom_models()
            text = "\n".join(f"{m['id']}  ({m['source_url']})" for m in models)
            _emit(as_json, {"models": models}, text or "No added models.")
            return 0
        if action in ("check", "add"):
            from gaia.config import GaiaConfig

            report = inspect_link(
                args.url,
                system_info=_system_info(),
                preferred_backend=GaiaConfig.load().llamacpp_backend,
                variant=args.quant,
                catalog=_catalog(_client()),
            )
            if action == "check" or not report["ok"]:
                _emit(as_json, report, _describe(report))
                return 0 if report["ok"] else 1
            entry = add_custom_model(report)
            _emit(
                as_json,
                {"model": entry, "report": report},
                _describe(report)
                + f"\n✅ Added. Pick {entry['display_name']} in the model list; it "
                "downloads the first time you use it.",
            )
            return 0
        if action == "remove":
            result = remove_custom_model(args.model_id, _delete_from_lemonade)
            _emit(as_json, result, f"✅ Removed {result['display_name']}.")
            return 0
    except CustomModelError as e:
        _emit(as_json, {"ok": False, "error": str(e)}, f"❌ {e}")
        return 1
    except (_ServerError, GaiaConfigError) as e:
        _emit(as_json, {"ok": False, "error": str(e)}, f"❌ {e}")
        return 2
    print("Use: gaia model check|add|remove|list", file=sys.stderr)
    return 2
