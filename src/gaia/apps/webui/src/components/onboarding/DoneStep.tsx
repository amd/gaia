// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { PartyPopper } from 'lucide-react';

/** Step 5 — setup complete confirmation. */
export function DoneStep() {
    return (
        <div className="onboarding-body" data-testid="onboarding-done">
            <h1 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <PartyPopper size={24} /> You're all set
            </h1>
            <p className="lede">
                GAIA is ready. Ask a question, drop in a document, or explore the agents in
                the sidebar. Everything runs locally on your machine.
            </p>
        </div>
    );
}
