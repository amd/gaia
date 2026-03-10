// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Structured console logger for GAIA Agent UI.
 *
 * Provides color-coded, categorized logging with timing information.
 * All output goes to the browser dev console for easy debugging.
 */

const COLORS: Record<string, string> = {
    api: '#4fc3f7',      // light blue
    store: '#ce93d8',    // purple
    chat: '#81c784',     // green
    stream: '#ffb74d',   // orange
    ui: '#90a4ae',       // grey-blue
    doc: '#a1887f',      // brown
    system: '#e57373',   // red
    nav: '#fff176',      // yellow
};

const ICONS: Record<string, string> = {
    api: 'fetch',
    store: 'state',
    chat: 'chat',
    stream: 'stream',
    ui: 'ui',
    doc: 'docs',
    system: 'sys',
    nav: 'nav',
};

type Category = keyof typeof COLORS;

function formatTimestamp(): string {
    const now = new Date();
    return now.toLocaleTimeString('en-US', { hour12: false }) + '.' + String(now.getMilliseconds()).padStart(3, '0');
}

function createLog(category: Category) {
    const color = COLORS[category] || '#ccc';
    const icon = ICONS[category] || category;
    const badge = `%c[${icon}]`;
    const badgeStyle = `color: ${color}; font-weight: bold;`;
    const timeStyle = 'color: #888; font-weight: normal;';

    return {
        info: (message: string, ...data: any[]) => {
            const ts = formatTimestamp();
            if (data.length > 0) {
                console.log(`${badge} %c${ts} %c${message}`, badgeStyle, timeStyle, 'color: inherit;', ...data);
            } else {
                console.log(`${badge} %c${ts} %c${message}`, badgeStyle, timeStyle, 'color: inherit;');
            }
        },
        warn: (message: string, ...data: any[]) => {
            const ts = formatTimestamp();
            console.warn(`${badge} %c${ts} %c${message}`, badgeStyle, timeStyle, 'color: inherit;', ...data);
        },
        error: (message: string, ...data: any[]) => {
            const ts = formatTimestamp();
            console.error(`${badge} %c${ts} %c${message}`, badgeStyle, timeStyle, 'color: inherit;', ...data);
        },
        debug: (message: string, ...data: any[]) => {
            const ts = formatTimestamp();
            console.debug(`${badge} %c${ts} %c${message}`, badgeStyle, timeStyle, 'color: #999;', ...data);
        },
        /** Log with a timing duration in ms */
        timed: (message: string, startMs: number, ...data: any[]) => {
            const duration = Math.round(performance.now() - startMs);
            const ts = formatTimestamp();
            const durColor = duration > 2000 ? 'color: #e57373;' : duration > 500 ? 'color: #ffb74d;' : 'color: #81c784;';
            if (data.length > 0) {
                console.log(
                    `${badge} %c${ts} %c${message} %c(${duration}ms)`,
                    badgeStyle, timeStyle, 'color: inherit;', durColor,
                    ...data,
                );
            } else {
                console.log(
                    `${badge} %c${ts} %c${message} %c(${duration}ms)`,
                    badgeStyle, timeStyle, 'color: inherit;', durColor,
                );
            }
        },
        /** Return current time for use with timed() */
        time: (): number => performance.now(),
    };
}

export const log = {
    api: createLog('api'),
    store: createLog('store'),
    chat: createLog('chat'),
    stream: createLog('stream'),
    ui: createLog('ui'),
    doc: createLog('doc'),
    system: createLog('system'),
    nav: createLog('nav'),
};

/** Log app startup banner */
export function logBanner(version: string) {
    console.log(
        '%c GAIA Agent UI %c v' + version + ' %c Local AI Desktop',
        'background: #E23C40; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px 0 0 4px;',
        'background: #333; color: #fff; padding: 4px 8px;',
        'background: #1a1a1a; color: #888; padding: 4px 8px; border-radius: 0 4px 4px 0;',
    );
    console.log(
        '%cDebug logging enabled. Categories: [fetch] [state] [chat] [stream] [ui] [docs] [sys] [nav]',
        'color: #666; font-style: italic;',
    );
}
