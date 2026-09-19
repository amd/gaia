# Audit slice: subprocess argv, process launch, daemon/sidecar IPC

**Headline:** two outgoing contracts are asserted by tests against values the real target
does not use — `LEMONADE_CTX_SIZE` (which the installed Lemonade 11.5.0 binary never
reads) and MCP `protocolVersion: "1.0.0"` (not a valid MCP protocol version). Both are the
`{"spec": "flm:npu"}` failure mode exactly: a mock proves we sent it, nothing proves it is
accepted.

The rest of this slice is healthier than expected. `test_broker_wiring.py`,
`test_daemon_remedy_commands.py`, `test_sidecar_alive_but_not_serving.py` and
`test_external_mcp_executable.py` already drive real processes and real argparse surfaces —
they are the pattern the rest should copy, not problems.

Evidence for the empirical claims below came from the Lemonade install on this machine
(`%LOCALAPPDATA%\lemonade_server\bin`, client reports `lemonade version 11.5.0`) and from
Launchpad's published-binaries API for `ppa:lemonade-team/stable`. No servers were started;
port 4001 was not touched.

---

## 1. Findings table

### DANGEROUS

| file:line | test | risk | evidence | proposed replacement |
|---|---|---|---|---|
| `tests/unit/test_lemonade_launcher.py:205` | `test_build_start_command_modern_windows` | **High.** Locks in `LEMONADE_CTX_SIZE` as the modern ctx-size channel. The installed Lemonade 11.5.0 server binary contains **no such string**: `grep -o -a -E "LEMONADE_[A-Z_]+" LemonadeServer.exe` yields only `MCP_IMAGE_DIR, API_KEY, ADMIN_API_KEY, DEFAULTS_PATH, CI_MODE, CACHE_DIR, BACKEND_WATCHDOG*, ALLOWED_ORIGINS`. `ctx_size` is instead a config-file key (`resources/defaults.json` → `"ctx_size": -1`, set via `lemonade config set`). If the var is ignored, issue #839 (auto-started server must come up at the profile ctx) is silently unfixed on the modern path — which is every current Windows user. | `assert spec.env == {"LEMONADE_CTX_SIZE": "32768"}` | Contract test modelled on `tests/integration/test_lemonade_backend_contract.py`: start the resolved launcher with `LEMONADE_CTX_SIZE=8192`, then assert `GET /api/v1/system-info` (or a `/load` + model-info round-trip) reports 8192. If the var is dead, replace the env channel with `lemonade config set ctx_size=N` and delete the assertion everywhere. |
| `tests/unit/installer/test_init_ctx_size.py:211,253` | `test_windows_modern_auto_start_passes_ctx_size_via_env_not_argv`, `test_linux_modern_auto_start_passes_ctx_size_via_env` | **High.** Same wrong contract, one layer up — and worse, `build_start_command` is *itself* mocked (line 175/225), so the test supplies the `StartSpec` it then asserts. The only non-tautological assertion is the env merge. | `assert env.get("LEMONADE_CTX_SIZE") == "32768", f"expected LEMONADE_CTX_SIZE=32768 in Popen env, got: {env}"` | Stop mocking `build_start_command` — it is pure and stdlib-only; call it for real with a fabricated `LemonadeTooling` (as `test_lemonade_launcher.py` already does). Keep the env-merge and `GAIA_TEST_SENTINEL`/`PATH` assertions (those are genuinely valuable). Move the ctx-actually-took-effect claim to the integration test above. |
| `tests/test_lemonade_client.py:1972,1992` | `TestLaunchServerModernLegacyDispatch` (modern branch) | **High.** Third copy of the same locked-in env contract, at the `launch_server` Popen seam — and `build_start_command` is mocked here too, so the argv equality at `:1990` is again asserting the test's own fixture. | `env={"LEMONADE_CTX_SIZE": "32768"}` … `self.assertEqual(env.get("LEMONADE_CTX_SIZE"), "32768")` | Converges with the two above once one integration test owns the claim. Keep the Popen-level test for the merge semantics only (`GAIA_TEST_SENTINEL` / `PATH` retention), and unmock `build_start_command`. |
| `tests/unit/mcp/client/test_mcp_client.py:281` | `test_connect_sends_initialize_request` | **High.** Asserts GAIA sends `protocolVersion: "1.0.0"` on MCP `initialize`. MCP protocol versions are date strings — the published set is `2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25`; `"1.0.0"` is not one of them. The transport is a `Mock`, so the test can only ever confirm we send the wrong value. Servers that validate strictly will reject the handshake; servers that fall back mask it until one doesn't. | `assert call_args[0][1]["protocolVersion"] == "1.0.0"` | Add a real-stdio contract test: launch a trivial in-repo MCP server over `StdioTransport` and assert the negotiated `result.protocolVersion` comes back non-empty and equal to something the client declared. Fix `src/gaia/mcp/client/mcp_client.py:447` and `transports/http.py:50` to a real version and negotiate the response, then let the unit test assert *the constant*, not a literal. |
| `tests/unit/test_llamacpp_backend.py:363-365` | `TestLaunchServerCtxSize::test_ctx_size_appended_to_command` | **Medium-high.** You asked me to judge this one specifically: **yes, it now asserts a flag no shipped binary on this machine accepts.** There is no `lemonade-server` / `lemonade-server-dev` anywhere on PATH here; the only install is modern (11.5.0), whose client has no `serve` subcommand at all (`lemonade --help` lists `run/launch/chat/status/pull/load/…`, no `serve`). The test is honest about it — the docstring says "Pinned to LEGACY tooling" — and it does force `resolve_lemonade` to return legacy tooling, so it is not *lying*. But it is guarding a code path that only fires for an install GAIA can no longer produce: `gaia init` on Linux installs from `ppa:lemonade-team/stable`, whose current published binaries are `lemonade-server 11.8.1~24.04` / `11.0.0~25.10` — i.e. the *package* is still called `lemonade-server` but ships the modern `lemonade`/`lemond` tooling. So the legacy branch is dead-ish weight, and the second test in the class asserts a flag's *absence*, which is even cheaper. | `cmd = mock_popen.call_args[0][0]`<br>`assert "--ctx-size" in cmd`<br>`assert "65536" in cmd` | Don't delete — legacy installs still exist in the field. Do two things: (a) demote it to a pure `build_start_command` unit test (no Popen mock needed — the argv is built by a stdlib-only pure function); (b) `NEEDS-LIVE-CHECK` whether the legacy branch is still reachable at all, and if the answer is "only via `LEMONADE_SERVER_PATH` pointing at an archived binary," say so in the docstring and stop treating it as a primary path. |

### MASKING

| file:line | test | risk | evidence | proposed replacement |
|---|---|---|---|---|
| `tests/unit/mcp/client/test_transports.py:68-70` | `test_send_request_sends_json_rpc` | **Medium.** `stdout` is a `StringIO` holding exactly one line that is exactly the matching response. Real MCP stdio servers interleave notifications and (badly-behaved ones) log noise on stdout. `StdioTransport.send_request` does a bare `readline()` and **never correlates the response `id`** (`stdio.py:338-345`) — the first line back is returned as the answer regardless of what it is. The mock guarantees that never happens. | `mock_process.stdout = StringIO('{"jsonrpc": "2.0", "id": 0, "result": {"status": "ok"}}\n')` | Feed a `StringIO` containing a notification line *before* the response and assert the transport skips it. That is a cheap unit test that fails today and would force id-correlation into the production code. |
| `tests/unit/mcp/client/test_transports.py` (whole module) | — | **Medium.** No test anywhere exercises the MCP handshake sequence. `MCPClient.connect` sends `initialize` and never sends `notifications/initialized`, which the spec requires before normal operation. Every test mocks the transport, so a server that enforces the sequence is never met. | absence of any `notifications/initialized` reference in `src/gaia/mcp/client/` | One integration test against an in-repo stdio MCP server: connect → `tools/list` → assert tools come back. Would catch both the missing notification and the bad `protocolVersion` in one shot. |
| `tests/unit/test_lemonade_launcher.py:80,94,114,127,144,294` | six `resolve_lemonade` tests | **Low-medium, but worth naming.** `mocker.patch("pathlib.Path.exists", return_value=True/False)` is a blanket override — every `Path.exists()` in the process returns the same answer, so the test cannot distinguish "the Windows canonical path exists" from "every path exists." The macOS tests in the same file already fixed this with the `only_these_paths_exist` helper (line 34). | `mocker.patch("pathlib.Path.exists", return_value=True)` | Mechanical: route the six blanket patches through the existing `only_these_paths_exist` helper. No new infrastructure needed. |

### UNNECESSARY

| file:line | test | risk | evidence | proposed replacement |
|---|---|---|---|---|
| `tests/unit/installer/test_init_ctx_size.py:93,134,175,225,277` | all five `@patch("gaia.installer.init_command.build_start_command")` | **Low risk, real waste.** `build_start_command` is a pure stdlib function over a dataclass. Mocking it means the argv assertions in the same tests are asserting the test's own fixture data. | `mock_build_cmd.return_value = StartSpec(argv=[...], env={...})` then `assert "--ctx-size" in argv` | Drop the patch; let the real function build the argv from a fabricated `LemonadeTooling`. Same test, now non-circular. |
| `tests/unit/test_lemonade_launcher.py:527,549,571,593,617,707,728` | seven `describe_start_hint` tests | **Low.** They patch `resolve_lemonade` wholesale, so only the rendering half is under test. The file's own better tests (`test_start_hint_macos_names_the_daemon_via_real_detection:637`) drive the real probe and say why: *"no mocking of resolve_lemonade, which would only prove we called it."* | `mocker.patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=LemonadeTooling(...))` | Same treatment as the macOS ones — patch `platform.system` + `only_these_paths_exist` and let the real resolver run. |

### JUSTIFIED

| file:line | test | why leave it alone |
|---|---|---|
| `tests/unit/mcp/client/test_transports.py:19-548` — the ~25 `subprocess.Popen` patches | Spawning `npx`/`uvx` per test would pull the network and take seconds each. Critically, the *assertion target* is `_build_argv`'s tokenizer output, which is pure local logic — the Popen mock is just the observation point, not a contract stand-in. The shell-injection rejection tests (`:298`, `:338`) assert `mock_popen.assert_not_called()`, which is the correct shape for a security guard. |
| `tests/unit/test_webui_build.py` — all `gaia.ui.build.subprocess.run` patches | A real `npm install && npm run build` is a multi-minute network operation. `npm run build` and `node --version` are argv that have been stable for a decade; there is no realistic contract drift. The tests additionally assert `shell=False` on every call (`:163`) and that only the version probe runs when Node is too old (`:310`) — both are real invariants. |
| `tests/unit/test_daemon_dev_anchor_spec.py` — `subprocess.run` seam for `git` | Deliberately isolates the suite from the ambient checkout's git state, and the module docstring says exactly that. `git rev-parse` argv is not going to move. |
| `tests/unit/test_agent_sidecar_manager.py:408-413` — the fake `Popen` | The alternative is spawning a real uvicorn per test. The fake captures argv and kwargs, and the surrounding tests assert things a mock genuinely can prove: log-file redirection rather than `PIPE` (`:440`), the 0600 secret file and that the token appears in **neither** env nor argv (`:467-470`). That last one is a real security invariant that only a Popen-level observation can check. |
| `tests/unit/test_daemon_remedy_commands.py:193-230` | **Not a mock at all** — spawns a real `python -c "time.sleep(120)"` and executes the remedy command against it. This is the pattern the rest of the slice should copy. |
| `tests/unit/test_sidecar_alive_but_not_serving.py:50,91` | Same — real subprocesses, real ports. |
| `tests/unit/test_external_mcp_executable.py` | Asserts `shutil.which` is applied so `npx.cmd` resolves on Windows, driven by the real PATHEXT behaviour. Exactly the "is the call valid" test this audit is asking for. |
| `tests/unit/test_broker_wiring.py` (entire module) | Runs a **real** `ModelSlotBroker` and, at `:599`, a real daemon over HTTP. Its docstring cites the CLAUDE.md rule verbatim: *"a mock that merely records 'a lease was requested' would prove invocation, not mutual exclusion."* Nothing to fix; cite it as the house style. |

---

## 2. Coverage gaps — per external process

| Target | Argv/contract GAIA sends | Would any test fail if the contract moved? | Cheap probe available? |
|---|---|---|---|
| `LemonadeServer.exe` (modern Windows) | `[exe, "--silent"]` + `LEMONADE_CTX_SIZE` env | **No.** `--silent` is at least present in the binary's flag table (verified by string scan). The env var is not, and nothing would catch that. | Partly. `--silent` is confirmable by inspection. The ctx claim needs a live server. |
| `lemond` (modern Linux/macOS) | `[lemond]` or `["systemctl","--user","start","lemond"]` | **No.** Pure argv-construction tests only. Neither binary exists on any CI runner GAIA uses. | No — needs a Linux/macOS box with the PPA package installed. |
| `lemonade-server serve` (legacy) | `["lemonade-server","serve","--ctx-size",N]` (+`--no-tray` on Windows) | Only against a hand-built `LemonadeTooling`. No binary on this machine, none on PATH. | Yes if a legacy binary is obtainable — `lemonade-server --help` would settle whether `serve --ctx-size` still parses. |
| `lemonade` client (`pull`/`load`) | `[client, "pull"|"load", model]` — `describe_client_hint` | **No test at all.** But I verified by probe: `lemonade --help` lists both `pull` and `load`, so this one is currently **correct**. | Yes, and I ran it. Cheap to automate as a skipif-guarded `--help` grep. |
| `apt-get install -y lemonade-server` (PPA) | package name `lemonade-server` from `ppa:lemonade-team/stable` | Tests assert the literal string is in the argv (`test_init_command.py:1485`), which is invocation-only. | Verified externally: Launchpad's `getPublishedBinaries` confirms `lemonade-server` **is** a published binary name (source package `lemonade`, versions `11.8.1~24.04`, `11.0.0~25.10`). **This is correct — not a finding.** Worth noting only that the deb now ships *modern* tooling, so the package name and the binary names diverge. |
| Email sidecar frozen binary | `[binary, "--host", H, "--port", P]` (`manager.py:319`) | **No.** The sidecar's own argparse (`gaia_agent_email/server.py:316`, `_add_serve_args`) is in this repo and currently accepts both flags — but nothing cross-checks them. A rename in `_add_serve_args` breaks the daemon with a fully green suite. | **Yes, and it is free** — see remediation R3. |
| Dev-mode sidecar (uvicorn) | `[sys.executable,"-m","uvicorn",module,"--app-dir",dir,"--host",H,"--port",P]` | **Partly, and well.** `test_agent_sidecar_manager.py:108-115` asserts the app-dir actually exists on disk and contains `server.py`. That is real state validation. uvicorn's flags are stable. | n/a |
| `python -m gaia.daemon` | `[sys.executable,"-m","gaia.daemon"]` | **Yes** — `tests/integration/test_daemon.py` and `test_daemon_sidecars.py` spawn it for real. | n/a |
| MCP stdio servers (`npx`/`uvx`/`python`) | JSON-RPC over newline-delimited stdio, `initialize` first | **No.** Every test mocks the transport. The `protocolVersion` and the missing `notifications/initialized` are both invisible. | Yes — an in-repo stdio MCP server fixture, no network. |
| `npm` / `node` | `["npm","run","build"]`, `["node","--version"]` | No, but the contract is stable enough that it doesn't matter. | Yes, trivially, but low value. |

**The gap that matters most and costs least:** the email sidecar argv. Both sides of that
contract live in this repo, so a real test is a five-line import — no service, no network,
no hardware.

**Windows blind spot:** `tests/integration/test_daemon_sidecars.py` is `skipif` POSIX-only
(`:46`) *and* drives a toy sidecar rather than the real one. So on Windows — the primary
GAIA platform — no test in this slice launches a real supervised sidecar at all.

---

## 3. Remediation, ordered by value per unit of work

**R1 — one integration test retires three DANGEROUS findings.**
`tests/integration/test_lemonade_ctx_contract.py`, gated on `require_lemonade`
(`tests/conftest.py:183`). Launch via the resolved tooling with a distinctive ctx (8192,
not the profile default), then assert the running server reports it. One test settles
`test_lemonade_launcher.py:205`, `test_init_ctx_size.py:211,253`, and
`test_lemonade_client.py:1970` — after which those three become pure argv-shape unit
tests, which is all they were ever able to be.

`NEEDS-LIVE-CHECK:` on a machine with Lemonade 11.5+, from
`%LOCALAPPDATA%\lemonade_server\bin`:
```
LEMONADE_CTX_SIZE=8192 ./LemonadeServer.exe --silent   # then, from another shell:
curl -s http://localhost:13305/api/v1/system-info
./lemonade.exe config          # compare against the config-file ctx_size path
```
If 8192 does not surface, the env channel is dead and `build_start_command` should switch
to `lemonade config set ctx_size=N` (or drop the ctx claim and rely on the per-request
`/load` `ctx_size`, which `test_llamacpp_backend.py:74` shows already works).

**R2 — one stdio MCP fixture retires the MCP findings.**
Add `tests/fixtures/toy_mcp_server.py` (a ~40-line newline-delimited JSON-RPC responder,
mirroring the existing `tests/fixtures/toy_sidecar.py` pattern). Drive it through the real
`StdioTransport` + `MCPClient.connect`. Catches the invalid `protocolVersion`, the missing
`notifications/initialized`, and the uncorrelated `readline()`. No network, no skip
condition, runs everywhere.

**R3 — free, do it today.** A unit test in `tests/unit/test_agent_sidecar_manager.py` that
takes `build_spawn_command(port=…)` output in user mode and feeds it to the sidecar's own
parser:
```python
from gaia_agent_email.server import main  # or the parser factory
argv, _ = m.build_spawn_command(port=9123)
# argv[0] is the binary path; the rest must parse against the sidecar's own argparse
```
Both halves are in-repo. Turns an untested cross-process contract into a compile-time-ish
check.

**R4 — mechanical, one commit.** Convert the six blanket `Path.exists` patches in
`test_lemonade_launcher.py` to the file's own `only_these_paths_exist` helper, and drop the
five `build_start_command` mocks in `test_init_ctx_size.py`. Pure subtraction; no new
fixtures.

**R5 — `NEEDS-LIVE-CHECK` on the legacy branch.** On an Ubuntu 24.04 box:
`sudo add-apt-repository -y ppa:lemonade-team/stable && sudo apt-get install -y
lemonade-server && lemonade-server --help 2>&1 | head`. If that binary does not exist after
install (only `/usr/bin/lemonade` + `lemond` do), then `TestLaunchServerCtxSize` and the two
`_legacy_tooling()` cases in `test_init_ctx_size.py` guard a path `gaia init` can no longer
reach on any supported platform, and their docstrings should say so.

**One shared fixture, many conversions:** a `lemonade_tooling(kind, platform)` factory would
let R1's integration test and the argv unit tests in `test_lemonade_launcher.py`,
`test_init_ctx_size.py`, `test_llamacpp_backend.py` and `test_lemonade_client.py` share one
definition of each tooling shape — today each file rolls its own `_legacy_tooling()` /
`_modern_tooling_windows()` helper, four near-identical copies.

---

## Notes on scope and honesty

- Nothing was modified. The only write is this file.
- The `apt-get install lemonade-server` package name looked wrong from the Launchpad
  *source*-package list and turned out to be **correct** on the binary list. Reported as a
  non-finding rather than padding the count.
- The `LEMONADE_CTX_SIZE` claim rests on a string scan of a shipped binary plus the
  presence of `ctx_size` as a config key. That is strong but not proof — an env name built
  by concatenation would evade the scan. Hence `NEEDS-LIVE-CHECK` rather than a flat
  assertion.
- `test_broker_wiring.py`, `test_daemon_remedy_commands.py`,
  `test_sidecar_alive_but_not_serving.py` and `test_external_mcp_executable.py` need no
  changes. They already do what this audit is asking for, and three of them say so in
  their docstrings.
