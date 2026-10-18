// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Ask before quitting while an answer is being written or a document is
 * being indexed: quitting stops both, and the answer is lost.
 *
 * The backend is asked at quit time (`GET /api/system/work-in-progress`),
 * so nothing polls while GAIA is idle.
 */

const CHECK_TIMEOUT_MS = 3000;

/** Read JSON from the GAIA service; throws an error that says what failed. */
async function backendJson(
  port,
  route,
  { method = "GET", body, timeoutMs = CHECK_TIMEOUT_MS, fetchImpl = fetch } = {}
) {
  const url = `http://127.0.0.1:${port}${route}`;
  let resp;
  try {
    const headers = { "X-Gaia-UI": "1" };
    // The service refuses a state-changing request without X-Gaia-UI (ui/security.py).
    if (body !== undefined) headers["Content-Type"] = "application/json";
    resp = await fetchImpl(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    throw new Error(`the GAIA service at ${url} did not answer (${err.message})`);
  }
  let reply = null;
  try {
    reply = await resp.json();
  } catch (err) {
    throw new Error(`the GAIA service sent an unreadable reply from ${route} (HTTP ${resp.status}): ${err.message}`);
  }
  if (!resp.ok) {
    const detail = reply && typeof reply.detail === "string" ? reply.detail : `HTTP ${resp.status}`;
    const error = new Error(detail);
    error.status = resp.status;
    throw error;
  }
  return reply;
}

function fetchWorkInProgress(port, opts) {
  return backendJson(port, "/api/system/work-in-progress", opts);
}

function quoteList(names) {
  const quoted = names.map((n) => `'${n}'`);
  if (quoted.length <= 2) return quoted.join(" and ");
  return `${quoted.slice(0, 2).join(", ")} and ${quoted.length - 2} more`;
}

/** What is about to happen, in the dialog's words. */
const QUIT = Object.freeze({ title: "Quit GAIA?", verb: "Quitting", confirm: "Quit anyway" });
const RESTART_FOR_UPDATE = Object.freeze({
  title: "Restart to update?",
  verb: "Restarting",
  confirm: "Restart anyway",
});

/**
 * The warning for this work, or null when nothing would be lost.
 *
 * @param {{answers: Array<{title: string|null}>, indexing: Array<{filename: string|null}>}} work
 * @param {string} [verb] "Quitting" or "Restarting".
 */
function describeWork(work, verb = QUIT.verb) {
  const answers = work.answers.map((a) => a.title || "a chat");
  const files = work.indexing.map((d) => d.filename || "a document");
  if (answers.length === 0 && files.length === 0) return null;

  const lines = [];
  if (answers.length === 1) {
    lines.push(`${verb} stops the answer in ${quoteList(answers)}, and that answer will be lost.`);
  } else if (answers.length > 1) {
    lines.push(`${verb} stops the answers in ${quoteList(answers)}, and those answers will be lost.`);
  }
  if (files.length > 0) {
    lines.push(`Indexing ${quoteList(files)} stops too; add ${files.length === 1 ? "it" : "them"} again later.`);
  }
  let message;
  if (files.length === 0) {
    message = answers.length === 1 ? "GAIA is still writing an answer." : `GAIA is still writing ${answers.length} answers.`;
  } else if (answers.length === 0) {
    message = "GAIA is still indexing documents.";
  } else {
    message = "GAIA is still working.";
  }
  return { message, detail: lines.join("\n\n") };
}

/**
 * Whether the quit (or update restart) should go ahead. Asks only when work
 * would be lost, or when the check itself failed and the answer is unknown.
 *
 * @param {{getWork: () => Promise<object>, showMessageBox: (opts: object) => Promise<{response: number}>,
 *   action?: typeof QUIT}} deps `action` is QUIT (the default) or RESTART_FOR_UPDATE.
 * @returns {Promise<boolean>}
 */
async function confirmQuit({ getWork, showMessageBox, action = QUIT }) {
  let warning;
  try {
    warning = describeWork(await getWork(), action.verb);
  } catch (err) {
    warning = {
      message: "GAIA couldn't check whether it is still working.",
      detail: `${err.message}\n\nIf an answer is being written, ${action.verb.toLowerCase()} stops it and the answer will be lost.`,
    };
  }
  if (!warning) return true;
  const { response } = await showMessageBox({
    type: "warning",
    title: action.title,
    message: warning.message,
    detail: warning.detail,
    buttons: ["Keep running", action.confirm],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  });
  return response === 1;
}

/**
 * Hold every quit until `confirm` agrees, then quit for real.
 *
 * Electron's before-quit does not wait for async handlers, so the first quit
 * is cancelled while the check runs and re-issued once it passes. Quits that
 * must not ask (an update restart, an OS sign-out or shutdown) call `allow()`.
 *
 * @param {{app: Electron.App, updater: NodeJS.EventEmitter,
 *   shouldAsk: () => boolean, confirm: () => Promise<boolean>,
 *   onQuitting: () => void, onError: (err: Error) => void}} deps
 * @returns {{readonly confirmed: boolean, allow(): void}}
 */
function installQuitGuard({ app, updater, shouldAsk, confirm, onQuitting, onError }) {
  let confirmed = false;
  let inFlight = false;
  // The restart was already confirmed in the update dialog.
  updater.on("before-quit-for-update", () => {
    confirmed = true;
  });
  app.on("before-quit", (event) => {
    if (!confirmed && shouldAsk()) {
      event.preventDefault();
      if (inFlight) return;
      inFlight = true;
      confirm()
        .then((go) => {
          inFlight = false;
          if (!go) {
            console.log("[quit-guard] Quit cancelled: work still running");
            return;
          }
          confirmed = true;
          app.quit();
        })
        .catch((err) => {
          inFlight = false;
          onError(err);
        });
      return;
    }
    confirmed = true;
    onQuitting();
  });
  return {
    get confirmed() {
      return confirmed;
    },
    allow() {
      confirmed = true;
    },
  };
}

/**
 * What closing the window does: hide it to the tray (telling the user once),
 * or, when closing quits, run the quit guard first and keep the window open
 * until it agrees. An OS sign-out (Windows' session-end) never waits on it.
 *
 * @param {Electron.BrowserWindow} win
 * @param {{app: Electron.App, guard: ReturnType<typeof installQuitGuard>,
 *   getTray: () => (object|null), isQuitting: () => boolean, platform: string,
 *   reportNoticeError: (err: Error) => void}} deps
 */
function attachWindowGuards(win, { app, guard, getTray, isQuitting, platform, reportNoticeError }) {
  win.on("close", (event) => {
    if (isQuitting()) return;
    const tray = getTray();
    if (tray && tray.minimizeToTray) {
      event.preventDefault();
      win.hide();
      console.log("[main] Window hidden to tray");
      if (tray.closeNoticeDue) {
        Promise.resolve()
          .then(() => tray.showCloseNotice())
          .then((quit) => {
            if (quit) app.quit();
          })
          .catch(reportNoticeError);
      }
      return;
    }
    // macOS keeps the app running without a window, so only there does close just close.
    if (platform !== "darwin" && !guard.confirmed) {
      event.preventDefault();
      app.quit();
    }
  });
  win.on("session-end", () => guard.allow());
}

module.exports = {
  attachWindowGuards,
  backendJson,
  fetchWorkInProgress,
  describeWork,
  confirmQuit,
  installQuitGuard,
  QUIT,
  RESTART_FOR_UPDATE,
  CHECK_TIMEOUT_MS,
};
