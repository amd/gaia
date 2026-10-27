// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState } from 'react';
import { CircleStop, PanelTopOpen, Server } from 'lucide-react';
import { Popover } from '../Popover';
import { log } from '../../utils/logger';
import type { DevServerState, DevServerStatus, GaiaBrowserPaneAPI } from '../../../services/browser-pane/contract';

const STATUS: Record<DevServerStatus, string> = {
    starting: 'Starting',
    ready: 'Running',
    failed: 'Failed to start',
    exited: 'Exited',
    stopped: 'Stopped',
};

const isRunning = (s: DevServerState) => s.status === 'starting' || s.status === 'ready';

function run(what: string, call: Promise<unknown>): void {
    call.catch((err) => log.ui.error(`Browser pane: ${what} failed`, err));
}

/** The dev servers GAIA started: status and port, Open in pane, and Stop. */
export function DevServersMenu({ api, servers }: { api: GaiaBrowserPaneAPI; servers: DevServerState[] }) {
    const [open, setOpen] = useState(false);
    const anchor = useRef<HTMLButtonElement>(null);
    const close = () => setOpen(false);
    const running = servers.filter(isRunning).length;
    return (
        <div className="bp-menu">
            <button
                ref={anchor}
                type="button"
                className={`btn-icon-sm${open || running ? ' active' : ''}`}
                aria-label="Dev servers"
                aria-expanded={open}
                title={running ? `Dev servers: ${running} running` : 'Dev servers'}
                onClick={() => setOpen((o) => !o)}
            >
                <Server size={15} />
            </button>
            <Popover open={open} onClose={close} anchor={anchor} label="Dev servers" placement="below" align="end">
                <div className="popover-heading">Dev servers</div>
                {servers.length === 0 && (
                    <p className="bp-devservers-empty">
                        None yet. Ask GAIA to start your project&apos;s dev server; it reads .gaia/launch.json.
                    </p>
                )}
                {servers.map((s) => (
                    <div key={s.key} className="bp-devserver" role="group" aria-label={`Dev server ${s.name}`}>
                        <div className="bp-devserver-head">
                            <span className="bp-devserver-name">{s.name}</span>
                            <span className={`bp-devserver-status is-${s.status}`}>{STATUS[s.status]}</span>
                            <span className="bp-devserver-port">:{s.port}</span>
                        </div>
                        <div className="bp-devserver-cmd" title={s.cwd}>
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
                                <PanelTopOpen size={12} aria-hidden="true" /> Open in pane
                            </button>
                            <button
                                type="button"
                                className="bp-chip"
                                disabled={!isRunning(s)}
                                onClick={() => run('stop dev server', api.stopDevServer(s.key))}
                            >
                                <CircleStop size={12} aria-hidden="true" /> Stop
                            </button>
                        </div>
                    </div>
                ))}
            </Popover>
        </div>
    );
}
