#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# One phase of one installer journey on macOS or Linux:
#
#   posix-journey.sh <download|install|upgrade|launch|health|engine|setup|preflight|quit|uninstall|leftovers>
#
# It installs and uninstalls system packages, writes under /usr and /Applications,
# and deletes the sandbox home it creates. It refuses to run anywhere but a
# GitHub-hosted CI runner. Run it from .github/workflows/test_installers_headless.yml,
# never on a machine you use.
#
# Inputs (environment):
#   KIND          tui-pkg | agent-ui-dmg | agent-ui-deb | appimage | tui-deb |
#                 tui-deb-upgrade | tui-rpm | install-sh
#   WORK          a fresh directory under $RUNNER_TEMP; everything this writes
#                 outside the system install locations lives here
#   RELEASE_JSON  resolve_release.py's output for the release under test
#   PKG_DIR       (rebuilt packages) where this run's rebuilt packages are
#   PREV_PKG_DIR  (tui-deb-upgrade) where the previous version's package is
#   TUI_PLATFORM  (tui-pkg) darwin-arm64 | darwin-x64
#   SETUP_JOURNEY 1 when the row runs the setup phase: the app starts against
#                 a fake Lemonade with nothing downloaded
#
# Bash 3.2 compatible: it runs on the macOS system bash.

set -euo pipefail

STEP="${1:?usage: posix-journey.sh <phase>}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "${HERE}/../.." && pwd)"
JOURNEY="${HERE}/journey.py"

die() { echo "::error::${ROW:-journey} ${STEP}: $*" >&2; exit 1; }
# Absolute, so a phase that puts GAIA's venv first on PATH still runs the
# runner's Python for the helpers.
PYTHON="$(command -v "${PYTHON:-python3}")" || die "no Python for the journey helpers"
# A defect that fails the row without stopping its later phases.
finding() {
  "${PYTHON}" "${JOURNEY}" finding --results "${RESULTS:?RESULTS is required}" --phase "${STEP}" \
    --known "${HERE}/known_failures.json" --text "$*"
}

# ── Safety: CI runners only, and only inside a sandbox under RUNNER_TEMP ────
[ "${GITHUB_ACTIONS:-}" = "true" ] && [ "${CI:-}" = "true" ] \
  || die "refusing to run outside GitHub Actions: this installs and removes system packages."
[ -n "${RUNNER_TEMP:-}" ] && [ -d "${RUNNER_TEMP}" ] || die "RUNNER_TEMP is not set."
case "${WORK:?WORK is required}" in
  "${RUNNER_TEMP}"/*) ;;
  *) die "WORK (${WORK}) must be inside RUNNER_TEMP (${RUNNER_TEMP})." ;;
esac
mkdir -p "${WORK}/home" "${WORK}/dl" "${WORK}/logs"
# Every phase runs with the sandbox as HOME, so ~/.gaia is the sandbox's.
export HOME="${WORK}/home"
export GAIA_HOME="${HOME}/.gaia"
case "${GAIA_HOME}" in
  "${RUNNER_TEMP}"/*) ;;
  *) die "GAIA_HOME (${GAIA_HOME}) is not inside the sandbox." ;;
esac
LOGS="${WORK}/logs"
OS="$(uname -s)"

json_get() {  # json_get <file> <python expression on d>
  "${PYTHON}" -c "import json,sys; d=json.load(open(sys.argv[1])); print($2)" "$1"
}
rel() { json_get "${RELEASE_JSON}" "$1"; }
asset_name() { rel "d['assets'].get('$1') or ''"; }
asset_url() { local n; n="$(asset_name "$1")"; [ -n "$n" ] || die "the release carries no $1"; rel "d['urls']['$n']"; }
PEP440="$(rel "d['pep440']")"
VERSION="$(rel "d['version']")"
TUI_VERSION="$(rel "d['tui_version']")"

fetch() {  # fetch <url> <dest>
  echo "GET $1"
  curl -fL --retry 3 --retry-delay 5 --connect-timeout 30 -sS -o "$2" "$1"
  [ -s "$2" ] || die "downloaded nothing from $1"
  ls -l "$2"
}

# The one file a package-type row installs: this release's asset, or the
# package this run rebuilt from the release's own binaries (PKG_DIR).
package_file() {  # package_file <asset id>
  local name
  name="$(asset_name "$1")"
  if [ -n "${name}" ]; then
    echo "${WORK}/dl/${name}"
  else
    local f
    for f in "${PKG_DIR:?the release carries no $1 and no rebuilt package was given}"/*"$(pkg_suffix "$1")"; do
      [ -f "${f}" ] && { echo "${f}"; return 0; }
    done
    die "no rebuilt package for $1 in ${PKG_DIR}"
  fi
}
pkg_suffix() {
  case "$1" in
    tui-deb) echo '_amd64.deb' ;;
    tui-rpm) echo '.x86_64.rpm' ;;
    tui-pkg-darwin-arm64) echo '-darwin-arm64.pkg' ;;
    tui-pkg-darwin-x64) echo '-darwin-x64.pkg' ;;
    *) die "no package suffix for $1" ;;
  esac
}

# ── The fake model server and the TUI checks ─────────────────────────────────
run_fake() {
  rm -f "${WORK}/fake.port"
  nohup "${PYTHON}" "${HERE}/fake_model_server.py" --port-file "${WORK}/fake.port" \
    --log "${LOGS}/fake-requests.jsonl" "$@" >"${LOGS}/fake-server.log" 2>&1 &
  echo $! >"${WORK}/fake.pid"
  for _ in $(seq 1 150); do [ -s "${WORK}/fake.port" ] && break; sleep 0.2; done
  [ -s "${WORK}/fake.port" ] || { cat "${LOGS}/fake-server.log"; die "the fake model server did not start"; }
  FAKE_URL="http://127.0.0.1:$(cat "${WORK}/fake.port")/api/v1"
}
# The flagship profile's models (INIT_PROFILES in src/gaia/installer/
# init_command.py) and the larger default a big machine picks, as downloaded.
start_fake() {
  run_fake --model Gemma-4-E4B-it-GGUF --model user.embeddinggemma-300m-GGUF \
    --model Qwen3.6-35B-A3B-GGUF
}
stop_fake() { kill "$(cat "${WORK}/fake.pid" 2>/dev/null)" 2>/dev/null || true; }

# Runs a command in the place the row installed into: the runner itself, or the
# Fedora container for the rpm row.
target() {
  if [ "${KIND}" = "tui-rpm" ]; then
    docker exec -e HOME=/root -e "LEMONADE_BASE_URL=${LEMONADE_BASE_URL:-}" gaia-fedora "$@"
  else
    "$@"
  fi
}

tui_bin() {
  case "${KIND}" in
    tui-pkg) echo /usr/local/bin/gaia-tui ;;
    install-sh) echo "${HOME}/.gaia/bin/gaia-tui" ;;
    *) echo /usr/bin/gaia-tui ;;
  esac
}

# A release stamps gaia-tui in two spellings today: the raw binaries say the
# tag (v0.25.1-rc2), the installers' copies the package version (0.25.1-rc.2).
check_tui_version() {  # check_tui_version <expected substring>...
  local out want
  out="$(target "$(tui_bin)" version 2>&1)" || die "gaia-tui version exited non-zero: ${out}"
  echo "gaia-tui version -> ${out}"
  for want in "$@"; do
    case "${out}" in *"${want}"*) return 0 ;; esac
  done
  die "gaia-tui reports '${out}', expected one of: $*"
}

# One real non-interactive turn: gaia-tui spawns the installed gaia-agent, which
# talks to the fake model server. Exit 0 and the server's answer on stdout is a
# pass; anything else fails with both streams and the request log printed.
one_shot() {
  start_fake
  local rc=0
  LEMONADE_BASE_URL="${FAKE_URL}" target "$(tui_bin)" chat --agent gaia \
    --query "Reply with a short greeting." --timeout 8m \
    >"${LOGS}/oneshot.out" 2>"${LOGS}/oneshot.err" || rc=$?
  stop_fake
  echo "--- stdout ---"; cat "${LOGS}/oneshot.out"
  echo "--- stderr (tail) ---"; tail -n 60 "${LOGS}/oneshot.err"
  echo "--- endpoints the agent called ---"
  "${PYTHON}" -c "import json,sys,collections; c=collections.Counter((e['method'],e['path'].split('?')[0],e['status']) for e in map(json.loads,open(sys.argv[1]))); [print(n,*k) for k,n in c.most_common()]" \
    "${LOGS}/fake-requests.jsonl" 2>/dev/null || echo "(no requests reached the fake server)"
  [ "${rc}" -eq 0 ] || die "the one-shot turn exited ${rc}"
  grep -q "fake model server" "${LOGS}/oneshot.out" || die "the one-shot exited 0 but did not print the model's answer"
}

# With no Lemonade anywhere, a one-shot must refuse quickly and say how to fix
# it -- not hang, and not exit 0.
preflight_no_lemonade() {
  local rc=0
  local where=(env -u LEMONADE_BASE_URL)
  [ "${KIND}" = "tui-rpm" ] && where=(docker exec gaia-fedora)
  "${PYTHON}" "${JOURNEY}" bounded --seconds 120 --stdout "${LOGS}/preflight.out" \
    --stderr "${LOGS}/preflight.err" --elapsed-file "${WORK}/preflight.elapsed" -- \
    "${where[@]}" "$(tui_bin)" chat --agent gaia --query "hello" --timeout 2m || rc=$?
  echo "--- stderr ---"; cat "${LOGS}/preflight.err"
  [ "${rc}" -ne 124 ] || die "with no Lemonade, the one-shot was still running after 120s; it should refuse in seconds"
  [ "${rc}" -ne 0 ] || die "with no Lemonade, the one-shot exited 0"
  grep -q "is not ready" "${LOGS}/preflight.err" || die "the refusal does not say what is not ready"
  grep -qi "lemonade" "${LOGS}/preflight.err" || die "the refusal does not name Lemonade"
  grep -q "Nothing was sent" "${LOGS}/preflight.err" || die "the refusal does not say the query was not sent"
  echo "refused in $(cat "${WORK}/preflight.elapsed")s with exit ${rc}"
}

# ── The Agent UI ──────────────────────────────────────────────────────────────
# The install phase records the executable it installed, so launch and quit use
# what is really on disk rather than a path this script assumes.
agent_ui_cmd() { cat "${WORK}/app.cmd"; }
APP_PATTERN='gaia-desktop'

launch_agent_ui() {
  local cmd
  cmd="$(agent_ui_cmd)"
  [ -x "${cmd}" ] || die "${cmd} is not there to launch"
  # These rows install a published release; one before #5629 runs gaia init
  # (several GB of models) on first launch. Later apps ignore the variable.
  export GAIA_SKIP_GAIA_INIT=1
  if [ "${SETUP_JOURNEY:-}" = "1" ]; then
    run_fake --catalog
    export LEMONADE_BASE_URL="${FAKE_URL}"
  fi
  if [ "${OS}" = "Linux" ]; then
    nohup xvfb-run --auto-servernum "${cmd}" >"${LOGS}/app.out" 2>"${LOGS}/app.err" &
  else
    nohup "${cmd}" >"${LOGS}/app.out" 2>"${LOGS}/app.err" &
  fi
  echo $! >"${WORK}/app.pid"
  sleep 10
  kill -0 "$(cat "${WORK}/app.pid")" 2>/dev/null || { tail -n 50 "${LOGS}/app.err"; die "the app exited within 10s of launch"; }
  wait_for_main_log "$(cat "${WORK}/app.pid")" "${GAIA_HOME}/electron-main.log" 50
  echo "launched pid $(cat "${WORK}/app.pid")"
}

# wait_for_main_log <pid> <log> <seconds>: a cold AppImage mount plus a first
# fontconfig scan can open the log just past the 10s survival check.
wait_for_main_log() {
  local deadline=$(( $(date +%s) + $3 ))
  while [ ! -f "$2" ]; do
    kill -0 "$1" 2>/dev/null || { tail -n 50 "${LOGS}/app.err"; die "the app exited without writing $2"; }
    [ "$(date +%s)" -lt "${deadline}" ] || die "the app is running but wrote no $2 within $(( $3 + 10 ))s of launch"
    sleep 1
  done
}

# The `gaia` the app runs, from the backend start it logged: the one it ships
# (an AppImage's copy under ~/.gaia), or an older release's venv.
backend_gaia() {
  grep -oE 'Starting backend: .+ chat --ui' "${GAIA_HOME}/electron-main.log" 2>/dev/null | tail -n1 | sed -E 's/^Starting backend: //; s/ chat --ui$//'
}

# A release from before the app shipped its backend installs it from PyPI on
# first launch, so the bound is generous; the threshold file flags it slowing.
wait_agent_ui_health() {
  local port="" body="" deadline=$(( $(date +%s) + 1500 ))
  while [ "$(date +%s)" -lt "${deadline}" ]; do
    kill -0 "$(cat "${WORK}/app.pid")" 2>/dev/null || { tail -n 80 "${GAIA_HOME}/electron-main.log"; die "the app exited before its backend answered"; }
    port="$(grep -oE 'ui-port[ =]+[0-9]+' "${GAIA_HOME}/electron-main.log" 2>/dev/null | tail -n1 | grep -oE '[0-9]+$' || true)"
    if [ -n "${port}" ]; then
      body="$(curl -fsS --max-time 5 "http://127.0.0.1:${port}/api/health" 2>/dev/null || true)"
      case "${body}" in *gaia-agent-ui*) break ;; esac
    fi
    sleep 3
  done
  case "${body}" in
    *gaia-agent-ui*) echo "GET /api/health on ${port} -> ${body}" ;;
    *) tail -n 80 "${GAIA_HOME}/electron-main.log" || true; die "the backend did not answer /api/health within 25 min" ;;
  esac
  local got
  local gaia
  gaia="$(backend_gaia)"
  [ -n "${gaia}" ] || die "no backend start in ${GAIA_HOME}/electron-main.log"
  got="$("${gaia}" --version 2>&1 || true)"
  echo "backend ${gaia} --version -> ${got}"
  case "${got}" in *"${PEP440}"*) ;; *) die "the backend the app runs is '${got}', expected ${PEP440}" ;; esac
}

# First-run setup through the installed app's own UI, which has been talking to
# the fake since it launched. HOME is the sandbox, so the browser stays in WORK.
setup_journey() {
  local port
  port="$(grep -oE 'ui-port[ =]+[0-9]+' "${GAIA_HOME}/electron-main.log" | tail -n1 | grep -oE '[0-9]+$' || true)"
  [ -n "${port}" ] || die "no backend port in ${GAIA_HOME}/electron-main.log"
  (
    cd "${REPO}/src/gaia/apps/webui"
    export PLAYWRIGHT_BROWSERS_PATH="${WORK}/playwright-browsers"
    npm ci --ignore-scripts
    npx playwright install --with-deps chromium
    GAIA_E2E_BASE_URL="http://127.0.0.1:${port}" GAIA_E2E_SCREENS="${LOGS}/setup-screens" \
      npx playwright test -c e2e/playwright.config.ts --project=en
  ) || die "setup did not finish through the installed app; its Playwright report is above"
  stop_fake
}

# The app's own processes and the backend it started.
app_pids() {
  {
    pgrep -f "${APP_PATTERN}" || true
    if [ -f "${GAIA_HOME}/backend.pid" ] && kill -0 "$(cat "${GAIA_HOME}/backend.pid")" 2>/dev/null; then
      cat "${GAIA_HOME}/backend.pid"
    fi
  } | sort -u
}

# With no Lemonade answering, the app's setup screen starts installing one by
# itself shortly after launch, and SIGTERM during that run is #6579's case.
# Quit once it is under way, so every row quits in the same state instead of
# racing it.
wait_for_setup_run() {
  local port state="" deadline=$(( $(date +%s) + 120 ))
  port="$(grep -oE 'ui-port[ =]+[0-9]+' "${GAIA_HOME}/electron-main.log" | tail -n1 | grep -oE '[0-9]+$' || true)"
  [ -n "${port}" ] || die "no backend port in ${GAIA_HOME}/electron-main.log"
  while [ "$(date +%s)" -lt "${deadline}" ]; do
    state="$(curl -fsS --max-time 5 -H 'X-Gaia-UI: 1' "http://127.0.0.1:${port}/api/setup/status" 2>/dev/null \
      | "${PYTHON}" -c 'import json,sys; print(json.load(sys.stdin).get("state", ""))' 2>/dev/null || true)"
    case "${state}" in
      ''|idle) sleep 1 ;;
      *) echo "setup is ${state}"; return 0 ;;
    esac
  done
  echo "::warning::the app had not started setup 120 s into the quit phase (last state '${state:-no answer}'); quitting anyway"
}

# Quit the way the OS does at logout: SIGTERM to the app's main process (the
# one without a Chromium --type= flag), which runs the app's own quit path and
# stops the backend. Whatever is still running 30 s later is recorded for the
# leftovers phase, then killed so the uninstall can proceed.
stop_agent_ui() {
  [ "${SETUP_JOURNEY:-}" = "1" ] || wait_for_setup_run
  local p mains=""
  for p in $(pgrep -f "${APP_PATTERN}" || true); do
    ps -o args= -p "${p}" 2>/dev/null | grep -q -- '--type=' || mains="${mains} ${p}"
  done
  echo "asking the app to quit: SIGTERM to${mains:- (no main process found)}"
  # shellcheck disable=SC2086 # a list of pids
  [ -z "${mains}" ] || kill -TERM ${mains} 2>/dev/null || true
  local i
  for i in $(seq 1 30); do
    if [ -z "$(app_pids)" ]; then echo "the app and its backend exited ${i}s after SIGTERM"; return 0; fi
    sleep 1
  done
  local left
  left="$(for p in $(app_pids); do ps -o pid=,args= -p "${p}" 2>/dev/null; done)"
  echo "${left}" >"${WORK}/quit-leftovers"
  echo "::warning::still running 30 s after the app was asked to quit:"
  echo "${left}"
  # shellcheck disable=SC2046 # a list of pids
  kill -9 $(app_pids) 2>/dev/null || true
}

# The daemon the backend started outlives the app by design, and keeps the
# agent sidecar it supervises running, so stop it the way a user would.
stop_daemon() {
  local gaia
  gaia="$(backend_gaia)"
  if [ -z "${gaia}" ] || [ ! -x "${gaia}" ]; then echo "no backend gaia ('${gaia}'): the app started no backend, so no daemon"; return 0; fi
  local rc=0
  "${gaia}" daemon stop || rc=$?
  [ "${rc}" -eq 0 ] || die "\`gaia daemon stop\` exited ${rc}"
}

assert_gone() { for p in "$@"; do [ ! -e "${p}" ] || die "left behind: ${p}"; done; }

no_gaia_processes() {
  local left
  left="$(pgrep -fl 'gaia-desktop|gaia-tui|gaia-agent|\.gaia/venv/bin/|\.gaia/core/|\.gaia/app-core/|resources/core/gaia|-m gaia\.daemon' || true)"
  [ -z "${left}" ] || die "still running after uninstall: ${left}"
}

# ── The real AI engine ────────────────────────────────────────────────────────
# The packaged gaia starts its own real Lemonade, exactly as setup's "Check this
# PC" does: no fake server, no model download. A bundle library leaking into
# lemond (rc13's libstdc++) fails here, not on a user's PC.
real_engine() {
  local gaia="$1" rc=0
  [ -x "${gaia}" ] || die "no packaged gaia at '${gaia}' to start the engine with"
  env -u LEMONADE_BASE_URL "${gaia}" lemonade embedded start --timeout 180 || rc=$?
  if [ "${rc}" -ne 0 ]; then
    echo "--- lemond.log (last 60 lines) ---"
    tail -n 60 "${GAIA_HOME}/lemonade/lemond.log" 2>/dev/null || echo "(no lemond.log)"
    die "the packaged gaia could not start its real Lemonade (exit ${rc})"
  fi
  env -u LEMONADE_BASE_URL "${gaia}" lemonade embedded status
  "${gaia}" lemonade embedded stop || die "\`gaia lemonade embedded stop\` failed after a good start"
}

# The reporter of rc13's bug runs Fedora, where nothing else starts the
# packaged gaia: install it with install.sh in the container, then start its
# real Lemonade there, with the libraries a Fedora desktop has.
fedora_engine() {
  local tag rc=0
  tag="$(rel "d['tag']")"
  cp "${REPO}/installer/scripts/install.sh" "${WORK}/install.sh"
  # shellcheck disable=SC2046  # one package per word
  docker exec gaia-fedora dnf install -y --setopt=install_weak_deps=False \
    $(grep -v '^#' "${REPO}/installer/core/fedora-engine-packages.txt") >/dev/null \
    || die "could not prepare the Fedora container"
  docker exec -e HOME=/root -e GAIA_RELEASE_TAG="${tag}" gaia-fedora sh /work/install.sh \
    || die "install.sh failed in the Fedora container"
  docker exec -e HOME=/root gaia-fedora /root/.gaia/bin/gaia lemonade embedded start --timeout 180 || rc=$?
  if [ "${rc}" -ne 0 ]; then
    echo "--- lemond.log (last 60 lines) ---"
    docker exec gaia-fedora tail -n 60 /root/.gaia/lemonade/lemond.log 2>/dev/null || echo "(no lemond.log)"
    die "the packaged gaia could not start its real Lemonade on Fedora (exit ${rc})"
  fi
  docker exec -e HOME=/root gaia-fedora /root/.gaia/bin/gaia lemonade embedded status
  docker exec -e HOME=/root gaia-fedora /root/.gaia/bin/gaia lemonade embedded stop \
    || die "\`gaia lemonade embedded stop\` failed after a good start"
}

# ── install.sh ────────────────────────────────────────────────────────────────
script_pins_release() { grep -q 'GAIA_RELEASE_TAG' "${REPO}/installer/scripts/install.sh"; }

# ── Phases ────────────────────────────────────────────────────────────────────
case "${STEP}" in
download)
  case "${KIND}" in
    agent-ui-dmg) fetch "$(asset_url agent-ui-dmg)" "${WORK}/dl/$(asset_name agent-ui-dmg)" ;;
    agent-ui-deb) fetch "$(asset_url agent-ui-deb)" "${WORK}/dl/$(asset_name agent-ui-deb)" ;;
    appimage) fetch "$(asset_url agent-ui-appimage)" "${WORK}/dl/$(asset_name agent-ui-appimage)" ;;
    tui-pkg) fetch "$(asset_url "tui-pkg-${TUI_PLATFORM}")" "${WORK}/dl/$(asset_name "tui-pkg-${TUI_PLATFORM}")" ;;
    tui-deb|tui-deb-upgrade) fetch "$(asset_url tui-deb)" "${WORK}/dl/$(asset_name tui-deb)" ;;
    tui-rpm) fetch "$(asset_url tui-rpm)" "${WORK}/dl/$(asset_name tui-rpm)" ;;
    *) die "no download for ${KIND}" ;;
  esac
  ;;

install)
  case "${KIND}" in
    tui-pkg)
      test ! -e /usr/local/bin/gaia-tui || die "gaia-tui is already installed: the runner is not clean"
      sudo installer -pkg "$(package_file "tui-pkg-${TUI_PLATFORM}")" -target /
      for b in gaia-tui gaia-agent; do [ -x "/usr/local/bin/${b}" ] || die "${b} did not land in /usr/local/bin"; done
      ;;
    agent-ui-dmg)
      dmg="${WORK}/dl/$(asset_name agent-ui-dmg)"
      mkdir -p "${WORK}/mnt"
      hdiutil attach -nobrowse -readonly -mountpoint "${WORK}/mnt" "${dmg}"
      ls "${WORK}/mnt"
      # The user drags whatever .app the image holds; its name is the image's.
      app="$(cd "${WORK}/mnt" && ls -d -- *.app 2>/dev/null | head -n1 || true)"
      [ -n "${app}" ] || { hdiutil detach "${WORK}/mnt" || true; die "the disk image holds no .app"; }
      test ! -e "/Applications/${app}" || die "/Applications/${app} already exists: the runner is not clean"
      cp -R "${WORK}/mnt/${app}" /Applications/
      hdiutil detach "${WORK}/mnt"
      echo "/Applications/${app}" >"${WORK}/app.bundle"
      exe="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "/Applications/${app}/Contents/Info.plist")"
      echo "/Applications/${app}/Contents/MacOS/${exe}" >"${WORK}/app.cmd"
      codesign --verify --deep --strict "/Applications/${app}" && echo "codesign: valid" \
        || echo "::warning::codesign --verify failed on /Applications/${app}"
      ;;
    agent-ui-deb)
      deb="${WORK}/dl/$(asset_name agent-ui-deb)"
      pkg="$(dpkg-deb -f "${deb}" Package)"
      echo "${pkg}" >"${WORK}/deb.package"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${deb}"
      # Launch what the menu entry launches.
      desktop_file="$(dpkg -L "${pkg}" | grep -E '^/usr/share/applications/.*\.desktop$' | head -n1 || true)"
      [ -n "${desktop_file}" ] || die "the package installed no menu entry"
      exec_line="$(grep -m1 '^Exec=' "${desktop_file}" | sed 's/^Exec=//')"
      echo "menu entry ${desktop_file}: Exec=${exec_line}"
      exe="$(echo "${exec_line}" | sed -E 's/^"([^"]+)".*/\1/; t; s/ .*//')"
      [ -x "${exe}" ] || die "the menu entry runs ${exe}, which is not there"
      echo "${exe}" >"${WORK}/app.cmd"
      # What docs/guides/install.mdx and docs/deployment/ui.mdx promise.
      [ "${pkg}" = "gaia-desktop" ] || finding "the desktop app's .deb is named '${pkg}', but the docs say \`sudo apt remove gaia-desktop\`; and the terminal's .deb is also named 'gaia', so installing one replaces the other"
      command -v gaia-desktop >/dev/null || finding "\`gaia-desktop\` is not on PATH after installing the .deb, but the docs say to run it from a terminal"
      ;;
    appimage)
      mkdir -p "${HOME}/Applications"
      install -m 0755 "${WORK}/dl/$(asset_name agent-ui-appimage)" "${HOME}/Applications/gaia-agent-ui.AppImage"
      echo "${HOME}/Applications/gaia-agent-ui.AppImage" >"${WORK}/app.cmd"
      ;;
    tui-deb|tui-deb-upgrade)
      if [ "${KIND}" = "tui-deb-upgrade" ]; then
        deb="$(ls "${PREV_PKG_DIR:?}"/*_amd64.deb | head -n1)"
      else
        deb="$(package_file tui-deb)"
      fi
      test ! -e /usr/bin/gaia-tui || die "gaia-tui is already installed: the runner is not clean"
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${deb}"
      for b in gaia-tui gaia-agent; do [ -x "/usr/bin/${b}" ] || die "${b} did not land in /usr/bin"; done
      if [ "${KIND}" = "tui-deb-upgrade" ]; then
        check_tui_version "$(rel "d['prev_tui_version']")" "$(rel "d['prev_version']")"
        mkdir -p "${GAIA_HOME}"
        date +%s%N >"${GAIA_HOME}/headless-upgrade-marker"
        cp "${GAIA_HOME}/headless-upgrade-marker" "${WORK}/marker.expected"
      fi
      ;;
    tui-rpm)
      rpm="$(package_file tui-rpm)"
      # The container sees only WORK (as /work); a rebuilt package lives outside it.
      case "${rpm}" in "${WORK}/dl/"*) ;; *) cp "${rpm}" "${WORK}/dl/" ;; esac
      docker exec gaia-fedora test ! -e /usr/bin/gaia-tui || die "gaia-tui is already in the container"
      docker exec gaia-fedora dnf install -y "/work/dl/$(basename "${rpm}")"
      for b in gaia-tui gaia-agent; do docker exec gaia-fedora test -x "/usr/bin/${b}" || die "${b} did not land in /usr/bin"; done
      ;;
    install-sh)
      [ ! -e "${GAIA_HOME}" ] || die "${GAIA_HOME} exists before install: the sandbox is not fresh"
      if script_pins_release; then
        GAIA_RELEASE_TAG="$(rel "d['tag']")"
        export GAIA_RELEASE_TAG
      else
        echo "::notice::install.sh on this ref does not read GAIA_RELEASE_TAG (#5509 not merged): it installs the newest published release, not $(rel "d['tag']")."
      fi
      # Exactly the documented `curl ... | sh`, with the script from this checkout.
      sh <"${REPO}/installer/scripts/install.sh"
      for f in "${GAIA_HOME}/core/gaia" "${GAIA_HOME}/bin/gaia" "${GAIA_HOME}/bin/gaia-mcp" "${GAIA_HOME}/bin/gaia-tui" "${GAIA_HOME}/bin/gaia-agent"; do
        [ -x "${f}" ] || die "install.sh finished but ${f} is not there"
      done
      # It installs the frozen gaia: no venv, so no Python of GAIA's on any PATH.
      [ ! -e "${GAIA_HOME}/venv" ] || die "install.sh built ${GAIA_HOME}/venv; it installs the frozen gaia"
      ;;
    *) die "no install for ${KIND}" ;;
  esac
  ;;

upgrade)
  [ "${KIND}" = "tui-deb-upgrade" ] || die "no upgrade for ${KIND}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$(package_file tui-deb)"
  check_tui_version "${TUI_VERSION}" "${VERSION}"
  cmp -s "${WORK}/marker.expected" "${GAIA_HOME}/headless-upgrade-marker" \
    || die "the upgrade lost or changed ${GAIA_HOME}/headless-upgrade-marker"
  echo "marker survived the upgrade"
  ;;

launch)
  case "${KIND}" in
    agent-ui-dmg|agent-ui-deb|appimage) launch_agent_ui ;;
    install-sh)
      check_tui_version ""
      "${GAIA_HOME}/bin/gaia" --version
      if script_pins_release; then
        check_tui_version "${TUI_VERSION}" "${VERSION}"
        "${GAIA_HOME}/bin/gaia" --version 2>&1 | grep -q "${PEP440}" \
          || die "install.sh was asked for $(rel "d['tag']") but installed $("${GAIA_HOME}/bin/gaia" --version 2>&1)"
      fi
      ;;
    *) check_tui_version "${TUI_VERSION}" "${VERSION}" ;;
  esac
  ;;

health)
  case "${KIND}" in
    agent-ui-dmg|agent-ui-deb|appimage) wait_agent_ui_health ;;
    install-sh) PATH="${GAIA_HOME}/bin:${PATH}" one_shot ;;
    *) one_shot ;;
  esac
  ;;

engine)
  case "${KIND}" in
    # After quit, so the app's own Lemonade can't stand in for a fresh start.
    agent-ui-deb|appimage) real_engine "$(backend_gaia)" ;;
    install-sh) real_engine "${GAIA_HOME}/bin/gaia" ;;
    tui-rpm) fedora_engine ;;
    *) die "no packaged gaia to start the engine with for ${KIND}" ;;
  esac
  ;;

setup) setup_journey ;;

preflight) preflight_no_lemonade ;;

quit)
  stop_agent_ui
  stop_daemon
  if [ -s "${WORK}/quit-leftovers" ]; then
    finding "processes outlived the app's quit by 30 s (killed to continue): $(tr '\n' ';' <"${WORK}/quit-leftovers")"
  fi
  ;;

uninstall)
  case "${KIND}" in
    tui-pkg)
      # A .pkg ships no uninstaller (macOS convention); this is the documented
      # removal from installer/tui/macos/README.md.
      sudo rm -f /usr/local/bin/gaia-tui /usr/local/bin/gaia-agent
      sudo rm -rf /usr/local/share/doc/gaia-tui
      for id in $(pkgutil --pkgs | grep '^ai\.amd\.gaia\.' || true); do sudo pkgutil --forget "${id}"; done
      ;;
    agent-ui-dmg) rm -rf "$(cat "${WORK}/app.bundle")" ;;
    agent-ui-deb) sudo DEBIAN_FRONTEND=noninteractive apt-get remove -y "$(cat "${WORK}/deb.package")" ;;
    appimage) rm -f "${HOME}/Applications/gaia-agent-ui.AppImage" ;;
    tui-deb|tui-deb-upgrade) sudo DEBIAN_FRONTEND=noninteractive apt-get remove -y gaia ;;
    tui-rpm) docker exec gaia-fedora dnf remove -y gaia ;;
    install-sh) "${GAIA_HOME}/bin/gaia" uninstall --purge --yes ;;
    *) die "no uninstall for ${KIND}" ;;
  esac
  ;;

leftovers)
  case "${KIND}" in
    tui-pkg)
      assert_gone /usr/local/bin/gaia-tui /usr/local/bin/gaia-agent /usr/local/share/doc/gaia-tui
      [ -z "$(pkgutil --pkgs | grep '^ai\.amd\.gaia\.' || true)" ] || die "package receipts remain: $(pkgutil --pkgs | grep '^ai\.amd\.gaia\.')"
      ;;
    agent-ui-dmg)
      assert_gone "$(cat "${WORK}/app.bundle")"
      agents="$(find "${HOME}/Library/LaunchAgents" -iname '*gaia*' 2>/dev/null || true)"
      [ -z "${agents}" ] || die "launch agents left behind: ${agents}"
      ;;
    agent-ui-deb)
      assert_gone /usr/bin/gaia-desktop "$(cat "${WORK}/app.cmd")" "$(dirname "$(cat "${WORK}/app.cmd")")"
      menu="$(find /usr/share/applications -iname '*gaia*desktop*' 2>/dev/null || true)"
      [ -z "${menu}" ] || die "menu entries left behind: ${menu}"
      ! dpkg -s "$(cat "${WORK}/deb.package")" 2>/dev/null | grep -q '^Status: install' || die "dpkg still lists the package as installed"
      ;;
    appimage)
      assert_gone "${HOME}/Applications/gaia-agent-ui.AppImage"
      entries="$(find "${HOME}/.local/share/applications" -iname '*gaia*' 2>/dev/null || true)"
      [ -z "${entries}" ] || echo "::warning::the AppImage left desktop entries: ${entries}"
      ;;
    tui-deb|tui-deb-upgrade)
      assert_gone /usr/bin/gaia-tui /usr/bin/gaia-agent /usr/share/applications/gaia-tui.desktop
      ;;
    tui-rpm)
      for p in /usr/bin/gaia-tui /usr/bin/gaia-agent /usr/share/applications/gaia-tui.desktop; do
        docker exec gaia-fedora test ! -e "${p}" || die "left behind in the container: ${p}"
      done
      ;;
    install-sh)
      assert_gone "${GAIA_HOME}/venv" "${GAIA_HOME}/bin" "${GAIA_HOME}/core"
      for rc in .bashrc .zshrc .profile .bash_profile; do
        if [ -f "${HOME}/${rc}" ] && grep -n "\.gaia" "${HOME}/${rc}"; then
          die "${HOME}/${rc} still puts GAIA on PATH"
        fi
      done
      ;;
  esac
  [ "${KIND}" = "tui-rpm" ] || no_gaia_processes
  # Kept on purpose, so not leftovers: user data in ~/.gaia (removed only by
  # `gaia uninstall --purge`). Listed so a reader can see what stayed.
  echo "--- left in the sandbox home (user data, kept by design) ---"
  (cd "${HOME}" && find . -maxdepth 2 2>/dev/null | head -n 40) || true
  ;;

*) die "unknown phase ${STEP}" ;;
esac
