// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState } from 'react';
import { CircleStop, PanelTopOpen, Server } from 'lucide-react';
import { Popover } from '../Popover';
import { log } from '../../utils/logger';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import type { MessageKey } from '../../i18n/registry';
import type { DevServerState, DevServerStatus, GaiaBrowserPaneAPI } from '../../../services/browser-pane/contract';

const STATUS: Record<DevServerStatus, MessageKey> = {
    starting: 'browserPane.devServer.starting',
    ready: 'browserPane.devServer.ready',
    failed: 'browserPane.devServer.failed',
    exited: 'browserPane.devServer.exited',
    stopped: 'browserPane.devServer.stopped',
};

const isRunning = (s: DevServerState) => s.status === 'starting' || s.status === 'ready';

function run(what: string, call: Promise<unknown>): void {
    call.catch((err) => log.ui.error(`Browser pane: ${what} failed`, err));
}

/** Which chat started the server, by its title; nothing while the chat list has not loaded it. */
function useStartedIn(server: DevServerState): string | null {
    const starter = server.chats[0];
    const current = useChatStore((s) => s.currentSessionId);
    const title = useChatStore((s) => s.sessions.find((c) => c.id === starter)?.title);
    const t = useT();
    if (starter === current) return t('browserPane.startedHere');
    return title === undefined ? null : t('browserPane.startedIn', { title });
}

function StartedIn({ server }: { server: DevServerState }) {
    const text = useStartedIn(server);
    return text === null ? null : <div className="bp-devserver-chat">{text}</div>;
}

/** The dev servers GAIA started: status and port, the chat that started each, Open in pane, and Stop. */
export function DevServersMenu({ api, servers }: { api: GaiaBrowserPaneAPI; servers: DevServerState[] }) {
    const [open, setOpen] = useState(false);
    const anchor = useRef<HTMLButtonElement>(null);
    const close = () => setOpen(false);
    const running = servers.filter(isRunning).length;
    const t = useT();
    return (
        <div className="bp-menu">
            <button
                ref={anchor}
                type="button"
                className={`btn-icon-sm${open || running ? ' active' : ''}`}
                aria-label={t('browserPane.devServers')}
                aria-expanded={open}
                title={running ? t('browserPane.devServersRunning', { n: running }) : t('browserPane.devServers')}
                onClick={() => setOpen((o) => !o)}
            >
                <Server size={15} />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label={t('browserPane.devServers')} placement="below" align="end">
                <div className="popover-heading">{t('browserPane.devServers')}</div>
                {servers.length === 0 && (
                    <p className="bp-devservers-empty">{t('browserPane.devServersEmpty')}</p>
                )}
                {servers.map((s) => (
                    <div key={s.key} className="bp-devserver" role="group" aria-label={t('browserPane.devServerNamed', { name: s.name })}>
                        <div className="bp-devserver-head">
                            <span className="bp-devserver-name">{s.name}</span>
                            <span className={`bp-devserver-status is-${s.status}`}>{t(STATUS[s.status])}</span>
                            <span className="bp-devserver-port" dir="ltr">:{s.port}</span>
                        </div>
                        <StartedIn server={s} />
                        <div className="bp-devserver-cmd" title={s.cwd} dir="ltr">
                            {s.argv.join(' ')}
                        </div>
                        <div className="bp-devserver-actions">
                            <button
                                type="button"
                                className="bp-chip"
                                disabled={s.status !== 'ready'}
                                onClick={() => {
                                    close();
                                    run('open dev server', api.openTab(s.url));
                                }}
                            >
                                <PanelTopOpen size={12} aria-hidden="true" /> {t('browserPane.openInPane')}
                            </button>
                            <button
                                type="button"
                                className="bp-chip"
                                disabled={!isRunning(s)}
                                onClick={() => run('stop dev server', api.stopDevServer(s.key))}
                            >
                                <CircleStop size={12} aria-hidden="true" /> {t('browserPane.stopServer')}
                            </button>
                        </div>
                    </div>
                ))}
            </Popover>
        </div>
    );
}
