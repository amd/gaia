// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { purgeLegacyAlwaysAllow } from './stores/notificationStore';
import { applyTheme, resolveTheme } from './utils/theme';
import './styles/index.css';
import './styles/motion.css';

// Always-allow tool grants are session-scoped; drop anything an older build
// persisted so a past tick cannot silently approve tools in this session.
purgeLegacyAlwaysAllow();

// Paint the saved theme before React mounts; with none saved, follow the OS.
const savedTheme = localStorage.getItem('gaia-chat-theme');
applyTheme(resolveTheme(savedTheme === 'light' || savedTheme === 'dark' ? savedTheme : 'system'));

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
);
