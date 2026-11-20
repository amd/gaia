// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { useT } from '../i18n/useT';
import { CodeBlock } from './PermissionPrompt';
import { CHANGE_PREVIEW_LINES, editLines, fileChange, fileText, fileUnseen } from '../utils/permissionChange.core';
import './render/render.css';
import './PermissionChangePreview.css';

export { editLines, fileChange, fileText, fileUnseen };

/** Fixed prefix -> class; never derived from the line's own text. */
function lineClass(line: string): string {
    if (line.startsWith('+')) return 'render-diff__line render-diff__line--added';
    if (line.startsWith('-')) return 'render-diff__line render-diff__line--removed';
    return 'render-diff__line';
}

function EditDiff({ lines }: { lines: string[] }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const long = lines.length > CHANGE_PREVIEW_LINES;
    const shown = long && !expanded ? lines.slice(0, CHANGE_PREVIEW_LINES) : lines;
    return (
        <>
            <pre dir="ltr" className="render-diff__body perm-card-diff" data-testid="perm-edit-diff">
                {shown.map((line, i) => <div key={i} className={lineClass(line)}>{fileText(line, true)}</div>)}
            </pre>
            {long && (
                <button type="button" className="perm-card-args-toggle" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
                    {expanded ? t('prompt.showLess') : t('prompt.showAllLines', { n: lines.length })}
                </button>
            )}
        </>
    );
}

/** The file content a write will save, or the lines an edit will change, as they will land. */
export function ChangePreview({ args }: { args: Record<string, unknown> }) {
    const t = useT();
    const change = fileChange(args);
    if (!change) return null;
    // A character that hides or reorders text would make the preview read unlike the file.
    const hidden = change.kind === 'edit' ? fileUnseen(args.old_content) || fileUnseen(args.new_content) : fileUnseen(change.text);
    return (
        <div className="perm-card-change">
            <p className="perm-card-change-label">{t(change.kind === 'edit' ? 'prompt.willChange' : 'prompt.willWrite')}</p>
            {change.kind === 'edit'
                ? <EditDiff lines={change.lines} />
                : change.text
                ? <CodeBlock code={fileText(change.text, true)} previewLines={CHANGE_PREVIEW_LINES} testId="perm-write-preview" exact />
                : <p className="perm-card-detail">{t('prompt.emptyFile')}</p>}
            {hidden && <p className="perm-card-risk" role="note" data-testid="perm-change-unseen">{t('prompt.unseenChars')}</p>}
        </div>
    );
}
