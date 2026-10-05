// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react';
import { ArrowUp, FileText, Plus, Square, X } from 'lucide-react';
import type { UseAttachments } from '../hooks/useAttachments';
import './Composer.css';

interface ComposerProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onStop?: () => void;
    streaming?: boolean;
    /** Stop was pressed and the run is winding down. */
    stopping?: boolean;
    /** Blocks typing and sending, with the reason shown as the placeholder. */
    disabledReason?: string | null;
    attachments: UseAttachments;
    placeholder?: string;
    autoFocus?: boolean;
    inputRef?: RefObject<HTMLTextAreaElement | null>;
    /** Small inline controls on the left of the bottom row (permission mode). */
    leftControls?: ReactNode;
    /** Small inline controls before the send button (model). */
    rightControls?: ReactNode;
}

const MAX_INPUT_HEIGHT = 240;

export function Composer({
    value, onChange, onSubmit, onStop, streaming = false, stopping = false, disabledReason, attachments,
    placeholder = 'Ask GAIA anything', autoFocus, inputRef, leftControls, rightControls,
}: ComposerProps) {
    const ownRef = useRef<HTMLTextAreaElement | null>(null);
    const textareaRef = inputRef ?? ownRef;
    const fileRef = useRef<HTMLInputElement>(null);
    const disabled = !!disabledReason;
    const canSend = !disabled && !streaming && !attachments.uploading
        && (value.trim().length > 0 || attachments.hasUploaded);

    // Grow with the text, up to a cap; shrink back when it is cleared.
    useLayoutEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
    }, [value, textareaRef]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSubmit();
        }
    }, [canSend, onSubmit]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        if (!e.dataTransfer?.files?.length) return;
        e.preventDefault();
        e.stopPropagation();
        attachments.addFiles(Array.from(e.dataTransfer.files));
    }, [attachments]);

    return (
        <div
            className={`composer${disabled ? ' composer-disabled' : ''}`}
            onDrop={handleDrop}
            onDragOver={(e) => { if (e.dataTransfer?.types?.includes('Files')) e.preventDefault(); }}
        >
            {attachments.attachments.length > 0 && (
                <ul className="composer-attachments" aria-label="Attached files">
                    {attachments.attachments.map((a) => (
                        <li key={a.id} className={`composer-attachment${a.error ? ' has-error' : ''}`}>
                            {a.isImage && a.url
                                ? <img src={a.url} alt="" className="composer-attachment-thumb" />
                                : <FileText size={14} aria-hidden="true" />}
                            <span className="composer-attachment-name" title={a.name}>{a.name}</span>
                            {a.uploading && <span className="composer-attachment-state">Uploading…</span>}
                            {a.error && <span className="composer-attachment-state" role="alert">{a.error}</span>}
                            <button
                                type="button"
                                className="composer-icon-btn"
                                onClick={() => attachments.remove(a.id)}
                                aria-label={`Remove ${a.name}`}
                            >
                                <X size={12} />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            <textarea
                ref={textareaRef}
                className="composer-input"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={(e) => { attachments.handlePaste(e); }}
                placeholder={disabledReason || placeholder}
                rows={1}
                disabled={disabled}
                autoFocus={autoFocus}
                aria-label="Message"
            />
            <div className="composer-row">
                <div className="composer-row-left">
                    <button
                        type="button"
                        className="composer-icon-btn"
                        onClick={() => fileRef.current?.click()}
                        disabled={disabled}
                        aria-label="Attach files"
                        title="Attach files"
                    >
                        <Plus size={16} />
                    </button>
                    <input
                        ref={fileRef}
                        type="file"
                        multiple
                        hidden
                        onChange={(e) => {
                            if (e.target.files) attachments.addFiles(Array.from(e.target.files));
                            e.target.value = '';
                        }}
                    />
                    {leftControls}
                </div>
                <div className="composer-row-right">
                    {rightControls}
                    {streaming ? (
                        <button
                            type="button"
                            className="composer-send is-stop"
                            onClick={onStop}
                            disabled={stopping}
                            aria-label="Stop"
                            title={stopping ? 'Stopping…' : 'Stop (Esc)'}
                        >
                            <Square size={12} fill="currentColor" />
                        </button>
                    ) : (
                        <button
                            type="button"
                            className="composer-send"
                            onClick={onSubmit}
                            disabled={!canSend}
                            aria-label="Send"
                            title="Send (Enter)"
                        >
                            <ArrowUp size={16} />
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
