// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Cloud, HelpCircle, Lock } from 'lucide-react';
import type { Session } from '../types';

export type InferenceLocation = Pick<
    Session,
    'inference_remote' | 'inference_provider_name' | 'inference_description'
>;

/**
 * Location for UI outside a chat: the open session, else the most recent one
 * (the model override is global, so it answers for every session). Undefined
 * when there is no session to ask.
 */
export function selectInferenceLocation(
    sessions: Session[],
    currentSessionId: string | null,
): InferenceLocation | undefined {
    return sessions.find((s) => s.id === currentSessionId) ?? sessions[0];
}

/** Footer line saying where this chat is answered; claims "local" only when the backend says so. */
export function InferenceLocationBadge({ session }: { session?: InferenceLocation | null }) {
    const remote = session?.inference_remote;
    const description = session?.inference_description ?? undefined;

    if (remote === false) {
        return (
            <span
                className="input-footer-item"
                data-testid="inference-location"
                title={description ?? 'Chat runs through Lemonade on this machine.'}
            >
                <Lock size={10} />
                <span>100% local &amp; private</span>
            </span>
        );
    }

    if (remote === true) {
        const provider = session?.inference_provider_name || 'a cloud provider';
        return (
            <span
                className="input-footer-item"
                data-testid="inference-location"
                title={
                    description
                    ?? `Chat inference runs on ${provider}, a cloud provider. `
                        + 'Everything in this conversation is sent there to be answered.'
                }
            >
                <Cloud size={10} />
                <span>Sent to {provider}</span>
            </span>
        );
    }

    return (
        <span
            className="input-footer-item"
            data-testid="inference-location"
            title="GAIA can't tell whether this chat is answered on this machine or by a cloud provider, so it isn't claiming either."
        >
            <HelpCircle size={10} />
            <span>Model location unknown</span>
        </span>
    );
}
