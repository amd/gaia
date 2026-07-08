# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CI helper: drive a Lemonade model through GAIA's ``LemonadeClient``.

CI must validate **our interface to Lemonade** (``LemonadeClient``), not raw
REST - so a flaky/transient backend fault is handled exactly as it is in
production (including the model-load retry), and the client surface is what gets
exercised. This pulls + loads a model via the client and optionally verifies an
embeddings or chat round-trip, then exits non-zero on failure.

Usage:
    python tests/ci_lemonade_check.py --model nomic-embed-text-v2-moe-GGUF --embeddings
    python tests/ci_lemonade_check.py --model Gemma-4-E4B-it-GGUF --chat --ctx-size 4096
    # Custom (``user.``-namespaced) model — register on first pull:
    python tests/ci_lemonade_check.py --model user.embeddinggemma-300m-GGUF \
        --checkpoint ggml-org/embeddinggemma-300M-GGUF:Q8_0 --recipe llamacpp \
        --register-embedding --embeddings
"""

import argparse
import sys

from gaia.llm.lemonade_client import LemonadeClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Lemonade model name")
    parser.add_argument(
        "--embeddings", action="store_true", help="verify an embeddings round-trip"
    )
    parser.add_argument(
        "--chat", action="store_true", help="verify a chat-completion round-trip"
    )
    parser.add_argument("--ctx-size", type=int, default=None, dest="ctx_size")
    parser.add_argument(
        "--pull-only",
        action="store_true",
        dest="pull_only",
        help="download the model via the client without loading it",
    )
    # Custom-model registration (``user.``-namespaced models that aren't Lemonade
    # built-ins). When --checkpoint is given, the model is registered+downloaded
    # via ensure_model_downloaded before load, exactly as init/RAG do.
    parser.add_argument(
        "--checkpoint", default=None, help="HF checkpoint for custom-model registration"
    )
    parser.add_argument(
        "--recipe", default=None, help="recipe for custom-model registration"
    )
    parser.add_argument(
        "--register-embedding",
        action="store_true",
        dest="register_embedding",
        help="set the 'embeddings' label when registering a custom model",
    )
    args = parser.parse_args()

    client = LemonadeClient()

    # Register + download a custom model (checkpoint given) through the same
    # client path production uses, so the registration contract is validated.
    if args.checkpoint:
        print(
            "[ci] register+download %s (checkpoint=%s) via LemonadeClient..."
            % (args.model, args.checkpoint),
            flush=True,
        )
        ok = client.ensure_model_downloaded(
            args.model,
            checkpoint=args.checkpoint,
            recipe=args.recipe,
            embedding=args.register_embedding or None,
        )
        if not ok:
            print("[ci] ERROR: failed to register/download %s" % args.model, flush=True)
            return 1
        print("[ci] registered+downloaded %s" % args.model, flush=True)

    if args.pull_only:
        if not args.checkpoint:
            print("[ci] pull %s via LemonadeClient..." % args.model, flush=True)
            client.pull_model(args.model)
        print("[ci] pulled %s" % args.model, flush=True)
        return 0

    print("[ci] load %s via LemonadeClient (with retry)..." % args.model, flush=True)
    client.load_model(
        args.model, prompt=False, auto_download=True, ctx_size=args.ctx_size
    )
    print("[ci] loaded %s" % args.model, flush=True)

    # Confirm the model shows up through the client's models surface too.
    models = client.list_models()
    ids = [m.get("id") for m in (models.get("data") or [])]
    print("[ci] models via client: %s" % ", ".join(str(i) for i in ids), flush=True)

    if args.embeddings:
        resp = client.embeddings(["ci validation text"], model=args.model)
        dim = len(resp["data"][0]["embedding"])
        if dim <= 0:
            print("[ci] ERROR: empty embedding vector", flush=True)
            return 1
        print("[ci] embeddings OK (dim=%d)" % dim, flush=True)

    if args.chat:
        resp = client.chat_completions(
            messages=[{"role": "user", "content": "Reply with the word OK."}],
            model=args.model,
            max_tokens=32,
            stream=False,
        )
        # Verify the LLM round-trips through the client: a successful response
        # with a choices array. Don't require non-empty content -- reasoning
        # models can spend the token budget on thinking and return empty
        # content, which is not a failure of the round-trip.
        choices = resp.get("choices") or []
        if not choices:
            print("[ci] ERROR: chat completion returned no choices", flush=True)
            return 1
        text = (choices[0].get("message") or {}).get("content") or ""
        print(
            "[ci] chat OK (choices=%d, content=%r)" % (len(choices), text[:40]),
            flush=True,
        )

    print("[ci] OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
