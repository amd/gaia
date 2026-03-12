// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Notification Service (T5-service)
 *
 * Routes notifications from agents to OS native toasts and the renderer process.
 *
 * Design decisions (from spec):
 *   - OS native toasts are click-to-focus only (S5 fix) — no action buttons
 *   - All interactive prompts (Approve/Deny) happen in-app via PermissionPrompt modal
 *   - Permission responses are sent back to agents via JSON-RPC
 *   - Notification persistence in ~/.gaia/notifications.json (optional, last 200)
 *
 * Notification types:
 *   permission_request — Modal dialog (blocks action) + OS click-to-focus toast
 *   security_alert     — In-app toast + OS click-to-focus toast
 *   status_change      — In-app toast (auto-dismiss 5s)
 *   info               — Notification center only
 *   error              — In-app toast (persistent) + OS click-to-focus toast
 */

const { Notification, ipcMain } = require("electron");
const { EventEmitter } = require("events");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Constants ────────────────────────────────────────────────────────────

const GAIA_DIR = path.join(os.homedir(), ".gaia");
const NOTIFICATIONS_PATH = path.join(GAIA_DIR, "notifications.json");

/** Max persisted notifications */
const MAX_PERSISTED = 200;

/** Notification types that trigger OS native toasts */
const OS_TOAST_TYPES = new Set([
  "permission_request",
  "security_alert",
  "error",
]);

// ── NotificationService ──────────────────────────────────────────────────

class NotificationService extends EventEmitter {
  /**
   * @param {Electron.BrowserWindow} mainWindow
   * @param {import('./agent-process-manager')} agentProcessManager
   * @param {import('./tray-manager')} trayManager
   */
  constructor(mainWindow, agentProcessManager, trayManager) {
    super();

    /** @type {Electron.BrowserWindow} */
    this.mainWindow = mainWindow;

    /** @type {import('./agent-process-manager')} */
    this.agentProcessManager = agentProcessManager;

    /** @type {import('./tray-manager')} */
    this.trayManager = trayManager;

    /**
     * All notifications (in-memory, most recent last).
     * @type {Array<{
     *   id: string,
     *   type: string,
     *   agentId: string,
     *   title: string,
     *   message: string,
     *   tool?: string,
     *   toolArgs?: object,
     *   actions?: string[],
     *   timeoutSeconds?: number,
     *   timestamp: number,
     *   read: boolean,
     *   responded: boolean,
     *   response?: { action: string, remember: boolean },
     * }>}
     */
    this.notifications = this._loadNotifications();

    /** Counter for generating notification IDs (timestamp-based to avoid collisions across restarts) */
    this._idCounter = Date.now();

    /** Pending permission request timers (auto-deny on timeout) */
    this._permissionTimers = {};

    this._registerIpcHandlers();
    this._listenToAgentEvents();
  }

  // ── Public API ───────────────────────────────────────────────────────

  /**
   * Handle an incoming notification from an agent.
   * Called by AgentProcessManager when it receives a "notification/send" JSON-RPC message.
   *
   * @param {string} agentId
   * @param {object} params — from the JSON-RPC notification/send message
   */
  handleAgentNotification(agentId, params) {
    const notif = {
      id: `notif-${this._idCounter++}`,
      type: params.type || "info",
      agentId,
      title: params.title || "Agent Notification",
      message: params.message || "",
      tool: params.tool,
      toolArgs: params.tool_args,
      actions: params.actions,
      timeoutSeconds: params.timeout_seconds,
      timestamp: Date.now(),
      read: false,
      responded: false,
    };

    // Add to in-memory list
    this.notifications.push(notif);
    if (this.notifications.length > MAX_PERSISTED * 2) {
      this.notifications = this.notifications.slice(-MAX_PERSISTED);
    }

    console.log(
      `[notif] ${notif.type} from ${agentId}: ${notif.title} — ${notif.message}`
    );

    // Route based on type
    switch (notif.type) {
      case "permission_request":
        this._handlePermissionRequest(notif);
        break;
      case "security_alert":
      case "error":
        this._sendToRenderer("notification:new", notif);
        this._showOsToast(notif);
        break;
      case "status_change":
        this._sendToRenderer("notification:new", notif);
        break;
      case "info":
      default:
        this._sendToRenderer("notification:new", notif);
        break;
    }

    // Update tray badge
    this._updateTrayBadge();

    // Persist
    this._saveNotifications();
  }

  /**
   * Get the current unread notification count.
   * @returns {number}
   */
  getUnreadCount() {
    return this.notifications.filter((n) => !n.read).length;
  }

  /**
   * Mark all notifications as read.
   */
  markAllRead() {
    for (const notif of this.notifications) {
      notif.read = true;
    }
    this._updateTrayBadge();
    this._saveNotifications();
  }

  /**
   * Clear all notifications.
   */
  clearAll() {
    this.notifications = [];
    this._updateTrayBadge();
    this._saveNotifications();
  }

  /**
   * Clean up all pending timers. Call during shutdown to prevent leaked timers.
   */
  destroy() {
    for (const [id, timer] of Object.entries(this._permissionTimers)) {
      clearTimeout(timer);
    }
    this._permissionTimers = {};
  }

  // ── Private: Permission requests ─────────────────────────────────────

  _handlePermissionRequest(notif) {
    // Send to renderer as a permission prompt
    this._sendToRenderer("notification:permission-request", notif);

    // Show OS toast (click-to-focus only)
    this._showOsToast(notif);

    // Set up auto-deny timeout if specified
    if (notif.timeoutSeconds && notif.timeoutSeconds > 0) {
      this._permissionTimers[notif.id] = setTimeout(() => {
        if (!notif.responded) {
          console.log(
            `[notif] Permission request ${notif.id} timed out — auto-denying`
          );
          this._respondToPermission(notif.id, "deny", false);
        }
      }, notif.timeoutSeconds * 1000);
    }
  }

  /**
   * Respond to a permission request.
   * @param {string} notifId
   * @param {string} action — "allow" or "deny"
   * @param {boolean} remember — whether to remember this choice
   */
  _respondToPermission(notifId, action, remember) {
    const notif = this.notifications.find((n) => n.id === notifId);
    if (!notif) {
      console.warn(`[notif] Permission response for unknown notification: ${notifId}`);
      return;
    }

    if (notif.responded) {
      console.warn(`[notif] Permission ${notifId} already responded`);
      return;
    }

    notif.responded = true;
    notif.response = { action, remember };

    // Clear timeout timer if exists
    if (this._permissionTimers[notifId]) {
      clearTimeout(this._permissionTimers[notifId]);
      delete this._permissionTimers[notifId];
    }

    // Send response back to the agent via JSON-RPC notification (no id, no response expected).
    // We use _sendJsonRpcRaw (not sendJsonRpc) because this is a notification TO the agent,
    // not a request — the agent doesn't reply, so we must not wait for one.
    if (this.agentProcessManager) {
      try {
        this.agentProcessManager._sendJsonRpcRaw(
          notif.agentId,
          "notification/response",
          {
            notification_id: notifId,
            action,
            remember,
          }
        );
      } catch (err) {
        console.error(
          `[notif] Failed to send permission response to ${notif.agentId}:`,
          err.message
        );
      }
    }

    console.log(
      `[notif] Permission ${notifId}: ${action} (remember=${remember})`
    );

    this._saveNotifications();
  }

  // ── Private: OS native toasts ────────────────────────────────────────

  _showOsToast(notif) {
    if (!OS_TOAST_TYPES.has(notif.type)) return;

    try {
      // Check if Notification is supported
      if (!Notification.isSupported()) {
        console.warn("[notif] OS notifications not supported on this platform");
        return;
      }

      const osNotif = new Notification({
        title: notif.title,
        body: notif.message,
        icon: path.join(__dirname, "..", "assets", "icon.png"),
        urgency: notif.type === "security_alert" ? "critical" : "normal",
        // No action buttons — click-to-focus only (S5 fix)
      });

      // Click → show and focus the main window
      osNotif.on("click", () => {
        this._showAndFocusWindow(notif);
      });

      osNotif.show();
    } catch (err) {
      console.warn("[notif] Failed to show OS notification:", err.message);
    }
  }

  _showAndFocusWindow(notif) {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;

    if (this.mainWindow.isMinimized()) {
      this.mainWindow.restore();
    }
    this.mainWindow.show();
    this.mainWindow.focus();

    // Tell the renderer which notification to focus on.
    // Note: for permission_request, the notification was already sent to the renderer
    // via _handlePermissionRequest — we just navigate to it here, don't re-send.
    this._sendToRenderer("tray:navigate", `notification:${notif.id}`);
  }

  // ── Private: Tray badge ──────────────────────────────────────────────

  _updateTrayBadge() {
    if (this.trayManager) {
      this.trayManager.setNotificationCount(this.getUnreadCount());
    }
  }

  // ── Private: Event listeners ─────────────────────────────────────────

  _listenToAgentEvents() {
    if (!this.agentProcessManager) return;

    // Listen for agent notifications via the EventEmitter
    this.agentProcessManager.on(
      "agent-notification",
      (agentId, params) => {
        this.handleAgentNotification(agentId, params);
      }
    );

    // Agent crash → generate error notification
    this.agentProcessManager.on("status-change", (payload) => {
      if (payload.status === "stopped" && payload.detail) {
        // Only notify on unexpected stops (crashes)
        this.handleAgentNotification(payload.agentId, {
          type: "error",
          title: "Agent Crashed",
          message: payload.detail || `Agent ${payload.agentId} stopped unexpectedly`,
        });
      }
    });

    // Crash limit reached → generate error notification
    this.agentProcessManager.on(
      "agent-crash-limit",
      (agentId, crashCount) => {
        this.handleAgentNotification(agentId, {
          type: "error",
          title: "Agent Crash Limit Reached",
          message: `Agent ${agentId} crashed ${crashCount} times — automatic restart disabled`,
        });
      }
    );
  }

  // ── Private: Persistence ─────────────────────────────────────────────

  _loadNotifications() {
    try {
      if (fs.existsSync(NOTIFICATIONS_PATH)) {
        const raw = fs.readFileSync(NOTIFICATIONS_PATH, "utf8");
        return JSON.parse(raw);
      }
    } catch (err) {
      console.warn("[notif] Could not load notifications:", err.message);
    }
    return [];
  }

  _saveNotifications() {
    try {
      if (!fs.existsSync(GAIA_DIR)) {
        fs.mkdirSync(GAIA_DIR, { recursive: true });
      }

      // Only persist the last MAX_PERSISTED entries
      const toSave = this.notifications.slice(-MAX_PERSISTED);
      fs.writeFileSync(
        NOTIFICATIONS_PATH,
        JSON.stringify(toSave, null, 2),
        "utf8"
      );
    } catch (err) {
      console.warn("[notif] Could not save notifications:", err.message);
    }
  }

  // ── Private: IPC handlers ────────────────────────────────────────────

  _registerIpcHandlers() {
    ipcMain.handle(
      "notification:respond",
      (_event, notifId, action, remember) => {
        this._respondToPermission(notifId, action, remember);
      }
    );
  }

  // ── Private: Helpers ─────────────────────────────────────────────────

  _sendToRenderer(channel, data) {
    try {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send(channel, data);
      }
    } catch (err) {
      console.warn("[notif] Could not send to renderer:", err.message);
    }
  }
}

module.exports = NotificationService;
