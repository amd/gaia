// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { UnsupportedCard } from './UnsupportedCard';
import { respondToInput } from '../../services/api';

/**
 * NeedsInputCard — the answerable `needs_input` surface (#2595).
 *
 * Unlike `needs_confirmation` (stateless, informational, run already over),
 * this is a LIVE mid-run question: the agent is blocked server-side waiting
 * for an answer, and submitting one unblocks the same run. `session_id` and
 * `request_id` travel inside `data` (set by ChatView from the SSE event) so
 * this card can post the answer without any extra context plumbing.
 *
 * Rendering safety: `question`/option labels can carry agent/tool-derived
 * text — render as plain text nodes only, never through a markdown/innerHTML
 * path (mirrors ConfirmationCard's rule for `summary`).
 *
 * Sensitive answers (`sensitive: true`) are masked in the input as the user
 * types and the value is NEVER echoed back into the card's own "answered"
 * state — only a placeholder is shown, so a sensitive answer never lands in
 * visible chat history.
 */

interface InputOption {
    value: string;
    label: string;
    description: string;
}

interface NeedsInputPayload {
    session_id: string;
    request_id: string;
    question: string;
    options: InputOption[];
    allow_free_text: boolean;
    sensitive: boolean;
}

function isNeedsInputPayload(value: unknown): value is NeedsInputPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        typeof v.session_id === 'string' &&
        typeof v.request_id === 'string' &&
        typeof v.question === 'string' &&
        Array.isArray(v.options) &&
        typeof v.allow_free_text === 'boolean' &&
        typeof v.sensitive === 'boolean'
    );
}

type SubmitState =
    | { status: 'pending' }
    | { status: 'submitting' }
    | { status: 'answered' }
    | { status: 'error'; message: string };

const SENSITIVE_PLACEHOLDER = '••••••';

export function NeedsInputCard({ data }: { data: unknown }) {
    const [state, setState] = useState<SubmitState>({ status: 'pending' });
    const [freeText, setFreeText] = useState('');

    if (!isNeedsInputPayload(data)) {
        return <UnsupportedCard variant="invalid" render="needs_input" data={data} />;
    }

    const submitting = state.status === 'submitting';

    const submit = async (value: string) => {
        if (!value.trim() || submitting) return;
        setState({ status: 'submitting' });
        try {
            await respondToInput(data.session_id, data.request_id, value);
            setState({ status: 'answered' });
        } catch (err) {
            setState({
                status: 'error',
                message: err instanceof Error ? err.message : 'Failed to send the answer.',
            });
        }
    };

    if (state.status === 'answered') {
        return (
            <div className="render-needs-input render-needs-input--answered">
                <span className="render-needs-input__answered-note">Answered</span>
            </div>
        );
    }

    return (
        <div className="render-needs-input">
            <div className="render-needs-input__header">
                <span className="render-needs-input__badge">Question</span>
                {data.sensitive && (
                    <span className="render-needs-input__sensitive-badge">Sensitive</span>
                )}
            </div>
            {/* Plain text node only — question text can carry tool-derived content. */}
            <p className="render-needs-input__question">{data.question}</p>
            {data.options.length > 0 && (
                <div className="render-needs-input__options">
                    {data.options.map((opt) => (
                        <button
                            key={opt.value}
                            type="button"
                            className="render-needs-input__option"
                            disabled={submitting}
                            title={opt.description || undefined}
                            onClick={() => submit(opt.value)}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>
            )}
            {data.allow_free_text && (
                <form
                    className="render-needs-input__free-text"
                    onSubmit={(e) => {
                        e.preventDefault();
                        submit(freeText);
                    }}
                >
                    <input
                        type={data.sensitive ? 'password' : 'text'}
                        className="render-needs-input__free-text-input"
                        value={freeText}
                        disabled={submitting}
                        placeholder={
                            data.sensitive
                                ? `Type your answer (hidden as ${SENSITIVE_PLACEHOLDER})`
                                : 'Type your answer…'
                        }
                        onChange={(e) => setFreeText(e.target.value)}
                        autoComplete="off"
                    />
                    <button
                        type="submit"
                        className="render-needs-input__free-text-submit"
                        disabled={submitting || !freeText.trim()}
                    >
                        Send
                    </button>
                </form>
            )}
            {state.status === 'error' && (
                <p className="render-needs-input__error">{state.message}</p>
            )}
        </div>
    );
}
