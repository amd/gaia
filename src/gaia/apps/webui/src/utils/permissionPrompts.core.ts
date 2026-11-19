// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Permission-prompt wording and consent rules, with no store, so the phone app can import them. */

import type { GaiaNotification } from '../types/agent';
import { isMessageKey, ref, type MessageKey, type Say } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/translator';

/** These decisions apply to one displayed snapshot or code scope, never a tool name. */
/** The prompt raised when a tool reaches outside the chat's files (security.py). */
export const PATH_ACCESS_TOOL = 'allow_path_access';

/** The prompt asking whether to read a folder's CLAUDE.md, AGENTS.md and skills. Answered per folder, never "always". */
export const PROJECT_TRUST_TOOL = 'trust_project_folder';

function pathAccessArgs(args: unknown): { target: string | null; kind: unknown } {
  const { path, kind } = (args ?? {}) as { path?: unknown; kind?: unknown };
  return { target: typeof path === 'string' && path.trim() ? path : null, kind };
}

/**
 * The question a path-access prompt asks. `kind` is the agent's: it omits it only
 * when nothing exists at the path yet, so a prompt that names no path at all
 * says nothing about whether it exists. `followUp` means it backs a call just allowed.
 */
export function pathAccessQuestion(
  args: unknown,
  followUp = false,
  t: Translate = tEn,
  mark: (path: string) => string = (p) => p,
): string {
  const { target, kind } = pathAccessArgs(args);
  const what = !target
    ? ref('prompt.path.what.unknown')
    : ref(kind === 'folder' ? 'prompt.path.what.folder' : kind === 'file' ? 'prompt.path.what.file' : 'prompt.path.what.new', {
      path: mark(target),
    });
  return t(followUp ? 'prompt.path.askAlso' : 'prompt.path.ask', { what });
}

/** The path-access prompt's heading, in the user's terms rather than the tool's name. */
export function pathAccessTitle(args: unknown, followUp = false, t: Translate = tEn): string {
  const { kind } = pathAccessArgs(args);
  const noun = kind === 'folder' ? 'folder' : kind === 'file' ? 'file' : 'location';
  return t(`prompt.path.${followUp ? 'titleAlso' : 'title'}.${noun}`);
}

/**
 * A permission prompt's heading and question, worded from the call in the UI
 * language rather than from its stored English title and message.
 */
/** Calls asked about in the user's words, not as "Allow running python?". */
const PLAIN_TITLES: Record<string, MessageKey> = {
  run_python: 'prompt.title.python',
  execute_python_file: 'prompt.title.pythonFile',
  run_shell_command: 'prompt.title.command',
};

export function permissionPromptText(
  n: GaiaNotification,
  t: Translate = tEn,
  mark: (text: string) => string = (s) => s,
): { heading: string; question: string } {
  const args = n.toolArgs ?? {};
  if (n.tool === PATH_ACCESS_TOOL) {
    return {
      heading: pathAccessTitle(args, n.pathFollowUp, t),
      question: pathAccessQuestion(args, n.pathFollowUp, t, mark),
    };
  }
  if (n.tool === PROJECT_TRUST_TOOL) {
    return { heading: t('prompt.trust.title'), question: t('prompt.trust.ask') };
  }
  // The action fills each half of the sentence in its own language, so Arabic never lands in the English gloss.
  const tool = (say: Say) => toolAction(n, say) ?? mark(n.toolLabel ?? n.tool ?? '');
  const plain = PLAIN_TITLES[n.tool ?? ''];
  return {
    heading: plain
      ? t(plain)
      : t.compose((say) => say(n.toolLabel ? 'prompt.title.tool' : 'prompt.title.toolId', { tool: tool(say) })),
    question: t.compose((say) => say('prompt.needsOk', { tool: tool(say) })),
  };
}

/** The call in the UI language ("writing file"), for a tool whose activity label is translated; the backend's own label is English. */
function toolAction(n: GaiaNotification, say: Say): string | null {
  const key = `tool.${n.tool}.active`;
  if (!n.toolLabel || !isMessageKey(key)) return null;
  const label = say(key as MessageKey);
  return label.charAt(0).toLowerCase() + label.slice(1);
}

/** The scope of a folder grant shared by writes, edits and renames. */
const FILE_CHANGE_SCOPE = 'changes to files in ';

/**
 * What an "always allow" answer covers, in the UI language. The backend words
 * its scope in English (`write_file on notes.txt`, `changes to files in /x`);
 * null when the scope isn't one of those, and the caller keeps the backend's text.
 */
export function alwaysScopeText(n: GaiaNotification, t: Translate): string | null {
  return alwaysScopeWith(n, (key, params) => t.bare(key, params));
}

/** `alwaysScopeText` in the language `say` writes, for a line built inside `t.compose`. */
export function alwaysScopeWith(n: GaiaNotification, say: Say, wrapName: (name: string) => string = (name) => name): string | null {
  if (n.alwaysScope?.startsWith(FILE_CHANGE_SCOPE)) {
    return say('prompt.always.changes', { folder: wrapName(n.alwaysScope.slice(FILE_CHANGE_SCOPE.length)) });
  }
  const action = toolAction(n, say);
  const prefix = `${n.tool} `;
  if (!action || !n.alwaysScope?.startsWith(prefix)) return null;
  const rest = n.alwaysScope.slice(prefix.length);
  if (rest.startsWith('on files in ')) return say('prompt.always.folder', { action, folder: wrapName(rest.slice('on files in '.length)) });
  return say('prompt.always.file', { action, name: wrapName(rest.startsWith('on ') ? rest.slice(3) : rest) });
}

/**
 * The path in the backend's "outside this chat's folders" risk note, else null.
 * The backend sends only that English sentence (`call_risk` has no structured flag), so the
 * phone matches it and words the note from the catalog.
 */
export function outsideFolderPath(note: string): string | null {
  const m = /^([^]+) is outside this chat's folders\. Allowing this also lets GAIA use that file for the rest of the chat\.$/.exec(note);
  return m ? m[1] : null;
}

/** `C:\A\b.txt` and `c:/a/b.txt/` compare equal. Picks card wording only; too loose for an access check. */
function normalizePath(p: string): string {
  return p.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

/** The path a gated tool call acts on, if its arguments name one. */
function toolTargetPath(args: Record<string, unknown> | undefined): string | null {
  const value = ['file_path', 'path', 'directory', 'dir_path', 'destination']
    .map((k) => args?.[k])
    .find((v) => typeof v === 'string' && v.trim());
  return typeof value === 'string' ? value : null;
}

/**
 * Whether a path-access request backs the call this chat just allowed — the same
 * path, or a folder holding it. Only then may the prompt read as a follow-up.
 */
export function isPathAccessFollowUp(
  notifications: GaiaNotification[],
  sessionId: string,
  args: unknown,
): boolean {
  const previous = notifications.find((n) => n.type === 'permission_request' && n.sessionId === sessionId);
  if (!previous || previous.response !== 'allow' || previous.tool === PATH_ACCESS_TOOL) return false;
  const acted = toolTargetPath(previous.toolArgs);
  const { path } = (args ?? {}) as { path?: unknown };
  if (!acted || typeof path !== 'string' || !path.trim()) return false;
  const a = normalizePath(acted);
  const p = normalizePath(path);
  if (a === p || a.startsWith(`${p}/`)) return true;
  // A relative argument resolves against a directory the prompt doesn't show.
  const relative = !/^([a-z]:)?\//.test(a) && !a.startsWith('~');
  return relative && p.endsWith(`/${a.replace(/^\.\//, '')}`);
}

export function requiresFreshConsent(tool: string | undefined): boolean {
  return tool === 'share_engineering_context'
    || tool === 'append_engineering_context'
    || tool === 'approve_engineering_code'
    || tool === PATH_ACCESS_TOOL
    || tool === PROJECT_TRUST_TOOL;
}

/**
 * How many earlier prompts in this chat asked the same thing and were allowed:
 * same tool, same "always" scope, same risk. Only counts when "always" is on
 * offer, so the prompt can point at it instead of asking a tenth time.
 */
export function repeatedAllowCount(notifications: GaiaNotification[], prompt: GaiaNotification): number {
  if (!prompt.alwaysScope || !prompt.sessionId || requiresFreshConsent(prompt.tool)) return 0;
  if (prompt.risk === 'destructive') return 0;
  return notifications.filter((n) =>
    n.id !== prompt.id
    && n.type === 'permission_request'
    && n.sessionId === prompt.sessionId
    && n.response === 'allow'
    && n.tool === prompt.tool
    && n.alwaysScope === prompt.alwaysScope
    && n.risk === prompt.risk
  ).length;
}

/** One-line description of a tool call, e.g. `run_shell_command · git status`. */
export function summarize(n: GaiaNotification): string {
  const args = n.toolArgs ?? {};
  // _site: the site a browser-pane read would show the model (SITE_ARG in tool_grants.py).
  const primary = ['command', 'file_path', 'path', 'url', '_site', 'query', 'name']
    .map((k) => args[k])
    .find((v) => typeof v === 'string' && v.trim());
  return typeof primary === 'string' ? primary : '';
}

/** Tools whose relayed summary is a command and its risk note (`COMMAND_SUMMARY_TOOLS` in sse_translation.py). */
export const COMMAND_SUMMARY_TOOLS = new Set(['run_shell_command', 'wait_for_condition']);

/** Tools whose relayed summary is the exact text being approved (`EXACT_TEXT_ARG` in call_risk.py). The flagship runs them from its send-email skill, so they also arrive namespaced. */
const EXACT_MAIL_TOOLS = ['send_now', 'send_draft', 'reply_to_message', 'forward_message', 'discard_draft', 'update_draft'];
export const EXACT_SUMMARY_TOOLS = new Set([...EXACT_MAIL_TOOLS, ...EXACT_MAIL_TOOLS.map((tool) => `send-email/${tool}`)]);

/**
 * A relayed command prompt with no args: `command[\n…]\nin <folder>[\n(extras)]\n<risk note>`
 * (`render_command_summary` in sse_translation.py). The command keeps its own lines.
 */
export function splitCommandSummary(summary: string): { command: string; folder: string; extras: string; note: string } | null {
  const lines = summary.split('\n');
  if (lines.length < 2) return null;
  // No note when the call carries no risk worth naming: the folder is then last.
  const hasNote = !lines[lines.length - 1].startsWith('in ');
  const note = hasNote ? lines[lines.length - 1].trim() : '';
  const body = hasNote ? lines.slice(0, -1) : lines;
  let at = -1;
  for (let i = body.length - 1; i > 0; i--) {
    if (body[i].startsWith('in ')) { at = i; break; }
  }
  if (at < 0) return { command: body.join('\n'), folder: '', extras: '', note };
  return {
    command: body.slice(0, at).join('\n'),
    folder: body[at].slice(3).trim(),
    extras: body.slice(at + 1).join(' ').trim(),
    note,
  };
}

/** A grant's folder by its name alone, for a button; the full path stays in its tooltip. */
export function shortScope(scope: string): string {
  // A translated scope wraps its path in direction isolates (U+2068 ... U+2069).
  return scope.replace(/(\s\u2068?)((?:[A-Za-z]:)?[\\/][^\u2069]*)(\u2069?)$/, (_, lead: string, path: string, close: string) => {
    const name = path.split(/[\\/]/).filter(Boolean).pop();
    return `${lead}${name ?? path}${close}`;
  });
}

/** Lines of code a prompt shows before "Show all". */
export const PROMPT_CODE_PREVIEW_LINES = 12;

/** `45` -> "45s", `600` -> "10:00". */
export function formatCountdown(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

