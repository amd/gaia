# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""dev_server: launch.json parsing, the folder rule, and approval by command digest.

The tool runs against the recorded loopback bridge, so the tests check which
ops reached GAIA desktop and what they carried. The gate is driven the way
``Agent._execute_tool`` drives it: the prompt's arguments first, then the
confirmation hooks, then the tool body.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.agents.base import console as console_mod
from gaia.agents.base import tools as tools_mod
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tool_grants import grant_scope
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import dev_servers
from gaia.browser import driver as browser_driver
from gaia.browser._pane_bridge import DISCOVERY_ENV
from gaia.browser.dev_servers import LaunchConfigError, load_specs, spec_named

from .browser_pane_fake import FakeBridge, fixture

pytestmark = pytest.mark.allow_network

FIXTURE_APP = (
    Path(__file__).resolve().parents[1] / "fixtures" / "browser_pane" / "devserver"
)


def write_launch(project: Path, *configs: dict) -> Path:
    path = project / ".gaia" / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "0.0.1", "configurations": list(configs)}))
    return path


WEB = {
    "name": "web",
    "runtimeExecutable": "node",
    "runtimeArgs": ["server.js"],
    "port": 4173,
}


class _Validator:
    """The chat's allowed folders, as PathValidator answers for them."""

    def __init__(self, *roots: Path) -> None:
        self.allowed_paths = {r.resolve() for r in roots}
        self.scratch_dir = None

    def is_path_allowed(self, path: str, prompt_user: bool = True) -> bool:
        assert (
            prompt_user is False
        ), "dev_server must never prompt for a folder from its gate"
        p = Path(path).resolve()
        return any(p == r or r in p.parents for r in self.allowed_paths)


def allow(*roots: Path):
    validator = _Validator(*roots)
    return lambda p: validator.is_path_allowed(str(p), prompt_user=False)


# ------------------------------------------------------------------ parsing


def test_a_configuration_becomes_argv_cwd_port_and_env(tmp_path):
    (tmp_path / "web").mkdir()
    write_launch(
        tmp_path,
        {
            **WEB,
            "cwd": "web",
            "env": {"NODE_ENV": "development", "VITE_API": "x"},
            "url": "/home",
        },
    )
    (spec,) = load_specs(tmp_path, allowed=allow(tmp_path))
    wire = spec.to_wire()
    assert set(wire) == set(fixture("requests.json")["devServers_ensure"]["spec"])
    assert wire == {
        "name": "web",
        "argv": ["node", "server.js"],
        "cwd": str((tmp_path / "web").resolve()),
        "port": 4173,
        "env": {"NODE_ENV": "development", "VITE_API": "x"},
        "url": "http://localhost:4173/home",
        "readyTimeoutMs": 60000,
    }


def test_a_missing_file_tells_the_agent_to_write_one(tmp_path):
    with pytest.raises(
        LaunchConfigError,
        match=r"does not exist\. Write it with write_file.*runtimeExecutable",
    ):
        load_specs(tmp_path, allowed=allow(tmp_path))


@pytest.mark.parametrize(
    "config, message",
    [
        (
            {**WEB, "runtimeExecutable": "npm run dev"} | {"runtimeArgs": "dev"},
            "runtimeArgs",
        ),
        ({**WEB, "port": 80}, '"port"'),
        ({**WEB, "port": "4173"}, '"port"'),
        (
            {**WEB, "env": {"ANTHROPIC_API_KEY": "x"}},
            "env ANTHROPIC_API_KEY is not allowed",
        ),
        (
            {**WEB, "runtimeExecutable": "rm", "runtimeArgs": ["-rf", "dist"]},
            "deletes files",
        ),
        ({**WEB, "url": "http://evil.example/"}, '"url"'),
        ({**WEB, "name": "../web"}, '"name"'),
    ],
)
def test_a_bad_configuration_names_the_field(tmp_path, config, message):
    write_launch(tmp_path, config)
    with pytest.raises(LaunchConfigError, match=message):
        load_specs(tmp_path, allowed=allow(tmp_path))


def test_cwd_may_not_leave_the_project(tmp_path):
    project = tmp_path / "site"
    project.mkdir()
    write_launch(project, {**WEB, "cwd": ".."})
    with pytest.raises(LaunchConfigError, match="leaves the project folder"):
        load_specs(project, allowed=allow(tmp_path))


def test_cwd_must_be_inside_the_chats_allowed_folders(tmp_path):
    project = tmp_path / "site"
    (project / "web").mkdir(parents=True)
    write_launch(project, {**WEB, "cwd": "web"})
    only_the_root = lambda p: p == project.resolve()  # noqa: E731
    with pytest.raises(
        LaunchConfigError, match="web is outside the folders this chat may use"
    ):
        load_specs(project, allowed=only_the_root)


def test_a_project_outside_the_allowed_folders_is_never_read(tmp_path):
    write_launch(tmp_path / "elsewhere", WEB)
    with pytest.raises(
        LaunchConfigError, match="outside the folders this chat may use"
    ):
        load_specs(tmp_path / "elsewhere", allowed=allow(tmp_path / "mine"))


def test_a_name_picks_one_server_and_none_needs_exactly_one(tmp_path):
    write_launch(tmp_path, WEB, {**WEB, "name": "api", "port": 4174})
    assert spec_named(tmp_path, "api", allowed=allow(tmp_path)).port == 4174
    with pytest.raises(LaunchConfigError, match="more than one server.*web, api"):
        spec_named(tmp_path, "", allowed=allow(tmp_path))


def test_env_names_match_the_list_the_app_uses():
    shared = fixture("dev_server_env.json")
    assert sorted(dev_servers.ENV_NAMES) == shared["names"]
    assert list(dev_servers.ENV_PREFIXES) == shared["prefixes"]


def test_the_fixture_app_parses(tmp_path):
    specs = load_specs(FIXTURE_APP, allowed=allow(FIXTURE_APP))
    assert {s.name for s in specs} >= {"web", "crash"}
    assert all(s.argv[0] == "node" for s in specs)


# ------------------------------------------------------- approval by digest


def test_always_is_keyed_on_the_exact_command(tmp_path):
    write_launch(tmp_path, WEB)
    before = spec_named(tmp_path, "web", allowed=allow(tmp_path))
    write_launch(tmp_path, {**WEB, "runtimeArgs": ["server.js", "--verbose"]})
    after = spec_named(tmp_path, "web", allowed=allow(tmp_path))
    a, b = grant_scope("dev_server", before.prompt_args()), grant_scope(
        "dev_server", after.prompt_args()
    )
    assert a.keys == (f"dev_server:{before.digest}",)
    assert a.keys != b.keys
    assert a.label == f"node server.js in {before.cwd.name}"
    console = SilentConsole()
    console.grant_call_for_session("dev_server", before.prompt_args())
    assert console.call_is_granted("dev_server", before.prompt_args())
    assert not console.call_is_granted("dev_server", after.prompt_args())


def test_env_and_port_are_part_of_what_was_approved(tmp_path):
    write_launch(tmp_path, WEB)
    base = spec_named(tmp_path, "web", allowed=allow(tmp_path)).digest
    write_launch(tmp_path, {**WEB, "port": 4180})
    assert spec_named(tmp_path, "web", allowed=allow(tmp_path)).digest != base
    write_launch(tmp_path, {**WEB, "env": {"DEBUG": "1"}})
    assert spec_named(tmp_path, "web", allowed=allow(tmp_path)).digest != base


def test_a_command_whose_name_bounds_nothing_never_offers_always(tmp_path):
    write_launch(
        tmp_path,
        {**WEB, "runtimeExecutable": "bash", "runtimeArgs": ["-c", "node server.js"]},
    )
    spec = spec_named(tmp_path, "web", allowed=allow(tmp_path))
    assert grant_scope("dev_server", spec.prompt_args()) is None


def test_only_start_has_a_grant():
    assert grant_scope("dev_server", {"action": "stop", "name": "web"}) is None


# ------------------------------------------------------------- the tool


class _Agent(BrowserUseToolsMixin):
    def __init__(self, root: Path):
        self.config = SimpleNamespace(ui_session_id="chat-1")
        self._tool_overrides = {}
        self.console = SilentConsole()
        self.path_validator = _Validator(root)

    @property
    def _tools_registry(self):
        return {**tools_mod._TOOL_REGISTRY, **self._tool_overrides}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    saved = dict(tools_mod._TOOL_REGISTRY)
    monkeypatch.setattr(console_mod, "auto_approve_env_enabled", lambda: False)
    monkeypatch.setattr(browser_driver, "installed", lambda: False)
    yield
    tools_mod._TOOL_REGISTRY.clear()
    tools_mod._TOOL_REGISTRY.update(saved)


def server_state(spec, status="ready"):
    return {
        "key": spec.key,
        "name": spec.name,
        "argv": list(spec.argv),
        "cwd": str(spec.cwd),
        "port": spec.port,
        "env": dict(spec.env),
        "url": spec.url,
        "status": status,
        "pid": 4242,
        "startedAt": 1,
        "exitCode": None,
        "logFile": "/tmp/x.log",
    }


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    fake = FakeBridge()
    fake.servers = []

    def dev(body):
        if body["op"] == "list":
            return {"servers": fake.servers}
        if body["op"] == "ensure":
            return {
                "server": {**server_state_from_wire(body), "status": "ready"},
                "reused": False,
                "ms": 820,
            }
        if body["op"] == "stop":
            return {"server": {**fake.servers[0], "status": "stopped", "pid": None}}
        return {
            "server": fake.servers[0],
            "lines": ["Error: Cannot find module './App'"],
            "matched": 1,
            "total": 9,
        }

    fake.replies["devServers"] = dev
    path = tmp_path / "run" / "browser-pane.json"
    monkeypatch.setenv(DISCOVERY_ENV, str(path))
    fake.write_discovery(path)
    yield fake
    fake.close()


def server_state_from_wire(body):
    spec = body["spec"]
    return {
        "key": body["key"],
        **{k: spec[k] for k in ("name", "argv", "cwd", "port", "env", "url")},
    }


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    write_launch(root, WEB)
    return root


@pytest.fixture
def agent(bridge, project):
    a = _Agent(project)
    a.register_browser_use_tools()
    return a


def call(agent, **args):
    """What Agent._execute_tool does: build the prompt, ask the gate, then run the tool."""
    shown = agent.confirmation_args_for_call("dev_server", args)
    asks = agent.browser_call_needs_confirmation("dev_server", args)
    granted = agent.console.call_is_granted("dev_server", shown)
    return (
        shown,
        asks and not granted,
        lambda: agent._tool_overrides["dev_server"]["function"](**args),
    )


def dev_ops(bridge):
    return [r["body"] for r in bridge.requests if r["op"] == "devServers"]


def test_starting_asks_and_shows_the_exact_command(agent, bridge, project):
    shown, asks, _run = call(agent, action="start", name="web")
    assert asks
    assert shown == {
        "action": "start",
        "name": "web",
        "command": "node server.js",
        "folder": str(project.resolve()),
        "port": 4173,
    }
    assert [b["op"] for b in dev_ops(bridge)] == ["list"]


def test_an_approved_start_sends_the_spec_and_opens_it_in_the_pane(
    agent, bridge, project
):
    _shown, _asks, run = call(agent, action="start", name="web")
    out = run()
    ensure = [b for b in dev_ops(bridge) if b["op"] == "ensure"][0]
    expected = fixture("requests.json")["devServers_ensure"]
    assert set(ensure) == set(expected)
    assert ensure["sessionId"] == "chat-1"
    assert ensure["searchPath"] == os.environ.get("PATH")
    assert ensure["spec"]["argv"] == ["node", "server.js"]
    assert ensure["spec"]["cwd"] == str(project.resolve())
    loads = [
        r["body"]
        for r in bridge.requests
        if r["op"] in ("navigate", "tabs") and r["body"].get("op") != "list"
    ]
    assert any(
        b.get("to") == "http://localhost:4173/"
        or b.get("url") == "http://localhost:4173/"
        for b in loads
    )
    assert out.splitlines()[0] == "(in the browser pane)"
    assert 'Dev server "web" (`node server.js`) started in 0.8 s on port 4173' in out


def test_writing_launch_json_approves_nothing(agent, bridge):
    out = agent._tool_overrides["dev_server"]["function"](action="start", name="web")
    assert "never approved" in out
    assert "ensure" not in [b["op"] for b in dev_ops(bridge)]


def test_an_edit_after_the_prompt_is_refused_not_run(agent, bridge, project):
    _shown, asks, run = call(agent, action="start", name="web")
    assert asks
    write_launch(project, {**WEB, "runtimeArgs": ["evil.js"]})
    out = run()
    assert "command changed after the user approved it" in out
    assert "ensure" not in [b["op"] for b in dev_ops(bridge)]


def test_an_always_answer_covers_the_same_command_only(agent, bridge, project):
    shown, _asks, _run = call(agent, action="start", name="web")
    agent.console.grant_call_for_session("dev_server", shown)
    _shown, asks, _run = call(agent, action="start", name="web")
    assert not asks
    write_launch(project, {**WEB, "runtimeArgs": ["server.js", "--port", "4173"]})
    _shown, asks, _run = call(agent, action="start", name="web")
    assert asks


def test_the_same_server_already_running_starts_with_no_prompt(agent, bridge, project):
    spec = spec_named(project, "web", allowed=allow(project))
    bridge.servers = [server_state(spec)]
    _shown, asks, run = call(agent, action="start", name="web")
    assert not asks
    run()
    assert [b["op"] for b in dev_ops(bridge)].count("ensure") == 1


def test_a_server_that_crashes_reports_its_last_log_lines(agent, bridge):
    def dev(body):
        if body["op"] == "list":
            return {"servers": []}
        return (
            409,
            {
                "error": {
                    "code": "dev_server_failed",
                    "message": "Dev server \"web\" (node server.js) exited before it answered on port 4173 (exit code 1). Its last log lines:\nError: Cannot find module './missing-config'",
                    "data": {"exitCode": 1},
                }
            },
        )

    bridge.replies["devServers"] = dev
    _shown, _asks, run = call(agent, action="start", name="web")
    out = run()
    assert out.splitlines()[0] == "(in the browser pane)"
    assert "Cannot find module './missing-config'" in out


def test_logs_send_a_filtered_slice_request(agent, bridge, project):
    spec = spec_named(project, "web", allowed=allow(project))
    bridge.servers = [server_state(spec)]
    out = agent._tool_overrides["dev_server"]["function"](
        action="logs", errors_only=True
    )
    logs = [b for b in dev_ops(bridge) if b["op"] == "logs"][0]
    assert set(logs) == set(fixture("requests.json")["devServers_logs"])
    assert logs["key"] == spec.key and logs["errorsOnly"] is True
    assert "Error: Cannot find module './App'" in out
    assert not agent.browser_call_needs_confirmation("dev_server", {"action": "logs"})


def test_stop_names_the_freed_port(agent, bridge, project):
    spec = spec_named(project, "web", allowed=allow(project))
    bridge.servers = [server_state(spec)]
    out = agent._tool_overrides["dev_server"]["function"](action="stop", name="web")
    stop = [b for b in dev_ops(bridge) if b["op"] == "stop"][0]
    assert set(stop) == set(fixture("requests.json")["devServers_stop"])
    assert 'Stopped dev server "web"; port 4173 is free.' in out


def test_no_project_named_finds_the_one_launch_json(agent, bridge):
    shown, asks, _run = call(agent, action="start")
    assert asks and shown["name"] == "web"


def test_an_app_too_old_for_dev_servers_says_so(agent, bridge):
    agent._pane()._bridge.ops = frozenset({"hello", "attach", "tabs"})
    out = agent._tool_overrides["dev_server"]["function"](action="list")
    assert "older than the agent" in out


# ------------------------------------------------- a plain HTML folder, no setup


@pytest.fixture
def site(project):
    """A folder of plain HTML inside the chat's allowed folder, with no launch.json."""
    folder = project / "landing page"
    folder.mkdir()
    (folder / "index.html").write_text("<h1>Hello</h1>")
    return folder.resolve()


def test_a_plain_html_folder_is_served_on_loopback_with_no_setup(agent, bridge, site):
    shown, asks, run = call(agent, action="start", project=str(site))
    port = shown["port"]
    assert asks
    assert shown["name"] == "static"
    assert shown["folder"] == str(site)
    assert shown["command"] == shlex.join(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
        + ["--directory", str(site)]
    )
    out = run()
    ensure = [b for b in dev_ops(bridge) if b["op"] == "ensure"][0]
    assert ensure["spec"]["argv"] == shlex.split(shown["command"])
    assert ensure["spec"]["cwd"] == str(site)
    assert ensure["spec"]["url"] == f"http://localhost:{port}/"
    assert f'Dev server "static"' in out and f"on port {port}" in out


def test_the_run_uses_the_port_the_prompt_showed(agent, bridge, site):
    shown, _asks, run = call(agent, action="start", project=str(site))
    with socket.socket() as held:
        held.bind(("127.0.0.1", shown["port"]))
        run()
    ensure = [b for b in dev_ops(bridge) if b["op"] == "ensure"][0]
    assert ensure["spec"]["port"] == shown["port"]
    assert str(shown["port"]) in ensure["spec"]["argv"]


def test_a_folder_keeps_its_port_so_always_still_covers_it(agent, bridge, site):
    first, _asks, _run = call(agent, action="start", project=str(site))
    agent.console.grant_call_for_session("dev_server", first)
    again, asks, _run = call(agent, action="start", project=str(site))
    assert again["port"] == first["port"]
    assert not asks


def test_a_running_static_server_keeps_its_port(agent, bridge, site):
    running = dev_servers.static_spec(site, 18321)
    bridge.servers = [server_state(running)]
    shown, asks, run = call(agent, action="start", project=str(site))
    assert shown["port"] == 18321
    assert not asks
    run()
    assert [b for b in dev_ops(bridge) if b["op"] == "ensure"][0]["spec"]["port"] == (
        18321
    )


def test_launch_json_wins_over_html_files(agent, bridge, project):
    (project / "index.html").write_text("<h1>Hi</h1>")
    shown, _asks, _run = call(agent, action="start", project=str(project))
    assert shown["name"] == "web" and shown["command"] == "node server.js"


def test_a_folder_with_no_html_and_no_launch_json_names_both_fixes(
    agent, bridge, project
):
    empty = project / "empty"
    empty.mkdir()
    shown, asks, run = call(agent, action="start", project=str(empty))
    assert not asks
    out = run()
    assert "index.html" in out and "launch.json" in out
    assert "ensure" not in [b["op"] for b in dev_ops(bridge)]


def test_an_html_folder_outside_the_allowed_folders_is_refused(agent, bridge, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "index.html").write_text("<h1>x</h1>")
    shown, asks, run = call(agent, action="start", project=str(outside))
    assert not asks
    out = run()
    assert "outside the folders this chat may use" in out
    assert "ensure" not in [b["op"] for b in dev_ops(bridge)]


def test_a_named_server_in_a_plain_folder_says_there_is_only_static(agent, site):
    _shown, _asks, run = call(agent, action="start", project=str(site), name="web")
    assert '"static"' in run()


def test_the_docstring_teaches_the_no_setup_call(agent):
    doc = agent._tool_overrides["dev_server"]["description"]
    assert 'dev_server(action="start", project="<folder>")' in " ".join(doc.split())


def test_the_static_server_answers_on_loopback_only(tmp_path):
    (tmp_path / "index.html").write_text("<h1>served</h1>")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    spec = dev_servers.static_spec(tmp_path, port)
    proc = subprocess.Popen(
        list(spec.argv),
        cwd=spec.cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        body = _fetch_when_up(f"http://127.0.0.1:{port}/")
        assert "<h1>served</h1>" in body
        lan = _non_loopback_address()
        if lan is not None:
            with pytest.raises(OSError):
                socket.create_connection((lan, port), timeout=2).close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    if lan is None:
        pytest.skip("no non-loopback address to check the bind against")


def _fetch_when_up(url: str) -> str:
    deadline = time.monotonic() + 15
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.read().decode()
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def _non_loopback_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: routes, sends nothing
        except OSError:
            return None
        address = s.getsockname()[0]
    return None if address.startswith("127.") or address == "0.0.0.0" else address


# ---------------------------------------------------------- browser_open file://


def test_a_file_url_points_at_dev_server_for_its_folder(agent, bridge, site):
    out = agent._tool_overrides["browser_open"]["function"](
        url=(site / "index.html").as_uri()
    )
    assert "file://" in out
    assert f'dev_server(action="start", project="{site}")' in out
    assert "invalid URL" not in out
    assert not [r for r in bridge.requests if r["op"] == "navigate"]


def test_a_file_url_without_the_pane_says_to_ask_the_user(monkeypatch, tmp_path):
    monkeypatch.setenv(DISCOVERY_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setattr(browser_driver, "installed", lambda: True)
    a = _Agent(tmp_path)
    a.register_browser_use_tools()
    out = tools_mod._TOOL_REGISTRY["browser_open"]["function"](
        url=(tmp_path / "index.html").as_uri()
    )
    assert "Ask the user to run the server" in out
    assert "dev_server" not in out
