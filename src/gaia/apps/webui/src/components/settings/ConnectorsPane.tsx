// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ConnectorsSection } from '../ConnectorsSection';
import '../ConnectorsSection.css';
import '../SettingsModal.css';

/** Google, Microsoft, GitHub and MCP-server connectors, and what GAIA may use. */
export function ConnectorsPane() {
    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Connectors</h2>
            <ConnectorsSection />
        </div>
    );
}
