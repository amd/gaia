# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Local GGUF models a user adds by pasting a Hugging Face link.

A pasted link is checked before anything is saved or downloaded: the repo must
be public and hold GGUF weights with a chat template, its architecture must be
one the llama.cpp build Lemonade runs can load, and the chosen quantization
must fit this PC. A model that passes is kept in ``config.json``'s
``custom_models`` list and registered with Lemonade as a ``user.`` model on its
first download, like GAIA's own custom entries in ``MODELS``. The Agent UI, the
TUI and the CLI all read and change the same list, so a model added in one
shows up in the others.

The architecture check reads the architecture table of the exact llama.cpp
release Lemonade reports in ``/system-info``, so it answers for the build that
will load the model, not for llama.cpp in general.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

#: Config key holding the user's models (a list of entry dicts).
CONFIG_KEY = "custom_models"

HF_HOSTS = ("huggingface.co", "hf.co")
HF_API = "https://huggingface.co/api/models/{repo}?blobs=true"
#: Where each llama.cpp release's architecture names live.
LLAMA_ARCH_SOURCE = (
    "https://raw.githubusercontent.com/ggml-org/llama.cpp/{tag}/src/llama-arch.cpp"
)
HTTP_TIMEOUT = 20
#: Window an added model loads with (or its own, if smaller). Its KV cache per
#: token is unknown, so it is not grown with memory like GAIA's own models.
ADDED_MODEL_CTX = 32768

#: Quantizations tried first when the link names none: the usual quality/size
#: sweet spot, then progressively larger ones. Anything else is picked by size.
PREFERRED_QUANTS = (
    "Q4_K_M",
    "UD-Q4_K_XL",
    "Q4_K_XL",
    "Q4_K_S",
    "IQ4_XS",
    "Q4_0",
    "MXFP4",
    "Q5_K_M",
    "UD-Q5_K_XL",
    "Q6_K",
    "UD-Q6_K_XL",
    "Q8_0",
)

_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
#: First path segments on huggingface.co that are not a model owner.
_NOT_OWNERS = {
    "blog",
    "collections",
    "datasets",
    "docs",
    "enterprise",
    "join",
    "learn",
    "login",
    "organizations",
    "papers",
    "posts",
    "pricing",
    "settings",
    "spaces",
    "tasks",
}
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})$", re.IGNORECASE)
_QUANT_RE = re.compile(
    r"(?:^|[-_.])((?:UD-)?(?:I?Q\d+(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?|"
    r"TQ\d_\d))$",
    re.IGNORECASE,
)
_ARCH_ENTRY_RE = re.compile(r"\{\s*LLM_ARCH_\w+\s*,\s*\"([^\"]+)\"\s*\}")
#: A llama.cpp release tag; it names a cache file and a URL path, so nothing else.
_TAG_RE = re.compile(r"b\d{1,6}")


class CustomModelError(ValueError):
    """A link cannot be added, removed or checked, with what to do about it."""


# ── Links ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HFLink:
    """A Hugging Face repo, and the file or quantization the link names, if any."""

    repo: str
    variant: Optional[str] = None


def parse_link(text: str) -> HFLink:
    """Read a pasted Hugging Face link, ``owner/repo`` or ``owner/repo:QUANT``.

    Any page of a model repo names that repo — its card, ``/tree/``,
    ``/discussions``, ``/commits``, with query strings and fragments — and so do
    ``hf.co`` short links. ``/blob/`` and ``/resolve/`` links to a ``.gguf`` file
    also pick that file.
    """
    raw = (text or "").strip()
    if not raw:
        raise CustomModelError("Paste a Hugging Face link to a GGUF model.")
    if "://" not in raw and not raw.lower().startswith(HF_HOSTS):
        repo, _, variant = raw.partition(":")
        if _REPO_RE.match(repo) and repo.split("/")[1] not in (".", ".."):
            return HFLink(repo, variant or None)
        raise CustomModelError(
            f"'{raw}' is not a Hugging Face link. Paste one like "
            "https://huggingface.co/unsloth/Qwen3-4B-GGUF, or owner/repo."
        )
    url = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (url.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in HF_HOSTS:
        raise CustomModelError(
            f"Only Hugging Face links can be added, and this one points at "
            f"{host or 'nothing'}. Find the model on https://huggingface.co and "
            "paste its page link."
        )
    parts = [unquote(p) for p in url.path.split("/") if p]
    if parts and parts[0] in ("models", "api"):
        parts = parts[1:] if parts[0] == "models" else parts[2:]
    if len(parts) < 2 or parts[0].lower() in _NOT_OWNERS:
        raise CustomModelError(
            f"{raw} is not a model page. Paste the link of a model repository, "
            "such as https://huggingface.co/unsloth/Qwen3-4B-GGUF."
        )
    repo = f"{parts[0]}/{parts[1]}"
    if not _REPO_RE.match(repo) or parts[1] in (".", ".."):
        raise CustomModelError(f"'{repo}' is not a Hugging Face repository name.")
    variant = None
    # /blob/<rev>/<path> and /resolve/<rev>/<path> name one file.
    if len(parts) >= 5 and parts[2] in ("blob", "resolve"):
        path = "/".join(parts[4:])
        if path.lower().endswith(".gguf"):
            variant = path
    return HFLink(repo, variant)


# ── GGUF files in a repo ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Variant:
    """One downloadable quantization: a single file, or the shards of one."""

    quant: str
    files: Tuple[str, ...]
    size_bytes: int

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1e9

    @property
    def checkpoint_variant(self) -> str:
        """What follows ``repo:`` in a Lemonade checkpoint for this variant.

        A single file is named exactly; shards are named by their quantization,
        which Lemonade expands to every shard.
        """
        return self.files[0] if len(self.files) == 1 else self.quant


def quant_of(path: str) -> str:
    """``UD-Q4_K_XL`` for ``flint-UD-Q4_K_XL.gguf``; the file stem when unrecognised."""
    stem = path.rsplit("/", 1)[-1]
    stem = stem[: -len(".gguf")] if stem.lower().endswith(".gguf") else stem
    stem = _SHARD_RE.sub("", stem)
    match = _QUANT_RE.search(stem)
    return match.group(1).upper() if match else stem


def _is_projector(path: str) -> bool:
    return "mmproj" in path.rsplit("/", 1)[-1].lower()


def gguf_variants(siblings: List[Dict[str, Any]]) -> Tuple[List[Variant], List[Dict]]:
    """The repo's weight variants, and its vision projector files."""
    groups: Dict[str, List[Tuple[str, int]]] = {}
    projectors = []
    for s in siblings:
        path = str(s.get("rfilename") or "")
        if not path.lower().endswith(".gguf"):
            continue
        size = int(s.get("size") or 0)
        if _is_projector(path):
            projectors.append({"file": path, "size": size})
            continue
        if "imatrix" in path.lower():
            continue
        stem = path[: -len(".gguf")]
        key = _SHARD_RE.sub("", stem)
        groups.setdefault(key, []).append((path, size))
    variants = [
        Variant(
            quant=quant_of(key + ".gguf"),
            files=tuple(sorted(p for p, _ in files)),
            size_bytes=sum(n for _, n in files),
        )
        for key, files in groups.items()
    ]
    variants.sort(key=lambda v: v.size_bytes)
    return variants, projectors


def _pick_projector(projectors: List[Dict]) -> Optional[Dict]:
    for want in ("f16", "bf16", "f32"):
        for p in projectors:
            if want in p["file"].lower().rsplit("/", 1)[-1]:
                return p
    return projectors[0] if projectors else None


def _find_variant(variants: List[Variant], wanted: str) -> Variant:
    w = wanted.strip().lower()
    for v in variants:
        if w in (v.quant.lower(), *(f.lower() for f in v.files)):
            return v
    names = ", ".join(v.quant for v in variants)
    raise CustomModelError(f"This repo has no '{wanted}' GGUF. Choose one of: {names}.")


def _default_variant(
    variants: List[Variant], fits: Callable[[Variant], bool]
) -> Variant:
    by_quant = {v.quant.upper(): v for v in variants}
    for q in PREFERRED_QUANTS:
        v = by_quant.get(q)
        if v is not None and fits(v):
            return v
    fitting = [v for v in variants if fits(v)]
    # The largest that fits is the best quality this PC can hold.
    return fitting[-1] if fitting else variants[0]


# ── llama.cpp architecture support ─────────────────────────────────────────


def llamacpp_release(
    system_info: Dict[str, Any], preferred_backend: Optional[str]
) -> str:
    """The llama.cpp release tag (``b10825``) Lemonade loads GGUF models with."""
    recipe = ((system_info or {}).get("recipes") or {}).get("llamacpp") or {}
    backends = recipe.get("backends") or {}
    name = preferred_backend or recipe.get("default_backend")
    entry = backends.get(name) if name else None
    version = (entry or {}).get("version")
    if not version:
        raise CustomModelError(
            "Lemonade did not say which llama.cpp release it runs, so GAIA cannot "
            "check that it loads this model. Update Lemonade with `gaia init`, "
            "then try again."
        )
    if not _TAG_RE.fullmatch(str(version)):
        raise CustomModelError(
            f"Lemonade reports llama.cpp version {version!r}, which is not a "
            "llama.cpp release tag (like b11393), so GAIA cannot look up which "
            "architectures it loads. Report this at https://github.com/amd/gaia/issues."
        )
    return str(version)


def _http_get(url: str, headers: Optional[Dict[str, str]] = None):
    import requests

    return requests.get(url, headers=headers or {}, timeout=HTTP_TIMEOUT)


def _arch_cache_path(tag: str) -> Path:
    from gaia.config import gaia_home

    return gaia_home() / "cache" / "llamacpp-arch" / f"{tag}.json"


def supported_architectures(tag: str) -> frozenset:
    """Architecture names llama.cpp release *tag* can load, cached per release."""
    import requests

    cache = _arch_cache_path(tag)
    if cache.is_file():
        try:
            return frozenset(json.loads(cache.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise CustomModelError(
                f"The saved architecture list for llama.cpp {tag} at {cache} is "
                f"unreadable ({e}). Delete that file and try again."
            ) from e
    url = LLAMA_ARCH_SOURCE.format(tag=tag)
    try:
        resp = _http_get(url)
    except requests.RequestException as e:
        raise CustomModelError(
            f"Could not fetch llama.cpp {tag}'s architecture list from GitHub "
            f"({e}), so GAIA cannot check this model will load. Check your "
            "internet connection and try again."
        ) from e
    if resp.status_code != 200:
        raise CustomModelError(
            f"GitHub answered HTTP {resp.status_code} for llama.cpp {tag}'s "
            f"architecture list ({url}), so GAIA cannot check this model will "
            "load. Try again later."
        )
    names = {n for n in _ARCH_ENTRY_RE.findall(resp.text) if n != "(unknown)"}
    if not names:
        raise CustomModelError(
            f"llama.cpp {tag}'s source at {url} lists no architectures; its "
            "layout may have changed. Report this at "
            "https://github.com/amd/gaia/issues."
        )
    from gaia.utils.file_lock import write_text_atomic

    cache.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(cache, json.dumps(sorted(names)))
    return frozenset(names)


# ── Checking a link ────────────────────────────────────────────────────────


def _fetch_repo(repo: str) -> Dict[str, Any]:
    """The repo as anyone sees it: no token, so a private repo reads as absent."""
    import requests

    try:
        resp = _http_get(HF_API.format(repo=repo))
    except requests.RequestException as e:
        raise CustomModelError(
            f"Could not reach Hugging Face ({e}). Check your internet connection "
            "and try again."
        ) from e
    if resp.status_code in (401, 404):
        raise CustomModelError(
            f"Hugging Face has no public model '{repo}'. Check the link; private "
            "models can't be added."
        )
    if resp.status_code != 200:
        raise CustomModelError(
            f"Hugging Face answered HTTP {resp.status_code} for {repo}. Try "
            "again later."
        )
    try:
        info = resp.json()
    except ValueError as e:
        raise CustomModelError(
            f"Hugging Face answered {repo} with something that is not JSON ({e}). "
            "Try again later."
        ) from e
    if not isinstance(info, dict):
        raise CustomModelError(
            f"Hugging Face answered {repo} with a {type(info).__name__}, not a "
            "model description. Check the link."
        )
    return info


def _repo_base(repo: str) -> str:
    """``flint`` for ``unsloth/flint-GGUF``."""
    return re.sub(r"[-_.]gguf$", "", repo.split("/", 1)[1], flags=re.IGNORECASE)


def _model_name(repo: str, quant: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{_repo_base(repo)}-{quant}-GGUF")
    return f"user.{name}"


def _check(checks: List[Dict], cid: str, label: str, ok: bool, detail: str) -> bool:
    checks.append({"id": cid, "label": label, "ok": ok, "detail": detail})
    return ok


def inspect_link(
    text: str,
    *,
    system_info: Dict[str, Any],
    preferred_backend: Optional[str] = None,
    variant: Optional[str] = None,
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Check a pasted link and describe the model it would add.

    *catalog* is Lemonade's ``/models?show_all=true`` list, so a name Lemonade
    already uses for another model is refused. *variant* is a quantization or
    one of its file names. Returns a JSON-ready report whose ``ok`` says
    whether it can be added and whose ``checks`` say what was verified;
    ``error`` is the first failure. Nothing is saved or downloaded.
    """
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info, check_fit

    checks: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {
        "ok": False,
        "url": text,
        "checks": checks,
        "warnings": [],
        "variants": [],
        "error": None,
    }

    def fail(cid: str, label: str, detail: str) -> Dict[str, Any]:
        _check(checks, cid, label, False, detail)
        report["error"] = detail
        return report

    try:
        link = parse_link(text)
    except CustomModelError as e:
        return fail("link", "Hugging Face link", str(e))
    report["repo"] = link.repo
    _check(checks, "link", "Hugging Face link", True, link.repo)

    try:
        info = _fetch_repo(link.repo)
    except CustomModelError as e:
        return fail("repo", "Public repository", str(e))
    if info.get("gated"):
        return fail(
            "repo",
            "Public repository",
            f"{link.repo} is gated: Hugging Face asks you to accept its terms "
            "before downloading, which GAIA can't do for you. Pick an ungated "
            "copy of the model.",
        )
    if info.get("disabled"):
        return fail(
            "repo", "Public repository", f"Hugging Face has disabled {link.repo}."
        )
    _check(checks, "repo", "Public repository", True, link.repo)

    variants, projectors = gguf_variants(info.get("siblings") or [])
    if not variants:
        return fail(
            "gguf",
            "GGUF weights",
            f"{link.repo} has no GGUF files, which is the format GAIA runs. Look "
            "for a repo whose name ends in -GGUF.",
        )
    try:
        chosen_hint = _find_variant(variants, link.variant) if link.variant else None
        if variant:
            chosen_hint = _find_variant(variants, variant)
    except CustomModelError as e:
        return fail("gguf", "GGUF weights", str(e))
    _check(
        checks,
        "gguf",
        "GGUF weights",
        True,
        f"{len(variants)} quantization{'s' if len(variants) != 1 else ''}",
    )

    gguf = info.get("gguf") or {}
    template = str(gguf.get("chat_template") or "")
    if not template:
        return fail(
            "chat",
            "Chat model",
            f"{link.repo} has no chat template, so it is a base model that can't "
            "hold a conversation. Pick the instruct or chat version.",
        )
    _check(checks, "chat", "Chat model", True, "has a chat template")

    architecture = str(gguf.get("architecture") or "")
    try:
        if not architecture:
            raise CustomModelError(
                f"Hugging Face doesn't report the GGUF architecture of "
                f"{link.repo}, so GAIA can't check that llama.cpp loads it."
            )
        tag = llamacpp_release(system_info, preferred_backend)
        report["llamacpp_version"] = tag
        if architecture not in supported_architectures(tag):
            raise CustomModelError(
                f"This model's architecture, '{architecture}', is not supported by "
                f"llama.cpp {tag}, the build your Lemonade runs. It needs a newer "
                "llama.cpp: update Lemonade (`gaia init` installs the newest one "
                "GAIA supports), then add the model again."
            )
    except CustomModelError as e:
        report["architecture"] = architecture or None
        return fail("architecture", "Supported architecture", str(e))
    report["architecture"] = architecture
    _check(
        checks,
        "architecture",
        "Supported architecture",
        True,
        f"{architecture} runs on llama.cpp {tag}",
    )

    projector = _pick_projector(projectors)
    extra = projector["size"] if projector else 0
    try:
        capacity = capacity_from_system_info(system_info)
    except ModelFitError as e:
        return fail("fit", "Fits this PC", str(e))

    def verdict(v: Variant):
        return check_fit((v.size_bytes + extra) / 1e9, capacity)

    report["variants"] = [
        {
            "quant": v.quant,
            "file": v.files[0],
            "size_gb": round((v.size_bytes + extra) / 1e9, 1),
            "fits": verdict(v).fits,
            "reason": verdict(v).reason or None,
        }
        for v in variants
    ]
    chosen = chosen_hint or _default_variant(variants, lambda v: verdict(v).fits)
    size_gb = round((chosen.size_bytes + extra) / 1e9, 1)
    tool_calling = "tools" in template
    report.update(
        {
            "model_id": _model_name(link.repo, chosen.quant),
            "display_name": f"{_repo_base(link.repo)} {chosen.quant}",
            "quant": chosen.quant,
            "file": chosen.files[0],
            "checkpoint": f"{link.repo}:{chosen.checkpoint_variant}",
            "recipe": "llamacpp",
            "mmproj": projector["file"] if projector else None,
            "vision": projector is not None,
            "reasoning": any(
                k in template
                for k in ("<think>", "reasoning_content", "enable_thinking")
            ),
            "tool_calling": tool_calling,
            "size_gb": size_gb,
            "context_length": gguf.get("context_length"),
        }
    )
    fit = verdict(chosen)
    if not fit.fits:
        return fail(
            "fit",
            "Fits this PC",
            f"{chosen.quant} ({size_gb} GB) {fit.reason}. Pick a smaller "
            "quantization.",
        )
    _check(checks, "fit", "Fits this PC", True, f"{chosen.quant}, {size_gb} GB")

    if not tool_calling:
        report["warnings"].append(
            "Its chat template has no tool support, so GAIA can chat with it but "
            "can't use tools (files, web, documents) through it."
        )

    from gaia.llm.lemonade_client import MODELS, _model_ids_match

    for req in MODELS.values():
        if _model_ids_match(req.model_id, report["model_id"]) or (
            req.checkpoint and req.checkpoint.lower() == report["checkpoint"].lower()
        ):
            return fail(
                "new",
                "Not already listed",
                f"GAIA already offers this model as {req.display_name}.",
            )
    added = find_custom_model(report["model_id"])
    if added is not None:
        same = added["checkpoint"].lower() == report["checkpoint"].lower()
        return fail(
            "new",
            "Not already listed",
            (
                f"You already added {report['display_name']}."
                if same
                else f"You already added a model named {report['model_id']} from "
                f"{added['source_url']}. Remove it first to add this one."
            ),
        )
    for m in catalog or []:
        listed = str(m.get("id") or "")
        theirs = str(m.get("checkpoint") or "")
        if _model_ids_match(listed, report["model_id"]) and (
            theirs.lower() != report["checkpoint"].lower()
        ):
            return fail(
                "new",
                "Not already listed",
                f"Lemonade already has another model named {listed}, so this "
                "one can't be added under the same name.",
            )
    report["ok"] = True
    return report


# ── The saved list ─────────────────────────────────────────────────────────

_ENTRY_KEYS = {
    "id": str,
    "display_name": str,
    "checkpoint": str,
    "recipe": str,
    "source_url": str,
}

_cache: Dict[str, Any] = {"key": None, "entries": []}


def validate_entries(value: Any, where: Path) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(e, dict)
        and all(isinstance(e.get(k), t) and e.get(k) for k, t in _ENTRY_KEYS.items())
        and str(e["id"]).startswith("user.")
        for e in value
    ):
        from gaia.config import GaiaConfigError

        raise GaiaConfigError(
            f"GAIA config at {where}: '{CONFIG_KEY}' must be a list of added "
            "models, each with id (starting 'user.'), display_name, checkpoint, "
            "recipe and source_url. Fix it by hand, or delete the key to forget "
            "every added model."
        )
    return value


def list_custom_models() -> List[Dict[str, Any]]:
    """The user's added models, as saved; re-read only when the file changes."""
    from gaia.config import GaiaConfig

    path = GaiaConfig.config_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["key"] != key:
        doc = GaiaConfig._parse_doc(path.read_text(encoding="utf-8"), path)
        entries = validate_entries(doc[CONFIG_KEY], path) if CONFIG_KEY in doc else []
        _cache.update(key=key, entries=entries)
    return [dict(e) for e in _cache["entries"]]


def find_custom_model(model_id: Optional[str]) -> Optional[Dict[str, Any]]:
    from gaia.llm.lemonade_client import _model_ids_match

    if not model_id:
        return None
    for e in list_custom_models():
        if _model_ids_match(e["id"], model_id):
            return e
    return None


def requirement_for(entry: Dict[str, Any]):
    """The :class:`ModelRequirement` an added model loads and downloads with."""
    from gaia.llm.lemonade_client import ModelRequirement, ModelType

    native = entry.get("context_length")
    native = native if isinstance(native, int) and native > 0 else None
    return ModelRequirement(
        model_type=ModelType.LLM,
        model_id=entry["id"],
        display_name=entry["display_name"],
        min_ctx_size=min(ADDED_MODEL_CTX, native) if native else ADDED_MODEL_CTX,
        tool_calling=bool(entry.get("tool_calling", True)),
        checkpoint=entry["checkpoint"],
        recipe=entry["recipe"],
        mmproj=entry.get("mmproj"),
        vision=bool(entry.get("vision")),
        reasoning=bool(entry.get("reasoning")),
        size_gb=entry.get("size_gb"),
        max_ctx_size=native,
    )


def add_custom_model(report: Dict[str, Any]) -> Dict[str, Any]:
    """Save a model :func:`inspect_link` passed. Returns the saved entry."""
    from gaia.config import GaiaConfig

    if not report.get("ok"):
        raise CustomModelError(
            report.get("error") or "The link has not passed its checks yet."
        )
    entry = {
        "id": report["model_id"],
        "display_name": report["display_name"],
        "checkpoint": report["checkpoint"],
        "recipe": report["recipe"],
        "mmproj": report.get("mmproj"),
        "vision": bool(report.get("vision")),
        "reasoning": bool(report.get("reasoning")),
        "tool_calling": bool(report.get("tool_calling")),
        "size_gb": report.get("size_gb"),
        "architecture": report.get("architecture"),
        "context_length": report.get("context_length"),
        "source_url": report["url"],
        "added_at": int(time.time()),
    }

    def _apply(doc: Dict[str, Any]) -> None:
        from gaia.llm.lemonade_client import _model_ids_match

        current = validate_entries(doc.get(CONFIG_KEY, []), GaiaConfig.config_path())
        if any(_model_ids_match(e["id"], entry["id"]) for e in current):
            raise CustomModelError(f"You already added {entry['display_name']}.")
        doc[CONFIG_KEY] = [*current, entry]

    GaiaConfig.edit_doc(_apply)
    return entry


def remove_custom_model(
    model_id: str, delete_from_server: Callable[[Dict[str, Any]], None]
) -> Dict[str, Any]:
    """Forget an added model and delete its download through *delete_from_server*.

    *delete_from_server* gets the saved entry and must delete it from Lemonade
    when Lemonade has it (and do nothing when it never downloaded). A model
    that was the default or the last one picked stops being either.
    """
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import _model_ids_match

    entry = find_custom_model(model_id)
    if entry is None:
        raise CustomModelError(
            f"{model_id} is not a model you added, so it can't be removed here."
        )
    delete_from_server(entry)
    cleared: List[str] = []

    def _apply(doc: Dict[str, Any]) -> None:
        current = validate_entries(doc.get(CONFIG_KEY, []), GaiaConfig.config_path())
        kept = [e for e in current if not _model_ids_match(e["id"], entry["id"])]
        if kept:
            doc[CONFIG_KEY] = kept
        else:
            doc.pop(CONFIG_KEY, None)
        for key in ("default_model", "last_model"):
            if _model_ids_match(doc.get(key), entry["id"]):
                doc.pop(key, None)
                cleared.append(key)
        if "last_model" in cleared:
            doc.pop("last_provider", None)

    GaiaConfig.edit_doc(_apply)
    return {
        "removed": entry["id"],
        "display_name": entry["display_name"],
        "cleared": cleared,
    }


def listed_entry(catalog: List[Dict[str, Any]], entry: Dict[str, Any]) -> Optional[str]:
    """The id Lemonade lists *entry* under, matched by name and checkpoint.

    The checkpoint keeps a remove from deleting another model that happens to
    share the name without its ``user.`` prefix.
    """
    from gaia.llm.lemonade_client import _model_ids_match

    for m in catalog:
        listed = str(m.get("id") or "")
        if _model_ids_match(listed, entry["id"]) and (
            str(m.get("checkpoint") or "").lower() == entry["checkpoint"].lower()
        ):
            return listed
    return None
