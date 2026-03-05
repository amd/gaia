// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import type { SystemStatus } from '../types';
import './SettingsModal.css';

export function SettingsModal() {
    const { setShowSettings, sessions, removeSession } = useChatStore();
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.getSystemStatus()
            .then(setStatus)
            .catch(() => setStatus(null))
            .finally(() => setLoading(false));
    }, []);

    const clearAll = async () => {
        if (!confirm('Delete ALL sessions, messages, and documents? This cannot be undone.')) return;
        for (const s of sessions) {
            await api.deleteSession(s.id).catch(() => {});
            removeSession(s.id);
        }
        setShowSettings(false);
    };

    const version = status?.version || '0.1.0';

    return (
        <div className="modal-overlay" onClick={() => setShowSettings(false)} role="dialog" aria-modal="true" aria-label="Settings">
            <div className="modal-panel settings-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Settings</h3>
                    <button className="btn-icon" onClick={() => setShowSettings(false)} aria-label="Close settings">
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body">
                    {/* System Status */}
                    <section className="settings-section">
                        <h4>System Status</h4>
                        {loading ? (
                            <p className="loading-text">Checking system...</p>
                        ) : status ? (
                            <div className="status-grid">
                                <StatusRow label="Lemonade Server" value={status.lemonade_running ? 'Running' : 'Not Running'} ok={status.lemonade_running} />
                                <StatusRow label="Model" value={status.model_loaded || 'None loaded'} ok={!!status.model_loaded} />
                                <StatusRow label="Embedding Model" value={status.embedding_model_loaded ? 'Available' : 'Not loaded'} ok={status.embedding_model_loaded} />
                                <StatusRow label="Disk Space" value={`${status.disk_space_gb} GB free`} ok={status.disk_space_gb > 5} />
                                <StatusRow label="Memory" value={`${status.memory_available_gb} GB available`} ok={status.memory_available_gb > 2} />
                            </div>
                        ) : (
                            <div className="status-error">
                                <p>Could not connect to server</p>
                                <code>gaia chat ui</code>
                            </div>
                        )}
                    </section>

                    {/* About */}
                    <section className="settings-section">
                        <h4>About</h4>
                        <div className="about-info">
                            <p>GAIA Chat v{version}</p>
                            <p className="about-sub">
                                Privacy-first AI chat for AMD Ryzen AI PCs.
                                <br />
                                No data ever leaves your device.
                            </p>
                        </div>
                    </section>

                    {/* Privacy & Data (danger zone at the bottom) */}
                    <section className="settings-section danger-zone">
                        <h4>Privacy & Data</h4>
                        <div className="setting-row">
                            <span>Data location</span>
                            <code className="setting-path">~/.gaia/chat/</code>
                        </div>
                        <div className="danger-divider" />
                        <div className="setting-actions">
                            <p className="danger-warning">This will permanently delete all sessions, messages, and documents.</p>
                            <button className="btn-danger" onClick={clearAll}>Clear All Data</button>
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
}

function StatusRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
    return (
        <div className="status-row">
            <span className="status-label">{label}</span>
            <span className={`status-value ${ok ? 'ok' : 'warn'}`}>{value}</span>
        </div>
    );
}
