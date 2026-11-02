// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { EngineInstallScreen } from './components/EngineInstallScreen';
import { isInstallScreen } from './utils/engineInstall';
import { purgeLegacyAlwaysAllow } from './stores/notificationStore';
import { applyTheme, markTitleBar, resolveTheme, syncTitleBar, watchTitleBarScrim } from './utils/theme';
import { applyPalette } from './utils/palette';
import { useChatStore } from './stores/chatStore';
import { applyLocale } from './i18n/registry';
import './styles/index.css';
import './styles/palettes.css';
import './styles/motion.css';

// Always-allow tool grants are session-scoped; drop anything an older build
// persisted so a past tick cannot silently approve tools in this session.
purgeLegacyAlwaysAllow();

markTitleBar();

// Paint the saved theme before React mounts; with none saved, follow the OS.
const savedTheme = localStorage.getItem('gaia-chat-theme');
applyTheme(resolveTheme(savedTheme === 'light' || savedTheme === 'dark' ? savedTheme : 'system'));
// Then the saved palette, which reads the theme it is painted in.
const ui = useChatStore.getState();
applyPalette(ui.palette, ui.customColors, ui.theme);
// And the language, so an RTL layout never paints left-to-right first.
applyLocale(ui.locale);
// The window controls follow the language, because it moves the sidebar under them.
syncTitleBar();
watchTitleBarScrim();

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        {isInstallScreen() ? <EngineInstallScreen /> : <App />}
    </React.StrictMode>,
);
