// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Lock, Zap, FileText, DollarSign, Terminal } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import './WelcomeScreen.css';

interface WelcomeScreenProps {
    onNewTask: () => void;
    onSendPrompt: (prompt: string) => void;
}

const SUGGESTIONS = [
    'Write a Python function to read a CSV file',
    'Explain how neural networks work in simple terms',
    'Help me write a professional email',
    'What are the key features of AMD Ryzen AI?',
];

export function WelcomeScreen({ onNewTask, onSendPrompt }: WelcomeScreenProps) {
    const { systemStatus } = useChatStore();

    // Determine if a setup hint should be shown to guide first-time users.
    // Only show hints when backend is reachable (systemStatus is not null).
    const notInitialized = systemStatus !== null && !systemStatus.initialized;
    const noModel = systemStatus !== null && systemStatus.lemonade_running && !systemStatus.model_loaded;

    return (
        <main className="welcome">
            <div className="welcome-inner">
                <h1 className="welcome-title">GAIA Agent UI</h1>
                <span className="welcome-version">v{__APP_VERSION__} <span className="beta-badge">BETA</span></span>
                <p className="welcome-sub">
                    Your private AI assistant, running 100% locally on AMD Ryzen AI
                </p>

                <div className="features">
                    <Feature icon={<Lock size={22} />} title="Private" desc="Data stays on your device" />
                    <Feature icon={<Zap size={22} />} title="Fast" desc="NPU acceleration" />
                    <Feature icon={<FileText size={22} />} title="Smart" desc="Document Q&A" />
                    <Feature icon={<DollarSign size={22} />} title="Free" desc="No subscriptions" />
                </div>

                {/* First-run setup hints */}
                {notInitialized && (
                    <div className="welcome-setup-hint">
                        <Terminal size={14} />
                        <span>
                            <strong>First time?</strong> Run <code>gaia init --profile chat</code> in a terminal to
                            install Lemonade Server and download the required AI models (~25&nbsp;GB).
                        </span>
                    </div>
                )}
                {!notInitialized && noModel && (
                    <div className="welcome-setup-hint">
                        <Terminal size={14} />
                        <span>
                            No model loaded. Run <code>gaia init --profile chat</code> to download models (~25&nbsp;GB).
                        </span>
                    </div>
                )}

                <button className="btn-primary start-btn" onClick={onNewTask}>
                    Start a New Task
                </button>

                <div className="suggestions">
                    <span className="suggestions-label">Try asking:</span>
                    <div className="suggestion-chips">
                        {SUGGESTIONS.map((s) => (
                            <button key={s} className="chip" onClick={() => onSendPrompt(s)}>
                                {s}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        </main>
    );
}

function Feature({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
    return (
        <div className="feature-card">
            <div className="feature-icon">{icon}</div>
            <h3>{title}</h3>
            <p>{desc}</p>
        </div>
    );
}
