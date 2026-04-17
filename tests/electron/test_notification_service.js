// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for NotificationService
 * (src/gaia/apps/webui/services/notification-service.js)
 *
 * Covers: construction, notification routing by type, list management,
 * permission request lifecycle (timeout auto-deny, manual respond,
 * double-respond prevention), OS toasts, window focus, tray badge,
 * agent event listeners, persistence, IPC handler, destroy(), and
 * edge cases with null dependencies.
 */

const { EventEmitter } = require("events");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Mocks ────────────────────────────────────────────────────────────────

// The electron mock is loaded via moduleNameMapper, but it does not export
// a Notification class.  We add one here and patch the mock before the
// service is required.

const electronMock = require("electron");

// Mock Notification class attached to the electron module
class MockNotification extends EventEmitter {
  constructor(opts = {}) {
    super();
    this.opts = opts;
  }
  show() {}
}
MockNotification.isSupported = jest.fn(() => true);
electronMock.Notification = MockNotification;

// Mock fs so the service never touches the real filesystem
jest.mock("fs", () => ({
  existsSync: jest.fn(() => false),
  readFileSync: jest.fn(() => "[]"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));

// Now require the service under test (after mocks are in place)
const NotificationService = require("../../src/gaia/apps/webui/services/notification-service");

// ── Helpers ──────────────────────────────────────────────────────────────

/**
 * Create a mock BrowserWindow with the methods used by NotificationService.
 * Extends the base MockBrowserWindow from the electron mock with extra
 * methods (isMinimized, restore) that the notification-service relies on.
 */
function createMockWindow(overrides = {}) {
  const win = new electronMock.BrowserWindow();
  win.isMinimized = jest.fn(() => false);
  win.restore = jest.fn();
  Object.assign(win, overrides);
  return win;
}

/**
 * Create a mock AgentProcessManager (EventEmitter + _sendJsonRpcRaw).
 */
function createMockAgentProcessManager() {
  const apm = new EventEmitter();
  apm._sendJsonRpcRaw = jest.fn();
  return apm;
}

/**
 * Create a mock TrayManager.
 */
function createMockTrayManager() {
  return { setNotificationCount: jest.fn() };
}

// ── Test suite ───────────────────────────────────────────────────────────

describe("NotificationService", () => {
  let mainWindow;
  let agentProcessManager;
  let trayManager;
  let service;

  beforeEach(() => {
    // Reset all mocks
    jest.clearAllMocks();
    fs.existsSync.mockReturnValue(false);
    fs.readFileSync.mockReturnValue("[]");
    MockNotification.isSupported.mockReturnValue(true);

    // Fresh instances for every test
    mainWindow = createMockWindow();
    agentProcessManager = createMockAgentProcessManager();
    trayManager = createMockTrayManager();

    // Reset ipcMain handlers so each test starts clean
    electronMock.ipcMain._handlers.clear();

    service = new NotificationService(mainWindow, agentProcessManager, trayManager);
  });

  afterEach(() => {
    if (service) service.destroy();
  });

  // ====================================================================
  // 1. Construction
  // ====================================================================
  describe("construction", () => {
    it("should initialize with an empty notification list when no file exists", () => {
      expect(service.notifications).toEqual([]);
    });

    it("should load persisted notifications from disk", () => {
      const saved = [{ id: "notif-1", read: false, responded: false }];
      fs.existsSync.mockReturnValue(true);
      fs.readFileSync.mockReturnValue(JSON.stringify(saved));

      electronMock.ipcMain._handlers.clear();
      const svc = new NotificationService(mainWindow, agentProcessManager, trayManager);
      expect(svc.notifications).toEqual(saved);
      svc.destroy();
    });

    it("should register the notification:respond IPC handler", () => {
      expect(electronMock.ipcMain._handlers.has("notification:respond")).toBe(true);
    });

    it("should listen to agent events when agentProcessManager is provided", () => {
      expect(agentProcessManager.listenerCount("agent-notification")).toBe(1);
      expect(agentProcessManager.listenerCount("status-change")).toBe(1);
      expect(agentProcessManager.listenerCount("agent-crash-limit")).toBe(1);
    });

    it("should not throw when agentProcessManager is null", () => {
      electronMock.ipcMain._handlers.clear();
      expect(() => {
        const svc = new NotificationService(mainWindow, null, trayManager);
        svc.destroy();
      }).not.toThrow();
    });
  });

  // ====================================================================
  // 2. handleAgentNotification — routing by type
  // ====================================================================
  describe("handleAgentNotification", () => {
    it("should assign incrementing IDs to notifications", () => {
      service.handleAgentNotification("agent-a", { message: "one" });
      service.handleAgentNotification("agent-a", { message: "two" });

      const ids = service.notifications.map((n) => n.id);
      expect(ids[0]).not.toEqual(ids[1]);
      // IDs should be "notif-<number>" and second number > first
      const num0 = parseInt(ids[0].replace("notif-", ""), 10);
      const num1 = parseInt(ids[1].replace("notif-", ""), 10);
      expect(num1).toBeGreaterThan(num0);
    });

    it("should default type to 'info' and title to 'Agent Notification'", () => {
      service.handleAgentNotification("agent-b", { message: "hello" });
      const notif = service.notifications[0];
      expect(notif.type).toBe("info");
      expect(notif.title).toBe("Agent Notification");
      expect(notif.message).toBe("hello");
    });

    it("should default message to empty string when not provided", () => {
      service.handleAgentNotification("agent-c", {});
      expect(service.notifications[0].message).toBe("");
    });

    it("should set read and responded to false on new notifications", () => {
      service.handleAgentNotification("agent-d", { message: "test" });
      const notif = service.notifications[0];
      expect(notif.read).toBe(false);
      expect(notif.responded).toBe(false);
    });

    it("should populate optional fields (tool, toolArgs, actions, timeoutSeconds)", () => {
      service.handleAgentNotification("agent-e", {
        type: "permission_request",
        title: "Run shell?",
        message: "exec ls",
        tool: "shell_exec",
        tool_args: { cmd: "ls" },
        actions: ["allow", "deny"],
        timeout_seconds: 30,
      });
      const notif = service.notifications[0];
      expect(notif.tool).toBe("shell_exec");
      expect(notif.toolArgs).toEqual({ cmd: "ls" });
      expect(notif.actions).toEqual(["allow", "deny"]);
      expect(notif.timeoutSeconds).toBe(30);
    });

    // -- Routing per type --

    it("should send 'info' notifications to renderer only (no OS toast)", () => {
      service.handleAgentNotification("a1", { type: "info", message: "hi" });
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:new",
        expect.objectContaining({ type: "info" })
      );
    });

    it("should send 'status_change' notifications to renderer only (no OS toast)", () => {
      service.handleAgentNotification("a2", { type: "status_change", message: "started" });
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:new",
        expect.objectContaining({ type: "status_change" })
      );
    });

    it("should send 'error' notifications to renderer AND show OS toast", () => {
      service.handleAgentNotification("a3", { type: "error", title: "Fail", message: "oops" });
      // renderer
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:new",
        expect.objectContaining({ type: "error" })
      );
    });

    it("should send 'security_alert' notifications to renderer AND show OS toast", () => {
      service.handleAgentNotification("a4", { type: "security_alert", title: "Alert", message: "bad" });
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:new",
        expect.objectContaining({ type: "security_alert" })
      );
    });

    it("should send 'permission_request' to renderer on the permission-request channel", () => {
      service.handleAgentNotification("a5", {
        type: "permission_request",
        title: "Allow?",
        message: "exec",
      });
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:permission-request",
        expect.objectContaining({ type: "permission_request" })
      );
    });

    it("should treat unknown types as default (renderer only)", () => {
      service.handleAgentNotification("a6", { type: "custom_type", message: "hmm" });
      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "notification:new",
        expect.objectContaining({ type: "custom_type" })
      );
    });

    it("should update tray badge after adding a notification", () => {
      service.handleAgentNotification("a7", { message: "tray" });
      expect(trayManager.setNotificationCount).toHaveBeenCalled();
    });

    it("should persist notifications after adding one", () => {
      service.handleAgentNotification("a8", { message: "persist" });
      expect(fs.writeFileSync).toHaveBeenCalled();
    });
  });

  // ====================================================================
  // 3. Notification list management
  // ====================================================================
  describe("list management", () => {
    it("should trim notifications when exceeding MAX_PERSISTED * 2", () => {
      // MAX_PERSISTED = 200, so threshold is 400
      // Pre-fill 400 notifications
      for (let i = 0; i < 400; i++) {
        service.notifications.push({ id: `notif-pre-${i}`, read: false });
      }
      // Adding one more crosses the threshold (401 > 400)
      service.handleAgentNotification("trim-agent", { message: "overflow" });
      // After trim, should be sliced to last MAX_PERSISTED (200)
      expect(service.notifications.length).toBe(200);
      // The last notification should be the one we just added
      expect(service.notifications[service.notifications.length - 1].message).toBe("overflow");
    });

    describe("getUnreadCount", () => {
      it("should return 0 when no notifications exist", () => {
        expect(service.getUnreadCount()).toBe(0);
      });

      it("should count only unread notifications", () => {
        service.notifications = [
          { id: "1", read: false },
          { id: "2", read: true },
          { id: "3", read: false },
        ];
        expect(service.getUnreadCount()).toBe(2);
      });
    });

    describe("markAllRead", () => {
      it("should mark every notification as read", () => {
        service.handleAgentNotification("r1", { message: "a" });
        service.handleAgentNotification("r2", { message: "b" });
        expect(service.getUnreadCount()).toBe(2);

        service.markAllRead();
        expect(service.getUnreadCount()).toBe(0);
        service.notifications.forEach((n) => expect(n.read).toBe(true));
      });

      it("should update tray badge after marking all read", () => {
        service.handleAgentNotification("r3", { message: "c" });
        trayManager.setNotificationCount.mockClear();

        service.markAllRead();
        expect(trayManager.setNotificationCount).toHaveBeenCalledWith(0);
      });

      it("should persist after marking all read", () => {
        service.handleAgentNotification("r4", { message: "d" });
        fs.writeFileSync.mockClear();

        service.markAllRead();
        expect(fs.writeFileSync).toHaveBeenCalled();
      });
    });

    describe("clearAll", () => {
      it("should remove all notifications", () => {
        service.handleAgentNotification("c1", { message: "e" });
        service.handleAgentNotification("c2", { message: "f" });
        expect(service.notifications.length).toBe(2);

        service.clearAll();
        expect(service.notifications).toEqual([]);
      });

      it("should update tray badge to 0 after clearing", () => {
        service.handleAgentNotification("c3", { message: "g" });
        trayManager.setNotificationCount.mockClear();

        service.clearAll();
        expect(trayManager.setNotificationCount).toHaveBeenCalledWith(0);
      });

      it("should persist empty list after clearing", () => {
        service.handleAgentNotification("c4", { message: "h" });
        fs.writeFileSync.mockClear();

        service.clearAll();
        expect(fs.writeFileSync).toHaveBeenCalled();
      });
    });
  });

  // ====================================================================
  // 4. Permission requests
  // ====================================================================
  describe("permission requests", () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it("should auto-deny after timeoutSeconds elapses", () => {
      service.handleAgentNotification("perm-agent", {
        type: "permission_request",
        title: "Allow shell?",
        message: "run ls",
        timeout_seconds: 10,
      });
      const notif = service.notifications[0];
      expect(notif.responded).toBe(false);

      jest.advanceTimersByTime(10 * 1000);

      expect(notif.responded).toBe(true);
      expect(notif.response).toEqual({ action: "deny", remember: false });
    });

    it("should send auto-deny response to agentProcessManager via JSON-RPC", () => {
      service.handleAgentNotification("perm-agent-2", {
        type: "permission_request",
        title: "Run?",
        message: "cmd",
        timeout_seconds: 5,
      });
      const notif = service.notifications[0];

      jest.advanceTimersByTime(5 * 1000);

      expect(agentProcessManager._sendJsonRpcRaw).toHaveBeenCalledWith(
        "perm-agent-2",
        "notification/response",
        {
          notification_id: notif.id,
          action: "deny",
          remember: false,
        }
      );
    });

    it("should allow manual response before timeout", () => {
      service.handleAgentNotification("perm-agent-3", {
        type: "permission_request",
        title: "Allow?",
        message: "x",
        timeout_seconds: 30,
      });
      const notif = service.notifications[0];

      service._respondToPermission(notif.id, "allow", true);

      expect(notif.responded).toBe(true);
      expect(notif.response).toEqual({ action: "allow", remember: true });
      expect(agentProcessManager._sendJsonRpcRaw).toHaveBeenCalledWith(
        notif.agentId,
        "notification/response",
        {
          notification_id: notif.id,
          action: "allow",
          remember: true,
        }
      );
    });

    it("should clear the timeout timer when manually responded", () => {
      service.handleAgentNotification("perm-agent-4", {
        type: "permission_request",
        title: "Allow?",
        message: "y",
        timeout_seconds: 30,
      });
      const notif = service.notifications[0];

      service._respondToPermission(notif.id, "allow", false);

      // Advancing time past the original timeout should not trigger auto-deny again
      agentProcessManager._sendJsonRpcRaw.mockClear();
      jest.advanceTimersByTime(30 * 1000);
      // _sendJsonRpcRaw should not have been called again
      expect(agentProcessManager._sendJsonRpcRaw).not.toHaveBeenCalled();
    });

    it("should prevent double-respond (second call is a no-op)", () => {
      service.handleAgentNotification("perm-agent-5", {
        type: "permission_request",
        title: "Allow?",
        message: "z",
        timeout_seconds: 60,
      });
      const notif = service.notifications[0];

      service._respondToPermission(notif.id, "allow", true);
      agentProcessManager._sendJsonRpcRaw.mockClear();

      service._respondToPermission(notif.id, "deny", false);
      // Second call should NOT send another JSON-RPC
      expect(agentProcessManager._sendJsonRpcRaw).not.toHaveBeenCalled();
      // Response should remain the first one
      expect(notif.response).toEqual({ action: "allow", remember: true });
    });

    it("should not auto-deny if already responded before timeout", () => {
      service.handleAgentNotification("perm-agent-6", {
        type: "permission_request",
        title: "Allow?",
        message: "w",
        timeout_seconds: 5,
      });
      const notif = service.notifications[0];

      service._respondToPermission(notif.id, "allow", false);
      agentProcessManager._sendJsonRpcRaw.mockClear();

      jest.advanceTimersByTime(5 * 1000);
      // No auto-deny sent
      expect(agentProcessManager._sendJsonRpcRaw).not.toHaveBeenCalled();
      expect(notif.response.action).toBe("allow");
    });

    it("should not set a timeout when timeoutSeconds is 0", () => {
      service.handleAgentNotification("perm-agent-7", {
        type: "permission_request",
        title: "Allow?",
        message: "no timeout",
        timeout_seconds: 0,
      });
      const notif = service.notifications[0];
      expect(service._permissionTimers[notif.id]).toBeUndefined();
    });

    it("should not set a timeout when timeoutSeconds is not provided", () => {
      service.handleAgentNotification("perm-agent-8", {
        type: "permission_request",
        title: "Allow?",
        message: "no timeout field",
      });
      const notif = service.notifications[0];
      expect(service._permissionTimers[notif.id]).toBeUndefined();
    });

    it("should silently ignore respond for unknown notifId", () => {
      expect(() => {
        service._respondToPermission("notif-nonexistent", "allow", false);
      }).not.toThrow();
      expect(agentProcessManager._sendJsonRpcRaw).not.toHaveBeenCalled();
    });

    it("should persist after responding to permission", () => {
      service.handleAgentNotification("perm-agent-9", {
        type: "permission_request",
        title: "Allow?",
        message: "save test",
      });
      const notif = service.notifications[0];
      fs.writeFileSync.mockClear();

      service._respondToPermission(notif.id, "deny", false);
      expect(fs.writeFileSync).toHaveBeenCalled();
    });
  });

  // ====================================================================
  // 5. OS toasts
  // ====================================================================
  describe("OS toasts", () => {
    it("should show an OS toast for 'error' type", () => {
      const showSpy = jest.spyOn(MockNotification.prototype, "show");
      service.handleAgentNotification("toast-1", {
        type: "error",
        title: "Error",
        message: "boom",
      });
      expect(showSpy).toHaveBeenCalled();
      showSpy.mockRestore();
    });

    it("should show an OS toast for 'security_alert' type", () => {
      const showSpy = jest.spyOn(MockNotification.prototype, "show");
      service.handleAgentNotification("toast-2", {
        type: "security_alert",
        title: "Alert",
        message: "danger",
      });
      expect(showSpy).toHaveBeenCalled();
      showSpy.mockRestore();
    });

    it("should show an OS toast for 'permission_request' type", () => {
      const showSpy = jest.spyOn(MockNotification.prototype, "show");
      service.handleAgentNotification("toast-3", {
        type: "permission_request",
        title: "Allow?",
        message: "exec",
      });
      expect(showSpy).toHaveBeenCalled();
      showSpy.mockRestore();
    });

    it("should NOT show an OS toast for 'info' type", () => {
      const showSpy = jest.spyOn(MockNotification.prototype, "show");
      service.handleAgentNotification("toast-4", {
        type: "info",
        title: "Info",
        message: "fyi",
      });
      expect(showSpy).not.toHaveBeenCalled();
      showSpy.mockRestore();
    });

    it("should NOT show an OS toast for 'status_change' type", () => {
      const showSpy = jest.spyOn(MockNotification.prototype, "show");
      service.handleAgentNotification("toast-5", {
        type: "status_change",
        title: "Status",
        message: "running",
      });
      expect(showSpy).not.toHaveBeenCalled();
      showSpy.mockRestore();
    });

    it("should not show OS toast when Notification.isSupported() returns false", () => {
      MockNotification.isSupported.mockReturnValue(false);
      const showSpy = jest.spyOn(MockNotification.prototype, "show");

      service.handleAgentNotification("toast-6", {
        type: "error",
        title: "Error",
        message: "no support",
      });
      expect(showSpy).not.toHaveBeenCalled();
      showSpy.mockRestore();
    });
  });

  // ====================================================================
  // 6. Window focus on toast click
  // ====================================================================
  describe("window focus", () => {
    it("should show and focus the window on _showAndFocusWindow", () => {
      const showSpy = jest.fn();
      const focusSpy = jest.fn();
      mainWindow.show = showSpy;
      mainWindow.focus = focusSpy;

      const notif = { id: "notif-focus-1", type: "error" };
      service._showAndFocusWindow(notif);

      expect(showSpy).toHaveBeenCalled();
      expect(focusSpy).toHaveBeenCalled();
    });

    it("should restore a minimized window before showing", () => {
      mainWindow.isMinimized = jest.fn(() => true);
      mainWindow.restore = jest.fn();
      mainWindow.show = jest.fn();
      mainWindow.focus = jest.fn();

      const notif = { id: "notif-focus-2", type: "error" };
      service._showAndFocusWindow(notif);

      expect(mainWindow.restore).toHaveBeenCalled();
      expect(mainWindow.show).toHaveBeenCalled();
    });

    it("should NOT restore when window is not minimized", () => {
      mainWindow.isMinimized = jest.fn(() => false);
      mainWindow.restore = jest.fn();
      mainWindow.show = jest.fn();
      mainWindow.focus = jest.fn();

      const notif = { id: "notif-focus-3", type: "error" };
      service._showAndFocusWindow(notif);

      expect(mainWindow.restore).not.toHaveBeenCalled();
    });

    it("should send tray:navigate to renderer with notification id", () => {
      mainWindow.show = jest.fn();
      mainWindow.focus = jest.fn();

      const notif = { id: "notif-focus-4", type: "error" };
      service._showAndFocusWindow(notif);

      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "tray:navigate",
        "notification:notif-focus-4"
      );
    });

    it("should do nothing when mainWindow is null", () => {
      service.mainWindow = null;
      expect(() => {
        service._showAndFocusWindow({ id: "notif-null-win" });
      }).not.toThrow();
    });

    it("should do nothing when mainWindow is destroyed", () => {
      mainWindow.close(); // sets _isDestroyed = true
      expect(() => {
        service._showAndFocusWindow({ id: "notif-destroyed-win" });
      }).not.toThrow();
    });

    it("should focus window when OS toast is clicked", () => {
      // We capture the MockNotification instance created inside _showOsToast
      // and simulate a click on it.
      mainWindow.show = jest.fn();
      mainWindow.focus = jest.fn();

      let capturedNotification;
      const origConstructor = MockNotification;
      // Intercept construction
      const instances = [];
      const showSpy = jest.spyOn(MockNotification.prototype, "show").mockImplementation(function () {
        instances.push(this);
      });

      service.handleAgentNotification("click-agent", {
        type: "error",
        title: "Click me",
        message: "test click",
      });

      expect(instances.length).toBeGreaterThan(0);
      const osNotif = instances[0];

      // Simulate click
      osNotif.emit("click");

      expect(mainWindow.show).toHaveBeenCalled();
      expect(mainWindow.focus).toHaveBeenCalled();

      showSpy.mockRestore();
    });
  });

  // ====================================================================
  // 7. Tray badge
  // ====================================================================
  describe("tray badge", () => {
    it("should set tray badge count when notification is added", () => {
      service.handleAgentNotification("tray-1", { message: "one" });
      expect(trayManager.setNotificationCount).toHaveBeenCalledWith(1);
    });

    it("should update tray badge count as notifications accumulate", () => {
      service.handleAgentNotification("tray-2", { message: "a" });
      service.handleAgentNotification("tray-3", { message: "b" });
      // Last call should reflect 2 unread
      expect(trayManager.setNotificationCount).toHaveBeenLastCalledWith(2);
    });

    it("should set tray badge to 0 after markAllRead", () => {
      service.handleAgentNotification("tray-4", { message: "c" });
      service.markAllRead();
      expect(trayManager.setNotificationCount).toHaveBeenLastCalledWith(0);
    });

    it("should set tray badge to 0 after clearAll", () => {
      service.handleAgentNotification("tray-5", { message: "d" });
      service.clearAll();
      expect(trayManager.setNotificationCount).toHaveBeenLastCalledWith(0);
    });

    it("should not throw when trayManager is null", () => {
      service.trayManager = null;
      expect(() => {
        service.handleAgentNotification("tray-6", { message: "e" });
      }).not.toThrow();
    });
  });

  // ====================================================================
  // 8. Agent event listeners
  // ====================================================================
  describe("agent event listeners", () => {
    it("should forward agent-notification events to handleAgentNotification", () => {
      const spy = jest.spyOn(service, "handleAgentNotification");
      agentProcessManager.emit("agent-notification", "agent-ev-1", {
        type: "info",
        message: "from event",
      });

      expect(spy).toHaveBeenCalledWith("agent-ev-1", {
        type: "info",
        message: "from event",
      });
      spy.mockRestore();
    });

    it("should generate an error notification on status-change with stopped + detail", () => {
      agentProcessManager.emit("status-change", {
        agentId: "crash-agent",
        status: "stopped",
        detail: "Segfault in worker",
      });

      const errorNotif = service.notifications.find(
        (n) => n.type === "error" && n.agentId === "crash-agent"
      );
      expect(errorNotif).toBeDefined();
      expect(errorNotif.title).toBe("Agent Crashed");
      expect(errorNotif.message).toBe("Segfault in worker");
    });

    it("should NOT generate a notification on status-change without detail", () => {
      agentProcessManager.emit("status-change", {
        agentId: "stop-agent",
        status: "stopped",
      });

      // detail is falsy, so the condition (payload.detail) is false
      const errorNotif = service.notifications.find(
        (n) => n.agentId === "stop-agent"
      );
      expect(errorNotif).toBeUndefined();
    });

    it("should NOT generate a notification on status-change with running status", () => {
      agentProcessManager.emit("status-change", {
        agentId: "running-agent",
        status: "running",
        detail: "all good",
      });

      const notif = service.notifications.find(
        (n) => n.agentId === "running-agent"
      );
      expect(notif).toBeUndefined();
    });

    it("should generate an error notification on agent-crash-limit", () => {
      agentProcessManager.emit("agent-crash-limit", "crashy-agent", 5);

      const notif = service.notifications.find(
        (n) => n.agentId === "crashy-agent"
      );
      expect(notif).toBeDefined();
      expect(notif.type).toBe("error");
      expect(notif.title).toBe("Agent Crash Limit Reached");
      expect(notif.message).toContain("crashy-agent");
      expect(notif.message).toContain("5");
    });
  });

  // ====================================================================
  // 9. Persistence
  // ====================================================================
  describe("persistence", () => {
    it("should save notifications to the correct path", () => {
      service.handleAgentNotification("save-1", { message: "persist" });

      const expectedPath = path.join(os.homedir(), ".gaia", "notifications.json");
      expect(fs.writeFileSync).toHaveBeenCalledWith(
        expectedPath,
        expect.any(String),
        "utf8"
      );
    });

    it("should create .gaia directory if it does not exist", () => {
      fs.existsSync.mockReturnValue(false);
      service.handleAgentNotification("save-2", { message: "mkdir" });

      expect(fs.mkdirSync).toHaveBeenCalledWith(
        path.join(os.homedir(), ".gaia"),
        { recursive: true }
      );
    });

    it("should only persist the last MAX_PERSISTED (200) notifications", () => {
      // Pre-fill with 250 notifications
      for (let i = 0; i < 250; i++) {
        service.notifications.push({ id: `notif-save-${i}`, read: false, responded: false });
      }
      fs.writeFileSync.mockClear();

      service._saveNotifications();

      const savedJson = fs.writeFileSync.mock.calls[0][1];
      const savedArray = JSON.parse(savedJson);
      expect(savedArray.length).toBe(200);
      // Should be the last 200 (indices 50-249)
      expect(savedArray[0].id).toBe("notif-save-50");
    });

    it("should handle missing notifications file gracefully", () => {
      fs.existsSync.mockReturnValue(false);
      electronMock.ipcMain._handlers.clear();

      const svc = new NotificationService(mainWindow, agentProcessManager, trayManager);
      expect(svc.notifications).toEqual([]);
      svc.destroy();
    });

    it("should handle corrupt notifications file gracefully", () => {
      fs.existsSync.mockReturnValue(true);
      fs.readFileSync.mockReturnValue("not valid json{{{");
      electronMock.ipcMain._handlers.clear();

      const svc = new NotificationService(mainWindow, agentProcessManager, trayManager);
      expect(svc.notifications).toEqual([]);
      svc.destroy();
    });

    it("should handle readFileSync throwing an error gracefully", () => {
      fs.existsSync.mockReturnValue(true);
      fs.readFileSync.mockImplementation(() => {
        throw new Error("EACCES");
      });
      electronMock.ipcMain._handlers.clear();

      const svc = new NotificationService(mainWindow, agentProcessManager, trayManager);
      expect(svc.notifications).toEqual([]);
      svc.destroy();
    });

    it("should not throw when writeFileSync fails", () => {
      fs.writeFileSync.mockImplementation(() => {
        throw new Error("ENOSPC");
      });

      expect(() => {
        service.handleAgentNotification("save-err", { message: "disk full" });
      }).not.toThrow();
    });
  });

  // ====================================================================
  // 10. IPC handler
  // ====================================================================
  describe("IPC handler", () => {
    it("should invoke _respondToPermission via notification:respond IPC channel", async () => {
      // Create a permission notification first
      service.handleAgentNotification("ipc-agent", {
        type: "permission_request",
        title: "Allow?",
        message: "ipc test",
      });
      const notif = service.notifications[0];

      // Simulate IPC invoke from renderer
      await electronMock.ipcMain.simulateInvoke(
        "notification:respond",
        notif.id,
        "allow",
        true
      );

      expect(notif.responded).toBe(true);
      expect(notif.response).toEqual({ action: "allow", remember: true });
      expect(agentProcessManager._sendJsonRpcRaw).toHaveBeenCalledWith(
        "ipc-agent",
        "notification/response",
        {
          notification_id: notif.id,
          action: "allow",
          remember: true,
        }
      );
    });

    it("should handle IPC respond for non-existent notification without throwing", async () => {
      await expect(
        electronMock.ipcMain.simulateInvoke(
          "notification:respond",
          "notif-does-not-exist",
          "deny",
          false
        )
      ).resolves.not.toThrow();
    });
  });

  // ====================================================================
  // 11. destroy()
  // ====================================================================
  describe("destroy", () => {
    it("should clear all permission timers", () => {
      jest.useFakeTimers();

      service.handleAgentNotification("destroy-1", {
        type: "permission_request",
        title: "A",
        message: "a",
        timeout_seconds: 60,
      });
      service.handleAgentNotification("destroy-2", {
        type: "permission_request",
        title: "B",
        message: "b",
        timeout_seconds: 60,
      });

      expect(Object.keys(service._permissionTimers).length).toBe(2);

      service.destroy();

      expect(service._permissionTimers).toEqual({});

      // Advancing time should not cause any auto-deny calls
      agentProcessManager._sendJsonRpcRaw.mockClear();
      jest.advanceTimersByTime(60 * 1000);
      expect(agentProcessManager._sendJsonRpcRaw).not.toHaveBeenCalled();

      jest.useRealTimers();
    });

    it("should be safe to call destroy() multiple times", () => {
      expect(() => {
        service.destroy();
        service.destroy();
      }).not.toThrow();
    });
  });

  // ====================================================================
  // 12. Edge cases
  // ====================================================================
  describe("edge cases", () => {
    it("should handle null mainWindow gracefully in _sendToRenderer", () => {
      service.mainWindow = null;
      expect(() => {
        service._sendToRenderer("test:channel", { data: "x" });
      }).not.toThrow();
    });

    it("should handle destroyed mainWindow gracefully in _sendToRenderer", () => {
      mainWindow.close(); // marks as destroyed
      expect(() => {
        service._sendToRenderer("test:channel", { data: "y" });
      }).not.toThrow();
      // Should NOT have attempted to send
      // webContents.send may have been called before close, so clear and verify
      mainWindow.webContents.send.mockClear();
      service._sendToRenderer("test:channel", { data: "z" });
      expect(mainWindow.webContents.send).not.toHaveBeenCalled();
    });

    it("should handle null agentProcessManager gracefully during construction", () => {
      electronMock.ipcMain._handlers.clear();
      expect(() => {
        const svc = new NotificationService(mainWindow, null, trayManager);
        svc.destroy();
      }).not.toThrow();
    });

    it("should handle null trayManager gracefully in _updateTrayBadge", () => {
      service.trayManager = null;
      expect(() => {
        service._updateTrayBadge();
      }).not.toThrow();
    });

    it("should handle _sendJsonRpcRaw throwing an error", () => {
      agentProcessManager._sendJsonRpcRaw.mockImplementation(() => {
        throw new Error("Agent process not running");
      });

      service.handleAgentNotification("err-agent", {
        type: "permission_request",
        title: "Allow?",
        message: "will fail",
      });
      const notif = service.notifications[0];

      // Should not throw even though _sendJsonRpcRaw throws
      expect(() => {
        service._respondToPermission(notif.id, "allow", false);
      }).not.toThrow();

      // The notification should still be marked as responded
      expect(notif.responded).toBe(true);
    });

    it("should handle null agentProcessManager gracefully when responding to permission", () => {
      service.agentProcessManager = null;

      service.handleAgentNotification("no-apm", {
        type: "permission_request",
        title: "Allow?",
        message: "no manager",
      });
      // Need to add the notification manually since the apm listener won't fire
      // but handleAgentNotification is called directly here
      const notif = service.notifications[0];

      expect(() => {
        service._respondToPermission(notif.id, "deny", false);
      }).not.toThrow();
      expect(notif.responded).toBe(true);
    });

    it("should handle webContents.send throwing an error", () => {
      mainWindow.webContents.send.mockImplementation(() => {
        throw new Error("WebContents destroyed");
      });

      expect(() => {
        service.handleAgentNotification("wc-err", {
          type: "info",
          message: "send will throw",
        });
      }).not.toThrow();
    });

    it("should handle Notification constructor throwing an error", () => {
      MockNotification.isSupported.mockReturnValue(true);
      // Temporarily make the constructor path throw by making isSupported
      // return true but sabotaging the show method
      const showSpy = jest.spyOn(MockNotification.prototype, "show").mockImplementation(
        function () {
          throw new Error("Notification display failed");
        }
      );

      // The error should be caught inside _showOsToast
      expect(() => {
        service.handleAgentNotification("notif-throw", {
          type: "error",
          title: "Boom",
          message: "throw in show",
        });
      }).not.toThrow();

      showSpy.mockRestore();
    });
  });
});
