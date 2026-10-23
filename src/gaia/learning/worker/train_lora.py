# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Train one LoRA adapter from a GAIA learning dataset.

Runs inside the training venv (``<gaia_home>/train/venv``), never inside GAIA's
own interpreter: it imports torch, transformers, peft and (optionally) unsloth,
none of which GAIA depends on. It imports nothing from ``gaia`` so the venv
needs no GAIA install.

Usage::

    python -I train_lora.py --job job.json

``job.json`` keys: ``base_model`` (HF id or local dir), ``train_file`` and
``eval_file`` (chat JSONL, one ``{"messages": [...], "tools": [...]}`` per
line; ``tools`` optional),
``output_dir``, ``backend`` (``peft`` or ``unsloth``), ``load_in_4bit``,
``rank``, ``alpha``, ``learning_rate``, ``epochs``, ``max_steps``,
``max_seq_len``, ``target_modules`` (a regex over module names),
``attn_implementation`` and ``stop_file``.

Writes to ``output_dir``: ``adapter/`` (PEFT safetensors), ``progress.jsonl``
(one line per logged step) and ``metrics.json``. Exit codes: 0 trained,
3 stopped because ``stop_file`` appeared, anything else failed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

EXIT_STOPPED = 3


def _log(out: Path, **fields) -> None:
    fields["t"] = round(time.time(), 3)
    with open(out / "progress.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(fields) + "\n")
    print("GAIA_PROGRESS " + json.dumps(fields), flush=True)


def _read_jsonl(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_model(job: dict):
    import torch

    base = job["base_model"]
    if job["backend"] == "unsloth":
        from unsloth import FastModel  # pylint: disable=import-error

        model, tok = FastModel.from_pretrained(
            base,
            max_seq_length=job["max_seq_len"],
            load_in_4bit=job["load_in_4bit"],
        )
        model = FastModel.get_peft_model(
            model,
            r=job["rank"],
            lora_alpha=job["alpha"],
            lora_dropout=0.0,
            target_modules=job["target_modules"],
            finetune_vision_layers=False,
            finetune_language_layers=True,
        )
        return model, getattr(tok, "tokenizer", tok)

    from peft import (  # pylint: disable=import-error
        LoraConfig,
        get_peft_model,
        prepare_model_for_kbit_training,
    )
    from transformers import AutoConfig, AutoTokenizer, BitsAndBytesConfig

    config = AutoConfig.from_pretrained(base)
    archs = config.architectures or []
    if any(a.endswith("ForConditionalGeneration") for a in archs):
        # Multimodal checkpoints keep their own tensor names, which the GGUF
        # LoRA converter maps; a text-only class would rename them.
        from transformers import AutoModelForImageTextToText as AutoModelClass
    else:
        from transformers import AutoModelForCausalLM as AutoModelClass

    kwargs = {
        "dtype": torch.bfloat16,
        "device_map": {"": 0},
        # PyTorch's fused SDPA returns NaN for Gemma 4 on Windows ROCm 7.14.
        "attn_implementation": job.get("attn_implementation", "eager"),
    }
    if job["load_in_4bit"]:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelClass.from_pretrained(base, **kwargs)
    if job["load_in_4bit"]:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    model = get_peft_model(
        model,
        LoraConfig(
            r=job["rank"],
            lora_alpha=job["alpha"],
            lora_dropout=0.0,
            target_modules=job["target_modules"],
            task_type="CAUSAL_LM",
        ),
    )
    tok = AutoTokenizer.from_pretrained(base)
    return model, tok


def _render(tok, messages, tools=None, *, add_generation_prompt=False) -> list:
    out = tok.apply_chat_template(
        messages,
        tools=tools or None,
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
        return_dict=True,
    )
    return list(out["input_ids"])


def _encode(tok, row: dict, max_len: int):
    """Token ids and labels with the loss on assistant tokens only."""
    messages, tools = row["messages"], row.get("tools")
    ids = _render(tok, messages, tools)
    labels = [-100] * len(ids)
    for i, msg in enumerate(messages):
        if msg["role"] != "assistant":
            continue
        start = len(_render(tok, messages[:i], tools, add_generation_prompt=True))
        end = len(_render(tok, messages[: i + 1], tools))
        for k in range(start, min(end, len(ids))):
            labels[k] = ids[k]
    ids, labels = ids[:max_len], labels[:max_len]
    if all(x == -100 for x in labels):
        return None
    return ids, labels


def _mean_loss(model, batches) -> float:
    import torch

    total, count = 0.0, 0
    model.eval()
    with torch.no_grad():
        for ids, labels in batches:
            t_ids = torch.tensor([ids], device=model.device)
            t_lab = torch.tensor([labels], device=model.device)
            loss = model(input_ids=t_ids, labels=t_lab).loss
            n = sum(1 for x in labels if x != -100)
            total += float(loss) * n
            count += n
    model.train()
    return total / max(count, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    job = json.loads(Path(ap.parse_args().job).read_text(encoding="utf-8"))
    out = Path(job["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    stop_file = Path(job["stop_file"])
    started = time.time()
    random.seed(job.get("seed", 1234))

    import torch

    if not torch.cuda.is_available():
        print(
            "No GPU visible to PyTorch in the training venv. Run "
            "`gaia learn setup` to reinstall the ROCm build for this GPU.",
            file=sys.stderr,
        )
        return 2

    _log(out, phase="loading", base_model=job["base_model"], backend=job["backend"])
    model, tok = _load_model(job)

    train = [
        _encode(tok, r, job["max_seq_len"]) for r in _read_jsonl(job["train_file"])
    ]
    evals = [_encode(tok, r, job["max_seq_len"]) for r in _read_jsonl(job["eval_file"])]
    train = [x for x in train if x]
    evals = [x for x in evals if x]
    if not train:
        print(
            "The training file has no assistant tokens to learn from.", file=sys.stderr
        )
        return 2

    with model.disable_adapter():
        base_eval_loss = _mean_loss(model, evals) if evals else None
    _log(
        out, phase="baseline", base_eval_loss=base_eval_loss, train_examples=len(train)
    )

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=job["learning_rate"], weight_decay=0.0)
    steps_per_epoch = len(train)
    total = steps_per_epoch * job["epochs"]
    if job.get("max_steps"):
        total = min(total, job["max_steps"])
    warmup = max(1, total // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt,
        lambda s: min(1.0, (s + 1) / warmup)
        * 0.5
        * (1 + math.cos(math.pi * min(s, total) / total)),
    )

    model.train()
    step, running = 0, []
    order = []
    while step < total:
        if not order:
            order = list(range(len(train)))
            random.shuffle(order)
        ids, labels = train[order.pop()]
        loss = model(
            input_ids=torch.tensor([ids], device=model.device),
            labels=torch.tensor([labels], device=model.device),
        ).loss
        if not torch.isfinite(loss):
            print(
                f"Training loss became {float(loss)} at step {step}.", file=sys.stderr
            )
            return 4
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        running.append(loss.item())
        if step % 5 == 0 or step == total:
            _log(
                out,
                phase="training",
                step=step,
                total=total,
                loss=sum(running) / len(running),
            )
            running = []
        if stop_file.exists():
            _log(out, phase="stopped", step=step, total=total)
            return EXIT_STOPPED

    eval_loss = _mean_loss(model, evals) if evals else None
    model.save_pretrained(str(out / "adapter"))
    metrics = {
        "steps": step,
        "train_examples": len(train),
        "eval_examples": len(evals),
        "base_eval_loss": base_eval_loss,
        "eval_loss": eval_loss,
        "seconds": round(time.time() - started, 1),
        "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
        "backend": job["backend"],
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    _log(out, phase="done", **metrics)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    sys.exit(main())
