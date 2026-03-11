// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Structural validation tests for the GAIA Agent UI components, stores,
 * types, and services added in the kalin/chat-ui branch.
 *
 * Validates:
 * - File existence for all new components, stores, types, services, and assets
 * - Export patterns and naming conventions
 * - Zustand store structure and consistency
 * - TypeScript type definitions completeness
 * - Electron preload script and build configuration
 * - CSS companion files for styled components
 * - Utility modules and startup scripts
 */

const path = require('path');
const fs = require('fs');

const CHAT_APP_PATH = path.join(__dirname, '../../src/gaia/apps/webui');
const COMPONENTS_PATH = path.join(CHAT_APP_PATH, 'src/components');
const STORES_PATH = path.join(CHAT_APP_PATH, 'src/stores');
const TYPES_PATH = path.join(CHAT_APP_PATH, 'src/types');
const UTILS_PATH = path.join(CHAT_APP_PATH, 'src/utils');
const SERVICES_PATH = path.join(CHAT_APP_PATH, 'services');
const ASSETS_PATH = path.join(CHAT_APP_PATH, 'assets');
const SCRIPTS_PATH = path.join(__dirname, '../../scripts');

/**
 * Helper: read a file as UTF-8 text.
 * @param {string} filePath
 * @returns {string}
 */
function readFile(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. New Component Files Exist
// ═══════════════════════════════════════════════════════════════════════════

describe('Agent UI Structure', () => {

  describe('1. New Component Files Exist', () => {
    const newComponents = [
      { tsx: 'AgentCard.tsx' },
      { tsx: 'AgentChat.tsx', css: 'AgentChat.css' },
      { tsx: 'AgentConfigDialog.tsx' },
      { tsx: 'AgentInstallDialog.tsx', css: 'AgentInstallDialog.css' },
      { tsx: 'AgentManager.tsx', css: 'AgentManager.css' },
      { tsx: 'AgentTerminal.tsx', css: 'AgentTerminal.css' },
      { tsx: 'NotificationCenter.tsx', css: 'NotificationCenter.css' },
      { tsx: 'PermissionManager.tsx', css: 'PermissionManager.css' },
      { tsx: 'PermissionPrompt.tsx', css: 'PermissionPrompt.css' },
    ];

    for (const component of newComponents) {
      it(`should have ${component.tsx}`, () => {
        const tsxPath = path.join(COMPONENTS_PATH, component.tsx);
        expect(fs.existsSync(tsxPath)).toBe(true);
      });

      if (component.css) {
        it(`should have ${component.css}`, () => {
          const cssPath = path.join(COMPONENTS_PATH, component.css);
          expect(fs.existsSync(cssPath)).toBe(true);
        });
      }
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 2. Zustand Stores Exist and Export Correctly
  // ═══════════════════════════════════════════════════════════════════════════

  describe('2. Zustand Stores Exist and Export Correctly', () => {

    describe('agentStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'agentStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useAgentStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useAgentStore/);
      });

      it('should export DEFAULT_AGENT_CONFIG', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+DEFAULT_AGENT_CONFIG/);
      });

      it('should export selectSortedAgents', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectSortedAgents/);
      });

      it('should export selectRunningCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectRunningCount/);
      });

      it('should export selectInstallingCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectInstallingCount/);
      });
    });

    describe('agentChatStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'agentChatStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useAgentChatStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useAgentChatStore/);
      });

      it('should export selectActiveSession', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectActiveSession/);
      });

      it('should export selectAgentMessages', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectAgentMessages/);
      });

      it('should export selectIsWaiting', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectIsWaiting/);
      });

      it('should export selectInputText', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectInputText/);
      });

      it('should export selectTotalMessageCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectTotalMessageCount/);
      });
    });

    describe('notificationStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'notificationStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useNotificationStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useNotificationStore/);
      });

      it('should export selectUnreadCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectUnreadCount/);
      });

      it('should export selectPendingPermissions', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectPendingPermissions/);
      });

      it('should export selectVisibleNotifications', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectVisibleNotifications/);
      });

      it('should export selectActivePermissionPrompt', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectActivePermissionPrompt/);
      });
    });

    describe('permissionStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'permissionStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export usePermissionStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+usePermissionStore/);
      });

      it('should export selectAgentPermissions', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectAgentPermissions/);
      });

      it('should export selectOverrideCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectOverrideCount/);
      });
    });

    describe('auditStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'auditStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useAuditStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useAuditStore/);
      });

      it('should export applyFilters', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+applyFilters/);
      });

      it('should export selectFilteredEntries', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectFilteredEntries/);
      });

      it('should export selectUniqueAgents', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectUniqueAgents/);
      });

      it('should export selectUniqueTools', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectUniqueTools/);
      });

      it('should export AuditFilters interface', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+interface\s+AuditFilters/);
      });
    });

    describe('terminalStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'terminalStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useTerminalStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useTerminalStore/);
      });

      it('should export selectFilteredLines', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+function\s+selectFilteredLines/);
      });

      it('should export selectLineCount', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectLineCount/);
      });

      it('should export selectIsPaused', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectIsPaused/);
      });
    });

    describe('systemStore.ts', () => {
      const filePath = path.join(STORES_PATH, 'systemStore.ts');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export useSystemStore', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+useSystemStore/);
      });

      it('should export selectCpuHistory', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectCpuHistory/);
      });

      it('should export selectMemoryHistory', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectMemoryHistory/);
      });

      it('should export selectGpuAvailable', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/export\s+const\s+selectGpuAvailable/);
      });
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 3. Type Definitions Complete
  // ═══════════════════════════════════════════════════════════════════════════

  describe('3. Type Definitions Complete', () => {
    const filePath = path.join(TYPES_PATH, 'agent.ts');

    it('should exist', () => {
      expect(fs.existsSync(filePath)).toBe(true);
    });

    // Agent types
    const agentTypes = [
      'AgentInfo',
      'AgentStatus',
      'AgentInstallState',
      'AgentInstallProgress',
    ];

    for (const typeName of agentTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Terminal types
    const terminalTypes = [
      'TerminalLine',
      'TerminalTab',
      'TerminalLineType',
    ];

    for (const typeName of terminalTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // JSON-RPC types
    const rpcTypes = [
      'JsonRpcRequest',
      'JsonRpcResponse',
      'JsonRpcNotification',
      'JsonRpcMessage',
    ];

    for (const typeName of rpcTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Notification types
    const notificationTypes = [
      'GaiaNotification',
      'NotificationType',
      'NotificationPriority',
    ];

    for (const typeName of notificationTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Permission types
    const permissionTypes = [
      'PermissionTier',
      'ToolPermission',
      'AgentPermissions',
    ];

    for (const typeName of permissionTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Config types
    const configTypes = [
      'AgentConfig',
      'TrayConfig',
    ];

    for (const typeName of configTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Chat types
    const chatTypes = [
      'AgentChatMessage',
      'AgentToolCall',
      'AgentChatSession',
      'QuickAction',
    ];

    for (const typeName of chatTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Audit type
    it('should define AuditEntry', () => {
      const content = readFile(filePath);
      expect(content).toContain('AuditEntry');
    });

    // System metrics types
    const systemTypes = [
      'ProcessInfo',
      'SystemMetrics',
    ];

    for (const typeName of systemTypes) {
      it(`should define ${typeName}`, () => {
        const content = readFile(filePath);
        expect(content).toContain(typeName);
      });
    }

    // Electron API type
    it('should define GaiaElectronAPI', () => {
      const content = readFile(filePath);
      expect(content).toContain('GaiaElectronAPI');
    });

    // All types should be exported with export keyword
    it('should use export for all interface and type declarations', () => {
      const content = readFile(filePath);
      const exportedTypes = [
        'AgentInfo', 'AgentStatus', 'AgentInstallState', 'AgentInstallProgress',
        'TerminalLine', 'TerminalTab', 'TerminalLineType',
        'JsonRpcRequest', 'JsonRpcResponse', 'JsonRpcNotification', 'JsonRpcMessage',
        'GaiaNotification', 'NotificationType', 'NotificationPriority',
        'PermissionTier', 'ToolPermission', 'AgentPermissions',
        'AgentConfig', 'TrayConfig',
        'AgentChatMessage', 'AgentToolCall', 'AgentChatSession', 'QuickAction',
        'AuditEntry',
        'ProcessInfo', 'SystemMetrics',
        'GaiaElectronAPI',
      ];
      for (const typeName of exportedTypes) {
        // Should match export interface/type/export type = patterns
        const exportPattern = new RegExp(`export\\s+(interface|type)\\s+${typeName}\\b`);
        expect(content).toMatch(exportPattern);
      }
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 4. Electron Services
  // ═══════════════════════════════════════════════════════════════════════════

  describe('4. Electron Services', () => {

    describe('agent-process-manager.js', () => {
      const filePath = path.join(SERVICES_PATH, 'agent-process-manager.js');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export AgentProcessManager class', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/class\s+AgentProcessManager/);
        expect(content).toMatch(/module\.exports\s*=\s*AgentProcessManager/);
      });

      it('should extend EventEmitter', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/class\s+AgentProcessManager\s+extends\s+EventEmitter/);
      });

      it('should implement startAgent method', () => {
        const content = readFile(filePath);
        expect(content).toContain('async startAgent(');
      });

      it('should implement stopAgent method', () => {
        const content = readFile(filePath);
        expect(content).toContain('async stopAgent(');
      });

      it('should implement restartAgent method', () => {
        const content = readFile(filePath);
        expect(content).toContain('async restartAgent(');
      });

      it('should implement sendJsonRpc method', () => {
        const content = readFile(filePath);
        expect(content).toContain('sendJsonRpc(');
      });

      it('should register IPC handlers', () => {
        const content = readFile(filePath);
        expect(content).toContain('_registerIpcHandlers');
        expect(content).toContain('ipcMain.handle');
      });

      it('should use JSON-RPC shutdown protocol for cross-platform compatibility', () => {
        const content = readFile(filePath);
        expect(content).toContain('"shutdown"');
      });

      it('should use "ping" for health checks (not "initialize")', () => {
        const content = readFile(filePath);
        expect(content).toContain('"ping"');
      });
    });

    describe('notification-service.js', () => {
      const filePath = path.join(SERVICES_PATH, 'notification-service.js');

      it('should exist', () => {
        expect(fs.existsSync(filePath)).toBe(true);
      });

      it('should export NotificationService class', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/class\s+NotificationService/);
        expect(content).toMatch(/module\.exports\s*=\s*NotificationService/);
      });

      it('should extend EventEmitter', () => {
        const content = readFile(filePath);
        expect(content).toMatch(/class\s+NotificationService\s+extends\s+EventEmitter/);
      });

      it('should implement handleAgentNotification method', () => {
        const content = readFile(filePath);
        expect(content).toContain('handleAgentNotification(');
      });

      it('should implement permission response handling', () => {
        const content = readFile(filePath);
        expect(content).toContain('_respondToPermission');
      });

      it('should register IPC handlers for notification:respond', () => {
        const content = readFile(filePath);
        expect(content).toContain('"notification:respond"');
      });
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 5. Preload Script
  // ═══════════════════════════════════════════════════════════════════════════

  describe('5. Preload Script', () => {
    const filePath = path.join(CHAT_APP_PATH, 'preload.cjs');

    it('should exist', () => {
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('should use contextBridge', () => {
      const content = readFile(filePath);
      expect(content).toContain('contextBridge');
    });

    it('should expose gaiaAPI via exposeInMainWorld', () => {
      const content = readFile(filePath);
      expect(content).toMatch(/contextBridge\.exposeInMainWorld\s*\(\s*["']gaiaAPI["']/);
    });

    it('should expose agent namespace', () => {
      const content = readFile(filePath);
      expect(content).toContain('agent:');
      // Verify key agent IPC channels
      expect(content).toContain('"agent:start"');
      expect(content).toContain('"agent:stop"');
      expect(content).toContain('"agent:restart"');
      expect(content).toContain('"agent:status"');
      expect(content).toContain('"agent:status-all"');
      expect(content).toContain('"agent:send-rpc"');
    });

    it('should expose tray namespace', () => {
      const content = readFile(filePath);
      expect(content).toContain('tray:');
      expect(content).toContain('"tray:get-config"');
      expect(content).toContain('"tray:set-config"');
    });

    it('should expose notification namespace', () => {
      const content = readFile(filePath);
      expect(content).toContain('notification:');
      expect(content).toContain('"notification:permission-request"');
      expect(content).toContain('"notification:respond"');
      expect(content).toContain('"notification:new"');
    });

    it('should have AMD copyright header', () => {
      const content = readFile(filePath);
      expect(content).toContain('Copyright(C)');
      expect(content).toContain('Advanced Micro Devices');
      expect(content).toContain('SPDX-License-Identifier: MIT');
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 6. Utility Files
  // ═══════════════════════════════════════════════════════════════════════════

  describe('6. Utility Files', () => {
    const filePath = path.join(UTILS_PATH, 'format.ts');

    it('should exist', () => {
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('should export formatSize', () => {
      const content = readFile(filePath);
      expect(content).toMatch(/export\s+function\s+formatSize/);
    });

    it('should export formatDuration', () => {
      const content = readFile(filePath);
      expect(content).toMatch(/export\s+function\s+formatDuration/);
    });

    it('should export formatTimeHMS', () => {
      const content = readFile(filePath);
      expect(content).toMatch(/export\s+function\s+formatTimeHMS/);
    });

    it('should have AMD copyright header', () => {
      const content = readFile(filePath);
      expect(content).toContain('Copyright(C)');
      expect(content).toContain('Advanced Micro Devices');
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 7. Assets for Tray
  // ═══════════════════════════════════════════════════════════════════════════

  describe('7. Assets for Tray', () => {
    const requiredAssets = [
      'tray-icon.png',
      'tray-icon.ico',
      'tray-iconTemplate.png',
    ];

    for (const asset of requiredAssets) {
      it(`should have ${asset}`, () => {
        const assetPath = path.join(ASSETS_PATH, asset);
        expect(fs.existsSync(assetPath)).toBe(true);
      });
    }

    it('should have non-empty tray-icon.png', () => {
      const assetPath = path.join(ASSETS_PATH, 'tray-icon.png');
      const stats = fs.statSync(assetPath);
      expect(stats.size).toBeGreaterThan(0);
    });

    it('should have non-empty tray-icon.ico', () => {
      const assetPath = path.join(ASSETS_PATH, 'tray-icon.ico');
      const stats = fs.statSync(assetPath);
      expect(stats.size).toBeGreaterThan(0);
    });

    it('should have retina assets for macOS', () => {
      // @2x variants for HiDPI macOS displays
      const retinaAssets = [
        'tray-icon@2x.png',
        'tray-iconTemplate@2x.png',
      ];
      for (const asset of retinaAssets) {
        const assetPath = path.join(ASSETS_PATH, asset);
        expect(fs.existsSync(assetPath)).toBe(true);
      }
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 8. Build Configuration
  // ═══════════════════════════════════════════════════════════════════════════

  describe('8. Build Configuration', () => {

    it('should have forge.config.cjs', () => {
      const filePath = path.join(CHAT_APP_PATH, 'forge.config.cjs');
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('forge.config.cjs should export packagerConfig and makers', () => {
      const content = readFile(path.join(CHAT_APP_PATH, 'forge.config.cjs'));
      expect(content).toContain('packagerConfig');
      expect(content).toContain('makers');
      expect(content).toContain('module.exports');
    });

    it('should have main.cjs', () => {
      const filePath = path.join(CHAT_APP_PATH, 'main.cjs');
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('main.cjs should reference preload.cjs', () => {
      const content = readFile(path.join(CHAT_APP_PATH, 'main.cjs'));
      expect(content).toContain('preload.cjs');
    });

    it('main.cjs should initialize services (AgentProcessManager, NotificationService)', () => {
      const content = readFile(path.join(CHAT_APP_PATH, 'main.cjs'));
      expect(content).toContain('AgentProcessManager');
      expect(content).toContain('NotificationService');
    });

    it('main.cjs should reference services directory', () => {
      const content = readFile(path.join(CHAT_APP_PATH, 'main.cjs'));
      expect(content).toContain('./services/agent-process-manager');
      expect(content).toContain('./services/notification-service');
    });

    it('package.json should have main entry pointing to main.cjs', () => {
      const pkg = JSON.parse(readFile(path.join(CHAT_APP_PATH, 'package.json')));
      expect(pkg.main).toBe('main.cjs');
    });

    it('package.json should reference forge config', () => {
      const pkg = JSON.parse(readFile(path.join(CHAT_APP_PATH, 'package.json')));
      expect(pkg.config).toBeDefined();
      expect(pkg.config.forge).toBe('./forge.config.cjs');
    });

    it('package.json should include zustand as a dependency', () => {
      const pkg = JSON.parse(readFile(path.join(CHAT_APP_PATH, 'package.json')));
      expect(pkg.dependencies).toHaveProperty('zustand');
    });

    it('package.json should include electron as a devDependency', () => {
      const pkg = JSON.parse(readFile(path.join(CHAT_APP_PATH, 'package.json')));
      expect(pkg.devDependencies).toHaveProperty('electron');
    });

    it('package.json files array should include preload.cjs and services/', () => {
      const pkg = JSON.parse(readFile(path.join(CHAT_APP_PATH, 'package.json')));
      expect(pkg.files).toContain('preload.cjs');
      expect(pkg.files).toContain('services/');
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 9. CSS Files for New Components
  // ═══════════════════════════════════════════════════════════════════════════

  describe('9. CSS Files for New Components', () => {
    const cssFiles = [
      'AgentChat.css',
      'AgentInstallDialog.css',
      'AgentManager.css',
      'AgentTerminal.css',
      'NotificationCenter.css',
      'PermissionManager.css',
      'PermissionPrompt.css',
    ];

    for (const cssFile of cssFiles) {
      it(`${cssFile} should exist and be non-empty`, () => {
        const cssPath = path.join(COMPONENTS_PATH, cssFile);
        expect(fs.existsSync(cssPath)).toBe(true);
        const content = readFile(cssPath);
        expect(content.trim().length).toBeGreaterThan(0);
      });
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 10. Store Consistency
  // ═══════════════════════════════════════════════════════════════════════════

  describe('10. Store Consistency', () => {
    const storeFiles = [
      'agentStore.ts',
      'agentChatStore.ts',
      'notificationStore.ts',
      'permissionStore.ts',
      'auditStore.ts',
      'terminalStore.ts',
      'systemStore.ts',
    ];

    for (const storeFile of storeFiles) {
      describe(storeFile, () => {
        const filePath = path.join(STORES_PATH, storeFile);

        it('should import create from zustand', () => {
          const content = readFile(filePath);
          expect(content).toMatch(/import\s+\{\s*create\s*\}\s+from\s+['"]zustand['"]/);
        });

        it('should not use Map<string, T> for state fields (Zustand devtools incompatible)', () => {
          const content = readFile(filePath);
          expect(content).not.toMatch(/:\s*Map<string,/);
        });

        it('should have AMD copyright header', () => {
          const content = readFile(filePath);
          expect(content).toContain('Copyright(C)');
          expect(content).toContain('Advanced Micro Devices');
          expect(content).toContain('SPDX-License-Identifier: MIT');
        });
      });
    }

    // Stores with keyed data should use Record<string, T> (not Map) for devtools compatibility
    const keyedStores = [
      'agentStore.ts',
      'agentChatStore.ts',
      'permissionStore.ts',
      'terminalStore.ts',
    ];

    for (const storeFile of keyedStores) {
      it(`${storeFile} should use Record<string, T> for keyed state`, () => {
        const content = readFile(path.join(STORES_PATH, storeFile));
        expect(content).toContain('Record<string,');
      });
    }

    it('agentStore should import from agentChatStore (cross-store action)', () => {
      const content = readFile(path.join(STORES_PATH, 'agentStore.ts'));
      expect(content).toMatch(/import\s+.*agentChatStore/);
    });

    it('agentChatStore should NOT import from agentStore (prevents circular dependency)', () => {
      const content = readFile(path.join(STORES_PATH, 'agentChatStore.ts'));
      expect(content).not.toMatch(/import\s+.*agentStore/);
    });

    it('no other stores should import from each other (isolation)', () => {
      // The only allowed cross-store import is agentStore -> agentChatStore
      const isolatedStores = [
        'notificationStore.ts',
        'permissionStore.ts',
        'auditStore.ts',
        'terminalStore.ts',
        'systemStore.ts',
      ];

      for (const storeFile of isolatedStores) {
        const content = readFile(path.join(STORES_PATH, storeFile));
        const otherStores = storeFiles.filter((f) => f !== storeFile);
        for (const otherStore of otherStores) {
          const otherStoreName = otherStore.replace('.ts', '');
          // Allow type imports (import type { ... } from './otherStore') but
          // not runtime imports. In practice, none of these stores import from
          // each other, so a simple check suffices.
          const runtimeImportPattern = new RegExp(
            `import\\s+\\{[^}]*\\}\\s+from\\s+['"]\\.\\/${otherStoreName}['"]`
          );
          expect(content).not.toMatch(runtimeImportPattern);
        }
      }
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 11. Scripts
  // ═══════════════════════════════════════════════════════════════════════════

  describe('11. Scripts', () => {

    it('should have start-agent-ui.sh', () => {
      const filePath = path.join(SCRIPTS_PATH, 'start-agent-ui.sh');
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('should have start-agent-ui.ps1', () => {
      const filePath = path.join(SCRIPTS_PATH, 'start-agent-ui.ps1');
      expect(fs.existsSync(filePath)).toBe(true);
    });

    it('start-agent-ui.sh should be non-empty', () => {
      const filePath = path.join(SCRIPTS_PATH, 'start-agent-ui.sh');
      const content = readFile(filePath);
      expect(content.trim().length).toBeGreaterThan(0);
    });

    it('start-agent-ui.ps1 should be non-empty', () => {
      const filePath = path.join(SCRIPTS_PATH, 'start-agent-ui.ps1');
      const content = readFile(filePath);
      expect(content.trim().length).toBeGreaterThan(0);
    });
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // 12. Component Export Patterns
  // ═══════════════════════════════════════════════════════════════════════════

  describe('12. Component Export Patterns', () => {
    const newComponentFiles = [
      { file: 'AgentCard.tsx', name: 'AgentCard' },
      { file: 'AgentChat.tsx', name: 'AgentChat' },
      { file: 'AgentConfigDialog.tsx', name: 'AgentConfigDialog' },
      { file: 'AgentInstallDialog.tsx', name: 'AgentInstallDialog' },
      { file: 'AgentManager.tsx', name: 'AgentManager' },
      { file: 'AgentTerminal.tsx', name: 'AgentTerminal' },
      { file: 'NotificationCenter.tsx', name: 'NotificationCenter' },
      { file: 'PermissionManager.tsx', name: 'PermissionManager' },
      { file: 'PermissionPrompt.tsx', name: 'PermissionPrompt' },
    ];

    for (const { file, name } of newComponentFiles) {
      it(`${file} should export a named function component "${name}"`, () => {
        const content = readFile(path.join(COMPONENTS_PATH, file));
        // Should have a named export (either export function X or export const X = memo(...))
        const namedExportPattern = new RegExp(
          `export\\s+(function\\s+${name}|const\\s+${name}\\s*=)`
        );
        expect(content).toMatch(namedExportPattern);
      });

      it(`${file} should NOT use default export`, () => {
        const content = readFile(path.join(COMPONENTS_PATH, file));
        expect(content).not.toMatch(/export\s+default/);
      });

      it(`${file} should import from react`, () => {
        const content = readFile(path.join(COMPONENTS_PATH, file));
        expect(content).toMatch(/import\s+.*from\s+['"]react['"]/);
      });

      it(`${file} should have AMD copyright header`, () => {
        const content = readFile(path.join(COMPONENTS_PATH, file));
        expect(content).toContain('Copyright(C)');
        expect(content).toContain('Advanced Micro Devices');
        expect(content).toContain('SPDX-License-Identifier: MIT');
      });
    }
  });

});
