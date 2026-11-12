// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** PowerShell's names for `-EncodedCommand`, as `_is_encoded_ps_flag` in shell_tools.py reads them. */
function isEncodedFlag(token: string): boolean {
    if (!/^[-/]/.test(token)) return false;
    const name = token.slice(1).split(':', 1)[0].toLowerCase();
    return name === 'e' || name === 'ec' || (name.length >= 3 && 'encodedcommand'.startsWith(name));
}

/** The script a `powershell -EncodedCommand <base64>` line runs, decoded; '' when it carries none. */
export function decodedPowerShell(command: string): string {
    const tokens = command.split(/\s+/).filter(Boolean);
    for (let at = 0; at < tokens.length - 1; at++) {
        if (!isEncodedFlag(tokens[at])) continue;
        const payload = tokens[at + 1].replace(/^["']|["']$/g, '');
        try {
            const bytes = Uint8Array.from(atob(payload), (c) => c.charCodeAt(0));
            return new TextDecoder('utf-16le', { fatal: true }).decode(bytes);
        } catch (err) {
            // Not base64 of UTF-16: there is no script to show, and the original stays on the card.
            console.debug('EncodedCommand operand is not base64 UTF-16LE', err);
            return '';
        }
    }
    return '';
}

/** cmd.exe's reading of a line, its `^` escapes dropped outside double quotes: `de^l x` runs `del x`. */
export function cmdReading(command: string): string {
    let out = '';
    let quoted = false;
    for (let at = 0; at < command.length; at++) {
        const char = command[at];
        if (char === '"') quoted = !quoted;
        if (char === '^' && !quoted) {
            at += 1;
            if (at < command.length) out += command[at];
            continue;
        }
        out += char;
    }
    return out;
}
