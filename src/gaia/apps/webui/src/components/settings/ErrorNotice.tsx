// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ChevronDown } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import type { SystemStatus } from '../../types';

/** True for a failure to reach the model server; its raw text means nothing to a person. */
export function isServerDown(message: string): boolean {
    return /not reachable|not answering|model server at|ConnectionPool|NewConnectionError|Connection refused|Max retries/i.test(message);
}

/** True for a remedy that sends a person to a terminal for GAIA's own setup, which the app handles. */
export function namesSetupCommand(text: string | null | undefined): boolean {
    return /gaia init/i.test(text ?? '');
}

/** True when this PC has no model server yet, the one case setup, not waiting, fixes. */
export function needsLocalSetup(status: SystemStatus | null | undefined): boolean {
    return !!status && !status.lemonade_running
        && (namesSetupCommand(status.start_command) || namesSetupCommand(status.start_instruction));
}

/** Reopens setup on local models, with a way back. */
export function SetUpLocalButton({ className = 'btn-secondary' }: { className?: string }) {
    const t = useT();
    return (
        <button type="button" className={className} onClick={() => window.dispatchEvent(new CustomEvent('gaia:set-up-local'))}>
            {t('settings.errors.setUpLocal')}
        </button>
    );
}

/** An error in plain words; a server failure keeps its raw text one click away. */
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
    const t = useT();
    const setupNeeded = useChatStore((s) => needsLocalSetup(s.systemStatus));
    if (!isServerDown(message)) return <p className="settings-error" role="alert">{message}</p>;
    return (
        <div className="settings-notice" role="alert">
            <strong>{t(setupNeeded ? 'settings.errors.notInstalledTitle' : 'settings.errors.serverDownTitle')}</strong>
            <p>{t(setupNeeded ? 'settings.errors.notInstalled' : 'settings.errors.serverDown')}</p>
            {setupNeeded && <SetUpLocalButton className="btn-primary" />}
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
