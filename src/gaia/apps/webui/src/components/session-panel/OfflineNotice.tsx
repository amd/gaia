// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { WifiOff } from 'lucide-react';
import * as api from '../../services/api';
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import './SessionPanel.css';

/** Above the composer while the chat's internet is off (#4608), with a way back on. */
export function OfflineNotice({ sessionId }: { sessionId: string }) {
    const { env, change } = useSessionEnvironment(sessionId);
    const [error, setError] = useState<string | null>(null);
    if (!env || env.internet) return null;
    const turnOn = () => {
        setError(null);
        change(() => api.setInternet(sessionId, true)).catch((err: unknown) => {
            setError(err instanceof api.ApiError ? err.detail || err.message : String(err));
        });
    };
    return (
        <div className="offline-notice" role="status">
            <WifiOff size={13} aria-hidden="true" />
            <span>
                Offline: internet is off for this chat, so GAIA won't search the web or open pages.
                {error && <span className="offline-notice-error"> Couldn't turn it on: {error}</span>}
            </span>
            <button type="button" className="link-btn" onClick={turnOn}>Turn on</button>
        </div>
    );
}
