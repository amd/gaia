# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ask the real classifier how ``hybrid`` would route some prompts.

A manual check, not a test: each prompt goes through GAIA's own router
(``HybridRouter.decide``). By default the classifier is called directly on the
AMD LLM gateway, so no Lemonade Server is needed; ``--lemonade`` sends it
through GAIA's own client and Lemonade instead, the path agent turns take.
Prints one line per prompt with the model it would run on and the label.

Usage::

    python util/hybrid_classify_live.py "Summarise this note" "Audit auth.py"
    python util/hybrid_classify_live.py --file prompts.json   # list or {name: prompt}
    python util/hybrid_classify_live.py --lemonade "Summarise this note"

The gateway key comes from ``gaia gateway auth`` (or the gateway env var) and is
never printed.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from gaia.llm.gateway import (
    DEFAULT_AUTH_HEADER_NAME,
    DEFAULT_GATEWAY_BASE_URL,
    GATEWAY_PROVIDER,
    resolve_gateway_token_with_source,
)
from gaia.llm.hybrid import HybridRouter, HybridSettings


class GatewayChat:
    """The one ``chat`` call the classifier makes, sent straight to the gateway."""

    def __init__(self, model: str, base_url: str, token: str):
        prefix = f"{GATEWAY_PROVIDER}."
        if not model.startswith(prefix):
            raise SystemExit(
                f"{model!r} is not a gateway model ({prefix}*); this script only "
                "calls the gateway. Set hybrid_classifier_model to one."
            )
        self.upstream = model[len(prefix) :]
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.token = token

    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        body = {
            "model": self.upstream,
            "messages": messages,
            "temperature": kwargs["temperature"],
            "max_tokens": kwargs["max_tokens"],
            "response_format": kwargs["response_format"],
        }
        response = requests.post(
            self.url,
            json=body,
            headers={DEFAULT_AUTH_HEADER_NAME: self.token},
            timeout=kwargs["timeout"],
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def _prompts(args: argparse.Namespace) -> Dict[str, str]:
    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(i + 1): str(p) for i, p in enumerate(data)}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        raise SystemExit(f"{args.file} must hold a JSON list or object of prompts.")
    return {str(i + 1): p for i, p in enumerate(args.prompts)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("prompts", nargs="*", help="Prompts to classify")
    parser.add_argument("--file", help="JSON list or {name: prompt} object")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_GATEWAY_BASE_URL,
        help="Gateway OpenAI-style base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--lemonade",
        action="store_true",
        help="Classify through GAIA's Lemonade client, as agent turns do",
    )
    args = parser.parse_args(argv)
    prompts = _prompts(args)
    if not prompts:
        parser.error("give prompts as arguments or with --file")

    settings = HybridSettings.load()
    if args.lemonade:
        factory = None
    else:
        token, _source = resolve_gateway_token_with_source()

        def factory(model: str, _url: Optional[str]) -> GatewayChat:
            return GatewayChat(model, args.base_url, token)

    print(
        f"cheap {settings.cheap_model} | strong {settings.strong_model} | "
        f"classifier {settings.classifier_model}"
    )
    for name, prompt in prompts.items():
        # A fresh router per prompt: each is judged alone, not as one session.
        router = (
            HybridRouter(settings)
            if factory is None
            else HybridRouter(settings, client_factory=factory)
        )
        route = router.decide(prompt)
        label = (
            f"{route.kind} d{route.difficulty} {route.stakes}"
            if route.classified
            else "unclassified"
        )
        print(f"{name}: {route.model} [{label}, {route.seconds:.2f}s] {route.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
