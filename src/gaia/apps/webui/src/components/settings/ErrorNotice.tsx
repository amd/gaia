// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ChevronDown } from 'lucide-react';
import { useT } from '../../i18n/useT';

/** True for a failure to reach the model server; its raw text means nothing to a person. */
export function isServerDown(message: string): boolean {
    return /not reachable|not answering|model server at|ConnectionPool|NewConnectionError|Connection refused|Max retries/i.test(message);
}

/** True for a remedy that sends a person to a terminal for GAIA's own setup, which the app handles. */
export function namesSetupCommand(text: string | null | undefined): boolean {
    return /gaia init/i.test(text ?? '');
}

/** An error in plain words; a server failure keeps its raw text one click away. */
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
    const t = useT();
    if (!isServerDown(message)) return <p className="settings-error" role="alert">{message}</p>;
    return (
        <div className="settings-notice" role="alert">
            <strong>{t('settings.errors.serverDownTitle')}</strong>
            <p>{t('settings.errors.serverDown')}</p>
            {onRetry && (
                <button type="button" className="btn-secondary" onClick={onRetry}>{t('settings.key.checkAgain')}</button>
            )}
            <details className="settings-more">
                <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.errors.details')}</summary>
                <bdi dir="ltr" className="settings-notice-raw">{message}</bdi>
            </details>
        </div>
    );
}
