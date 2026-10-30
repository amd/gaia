// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What the tray shows, worked out from what the app window reports.
 *
 * The renderer already follows the backend's events (active runs, approval
 * prompts, model phases, system status) and sends a snapshot over
 * `tray:set-activity` whenever it changes. This module turns that snapshot
 * into the tray's state, tooltip, status line and menu. Pure, so it is tested
 * without Electron.
 */

const MAX_TITLE = 40;
const MAX_RECENT = 5;
const MAX_NEEDS_YOU = 8;
// Windows truncates tray tooltips past 127 characters.
const MAX_TOOLTIP = 127;
const NEEDS_YOU_REASONS = ["approval", "answer"];

function fail(message) {
  throw new TypeError(`tray:set-activity: ${message}`);
}

function checkChatList(list, field, { withReason = false } = {}) {
  if (!Array.isArray(list)) fail(`${field} must be an array`);
  return list.map((item, i) => {
    if (item === null || typeof item !== "object") fail(`${field}[${i}] must be an object`);
    if (typeof item.id !== "string" || item.id === "") fail(`${field}[${i}].id must be a non-empty string`);
    if (typeof item.title !== "string") fail(`${field}[${i}].title must be a string`);
    const chat = { id: item.id, title: item.title };
    if (withReason) {
      if (!NEEDS_YOU_REASONS.includes(item.reason)) {
        fail(`${field}[${i}].reason must be one of ${NEEDS_YOU_REASONS.join(", ")}`);
      }
      chat.reason = item.reason;
    }
    return chat;
  });
}

/**
 * Validate a renderer snapshot; throws a TypeError naming the bad field.
 *
 * @returns {{backendConnected: boolean, modelServerRunning: boolean|null,
 *   model: {name: string, where: string|null}|null, waitingOnModel: string|null,
 *   running: Array<{id, title}>, needsYou: Array<{id, title, reason}>,
 *   recent: Array<{id, title}>}}
 */
function validateActivity(raw) {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) fail("expects an object payload");
  if (typeof raw.backendConnected !== "boolean") fail("backendConnected must be a boolean");
  if (raw.modelServerRunning !== null && typeof raw.modelServerRunning !== "boolean") {
    fail("modelServerRunning must be a boolean or null");
  }
  let model = null;
  if (raw.model !== null) {
    if (typeof raw.model !== "object" || typeof raw.model.name !== "string" || raw.model.name === "") {
      fail("model must be null or { name, where }");
    }
    if (raw.model.where !== null && typeof raw.model.where !== "string") fail("model.where must be a string or null");
    model = { name: raw.model.name, where: raw.model.where };
  }
  if (raw.waitingOnModel !== null && typeof raw.waitingOnModel !== "string") {
    fail("waitingOnModel must be a string or null");
  }
  return {
    backendConnected: raw.backendConnected,
    modelServerRunning: raw.modelServerRunning,
    model,
    waitingOnModel: raw.waitingOnModel || null,
    running: checkChatList(raw.running, "running"),
    needsYou: checkChatList(raw.needsYou, "needsYou", { withReason: true }),
    recent: checkChatList(raw.recent, "recent"),
  };
}

function shortTitle(title) {
  const t = title.trim() || "Untitled chat";
  return t.length > MAX_TITLE ? `${t.slice(0, MAX_TITLE - 1).trimEnd()}…` : t;
}

/** "Gemma-4-E4B-it-GGUF" on "gpu" → "Gemma-4-E4B-it on GPU". */
function modelLabel(model) {
  if (!model) return "No model loaded";
  const name = model.name.replace(/-GGUF$/i, "");
  if (!model.where) return name;
  return model.where.toLowerCase() === "cloud" ? `${name} (cloud)` : `${name} on ${model.where.toUpperCase()}`;
}

function plural(n, one, many) {
  return n === 1 ? one : many;
}

/**
 * The tray's whole view: icon state, tooltip, status line and menu data.
 *
 * Priority, highest first: error, needs you, waiting for the model, working, idle.
 *
 * @param {ReturnType<typeof validateActivity>|null} activity Null before the window first reports.
 * @param {{backendAlive: boolean, unread: number, attention?: number}} ctx `backendAlive` is false
 *   once the GAIA service process exits; `attention` counts chats in any window that need the user.
 */
function deriveTrayView(activity, { backendAlive, unread, attention = 0 }) {
  const unreadNote = unread > 0 ? ` · ${unread} unread ${plural(unread, "notification", "notifications")}` : "";
  const view = {
    state: "idle",
    activityText: "idle",
    statusLine: "",
    tooltip: "",
    needsYou: [],
    recent: [],
    canFreeMemory: false,
    // Until the window reports a loaded local model, there is nothing to free.
    freeMemoryHint: "GAIA isn't ready",
    modelName: null,
  };

  const finish = () => {
    // A needs-you state already names the chats waiting on the user.
    const attentionNote =
      attention > 0 && view.state !== "needs-you"
        ? ` · ${attention} ${plural(attention, "chat needs", "chats need")} you`
        : "";
    view.tooltip = `GAIA — ${view.activityText}${attentionNote}${unreadNote}`.slice(0, MAX_TOOLTIP);
    return view;
  };
  const error = (text) => {
    view.state = "error";
    view.activityText = text;
    view.statusLine = text.charAt(0).toUpperCase() + text.slice(1);
    return finish();
  };

  if (!backendAlive) return error("the GAIA service stopped. Quit and reopen GAIA");
  if (activity === null) {
    view.activityText = "starting";
    view.statusLine = "Starting…";
    return finish();
  }
  if (!activity.backendConnected) return error("can't reach the GAIA service");
  if (activity.modelServerRunning === false) return error("the model server isn't running");

  const running = new Set(activity.running.map((c) => c.id));
  const needsYou = activity.needsYou.slice(0, MAX_NEEDS_YOU);
  view.needsYou = needsYou.map((c) => ({ ...c, title: shortTitle(c.title) }));
  const shown = new Set(needsYou.map((c) => c.id));
  view.recent = activity.recent
    .filter((c) => !shown.has(c.id))
    .slice(0, MAX_RECENT)
    .map((c) => ({ id: c.id, title: shortTitle(c.title), running: running.has(c.id) }));
  view.modelName = activity.model ? activity.model.name : null;

  if (needsYou.length > 0) {
    view.state = "needs-you";
    if (needsYou.length === 1) {
      const why = needsYou[0].reason === "approval" ? "needs your approval" : "needs your answer";
      view.activityText = `'${shortTitle(needsYou[0].title)}' ${why}`;
    } else {
      view.activityText = `${activity.needsYou.length} chats need you`;
    }
  } else if (activity.waitingOnModel) {
    view.state = "waiting";
    view.activityText = activity.waitingOnModel.charAt(0).toLowerCase() + activity.waitingOnModel.slice(1);
  } else if (activity.running.length > 0) {
    view.state = "working";
    view.activityText =
      activity.running.length === 1
        ? `answering in '${shortTitle(activity.running[0].title)}'`
        : `answering in ${activity.running.length} chats`;
  }

  view.statusLine = `${modelLabel(activity.model)} · ${view.activityText}`;

  const local = activity.model && (activity.model.where || "").toLowerCase() !== "cloud";
  if (!local) {
    view.freeMemoryHint = activity.model ? "the model runs in the cloud" : "no model loaded";
  } else if (activity.running.length > 0) {
    view.freeMemoryHint = `${plural(activity.running.length, "an answer is", "answers are")} running`;
  } else {
    view.canFreeMemory = true;
  }
  return finish();
}

/** Native menus treat "&" as an access-key marker on Windows and Linux. */
function menuText(text) {
  return text.replace(/&/g, "&&");
}

/**
 * The tray menu as an Electron template.
 *
 * @param {ReturnType<typeof deriveTrayView>} view
 * @param {{openChat(id: string): void, newChat(): void, showWindow(): void,
 *   freeMemory(): void, openSettings(): void, quit(): void}} actions
 */
function buildMenuTemplate(view, actions) {
  const items = [{ label: menuText(view.statusLine), enabled: false }, { type: "separator" }];

  if (view.needsYou.length > 0) {
    items.push({ label: "Needs you", enabled: false });
    for (const chat of view.needsYou) {
      const verb = chat.reason === "approval" ? "approve" : "answer";
      items.push({ label: menuText(`${chat.title} — ${verb}`), click: () => actions.openChat(chat.id) });
    }
    items.push({ type: "separator" });
  }

  items.push({ label: "New chat", click: () => actions.newChat() });
  if (view.recent.length > 0) {
    items.push({ label: "Recent", enabled: false });
    for (const chat of view.recent) {
      const label = chat.running ? `${chat.title} — answering` : chat.title;
      items.push({ label: menuText(label), click: () => actions.openChat(chat.id) });
    }
  }
  items.push({ type: "separator" });
  items.push({ label: "Show GAIA", click: () => actions.showWindow() });
  items.push({
    label: view.canFreeMemory ? "Free memory…" : `Free memory — ${view.freeMemoryHint}`,
    enabled: view.canFreeMemory,
    click: () => actions.freeMemory(),
  });
  items.push({ label: "Settings…", click: () => actions.openSettings() });
  items.push({ type: "separator" });
  items.push({ label: "Quit GAIA", click: () => actions.quit() });
  return items;
}

module.exports = { validateActivity, deriveTrayView, buildMenuTemplate, modelLabel, menuText };
