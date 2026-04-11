# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Uninstall Command

Implements ``gaia uninstall`` with tiered cleanup. Designed to be the single
implementation called by every desktop installer platform (NSIS, DMG, DEB,
AppImage) via a subprocess call.

Tiers:
    * Default (no flags)  — print a friendly help message, touch nothing.
      Tier 1 (removing the Electron app binaries / shortcuts / registry
      entries) is always the responsibility of the platform's native
      uninstaller; this command only owns the shared Python-side state.
    * ``--venv``          — Tier 2: remove ``~/.gaia/venv/``.
    * ``--purge``         — Tier 3: venv + chat data + documents + electron
      config + install logs / state files. Always keeps ``~/.gaia/`` itself
      so other tools that store data there (MCP config, etc.) are preserved.

Opt-in extras (only valid alongside ``--purge``):
    * ``--purge-lemonade`` — best-effort Lemonade Server removal.
    * ``--purge-models``   — remove ``~/.cache/lemonade/models/``.
    * ``--purge-hf-cache`` — remove the HuggingFace hub cache (restores the
      legacy ``gaia uninstall --models`` cleanup capability).

Flags:
    * ``--dry-run`` — print paths that would be removed, touch nothing.
    * ``--yes``     — skip the interactive confirmation prompt. Required for
      CI / scripted use. Auto-detected when ``stdin`` is not a TTY so that
      silent NSIS uninstall and Debian ``postrm`` flows work without the
      flag. NOTE: ``--purge`` on a non-TTY stdin still requires an explicit
      ``--yes`` to avoid accidental user-data deletion from pipes / cron.

Environment:
    * ``GAIA_HOME``  — override the location of ``~/.gaia`` (useful for
      multi-user installs or alternate data roots).
    * ``HF_HOME``    — standard HuggingFace cache override, respected by
      ``--purge-hf-cache``.

Exit codes:
    * 0  — success, dry-run, or no-op
    * 1  — user aborted at the confirmation prompt, or refused a dangerous
           non-interactive ``--purge``
    * 2  — filesystem error (permission denied, unreadable path, ...)
    * 64 — usage error (invalid flag combination). Matches BSD ``EX_USAGE``
           so NSIS / ``postrm`` can tell "bad invocation" from "I/O broken"
           and avoid reinstall retries.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_FS_ERROR = 2
# BSD sysexits EX_USAGE. Distinct from EXIT_FS_ERROR so silent uninstallers
# (NSIS, Debian postrm) can tell a bad flag combination from a real I/O
# failure and avoid "failed, retry" loops on misuse.
EXIT_USAGE = 64


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _gaia_home(home: Optional[Path] = None) -> Path:
    """Return the GAIA home directory.

    Resolution order:
      1. ``$GAIA_HOME`` environment variable (expanded + resolved).
      2. ``<home>/.gaia`` where ``home`` is the passed-in override or
         ``Path.home()``.
    """
    env_override = os.environ.get("GAIA_HOME")
    if env_override:
        return Path(env_override).expanduser().resolve()
    base = home if home is not None else Path.home()
    return base / ".gaia"


def _lemonade_models_dir(home: Optional[Path] = None) -> Path:
    """Return the Lemonade models cache directory.

    On Windows the cache lives under ``%LOCALAPPDATA%\\lemonade\\models``
    when that env var is set; otherwise we fall back to the
    POSIX-style ``~/.cache/lemonade/models`` which also works on macOS and
    Linux.
    """
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "lemonade" / "models"
    base = home if home is not None else Path.home()
    return base / ".cache" / "lemonade" / "models"


def _huggingface_cache_dir(home: Optional[Path] = None) -> Path:
    """Return the HuggingFace hub cache directory.

    Many GAIA models (sentence-transformers, embedding models, etc.) are
    downloaded into the standard HuggingFace cache rather than Lemonade's
    cache. The location follows the HF convention:

      * ``HF_HOME/hub`` if ``HF_HOME`` is set
      * Otherwise ``%LOCALAPPDATA%\\huggingface\\hub`` on Windows when set
      * Otherwise ``~/.cache/huggingface/hub`` on POSIX and macOS

    Restoring the cleanup capability that the legacy
    ``gaia uninstall --models`` flag provided before Phase D's refactor.
    """
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "huggingface" / "hub"
    base = home if home is not None else Path.home()
    return base / ".cache" / "huggingface" / "hub"


def _venv_paths(home: Optional[Path] = None) -> List[Path]:
    """Paths that belong to Tier 2 (``--venv``)."""
    gaia = _gaia_home(home)
    return [gaia / "venv"]


def _purge_paths(home: Optional[Path] = None) -> List[Path]:
    """Paths that belong to Tier 3 (``--purge``).

    ``--purge`` implies ``--venv``. We keep ``~/.gaia/`` itself so other
    tooling that lives alongside us (e.g. MCP config) is preserved.
    """
    gaia = _gaia_home(home)
    return [
        gaia / "venv",
        gaia / "chat",
        gaia / "documents",
        gaia / "electron-config.json",
        gaia / "gaia.log",
        gaia / "electron-install-state.json",
        gaia / "electron-install.log",
    ]


def _safe_roots(home: Optional[Path] = None) -> List[Path]:
    """Return the set of directories that ``_remove_path`` is allowed to
    delete into.

    Containment guard: any path ``_remove_path`` touches must resolve to a
    location inside one of these roots. Defence-in-depth against future
    regressions that could otherwise escalate to arbitrary deletion.
    """
    return [
        _gaia_home(home),
        _lemonade_models_dir(home),
        _huggingface_cache_dir(home),
    ]


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


@dataclass
class UninstallPlan:
    """What an invocation of ``gaia uninstall`` intends to do.

    ``tiered_paths`` is a list of ``(tier_label, path)`` tuples so dry-run
    output can show *why* a path was selected.
    """

    tiered_paths: List[tuple] = field(default_factory=list)
    purge_lemonade: bool = False
    purge_models_path: Optional[Path] = None
    purge_hf_cache_path: Optional[Path] = None

    def unique_paths(self) -> List[Path]:
        """Return deduplicated paths preserving the order they were added."""
        seen = set()
        out: List[Path] = []
        for _, path in self.tiered_paths:
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
        return out

    def is_empty(self) -> bool:
        return (
            not self.tiered_paths
            and not self.purge_lemonade
            and self.purge_models_path is None
            and self.purge_hf_cache_path is None
        )


def build_plan(
    *,
    venv: bool,
    purge: bool,
    purge_lemonade: bool,
    purge_models: bool,
    purge_hf_cache: bool = False,
    home: Optional[Path] = None,
) -> UninstallPlan:
    """Build an :class:`UninstallPlan` from the parsed flag set.

    Assumes validation has already happened (``--purge-lemonade`` /
    ``--purge-models`` / ``--purge-hf-cache`` must come with ``--purge``).
    ``--purge`` wins over ``--venv`` if both are passed.
    """
    plan = UninstallPlan()

    if purge:
        for path in _purge_paths(home):
            plan.tiered_paths.append(("--purge", path))
    elif venv:
        for path in _venv_paths(home):
            plan.tiered_paths.append(("--venv", path))

    if purge_lemonade:
        plan.purge_lemonade = True

    if purge_models:
        plan.purge_models_path = _lemonade_models_dir(home)

    if purge_hf_cache:
        plan.purge_hf_cache_path = _huggingface_cache_dir(home)

    return plan


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print(msg: str = "", *, printer: Callable[[str], None] = print) -> None:
    printer(msg)


def _print_no_flags_help(printer: Callable[[str], None] = print) -> None:
    """Friendly message shown when ``gaia uninstall`` runs with no flags."""
    lines = [
        "",
        "gaia uninstall — tiered cleanup of the GAIA Python install.",
        "",
        "This command intentionally does nothing without a flag. The Electron",
        "app uninstaller removes the app binaries and shortcuts (Tier 1); this",
        "CLI only owns the shared Python state under ~/.gaia/ and the",
        "Lemonade cache.",
        "",
        "Choose a tier:",
        "  gaia uninstall --venv            Remove ~/.gaia/venv/ (Tier 2)",
        "  gaia uninstall --purge           Remove venv + chat data + documents +",
        "                                   electron config + install logs (Tier 3)",
        "",
        "Optional extras (must be combined with --purge):",
        "  --purge-lemonade                 Also uninstall Lemonade Server",
        "  --purge-models                   Also remove Lemonade's models cache",
        "  --purge-hf-cache                 Also remove the HuggingFace hub cache",
        "",
        "Safety:",
        "  --dry-run                        Show what would be removed, touch nothing",
        "  --yes, -y                        Skip the confirmation prompt",
        "",
        "Tip: run `gaia uninstall --dry-run --purge` first to preview everything.",
        "",
    ]
    for line in lines:
        _print(line, printer=printer)


def _print_plan(
    plan: UninstallPlan,
    *,
    dry_run: bool,
    printer: Callable[[str], None] = print,
) -> None:
    header = "[dry-run] Would remove:" if dry_run else "The following will be removed:"
    _print(header, printer=printer)
    if plan.tiered_paths:
        for label, path in plan.tiered_paths:
            _print(f"  ({label}) {path}", printer=printer)
    if plan.purge_lemonade:
        _print("  (--purge-lemonade) Lemonade Server (best-effort)", printer=printer)
    if plan.purge_models_path is not None:
        _print(
            f"  (--purge-models) {plan.purge_models_path}",
            printer=printer,
        )
    if plan.purge_hf_cache_path is not None:
        _print(
            f"  (--purge-hf-cache) {plan.purge_hf_cache_path}",
            printer=printer,
        )
    _print(printer=printer)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _should_skip_prompt(yes: bool) -> bool:
    """Return True if we should not show an interactive prompt.

    Explicit ``--yes`` always skips. Non-TTY stdin also skips so that silent
    uninstallers (NSIS, Debian ``postrm``, CI) work without the flag.

    NOTE: This applies to Tier 2 (``--venv``) only. ``--purge`` adds an
    extra guardrail in :func:`run` that refuses to proceed on non-TTY stdin
    without an explicit ``--yes`` — see ``_refuse_silent_purge``.
    """
    if yes:
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        # E.g. stdin closed; treat as non-interactive.
        return True


def _stdin_is_tty() -> bool:
    """Return True if stdin looks like an interactive terminal."""
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _confirm(
    prompt: str = "Continue? [y/N]: ",
    *,
    input_fn: Callable[[str], str] = input,
) -> bool:
    try:
        answer = input_fn(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Log handler cleanup (Windows data-loss guard)
# ---------------------------------------------------------------------------


def _close_gaia_log_handlers(home: Optional[Path] = None) -> None:
    """Detach + close any :class:`logging.FileHandler` whose target file
    lives inside the GAIA home directory.

    Required before a Tier 3 purge on Windows: ``gaia/gaia.log`` is opened
    by :mod:`gaia.logger` with an exclusive file handle, and leaving it
    open causes ``shutil.rmtree`` to raise ``PermissionError`` mid-delete,
    resulting in a partial-state uninstall. Closing the handler(s) up
    front makes the log file freely deletable across all platforms.

    This is a no-op on POSIX / macOS (where unlinking an open file is
    allowed) but harmless and kept unconditional for simplicity.
    """
    try:
        gaia_root = _gaia_home(home).resolve()
    except OSError:
        return

    # The GaiaLogger in gaia.logger attaches its FileHandler to the root
    # logger, but also inspect the "gaia" namespace handler list in case
    # future code attaches there directly.
    candidate_loggers = [logging.getLogger(), logging.getLogger("gaia")]

    for logger in candidate_loggers:
        for handler in list(logger.handlers):
            if not isinstance(handler, logging.FileHandler):
                continue
            base_name = getattr(handler, "baseFilename", None)
            if not base_name:
                continue
            try:
                handler_path = Path(base_name).resolve()
            except OSError:
                continue
            try:
                inside = handler_path.is_relative_to(gaia_root)
            except ValueError:
                inside = False
            if not inside:
                continue
            try:
                handler.close()
            except Exception:
                # Best-effort — never propagate a handler teardown error.
                pass
            try:
                logger.removeHandler(handler)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Removal primitives
# ---------------------------------------------------------------------------


def _remove_path(
    path: Path,
    *,
    allowed_roots: List[Path],
    printer: Callable[[str], None] = print,
) -> bool:
    """Remove ``path`` if it exists. Returns True on success or no-op.

    ``allowed_roots`` is a list of resolved absolute directories that
    ``path`` must fall inside once resolved. Any attempt to delete outside
    of those roots raises :class:`RuntimeError`, so a bug or regression
    elsewhere in the command cannot escalate into arbitrary filesystem
    deletion.

    Any :class:`OSError` (including ``PermissionError``) is caught and
    reported; the caller decides whether to escalate to an error exit code.
    """
    # Containment guard: resolve the candidate path and confirm it lives
    # inside at least one permitted root. We compare resolved paths on both
    # sides so symlinks / relative segments can't sneak out.
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        _print(f"  [error] could not resolve {path}: {exc}", printer=printer)
        return False

    resolved_roots: List[Path] = []
    for root in allowed_roots:
        try:
            resolved_roots.append(root.resolve(strict=False))
        except OSError:
            continue

    inside_any = False
    for root in resolved_roots:
        try:
            if resolved.is_relative_to(root):
                inside_any = True
                break
        except ValueError:
            continue

    if not inside_any:
        raise RuntimeError(
            f"Refusing to delete {resolved}: outside allowed roots "
            f"({', '.join(str(r) for r in resolved_roots)})"
        )

    try:
        if not path.exists() and not path.is_symlink():
            _print(f"  [skip] {path} (does not exist)", printer=printer)
            return True
    except OSError as exc:
        _print(f"  [error] could not stat {path}: {exc}", printer=printer)
        return False

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            _print(f"  [skip] {path} (unsupported file type)", printer=printer)
            return True
    except PermissionError as exc:
        _print(f"  [error] permission denied removing {path}: {exc}", printer=printer)
        return False
    except OSError as exc:
        _print(f"  [error] failed to remove {path}: {exc}", printer=printer)
        return False

    _print(f"  [removed] {path}", printer=printer)
    return True


def _resolve_lemonade_python() -> Optional[str]:
    """Return the best-guess Python interpreter that owns ``lemonade-server``.

    When called from the GAIA installer bundle, ``sys.executable`` is the
    bundled venv, NOT where Lemonade was installed — so running ``pip
    uninstall`` against it is always a no-op. Locate the real one by
    inspecting ``lemonade-server`` on PATH:

      * POSIX: read the script's shebang.
      * Windows: walk the parent directory for ``python.exe`` /
        ``Scripts/python.exe``.

    Returns ``None`` when we can't make a confident guess; callers should
    then skip the pip fallback rather than pip-uninstall the wrong env.
    """
    lemonade = shutil.which("lemonade-server")
    if not lemonade:
        return None

    lemonade_path = Path(lemonade)

    if sys.platform.startswith("win"):
        # Typical layouts:
        #   <env>/Scripts/lemonade-server.exe → <env>/python.exe
        #   <env>/Scripts/lemonade-server.exe → <env>/Scripts/python.exe
        scripts_dir = lemonade_path.parent
        candidates = [
            scripts_dir / "python.exe",
            scripts_dir.parent / "python.exe",
            scripts_dir / "pythonw.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    # POSIX / macOS: parse the shebang of the console script.
    try:
        with open(lemonade_path, "rb") as fp:
            first_line = fp.readline()
    except OSError:
        return None
    if not first_line.startswith(b"#!"):
        return None
    shebang = first_line[2:].strip().decode("utf-8", errors="replace")
    # Handle `#!/usr/bin/env python3` as well as a direct path.
    parts = shebang.split()
    if not parts:
        return None
    if parts[0].endswith("env") and len(parts) > 1:
        return parts[1]
    return parts[0]


def _remove_lemonade(printer: Callable[[str], None] = print) -> bool:
    """Best-effort Lemonade Server removal.

    Never fails the command — returns True even if Lemonade could not be
    removed, so the rest of the uninstall flow proceeds.
    """
    _print("  [lemonade] attempting to uninstall Lemonade Server...", printer=printer)

    # Strategy 1: the shared LemonadeInstaller class knows how to uninstall
    # on Windows (msiexec) and Linux (apt / dpkg).
    try:
        from gaia.installer.lemonade_installer import LemonadeInstaller

        installer = LemonadeInstaller()
        info = installer.check_installation()
        if not info.installed:
            _print(
                "  [lemonade] not installed — nothing to do",
                printer=printer,
            )
            return True

        result = installer.uninstall(silent=True)
        if result.success:
            _print(
                "  [lemonade] uninstalled via LemonadeInstaller",
                printer=printer,
            )
            return True
        _print(
            f"  [lemonade] LemonadeInstaller.uninstall failed: {result.error}",
            printer=printer,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _print(
            f"  [lemonade] LemonadeInstaller unavailable: {exc}",
            printer=printer,
        )

    # Strategy 2: pip uninstall against the interpreter that actually owns
    # the ``lemonade-server`` console script. Pip-uninstalling against
    # ``sys.executable`` is wrong here: when invoked from the installer
    # bundle that's the GAIA venv, NOT where Lemonade lives.
    python = _resolve_lemonade_python()
    if python is None:
        _print(
            "  [lemonade] lemonade-server not on PATH; skipping pip uninstall.",
            printer=printer,
        )
    else:
        try:
            result = subprocess.run(
                [python, "-m", "pip", "uninstall", "-y", "lemonade-sdk"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0:
                _print(
                    f"  [lemonade] removed via `{python} -m pip uninstall lemonade-sdk`",
                    printer=printer,
                )
                return True
            _print(
                "  [lemonade] pip uninstall did not remove lemonade-sdk "
                f"(exit {result.returncode}); giving up cleanly",
                printer=printer,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            _print(
                f"  [lemonade] pip uninstall unavailable: {exc}",
                printer=printer,
            )

    _print(
        "  [lemonade] could not auto-remove Lemonade Server. If you want it "
        "gone, uninstall it manually via your OS package manager.",
        printer=printer,
    )
    return True  # best-effort — never fail the command


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_plan(
    plan: UninstallPlan,
    *,
    allowed_roots: List[Path],
    printer: Callable[[str], None] = print,
) -> int:
    """Execute a plan and return the exit code.

    ``allowed_roots`` is forwarded to :func:`_remove_path` so every delete
    is containment-checked against the GAIA-owned directories.
    """
    all_ok = True

    for path in plan.unique_paths():
        if not _remove_path(path, allowed_roots=allowed_roots, printer=printer):
            all_ok = False

    if plan.purge_models_path is not None:
        if not _remove_path(
            plan.purge_models_path, allowed_roots=allowed_roots, printer=printer
        ):
            all_ok = False

    if plan.purge_hf_cache_path is not None:
        if not _remove_path(
            plan.purge_hf_cache_path, allowed_roots=allowed_roots, printer=printer
        ):
            all_ok = False

    if plan.purge_lemonade:
        _remove_lemonade(printer=printer)

    _print(printer=printer)
    if all_ok:
        _print("gaia uninstall: done.", printer=printer)
        return EXIT_OK

    _print(
        "gaia uninstall: completed with errors (see messages above).",
        printer=printer,
    )
    return EXIT_FS_ERROR


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run(
    args: argparse.Namespace,
    *,
    home: Optional[Path] = None,
    printer: Callable[[str], None] = print,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Run ``gaia uninstall`` from a parsed argparse namespace.

    Kept as a thin wrapper so tests can feed it a crafted namespace plus
    fake I/O functions without touching the real shell.
    """
    venv: bool = bool(getattr(args, "venv", False))
    purge: bool = bool(getattr(args, "purge", False))
    purge_lemonade: bool = bool(getattr(args, "purge_lemonade", False))
    purge_models: bool = bool(getattr(args, "purge_models", False))
    purge_hf_cache: bool = bool(getattr(args, "purge_hf_cache", False))
    dry_run: bool = bool(getattr(args, "dry_run", False))
    yes: bool = bool(getattr(args, "yes", False))

    # Validate: extras require --purge. Use EXIT_USAGE (64, BSD EX_USAGE)
    # so NSIS / postrm hooks can distinguish "bad flags" from a real I/O
    # failure and avoid triggering a reinstall retry.
    if purge_lemonade and not purge:
        _print(
            "error: --purge-lemonade requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-lemonade",
            printer=printer,
        )
        return EXIT_USAGE

    if purge_models and not purge:
        _print(
            "error: --purge-models requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-models",
            printer=printer,
        )
        return EXIT_USAGE

    if purge_hf_cache and not purge:
        _print(
            "error: --purge-hf-cache requires --purge.",
            printer=printer,
        )
        _print(
            "       Re-run as: gaia uninstall --purge --purge-hf-cache",
            printer=printer,
        )
        return EXIT_USAGE

    # No flags at all → friendly help (always exit 0, never crash even when
    # stdin is closed).
    if not (
        venv or purge or purge_lemonade or purge_models or purge_hf_cache or dry_run
    ):
        _print_no_flags_help(printer=printer)
        return EXIT_OK

    # Safety guardrail: refuse to Tier-3 purge on non-TTY stdin without an
    # explicit --yes. _should_skip_prompt() auto-skips confirmation when
    # stdin isn't a tty (needed for silent NSIS / postrm runs), which is
    # safe for Tier 2 but would silently nuke ~/.gaia/chat + documents on
    # Tier 3 if something accidentally pipes to us (cron, build job, etc.).
    if purge and not dry_run and not yes and not _stdin_is_tty():
        _print(
            "error: Refusing to --purge without --yes on non-interactive stdin. "
            "Pass --yes to confirm deletion of ~/.gaia/ user data.",
            printer=printer,
        )
        return EXIT_ABORTED

    plan = build_plan(
        venv=venv,
        purge=purge,
        purge_lemonade=purge_lemonade,
        purge_models=purge_models,
        purge_hf_cache=purge_hf_cache,
        home=home,
    )

    if plan.is_empty() and dry_run:
        _print("[dry-run] Nothing to do — no tier or extras selected.", printer=printer)
        return EXIT_OK

    if plan.is_empty():
        _print_no_flags_help(printer=printer)
        return EXIT_OK

    _print_plan(plan, dry_run=dry_run, printer=printer)

    if dry_run:
        _print("[dry-run] No files were modified.", printer=printer)
        return EXIT_OK

    if not _should_skip_prompt(yes):
        if not _confirm(input_fn=input_fn):
            _print("Aborted. Nothing was removed.", printer=printer)
            return EXIT_ABORTED

    # Windows data-loss guard: gaia.log is held open by a FileHandler
    # attached by gaia.logger. Detach + close it before rmtree walks into
    # ~/.gaia so the file can actually be removed.
    if purge:
        _close_gaia_log_handlers(home=home)

    allowed_roots = _safe_roots(home)
    return execute_plan(plan, allowed_roots=allowed_roots, printer=printer)


def register_subparser(
    subparsers: "argparse._SubParsersAction",
    parent_parser: Optional[argparse.ArgumentParser] = None,
) -> argparse.ArgumentParser:
    """Attach the ``uninstall`` subcommand to ``subparsers``.

    Called from ``gaia.cli`` so the CLI registration stays close to every
    other subcommand while the implementation lives in this module.
    """
    parents = [parent_parser] if parent_parser is not None else []
    parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall GAIA components (tiered cleanup of ~/.gaia and caches)",
        description=(
            "Tiered cleanup of the GAIA Python install. "
            "Default (no flags) prints a help message and exits. "
            "Use --dry-run first to preview what would be removed."
        ),
        parents=parents,
    )
    parser.add_argument(
        "--venv",
        action="store_true",
        help="Tier 2: remove ~/.gaia/venv/",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "Tier 3: remove venv + chat + documents + electron config + "
            "install logs. Implies --venv."
        ),
    )
    parser.add_argument(
        "--purge-lemonade",
        action="store_true",
        dest="purge_lemonade",
        help="Also uninstall Lemonade Server (requires --purge).",
    )
    parser.add_argument(
        "--purge-models",
        action="store_true",
        dest="purge_models",
        help="Also remove the Lemonade models cache (requires --purge).",
    )
    parser.add_argument(
        "--purge-hf-cache",
        action="store_true",
        dest="purge_hf_cache",
        help=(
            "Also remove the HuggingFace hub cache "
            "(~/.cache/huggingface/hub or $HF_HOME/hub). Requires --purge. "
            "Restores the cleanup behavior of the legacy "
            "`gaia uninstall --models` flag from before v0.17.2."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would be removed without touching the filesystem.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=(
            "Skip the interactive confirmation prompt. Also auto-skipped "
            "when stdin is not a TTY (Tier 2 only; --purge always needs "
            "--yes on non-TTY stdin)."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone entry point, useful for ``python -m`` style invocation."""
    parser = argparse.ArgumentParser(
        prog="gaia uninstall",
        description="Tiered cleanup of the GAIA Python install.",
    )
    parser.add_argument("--venv", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--purge-lemonade", action="store_true", dest="purge_lemonade")
    parser.add_argument("--purge-models", action="store_true", dest="purge_models")
    parser.add_argument("--purge-hf-cache", action="store_true", dest="purge_hf_cache")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--yes", "-y", action="store_true")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
