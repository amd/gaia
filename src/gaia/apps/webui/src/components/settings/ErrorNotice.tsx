// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ChevronDown } from 'lucide-react';
import { useT, type Translate } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import { isServerDown } from '../../utils/serverDown';
import type { SystemStatus } from '../../types';

export { isServerDown };

/** True for a remedy that sends a person to a terminal for GAIA's own setup, which the app handles. */
export function namesSetupCommand(text: string | null | undefined): boolean {
    return /gaia init/i.test(text ?? '');
}

/** The backend's sentence for a model server GAIA doesn't run (`OTHER_SERVER_START_HINT`). */
const OTHER_SERVER_START_HINT = "GAIA doesn't run this model server, so it can't start it. Start it the way you usually do.";

/** The backend's English start instruction in the app's language; one with a command or path in it shows as written. */
export function startInstructionText(t: Translate, text: string | null | undefined): string | null {
    if (!text) return null;
    return text === OTHER_SERVER_START_HINT ? t('server.otherServerStart') : text;
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
