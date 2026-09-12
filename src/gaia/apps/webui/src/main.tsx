// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { purgeLegacyAlwaysAllow } from './stores/notificationStore';
import './styles/index.css';

// Always-allow tool grants are session-scoped; drop anything an older build
// persisted so a past tick cannot silently approve tools in this session.
purgeLegacyAlwaysAllow();

// Apply saved theme (default to dark)
const savedTheme = localStorage.getItem('gaia-chat-theme');
if (savedTheme !== 'light') {
    document.documentElement.setAttribute('data-theme', 'dark');
}

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
);
