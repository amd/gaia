// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ComponentFileModal - Modal for viewing and editing Component Framework MD files.
 * Mirrors the AgentRegistry modal pattern with frontmatter-aware display.
 */

import { useState, useEffect } from 'react';
import { Code2, Edit3, Save, X, Copy, Check } from 'lucide-react';
import './ComponentRegistry.css';

interface ComponentFileModalProps {
    category: string;
    name: string;
    filePath: string;
    content: string;
    frontmatter: Record<string, unknown>;
    isLoading: boolean;
    isEditing: boolean;
    saveError: string | null;
    onClose: () => void;
    onSave: () => void;
    onCancel: () => void;
    onContentChange: (content: string) => void;
    onToggleEdit: () => void;
}

export function ComponentFileModal({
    category,
    name,
    filePath,
    content,
    frontmatter,
    isLoading,
    isEditing,
    saveError,
    onClose,
    onSave,
    onCancel,
    onContentChange,
    onToggleEdit,
}: ComponentFileModalProps) {
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [onClose]);

    const copyToClipboard = async () => {
        try {
            await navigator.clipboard.writeText(content);
            setCopied(true);
        } catch {
            // Clipboard API failed - this is okay, user can manually copy
        }
    };

    useEffect(() => {
        if (copied) {
            const timer = setTimeout(() => setCopied(false), 2000);
            return () => clearTimeout(timer);
        }
    }, [copied]);

    // Extract frontmatter fields for display
    const templateId = frontmatter.template_id as string || '';
    const templateType = frontmatter.template_type as string || '';
    const version = frontmatter.version as string || '';
    const maintainer = frontmatter.maintainer as string || '';
    const description = frontmatter.description as string || '';

    return (
        <div className="cr-modal-overlay" onClick={onClose}>
            <div className="cr-source-modal" onClick={(e) => e.stopPropagation()}>
                {/* Modal Header */}
                <div className="cr-modal-header">
                    <div className="cr-modal-title">
                        <Code2 size={18} />
                        <span>Component: {category}/{name}</span>
                    </div>
                    <div className="cr-modal-actions">
                        {!isEditing && (
                            <button
                                className="cr-modal-btn cr-modal-btn-copy"
                                onClick={copyToClipboard}
                                title="Copy to clipboard"
                            >
                                {copied ? <Check size={16} /> : <Copy size={16} />}
                                {copied ? 'Copied' : 'Copy'}
                            </button>
                        )}
                        <button
                            className="cr-modal-btn cr-modal-btn-close"
                            onClick={onClose}
                            title="Close"
                        >
                            <X size={18} />
                        </button>
                    </div>
                </div>

                {/* Frontmatter Info Bar */}
                {(templateId || templateType || version || maintainer) && !isEditing && (
                    <div className="cr-frontmatter-bar">
                        <div className="cr-frontmatter-info">
                            {templateId && (
                                <span className="cr-frontmatter-item">
                                    <strong>ID:</strong> {templateId}
                                </span>
                            )}
                            {templateType && (
                                <span className="cr-frontmatter-item">
                                    <strong>Type:</strong> {templateType}
                                </span>
                            )}
                            {version && (
                                <span className="cr-frontmatter-item">
                                    <strong>Version:</strong> {version}
                                </span>
                            )}
                            {maintainer && (
                                <span className="cr-frontmatter-item">
                                    <strong>Maintainer:</strong> {maintainer}
                                </span>
                            )}
                        </div>
                        {description && (
                            <div className="cr-frontmatter-description">
                                {description}
                            </div>
                        )}
                    </div>
                )}

                {/* Modal Body */}
                <div className="cr-modal-body">
                    {isLoading ? (
                        <div className="cr-modal-loading">
                            <div className="cr-loading-spinner" />
                            <span>Loading component file...</span>
                        </div>
                    ) : isEditing ? (
                        <div className="cr-editor-container">
                            <textarea
                                className="cr-source-editor"
                                value={content}
                                onChange={(e) => onContentChange(e.target.value)}
                                spellCheck={false}
                                autoFocus
                            />
                        </div>
                    ) : (
                        <div className="cr-source-viewer">
                            <pre><code>{content}</code></pre>
                        </div>
                    )}
                </div>

                {/* Error Display */}
                {saveError && (
                    <div className="cr-modal-error">
                        <X size={14} />
                        {saveError}
                    </div>
                )}

                {/* Modal Footer */}
                <div className="cr-modal-footer">
                    {isEditing ? (
                        <>
                            <button className="cr-modal-btn cr-modal-btn-cancel" onClick={onCancel}>
                                <X size={16} />
                                Cancel
                            </button>
                            <button className="cr-modal-btn cr-modal-btn-save" onClick={onSave}>
                                <Save size={16} />
                                Save Changes
                            </button>
                        </>
                    ) : (
                        <button className="cr-modal-btn cr-modal-btn-edit-large" onClick={onToggleEdit}>
                            <Edit3 size={16} />
                            Edit File
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
