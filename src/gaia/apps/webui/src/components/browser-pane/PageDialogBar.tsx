// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The box a pane page is waiting on (alert, confirm, prompt, or "leave this
 * page?"), shown above the page. Main owns it; the page stays frozen until
 * someone answers, here or through the agent.
 */

import { useEffect, useState } from 'react';
import { log } from '../../utils/logger';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';
import type { GaiaBrowserPaneAPI, PageDialog, TabState } from '../../../services/browser-pane/contract';

const BUTTONS: Record<PageDialog['type'], { ok: MessageKey; cancel: MessageKey | null }> = {
    alert: { ok: 'browserPane.dialog.ok', cancel: null },
    confirm: { ok: 'browserPane.dialog.ok', cancel: 'browserPane.dialog.cancel' },
    prompt: { ok: 'browserPane.dialog.ok', cancel: 'browserPane.dialog.cancel' },
    beforeunload: { ok: 'browserPane.dialog.leave', cancel: 'browserPane.dialog.stay' },
};

function hostOf(url: string): string {
    try {
        return new URL(url).host || url;
    } catch {
        return url;
    }
}

export function PageDialogBar({ tab, api }: { tab: TabState; api: GaiaBrowserPaneAPI }) {
    const dialog = tab.dialog;
    const t = useT();
    const [text, setText] = useState(dialog?.defaultText ?? '');
    useEffect(() => setText(dialog?.defaultText ?? ''), [dialog]);
    if (!dialog) return null;
    const { ok, cancel } = BUTTONS[dialog.type];
    const answer = (accept: boolean) => {
        api.answerDialog(tab.id, accept, dialog.type === 'prompt' && accept ? text : null).catch((err) =>
            log.ui.error('Browser pane: answering the page box failed', err),
        );
    };
    return (
        <form
            className="bp-dialog"
            role="alertdialog"
            aria-label={t('browserPane.dialog.says', { host: hostOf(dialog.url) })}
            onSubmit={(e) => {
                e.preventDefault();
                answer(true);
            }}
        >
            <span className="bp-dialog-from">{t('browserPane.dialog.says', { host: hostOf(dialog.url) })}</span>
            <span className="bp-dialog-message">{dialog.message}</span>
            {dialog.type === 'prompt' && (
                <input className="bp-dialog-input" aria-label={t('browserPane.dialog.answer')} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
            )}
            <span className="bp-dialog-actions">
                {cancel && (
                    <button type="button" className="bp-chip" onClick={() => answer(false)}>
                        {t(cancel)}
                    </button>
                )}
                <button type="submit" className="bp-chip is-on">
                    {t(ok)}
                </button>
            </span>
        </form>
    );
}
