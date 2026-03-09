// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Integration tests for GAIA Agent UI App (Electron / React+Vite)
 *
 * Validates:
 * - App configuration and structure
 * - React/TypeScript/Vite frontend structure
 * - API client completeness
 * - TypeScript type definitions
 * - Zustand store configuration
 * - React component structure
 * - Privacy-first design elements
 * - Framework compatibility
 */

const path = require('path');
const fs = require('fs');

const CHAT_APP_PATH = path.join(__dirname, '../../src/gaia/apps/webui');
const FRAMEWORK_PATH = path.join(__dirname, '../../src/gaia/electron');

describe('Chat App Integration', () => {

  // ── App Configuration ──────────────────────────────────────────────

  describe('app configuration', () => {
    it('should have valid app.config.json with required fields', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);

      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config).toHaveProperty('name', 'agent-ui');
      expect(config).toHaveProperty('displayName', 'GAIA Agent UI');
      expect(config).toHaveProperty('version');
      expect(config).toHaveProperty('description');
      expect(config).toHaveProperty('window');
    });

    it('should have window dimensions suitable for chat UI', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

      expect(config.window.width).toBeGreaterThanOrEqual(800);
      expect(config.window.height).toBeGreaterThanOrEqual(500);

      if (config.window.minWidth) {
        expect(config.window.minWidth).toBeGreaterThanOrEqual(600);
      }
      if (config.window.minHeight) {
        expect(config.window.minHeight).toBeGreaterThanOrEqual(400);
      }
    });

    it('should have valid package.json', () => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      expect(fs.existsSync(packagePath)).toBe(true);

      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg).toHaveProperty('name');
      expect(pkg).toHaveProperty('version');
    });

    it('should specify devServer port matching backend default (4200)', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

      if (config.devServer) {
        expect(config.devServer.port).toBe(4200);
      }
    });
  });

  // ── React/Vite Project Structure ───────────────────────────────────

  describe('React/Vite project structure', () => {
    it('should have index.html as Vite entry point', () => {
      const htmlPath = path.join(CHAT_APP_PATH, 'index.html');
      expect(fs.existsSync(htmlPath)).toBe(true);
    });

    it('should have vite.config.ts', () => {
      const vitePath = path.join(CHAT_APP_PATH, 'vite.config.ts');
      expect(fs.existsSync(vitePath)).toBe(true);
    });

    it('should have tsconfig.json', () => {
      const tsconfigPath = path.join(CHAT_APP_PATH, 'tsconfig.json');
      expect(fs.existsSync(tsconfigPath)).toBe(true);
    });

    it('should have main.tsx entry point', () => {
      const mainPath = path.join(CHAT_APP_PATH, 'src/main.tsx');
      expect(fs.existsSync(mainPath)).toBe(true);
    });

    it('should have App.tsx root component', () => {
      const appPath = path.join(CHAT_APP_PATH, 'src/App.tsx');
      expect(fs.existsSync(appPath)).toBe(true);
    });

    it('should have API service module', () => {
      const apiPath = path.join(CHAT_APP_PATH, 'src/services/api.ts');
      expect(fs.existsSync(apiPath)).toBe(true);
    });

    it('should have TypeScript type definitions', () => {
      const typesPath = path.join(CHAT_APP_PATH, 'src/types/index.ts');
      expect(fs.existsSync(typesPath)).toBe(true);
    });

    it('should have Zustand store', () => {
      const storePath = path.join(CHAT_APP_PATH, 'src/stores/chatStore.ts');
      expect(fs.existsSync(storePath)).toBe(true);
    });
  });

  // ── React Components ───────────────────────────────────────────────

  describe('React components', () => {
    const requiredComponents = [
      'ChatView',
      'Sidebar',
      'WelcomeScreen',
      'MessageBubble',
    ];

    requiredComponents.forEach(name => {
      it(`should have ${name} component (.tsx)`, () => {
        const componentPath = path.join(CHAT_APP_PATH, `src/components/${name}.tsx`);
        expect(fs.existsSync(componentPath)).toBe(true);
      });

      it(`should have ${name} CSS (.css)`, () => {
        const cssPath = path.join(CHAT_APP_PATH, `src/components/${name}.css`);
        expect(fs.existsSync(cssPath)).toBe(true);
      });
    });
  });

  // ── HTML Entry Point ───────────────────────────────────────────────

  describe('HTML entry point (index.html)', () => {
    let htmlContent;

    beforeAll(() => {
      const htmlPath = path.join(CHAT_APP_PATH, 'index.html');
      htmlContent = fs.readFileSync(htmlPath, 'utf8');
    });

    it('should have proper DOCTYPE', () => {
      expect(htmlContent).toContain('<!DOCTYPE html>');
    });

    it('should have html lang attribute', () => {
      expect(htmlContent).toMatch(/lang="en"/);
    });

    it('should have charset meta tag', () => {
      expect(htmlContent).toContain('charset="UTF-8"');
    });

    it('should have viewport meta tag', () => {
      expect(htmlContent).toContain('viewport');
    });

    it('should have GAIA Agent UI title', () => {
      expect(htmlContent).toContain('GAIA Agent UI');
    });

    it('should have React root div', () => {
      expect(htmlContent).toContain('id="root"');
    });

    it('should load main.tsx as module', () => {
      expect(htmlContent).toContain('type="module"');
      expect(htmlContent).toContain('src="/src/main.tsx"');
    });
  });

  // ── Vite Configuration ─────────────────────────────────────────────

  describe('Vite configuration', () => {
    let viteContent;

    beforeAll(() => {
      const vitePath = path.join(CHAT_APP_PATH, 'vite.config.ts');
      viteContent = fs.readFileSync(vitePath, 'utf8');
    });

    it('should use React plugin', () => {
      expect(viteContent).toContain('react');
      expect(viteContent).toContain('@vitejs/plugin-react');
    });

    it('should proxy /api to the FastAPI backend', () => {
      expect(viteContent).toContain("'/api'");
      expect(viteContent).toContain('localhost:4200');
    });

    it('should NOT use port 4001 (reserved)', () => {
      expect(viteContent).not.toContain('4001');
    });

    it('should set relative base for Electron compatibility', () => {
      expect(viteContent).toContain("base: './'");
    });
  });

  // ── API Service Validation ─────────────────────────────────────────

  describe('API service (src/services/api.ts)', () => {
    let apiContent;

    beforeAll(() => {
      const apiPath = path.join(CHAT_APP_PATH, 'src/services/api.ts');
      apiContent = fs.readFileSync(apiPath, 'utf8');
    });

    it('should define API_BASE using relative /api path', () => {
      expect(apiContent).toContain("'/api'");
    });

    it('should have system status endpoint function', () => {
      expect(apiContent).toContain('getSystemStatus');
      expect(apiContent).toContain('/system/status');
    });

    it('should have health check endpoint function', () => {
      expect(apiContent).toContain('getHealth');
      expect(apiContent).toContain('/health');
    });

    it('should have session CRUD functions', () => {
      expect(apiContent).toContain('listSessions');
      expect(apiContent).toContain('createSession');
      expect(apiContent).toContain('getSession');
      expect(apiContent).toContain('updateSession');
      expect(apiContent).toContain('deleteSession');
    });

    it('should have message retrieval function', () => {
      expect(apiContent).toContain('getMessages');
    });

    it('should have session export function', () => {
      expect(apiContent).toContain('exportSession');
    });

    it('should have streaming chat function with abort support', () => {
      expect(apiContent).toContain('sendMessageStream');
      expect(apiContent).toContain('AbortController');
      expect(apiContent).toContain('getReader');
      expect(apiContent).toContain('TextDecoder');
    });

    it('should parse SSE data format', () => {
      expect(apiContent).toContain("data: ");
      expect(apiContent).toContain('JSON.parse');
    });

    it('should handle streaming event types (chunk, done, error)', () => {
      expect(apiContent).toContain("'chunk'");
      expect(apiContent).toContain("'done'");
      expect(apiContent).toContain("'error'");
      expect(apiContent).toContain('onChunk');
      expect(apiContent).toContain('onDone');
      expect(apiContent).toContain('onError');
    });

    it('should have document management functions', () => {
      expect(apiContent).toContain('listDocuments');
      expect(apiContent).toContain('uploadDocumentByPath');
      expect(apiContent).toContain('deleteDocument');
    });

    it('should have document attachment functions', () => {
      expect(apiContent).toContain('attachDocument');
      expect(apiContent).toContain('detachDocument');
    });

    it('should use proper HTTP methods', () => {
      // apiFetch uses method as first arg: apiFetch('POST', ...), apiFetch('PUT', ...), etc.
      // The SSE streaming code also uses method: 'POST' directly in fetch options.
      expect(apiContent).toContain("'POST'");
      expect(apiContent).toContain("'PUT'");
      expect(apiContent).toContain("'DELETE'");
      expect(apiContent).toContain("'GET'");
    });

    it('should have copyright and license header', () => {
      expect(apiContent).toContain('Copyright');
      expect(apiContent).toContain('SPDX-License-Identifier');
    });
  });

  // ── TypeScript Type Definitions ────────────────────────────────────

  describe('TypeScript types (src/types/index.ts)', () => {
    let typesContent;

    beforeAll(() => {
      const typesPath = path.join(CHAT_APP_PATH, 'src/types/index.ts');
      typesContent = fs.readFileSync(typesPath, 'utf8');
    });

    it('should define Session interface', () => {
      expect(typesContent).toContain('interface Session');
      expect(typesContent).toContain('id: string');
      expect(typesContent).toContain('title: string');
      expect(typesContent).toContain('model: string');
      expect(typesContent).toContain('document_ids: string[]');
    });

    it('should define Message interface', () => {
      expect(typesContent).toContain('interface Message');
      expect(typesContent).toContain('role:');
      expect(typesContent).toContain('content: string');
    });

    it('should define SourceInfo interface', () => {
      expect(typesContent).toContain('interface SourceInfo');
      expect(typesContent).toContain('document_id: string');
      expect(typesContent).toContain('score: number');
    });

    it('should define Document interface', () => {
      expect(typesContent).toContain('interface Document');
      expect(typesContent).toContain('filename: string');
      expect(typesContent).toContain('filepath: string');
      expect(typesContent).toContain('chunk_count: number');
    });

    it('should define SystemStatus interface', () => {
      expect(typesContent).toContain('interface SystemStatus');
      expect(typesContent).toContain('lemonade_running: boolean');
      expect(typesContent).toContain('model_loaded:');
    });

    it('should define StreamEvent interface', () => {
      expect(typesContent).toContain('interface StreamEvent');
      expect(typesContent).toContain("'chunk'");
      expect(typesContent).toContain("'done'");
      expect(typesContent).toContain("'error'");
    });
  });

  // ── Zustand Store Validation ───────────────────────────────────────

  describe('Zustand chat store', () => {
    let storeContent;

    beforeAll(() => {
      const storePath = path.join(CHAT_APP_PATH, 'src/stores/chatStore.ts');
      storeContent = fs.readFileSync(storePath, 'utf8');
    });

    it('should use zustand create', () => {
      expect(storeContent).toContain("from 'zustand'");
      expect(storeContent).toContain('create<');
    });

    it('should manage session state', () => {
      expect(storeContent).toContain('sessions:');
      expect(storeContent).toContain('currentSessionId:');
      expect(storeContent).toContain('setSessions');
      expect(storeContent).toContain('setCurrentSession');
      expect(storeContent).toContain('addSession');
      expect(storeContent).toContain('removeSession');
    });

    it('should manage message state', () => {
      expect(storeContent).toContain('messages:');
      expect(storeContent).toContain('setMessages');
      expect(storeContent).toContain('addMessage');
    });

    it('should manage streaming state', () => {
      expect(storeContent).toContain('isStreaming');
      expect(storeContent).toContain('streamingContent');
      expect(storeContent).toContain('setStreaming');
      expect(storeContent).toContain('appendStreamContent');
      expect(storeContent).toContain('clearStreamContent');
    });

    it('should manage document state', () => {
      expect(storeContent).toContain('documents:');
      expect(storeContent).toContain('setDocuments');
    });

    it('should manage UI state (theme, modals)', () => {
      expect(storeContent).toContain('theme:');
      expect(storeContent).toContain('showDocLibrary');
      expect(storeContent).toContain('showSettings');
      expect(storeContent).toContain('toggleTheme');
    });

    it('should support dark theme via data-theme attribute', () => {
      expect(storeContent).toContain('data-theme');
    });

    it('should persist theme to localStorage', () => {
      expect(storeContent).toContain('localStorage');
      expect(storeContent).toContain('gaia-chat-theme');
    });

    it('should export useChatStore hook', () => {
      expect(storeContent).toContain('export const useChatStore');
    });
  });

  // ── App Component ──────────────────────────────────────────────────

  describe('App root component', () => {
    let appContent;

    beforeAll(() => {
      const appPath = path.join(CHAT_APP_PATH, 'src/App.tsx');
      appContent = fs.readFileSync(appPath, 'utf8');
    });

    it('should import required components', () => {
      expect(appContent).toContain('Sidebar');
      expect(appContent).toContain('ChatView');
      expect(appContent).toContain('WelcomeScreen');
    });

    it('should use Zustand chat store', () => {
      expect(appContent).toContain('useChatStore');
    });

    it('should use API service', () => {
      expect(appContent).toContain("from './services/api'");
    });

    it('should load sessions on mount', () => {
      expect(appContent).toContain('useEffect');
      expect(appContent).toContain('listSessions');
    });

    it('should handle new chat creation', () => {
      expect(appContent).toContain('handleNewChat');
      expect(appContent).toContain('createSession');
    });

    it('should render WelcomeScreen when no session is active', () => {
      expect(appContent).toContain('WelcomeScreen');
      expect(appContent).toContain('currentSessionId');
    });

    it('should render ChatView when a session is active', () => {
      expect(appContent).toContain('ChatView');
      expect(appContent).toContain('sessionId=');
    });

    it('should conditionally render DocumentLibrary', () => {
      expect(appContent).toContain('showDocLibrary');
      expect(appContent).toContain('DocumentLibrary');
    });

    it('should conditionally render SettingsModal', () => {
      expect(appContent).toContain('showSettings');
      expect(appContent).toContain('SettingsModal');
    });
  });

  // ── Package Dependencies ───────────────────────────────────────────

  describe('package dependencies', () => {
    let pkg;

    beforeAll(() => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    });

    it('should have React as dependency', () => {
      expect(pkg.dependencies.react).toBeDefined();
      expect(pkg.dependencies['react-dom']).toBeDefined();
    });

    it('should have Zustand for state management', () => {
      expect(pkg.dependencies.zustand).toBeDefined();
    });

    it('should have lucide-react for icons', () => {
      expect(pkg.dependencies['lucide-react']).toBeDefined();
    });

    it('should have TypeScript as devDependency', () => {
      expect(pkg.devDependencies.typescript).toBeDefined();
    });

    it('should have Vite as devDependency', () => {
      expect(pkg.devDependencies.vite).toBeDefined();
    });

    it('should have Vite React plugin', () => {
      expect(pkg.devDependencies['@vitejs/plugin-react']).toBeDefined();
    });

    it('should have React type definitions', () => {
      expect(pkg.devDependencies['@types/react']).toBeDefined();
      expect(pkg.devDependencies['@types/react-dom']).toBeDefined();
    });

    it('should have build script', () => {
      expect(pkg.scripts.build).toBeDefined();
      expect(pkg.scripts.build).toContain('vite build');
    });

    it('should have dev script', () => {
      expect(pkg.scripts.dev).toBeDefined();
      expect(pkg.scripts.dev).toContain('vite');
    });

    it('should use ES modules (type: module)', () => {
      expect(pkg.type).toBe('module');
    });
  });

  // ── Additional Components ──────────────────────────────────────────

  describe('additional components', () => {
    const additionalComponents = [
      'DocumentLibrary',
      'SettingsModal',
    ];

    additionalComponents.forEach(name => {
      it(`should have ${name} component (.tsx)`, () => {
        const componentPath = path.join(CHAT_APP_PATH, `src/components/${name}.tsx`);
        expect(fs.existsSync(componentPath)).toBe(true);
      });

      it(`should have ${name} CSS (.css)`, () => {
        const cssPath = path.join(CHAT_APP_PATH, `src/components/${name}.css`);
        expect(fs.existsSync(cssPath)).toBe(true);
      });
    });
  });

  // ── main.tsx Entry Point Validation ─────────────────────────────────

  describe('main.tsx entry point', () => {
    let mainContent;

    beforeAll(() => {
      const mainPath = path.join(CHAT_APP_PATH, 'src/main.tsx');
      mainContent = fs.readFileSync(mainPath, 'utf8');
    });

    it('should import React', () => {
      expect(mainContent).toContain("import React");
    });

    it('should use ReactDOM.createRoot', () => {
      expect(mainContent).toContain('createRoot');
      expect(mainContent).toContain("getElementById('root')");
    });

    it('should wrap App in StrictMode', () => {
      expect(mainContent).toContain('StrictMode');
    });

    it('should import global styles', () => {
      expect(mainContent).toContain("./styles/index.css");
    });

    it('should apply saved theme on load', () => {
      expect(mainContent).toContain('gaia-chat-theme');
      expect(mainContent).toContain('data-theme');
    });

    it('should have copyright header', () => {
      expect(mainContent).toContain('Copyright');
      expect(mainContent).toContain('SPDX-License-Identifier');
    });
  });

  // ── Styles ──────────────────────────────────────────────────────────

  describe('styles', () => {
    it('should have global index.css stylesheet', () => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/styles/index.css');
      expect(fs.existsSync(cssPath)).toBe(true);
    });
  });

  // ── Zustand Store Advanced Validation ───────────────────────────────

  describe('Zustand store advanced', () => {
    let storeContent;

    beforeAll(() => {
      const storePath = path.join(CHAT_APP_PATH, 'src/stores/chatStore.ts');
      storeContent = fs.readFileSync(storePath, 'utf8');
    });

    it('should have updateSessionInList for inline editing', () => {
      expect(storeContent).toContain('updateSessionInList');
    });

    it('should clear currentSessionId when active session is removed', () => {
      // removeSession should reset currentSessionId if it matches
      expect(storeContent).toContain('currentSessionId === id ? null');
    });

    it('should clear messages when active session is removed', () => {
      // removeSession should clear messages if removing the active session
      expect(storeContent).toContain('currentSessionId === id ? []');
    });

    it('should have dark theme as default', () => {
      // Store defaults to 'dark' theme (via localStorage or fallback)
      expect(storeContent).toContain("|| 'dark'");
    });

    it('should have setShowDocLibrary and setShowSettings actions', () => {
      expect(storeContent).toContain('setShowDocLibrary');
      expect(storeContent).toContain('setShowSettings');
    });
  });

  // ── App Component Advanced ──────────────────────────────────────────

  describe('App component advanced', () => {
    let appContent;

    beforeAll(() => {
      const appPath = path.join(CHAT_APP_PATH, 'src/App.tsx');
      appContent = fs.readFileSync(appPath, 'utf8');
    });

    it('should have handleNewChatWithPrompt for quick-start prompts', () => {
      expect(appContent).toContain('handleNewChatWithPrompt');
    });

    it('should dispatch custom event for initial prompts', () => {
      expect(appContent).toContain('gaia:send-prompt');
    });

    it('should use useCallback for memoized handlers', () => {
      expect(appContent).toContain('useCallback');
    });

    it('should have copyright header', () => {
      expect(appContent).toContain('Copyright');
      expect(appContent).toContain('SPDX-License-Identifier');
    });
  });

  // ── API Service Error Handling ──────────────────────────────────────

  describe('API service error handling', () => {
    let apiContent;

    beforeAll(() => {
      const apiPath = path.join(CHAT_APP_PATH, 'src/services/api.ts');
      apiContent = fs.readFileSync(apiPath, 'utf8');
    });

    it('should handle AbortError gracefully in streaming', () => {
      expect(apiContent).toContain('AbortError');
    });

    it('should handle missing response body in streaming', () => {
      expect(apiContent).toContain('No response body');
    });

    it('should use content-type JSON headers for POST/PUT', () => {
      expect(apiContent).toContain("'Content-Type': 'application/json'");
    });

    it('should use signal for abort support in streaming fetch', () => {
      expect(apiContent).toContain('signal: controller.signal');
    });
  });

  // ── Package Electron Configuration ──────────────────────────────────

  describe('package Electron configuration', () => {
    let pkg;

    beforeAll(() => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    });

    it('should have main field pointing to Electron entry', () => {
      expect(pkg.main).toBeDefined();
      // main can be .js or .cjs (CommonJS for Electron compatibility with ESM package)
      expect(pkg.main).toMatch(/main\.(c?js)$/);
    });

    it('should have Electron as devDependency', () => {
      expect(pkg.devDependencies.electron).toBeDefined();
    });

    it('should have package script for Electron packaging', () => {
      expect(pkg.scripts.package).toBeDefined();
      expect(pkg.scripts.package).toContain('build');
    });

    it('should have make script for installer creation', () => {
      expect(pkg.scripts.make).toBeDefined();
      expect(pkg.scripts.make).toContain('build');
    });

    it('should have start script for Electron dev', () => {
      expect(pkg.scripts.start).toBeDefined();
      expect(pkg.scripts.start).toContain('electron');
    });

    it('should have Electron Forge CLI as devDependency', () => {
      expect(pkg.devDependencies['@electron-forge/cli']).toBeDefined();
    });

    it('should have Electron Forge config reference', () => {
      expect(pkg.config).toBeDefined();
      expect(pkg.config.forge).toBeDefined();
      // Forge config can be an inline object or a path to external config file
      if (typeof pkg.config.forge === 'string') {
        expect(pkg.config.forge).toContain('forge');
      } else {
        expect(pkg.config.forge.packagerConfig).toBeDefined();
      }
    });

    it('should have Electron Forge makers available', () => {
      // Makers are either inline in config or in the external forge config
      if (typeof pkg.config.forge === 'string') {
        const forgePath = path.join(CHAT_APP_PATH, pkg.config.forge);
        expect(fs.existsSync(forgePath)).toBe(true);
      } else {
        expect(pkg.config.forge.makers).toBeDefined();
        expect(pkg.config.forge.makers.length).toBeGreaterThan(0);
      }
    });
  });

  // ── Security Checks ────────────────────────────────────────────────

  describe('security', () => {
    it('should not contain hardcoded secrets', () => {
      const filesToCheck = [
        'src/services/api.ts',
        'src/stores/chatStore.ts',
        'src/App.tsx',
      ];

      const secretPatterns = [
        /api[_-]?key\s*[:=]\s*["'][^"']+["']/i,
        /secret\s*[:=]\s*["'][^"']+["']/i,
        /password\s*[:=]\s*["'][^"']+["']/i,
      ];

      filesToCheck.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          secretPatterns.forEach(pattern => {
            expect(content).not.toMatch(pattern);
          });
        }
      });
    });

    it('should not reference port 4001 (reserved)', () => {
      const filesToCheck = [
        'src/services/api.ts',
        'vite.config.ts',
        'app.config.json',
      ];

      filesToCheck.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          expect(content).not.toContain(':4001');
        }
      });
    });

    it('should not use eval() or Function constructor in source', () => {
      const tsFiles = [
        'src/services/api.ts',
        'src/stores/chatStore.ts',
        'src/App.tsx',
      ];

      tsFiles.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          expect(content).not.toMatch(/\beval\s*\(/);
          expect(content).not.toMatch(/new\s+Function\s*\(/);
        }
      });
    });
  });

  // ── Responsive Design & Accessibility ─────────────────────────────

  describe('responsive design', () => {
    let indexCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/styles/index.css');
      indexCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have mobile breakpoint at 768px', () => {
      expect(indexCss).toContain('max-width: 768px');
    });

    it('should have tablet breakpoint at 900px', () => {
      expect(indexCss).toContain('max-width: 900px');
    });

    it('should have small mobile breakpoint at 480px', () => {
      expect(indexCss).toContain('max-width: 480px');
    });

    it('should have sidebar toggle button for mobile', () => {
      expect(indexCss).toContain('.sidebar-toggle');
    });

    it('should have sidebar overlay for mobile', () => {
      expect(indexCss).toContain('.sidebar-overlay');
    });

    it('should have focus-visible indicators for accessibility', () => {
      expect(indexCss).toContain(':focus-visible');
    });

    it('should position sidebar fixed on mobile', () => {
      expect(indexCss).toContain('position: fixed');
    });

    it('should have sidebar slide transform', () => {
      expect(indexCss).toContain('translateX(-100%)');
      expect(indexCss).toContain('translateX(0)');
    });
  });

  describe('responsive welcome screen', () => {
    let welcomeCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/WelcomeScreen.css');
      welcomeCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have responsive feature cards (2x2 on mobile)', () => {
      expect(welcomeCss).toContain('repeat(2, 1fr)');
    });

    it('should reduce title font size on mobile', () => {
      expect(welcomeCss).toContain('font-size: 28px');
    });

    it('should stack suggestion chips on small mobile', () => {
      expect(welcomeCss).toContain('flex-direction: column');
    });
  });

  describe('responsive chat view', () => {
    let chatCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/ChatView.css');
      chatCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should hide model badge on mobile', () => {
      expect(chatCss).toContain('display: none');
    });

    it('should reduce padding on mobile', () => {
      expect(chatCss).toMatch(/padding:\s*10px\s+16px/);
    });
  });

  // ── Sidebar Enhancements ──────────────────────────────────────────

  describe('sidebar enhancements', () => {
    let sidebarContent;

    beforeAll(() => {
      const sidebarPath = path.join(CHAT_APP_PATH, 'src/components/Sidebar.tsx');
      sidebarContent = fs.readFileSync(sidebarPath, 'utf8');
    });

    it('should have keyboard accessibility on session items', () => {
      expect(sidebarContent).toContain('role="button"');
      expect(sidebarContent).toContain('tabIndex={0}');
      expect(sidebarContent).toContain('onKeyDown');
    });

    it('should have ARIA labels on sidebar buttons', () => {
      expect(sidebarContent).toContain('aria-label="New Chat"');
      expect(sidebarContent).toContain('aria-label="Document Library"');
      expect(sidebarContent).toContain('aria-label="Settings"');
    });

    it('should have ARIA labels on sessions', () => {
      expect(sidebarContent).toContain('aria-label={`Open chat:');
    });

    it('should have aria-current on active session', () => {
      expect(sidebarContent).toContain('aria-current');
    });

    it('should have delete confirmation flow', () => {
      expect(sidebarContent).toContain('pendingDeleteId');
      // Confirmation UI shows "Delete?" label and "Click to confirm delete" title
      expect(sidebarContent).toMatch(/Click.*confirm.*delete|Delete\?/);
    });

    it('should auto-cancel delete confirmation after timeout', () => {
      expect(sidebarContent).toContain('setTimeout');
      expect(sidebarContent).toContain('3000');
    });

    it('should auto-close sidebar on mobile after selection', () => {
      expect(sidebarContent).toContain('window.innerWidth <= 768');
      expect(sidebarContent).toContain('setSidebarOpen(false)');
    });

    it('should support sidebar open/close class', () => {
      expect(sidebarContent).toContain("sidebarOpen ? 'open' : ''");
    });

    it('should have search with aria-label', () => {
      expect(sidebarContent).toContain('aria-label="Search conversations"');
    });

    it('should have version badge', () => {
      expect(sidebarContent).toContain('version-badge');
      expect(sidebarContent).toContain('__APP_VERSION__');
    });
  });

  describe('sidebar CSS enhancements', () => {
    let sidebarCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/Sidebar.css');
      sidebarCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have delete confirmation style', () => {
      expect(sidebarCss).toContain('.session-delete.confirm');
    });

    it('should have focus-visible style on session items', () => {
      expect(sidebarCss).toContain('.session-item:focus-visible');
    });

    it('should have version badge style', () => {
      expect(sidebarCss).toContain('.version-badge');
    });
  });

  // ── App Sidebar Toggle ────────────────────────────────────────────

  describe('App sidebar toggle', () => {
    let appContent;

    beforeAll(() => {
      const appPath = path.join(CHAT_APP_PATH, 'src/App.tsx');
      appContent = fs.readFileSync(appPath, 'utf8');
    });

    it('should import Menu icon for hamburger', () => {
      expect(appContent).toContain('Menu');
      expect(appContent).toContain('lucide-react');
    });

    it('should have sidebar toggle button', () => {
      expect(appContent).toContain('sidebar-toggle');
      expect(appContent).toContain('toggleSidebar');
    });

    it('should have sidebar overlay for mobile', () => {
      expect(appContent).toContain('sidebar-overlay');
    });

    it('should auto-restore sidebar on resize to desktop', () => {
      expect(appContent).toContain('resize');
      expect(appContent).toContain('innerWidth > 768');
    });

    it('should close sidebar on mobile after creating new chat', () => {
      expect(appContent).toContain('setSidebarOpen(false)');
    });
  });

  // ── Zustand Store UI State ────────────────────────────────────────

  describe('Zustand store UI state', () => {
    let storeContent;

    beforeAll(() => {
      const storePath = path.join(CHAT_APP_PATH, 'src/stores/chatStore.ts');
      storeContent = fs.readFileSync(storePath, 'utf8');
    });

    it('should have sidebarOpen state', () => {
      expect(storeContent).toContain('sidebarOpen');
    });

    it('should have toggleSidebar action', () => {
      expect(storeContent).toContain('toggleSidebar');
    });

    it('should have setSidebarOpen action', () => {
      expect(storeContent).toContain('setSidebarOpen');
    });

    it('should have isLoadingMessages state', () => {
      expect(storeContent).toContain('isLoadingMessages');
    });

    it('should have setLoadingMessages action', () => {
      expect(storeContent).toContain('setLoadingMessages');
    });

    it('should default sidebarOpen based on window width', () => {
      // sidebarOpen uses responsive default: window.innerWidth > 768
      expect(storeContent).toContain('sidebarOpen:');
      expect(storeContent).toMatch(/sidebarOpen.*window\.innerWidth.*768|sidebarOpen:\s*true/);
    });

    it('should default isLoadingMessages to false', () => {
      expect(storeContent).toContain('isLoadingMessages: false');
    });
  });

  // ── ChatView Enhancements ─────────────────────────────────────────

  describe('ChatView enhancements', () => {
    let chatContent;

    beforeAll(() => {
      const chatPath = path.join(CHAT_APP_PATH, 'src/components/ChatView.tsx');
      chatContent = fs.readFileSync(chatPath, 'utf8');
    });

    it('should have empty chat onboarding suggestions', () => {
      expect(chatContent).toContain('EMPTY_SUGGESTIONS');
      expect(chatContent).toContain('Start the conversation');
    });

    it('should have empty chat suggestion chips', () => {
      expect(chatContent).toContain('empty-chat-chip');
      expect(chatContent).toContain('handleSuggestionClick');
    });

    it('should show loading spinner during message fetch', () => {
      expect(chatContent).toContain('isLoadingMessages');
      expect(chatContent).toContain('loading-spinner');
    });

    it('should have drag-and-drop with visual overlay', () => {
      expect(chatContent).toContain('isDragOver');
      expect(chatContent).toContain('drag-overlay');
      expect(chatContent).toContain('Drop files to index');
    });

    it('should auto-upload dropped files (no TODO)', () => {
      expect(chatContent).toContain('uploadDocumentByPath');
      expect(chatContent).not.toContain('TODO: auto-upload');
    });

    it('should have drag active CSS class', () => {
      expect(chatContent).toContain('drag-active');
    });

    it('should handle dragLeave to reset overlay', () => {
      expect(chatContent).toContain('handleDragLeave');
      expect(chatContent).toContain('setIsDragOver(false)');
    });

    it('should have ARIA labels on input and buttons', () => {
      expect(chatContent).toContain('aria-label="Message input"');
      expect(chatContent).toContain('aria-label="Send message"');
      expect(chatContent).toContain('aria-label="Upload document"');
      expect(chatContent).toContain('aria-label="Rename chat"');
      expect(chatContent).toContain('aria-label="Export chat"');
      expect(chatContent).toContain('aria-label="Attach documents"');
    });
  });

  describe('ChatView CSS enhancements', () => {
    let chatCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/ChatView.css');
      chatCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have empty chat state styles', () => {
      expect(chatCss).toContain('.empty-chat');
      expect(chatCss).toContain('.empty-chat-title');
      expect(chatCss).toContain('.empty-chat-chip');
    });

    it('should have drag overlay styles', () => {
      expect(chatCss).toContain('.drag-overlay');
      expect(chatCss).toContain('.drag-active');
    });

    it('should have chat title overflow handling', () => {
      expect(chatCss).toContain('text-overflow: ellipsis');
    });
  });

  // ── MessageBubble Enhancements ────────────────────────────────────

  describe('MessageBubble enhancements', () => {
    let msgContent;

    beforeAll(() => {
      const msgPath = path.join(CHAT_APP_PATH, 'src/components/MessageBubble.tsx');
      msgContent = fs.readFileSync(msgPath, 'utf8');
    });

    it('should detect error messages', () => {
      expect(msgContent).toContain('isErrorContent');
      expect(msgContent).toContain("startsWith('error:')");
    });

    it('should render error banner with AlertTriangle icon', () => {
      expect(msgContent).toContain('AlertTriangle');
      expect(msgContent).toContain('error-banner');
      expect(msgContent).toContain('Something went wrong');
    });

    it('should apply error CSS class to error messages', () => {
      expect(msgContent).toContain('msg-error');
    });

    it('should have copy feedback with Check icon', () => {
      expect(msgContent).toContain("import { Copy, Check");
      expect(msgContent).toContain('copied');
      expect(msgContent).toContain('setCopied(true)');
    });

    it('should reset copy state after timeout', () => {
      expect(msgContent).toContain('setCopied(false)');
      expect(msgContent).toContain('2000');
    });

    it('should show Copied text in copy button', () => {
      expect(msgContent).toContain("'Copied'");
      expect(msgContent).toContain("'Copy'");
    });

    it('should have copy button aria-labels', () => {
      expect(msgContent).toContain('Copied to clipboard');
      expect(msgContent).toContain('Copy code');
    });
  });

  describe('MessageBubble CSS enhancements', () => {
    let msgCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/MessageBubble.css');
      msgCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have error message styles', () => {
      expect(msgCss).toContain('.msg-error');
      expect(msgCss).toContain('.error-banner');
    });

    it('should have red left border for errors', () => {
      expect(msgCss).toContain('border-left: 3px solid #ef4444');
    });

    it('should have error background tint', () => {
      expect(msgCss).toContain('rgba(239, 68, 68');
    });

    it('should have copy feedback green style', () => {
      expect(msgCss).toContain('.code-copy.copied');
      expect(msgCss).toContain('#22c55e');
    });

    it('should have responsive message padding', () => {
      expect(msgCss).toContain('max-width: 768px');
    });
  });

  // ── Settings Modal Enhancements ───────────────────────────────────

  describe('SettingsModal enhancements', () => {
    let settingsContent;

    beforeAll(() => {
      const settingsPath = path.join(CHAT_APP_PATH, 'src/components/SettingsModal.tsx');
      settingsContent = fs.readFileSync(settingsPath, 'utf8');
    });

    it('should use dynamic version from build constant', () => {
      expect(settingsContent).toContain('__APP_VERSION__');
    });

    it('should have ARIA role dialog', () => {
      expect(settingsContent).toContain('role="dialog"');
      expect(settingsContent).toContain('aria-modal="true"');
    });

    it('should have danger zone section at bottom', () => {
      expect(settingsContent).toContain('danger-zone');
      expect(settingsContent).toContain('danger-warning');
    });

    it('should have danger zone warning text', () => {
      expect(settingsContent).toContain('permanently delete all sessions');
    });
  });

  describe('SettingsModal CSS enhancements', () => {
    let settingsCss;

    beforeAll(() => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/components/SettingsModal.css');
      settingsCss = fs.readFileSync(cssPath, 'utf8');
    });

    it('should have danger zone styles', () => {
      expect(settingsCss).toContain('.danger-zone');
      expect(settingsCss).toContain('.danger-divider');
      expect(settingsCss).toContain('.danger-warning');
    });
  });

  // ── Document Library Accessibility ────────────────────────────────

  describe('DocumentLibrary accessibility', () => {
    let docContent;

    beforeAll(() => {
      const docPath = path.join(CHAT_APP_PATH, 'src/components/DocumentLibrary.tsx');
      docContent = fs.readFileSync(docPath, 'utf8');
    });

    it('should have ARIA role dialog', () => {
      expect(docContent).toContain('role="dialog"');
      expect(docContent).toContain('aria-modal="true"');
    });

    it('should have aria-label on file path input', () => {
      expect(docContent).toContain('aria-label="File path to index"');
    });

    it('should have aria-label on close button', () => {
      expect(docContent).toContain('aria-label="Close document library"');
    });

    it('should have aria-label on document delete buttons', () => {
      expect(docContent).toContain('aria-label={`Remove ${doc.filename}`}');
    });
  });

  // ── Vite Build Configuration ──────────────────────────────────────

  describe('Vite build configuration', () => {
    let viteContent;

    beforeAll(() => {
      const vitePath = path.join(CHAT_APP_PATH, 'vite.config.ts');
      viteContent = fs.readFileSync(vitePath, 'utf8');
    });

    it('should define __APP_VERSION__ at build time', () => {
      expect(viteContent).toContain('__APP_VERSION__');
      expect(viteContent).toContain('define');
    });

    it('should read version from version.py', () => {
      expect(viteContent).toContain('version.py');
      expect(viteContent).toContain('__version__');
    });

    it('should have fallback version from package.json', () => {
      expect(viteContent).toContain('package.json');
      expect(viteContent).toContain("'0.0.0'");
    });
  });

  // ── TypeScript Declarations ───────────────────────────────────────

  describe('TypeScript declarations', () => {
    it('should have vite-env.d.ts with __APP_VERSION__ declaration', () => {
      const dtsPath = path.join(CHAT_APP_PATH, 'src/vite-env.d.ts');
      expect(fs.existsSync(dtsPath)).toBe(true);

      const dtsContent = fs.readFileSync(dtsPath, 'utf8');
      expect(dtsContent).toContain('__APP_VERSION__');
      expect(dtsContent).toContain('declare const');
    });
  });

  // ── Framework Compatibility ────────────────────────────────────────

  describe('framework compatibility', () => {
    it('should be discoverable by framework main.js', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);

      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config.name).toBe('agent-ui');
      expect(config.displayName).toBeDefined();
    });

    it('should have framework shared services available', () => {
      const services = [
        'src/services/window-manager.js',
        'src/services/mcp-client.js',
        'src/services/base-ipc-handlers.js',
      ];

      services.forEach(service => {
        const servicePath = path.join(FRAMEWORK_PATH, service);
        expect(fs.existsSync(servicePath)).toBe(true);
      });
    });
  });
});
