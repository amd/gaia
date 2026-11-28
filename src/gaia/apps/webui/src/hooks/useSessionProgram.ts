// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { AgentStep } from '../types';
import type { AgentCapabilities } from '../types/sessionPanel';

type Program = NonNullable<AgentCapabilities['program']>;

/** Requests in flight, so every answer in a chat shares one. */
const inFlight = new Map<string, Promise<Program | null>>();

function read(sessionId: string): Promise<Program | null> {
    let pending = inFlight.get(sessionId);
    if (!pending) {
        pending = Promise.resolve()
            .then(() => api.getCapabilities(sessionId))
            .then((res) => res?.program ?? null)
            .finally(() => { inFlight.delete(sessionId); });
        inFlight.set(sessionId, pending);
    }
    return pending;
}

/**
 * The program running this chat's agent (`gaiapy` or `gaiacpp`), or null while
 * unknown: asked only when `wanted`, and never guessed when the agent can't say.
 */
export function useSessionProgram(sessionId: string, wanted: boolean): Program | null {
    const [program, setProgram] = useState<Program | null>(null);
    useEffect(() => {
        setProgram(null);
        if (!wanted) return;
        let live = true;
        read(sessionId)
            .then((found) => { if (live) setProgram(found); })
            // A chat whose agent isn't running answers 409; it names no program, so nothing is claimed.
            .catch((err) => log.ui.warn(`Could not tell which agent runs session=${sessionId}`, err));
        return () => { live = false; };
    }, [sessionId, wanted]);
    return program;
}

/** The Python names the UI shows for tools that write files or run commands. */
const CHANGING_TOOLS = new Set(['write_file', 'edit_file', 'run_shell_command']);

const READ_ONLY_COMMANDS = new Set([
    'ls', 'dir', 'pwd', 'whoami', 'hostname', 'uname', 'date', 'id', 'echo', 'printf',
    'cat', 'head', 'tail', 'wc', 'stat', 'file', 'which', 'where', 'type', 'readlink',
    'realpath', 'basename', 'dirname', 'du', 'df', 'uptime', 'printenv', 'ps',
    'grep', 'egrep', 'fgrep', 'rg', 'ag', 'diff', 'cmp', 'md5sum', 'sha256sum',
    'shasum', 'tr', 'cut', 'nl', 'less', 'more', 'true', 'false',
]);

const GIT_READ_ONLY = new Set([
    'status', 'log', 'diff', 'show', 'rev-parse', 'describe', 'ls-files', 'ls-tree',
    'blame', 'shortlog', 'cat-file',
]);

const GIT_LIST_FLAGS = new Set(['-a', '--all', '-r', '--remotes', '-v', '-vv', '--list', '-l', '--show-current']);

const FIND_WRITE_FLAGS = new Set([
    '-delete', '-exec', '-execdir', '-ok', '-okdir', '-fls', '-fprint', '-fprint0', '-fprintf',
]);

function shellCommandText(step: AgentStep): string | null {
    const detail = step.detail?.trim();
    if (!detail) return null;
    const match = /^command:\s*(.+?)(?:,\s*(?:cwd|timeout|working_directory):|$)/m.exec(detail);
    return match ? match[1].trim() : null;
}

function isReadOnlySegment(segment: string): boolean {
    const tokens = segment.trim().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return false;
    const cmd = tokens[0].toLowerCase();
    if (READ_ONLY_COMMANDS.has(cmd)) return true;
    if (cmd === 'sort') return !tokens.some((tok) => tok === '-o' || tok.startsWith('--output'));
    if (cmd === 'find') return !tokens.some((tok) => FIND_WRITE_FLAGS.has(tok.toLowerCase()));
    if (cmd === 'git') {
        const sub = tokens[1]?.toLowerCase();
        if (!sub) return false;
        if (GIT_READ_ONLY.has(sub)) return true;
        if (sub === 'branch' || sub === 'tag') {
            return tokens.slice(2).every((tok) => GIT_LIST_FLAGS.has(tok));
        }
    }
    return false;
}

/** True only when a shell step carries a command known not to write files. */
function isReadOnlyShellStep(step: AgentStep): boolean {
    if (step.tool !== 'run_shell_command') return false;
    const raw = shellCommandText(step);
    if (!raw) return false;
    const cleaned = raw.replace(/\b2>\s*\/dev\/null\b|\b2>&1\b/g, '').trim();
    if (!cleaned || /[><`;\n\r]|\$\(|(?<!&)&(?!&)/.test(cleaned)) return false;
    const segments = cleaned.split(/\s*(?:\|\||&&|\|)\s*/);
    return segments.length > 0 && segments.every(isReadOnlySegment);
}

/** Whether a turn ran a tool that can change files: one that was declined or failed changed nothing. */
export function ranChangingTool(steps: AgentStep[] | undefined): boolean {
    return (steps ?? []).some((s) => s.type === 'tool' && !!s.tool && CHANGING_TOOLS.has(s.tool)
        && !s.declined && !s.timedOut && !s.refused && s.success !== false
        && !isReadOnlyShellStep(s));
}
