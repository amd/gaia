# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Remedy commands must be *runnable*, not merely well-spelled.

The recurring failure this guards against is NOT a command that doesn't
exist — it is a command that exists but rejects what the message passes it.
`gaia download SD-Turbo` reads fine in review and passes any mocked test,
but `gaia download` takes no positional model argument, so argparse exits 2
and the stuck user stays stuck.

So these tests parse what the remedy helpers actually emit against the real
CLI parser, rather than asserting on the text.
"""

import re
import shlex
from pathlib import Path

import pytest

from gaia.llm.lemonade_launcher import (
    LemonadeTooling,
    describe_client_hint,
    describe_start_hint,
)

MODERN_LINUX = LemonadeTooling(
    found=True,
    kind="modern",
    client_path="/usr/bin/lemonade",
    server_launcher="/usr/bin/lemond",
)
LEGACY = LemonadeTooling(
    found=True,
    kind="legacy",
    client_path="/usr/bin/lemonade-server",
    server_launcher="/usr/bin/lemonade-server",
)
NOT_FOUND = LemonadeTooling(found=False, kind="none")


def _parser():
    from gaia.cli import build_parser

    return build_parser()


def _assert_parses(command):
    """A `gaia ...` command must be accepted by the real CLI parser."""
    argv = shlex.split(command)
    assert argv and argv[0] == "gaia", f"not a gaia command: {command!r}"
    try:
        _parser().parse_args(argv[1:])
    except SystemExit as exc:
        pytest.fail(f"CLI parser rejected remedy {command!r} (exit={exc.code})")


# ---------------------------------------------------------------------------
# The contract that keeps getting violated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, parses",
    [
        (["download"], True),
        (["download", "--agent", "sd"], True),
        (["download", "--clear-cache"], True),
        (["download", "--list"], True),
        (["init"], True),
        # The regression: `gaia download` has NO positional model argument.
        (["download", "SD-Turbo"], False),
        (["download", "user.embeddinggemma-300m-GGUF"], False),
    ],
)
def test_gaia_download_takes_no_positional_model(argv, parses):
    """Pins the real contract so a future edit reintroducing
    `gaia download <model>` fails here instead of in a user's terminal."""
    parser = _parser()
    if parses:
        parser.parse_args(argv)
    else:
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


# ---------------------------------------------------------------------------
# What the remedy helpers actually emit
# ---------------------------------------------------------------------------


def test_model_download_remedy_uses_the_lemonade_client_not_gaia_download(mocker):
    """Model pulls must route to the Lemonade client, which does take a
    positional model — never to `gaia download <model>`, which does not."""
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade", return_value=MODERN_LINUX
    )

    hint = describe_client_hint("pull", "SD-Turbo")

    assert hint.command == "/usr/bin/lemonade pull SD-Turbo"
    assert "gaia download" not in hint.instruction


def test_model_load_remedy_uses_the_lemonade_client(mocker):
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade", return_value=MODERN_LINUX
    )

    hint = describe_client_hint("load", "user.embeddinggemma-300m-GGUF")

    assert hint.command == "/usr/bin/lemonade load user.embeddinggemma-300m-GGUF"
    # `lemonade-server load` is the removed CLI's form.
    assert "lemonade-server load" not in hint.instruction


def test_legacy_client_keeps_its_own_binary_name(mocker):
    """A real legacy install DOES have lemonade-server — tell it the truth."""
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=LEGACY)

    hint = describe_client_hint("pull", "SD-Turbo")

    assert hint.command == "/usr/bin/lemonade-server pull SD-Turbo"


def test_client_hint_without_a_resolved_client_emits_no_command(mocker):
    """No client on disk -> prose, not an invented binary name."""
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=NOT_FOUND)

    hint = describe_client_hint("pull", "SD-Turbo")

    assert hint.command is None
    assert "lemonade-server pull" not in hint.instruction
    assert "gaia download" not in hint.instruction


def test_client_hint_ignores_a_server_only_env_override(mocker):
    """LEMONADE_SERVER_PATH names a *server*; using it as the client would be
    a guess, and `lemond pull X` is not a real command."""
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="modern",
            client_path="/opt/mine/lemond",
            server_launcher="/opt/mine/lemond",
            source="env",
        ),
    )

    hint = describe_client_hint("pull", "SD-Turbo")

    assert hint.command is None, "must not treat a server override as the client"


def test_client_hint_rejects_an_unsupported_action():
    """Fail loudly rather than rendering a subcommand the client lacks."""
    with pytest.raises(ValueError, match="Unsupported Lemonade client action"):
        describe_client_hint("yeet", "SD-Turbo")


# ---------------------------------------------------------------------------
# Any `gaia ...` a remedy helper emits must parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tooling", [MODERN_LINUX, LEGACY, NOT_FOUND])
@pytest.mark.parametrize("system", ["Linux", "Darwin", "Windows"])
def test_remedy_helpers_never_emit_an_unparseable_gaia_command(mocker, tooling, system):
    """Sweep every platform x tooling combination the helpers can produce and
    parse any `gaia ...` they name."""
    mocker.patch("platform.system", return_value=system)
    mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=tooling)
    mocker.patch("gaia.llm.lemonade_launcher._macos_app_installed", return_value=False)

    texts = [
        describe_start_hint().instruction,
        describe_start_hint(32768).instruction,
        describe_client_hint("pull", "SD-Turbo").instruction,
        describe_client_hint("load", "SD-Turbo").instruction,
    ]

    for text in texts:
        for fragment in text.replace("`", " ").replace(",", " ").split("."):
            if "gaia " not in fragment:
                continue
            command = "gaia " + fragment.split("gaia ", 1)[1]
            # Only the command itself, up to the first prose break.
            command = command.split(" to ")[0].split(" or ")[0].strip()
            _assert_parses(command)


# ---------------------------------------------------------------------------
# LemonadeError remedies the agent returns verbatim as its answer
# ---------------------------------------------------------------------------


def _gaia_commands(text):
    return re.findall(r"`(gaia [^`]*)`", text)


@pytest.mark.parametrize("tooling", [MODERN_LINUX, LEGACY, NOT_FOUND])
def test_missing_model_remedy_pulls_with_the_resolved_client(mocker, tooling):
    from gaia.llm.providers.lemonade import LemonadeModelNotFoundError

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=tooling)

    message = LemonadeModelNotFoundError(model_id="SD-Turbo").user_message

    assert "SD-Turbo" in message
    assert describe_client_hint("pull", "SD-Turbo").instruction in message
    assert "gaia download" not in message
    for command in _gaia_commands(message):
        _assert_parses(command)


def test_missing_model_remedy_without_a_model_id_names_no_dead_command():
    from gaia.llm.providers.lemonade import LemonadeModelNotFoundError

    message = LemonadeModelNotFoundError().user_message

    assert "gaia download" not in message
    for command in _gaia_commands(message):
        _assert_parses(command)


@pytest.mark.parametrize("tooling", [MODERN_LINUX, LEGACY, NOT_FOUND])
def test_flagship_readiness_hint_pulls_with_the_resolved_client(mocker, tooling):
    """The hub agent's readiness hint reaches the TUI's preflight screen, so a
    dead command there is as user-visible as one from the CLI."""
    import asyncio

    server = pytest.importorskip("gaia_agent.server")

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=tooling)
    mocker.patch.object(
        server,
        "_probe_lemonade",
        return_value={
            "base_url": "http://localhost:13305/api/v1",
            "reachable": True,
            "version": "999.0.0",
            "present": False,
            "ctx_size": None,
            "model_id": "Gemma-4-E4B-it-GGUF",
        },
    )

    hint = asyncio.run(server.init()).body.decode()

    assert "gaia download Gemma" not in hint
    for command in _gaia_commands(hint):
        _assert_parses(command)


def test_builder_no_model_remedy_pulls_with_the_resolved_client(mocker):
    from gaia.agents.builder import agent as builder_agent
    from gaia.llm.providers.lemonade import LemonadeError

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade", return_value=MODERN_LINUX
    )
    mocker.patch.object(builder_agent, "get_lemonade_models", return_value=[])

    with pytest.raises(LemonadeError) as excinfo:
        builder_agent._select_builder_model("http://localhost:13305/api/v1")

    message = excinfo.value.user_message
    model = builder_agent.BUILDER_PREFERRED_MODELS[-1]
    assert f"/usr/bin/lemonade pull {model}" in message
    assert "gaia download" not in message
    for command in _gaia_commands(message):
        _assert_parses(command)


# ---------------------------------------------------------------------------
# Static guard: no NEW `gaia download <model>` may appear anywhere
# ---------------------------------------------------------------------------

_DEAD_DOWNLOAD = re.compile(r"gaia download\s+(?!-)[\w.{<]")

_REPO = Path(__file__).resolve().parents[2]
# Every root a user-facing string can ship from. Scanning only src/gaia let the
# hub agent's readiness hint and its SKILL.md keep the dead command (#4216).
_SCAN_ROOTS = ("src/gaia", "hub", "tui", "docs")
_SCAN_EXTENSIONS = {".py", ".go", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".md", ".mdx"}
_SKIP_DIRS = {"node_modules", "dist", "build", ".turbo", "tests", "test", "__tests__"}


def _user_facing_files():
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(_REPO).parts):
                continue
            # Changelogs record history, including what older releases said.
            if path.name.startswith("test_") or path.name == "CHANGELOG.md":
                continue
            yield path


def test_no_new_gaia_download_with_a_positional_model():
    """`gaia download <model>` is rejected by argparse (see the contract test
    above). This has now shipped three times in three PRs, so fail on a
    fourth rather than waiting for a user to hit it."""
    offenders = []

    for path in _user_facing_files():
        rel = path.relative_to(_REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith(("#", "//")):
                continue  # a comment explaining the bug is not the bug
            if _DEAD_DOWNLOAD.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "`gaia download` takes no positional model argument — these would be "
        "rejected by argparse. Use describe_client_hint('pull', model) "
        "instead:\n" + "\n".join(offenders)
    )


def test_the_download_guard_actually_reaches_the_hub_and_the_docs():
    """A guard that scans the wrong roots passes while the bug ships.

    `src/gaia` alone is what let the hub agent's readiness hint keep the dead
    command through the PR that was meant to remove it.
    """
    scanned = {p.relative_to(_REPO).as_posix() for p in _user_facing_files()}
    for rel in (
        "hub/agents/gaia/python/gaia_agent/server.py",
        "hub/agents/gaia/npm/SKILL.md",
        "docs/guides/gaia.mdx",
    ):
        assert rel in scanned, f"{rel} is outside the guard's reach"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
