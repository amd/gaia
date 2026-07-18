// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ShieldCheck, Cpu, Download, MessageSquare } from 'lucide-react';

/** Step 1 — welcome + expectations. Purely presentational. */
export function WelcomeStep() {
    return (
        <div className="onboarding-body" data-testid="onboarding-welcome">
            <h1>Welcome to GAIA</h1>
            <p className="lede">
                Your private AI assistant that runs 100% locally on AMD hardware —
                no data ever leaves your device. Let's get you set up.
            </p>
            <ul className="onboarding-steps-list">
                <li>
                    <span className="num"><Cpu size={15} /></span>
                    Check your hardware
                </li>
                <li>
                    <span className="num"><Download size={15} /></span>
                    Download an AI model
                </li>
                <li>
                    <span className="num"><MessageSquare size={15} /></span>
                    Connect an app (optional) and start chatting
                </li>
            </ul>
            <p className="lede" style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                <ShieldCheck size={16} /> Everything stays on this machine.
            </p>
        </div>
    );
}
