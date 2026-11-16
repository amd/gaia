// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Keyboard, useWindowDimensions, View } from 'react-native';
import { useKeyboardState } from 'react-native-keyboard-controller';
import Animated, { FadeIn, FadeInDown, FadeOut, FadeOutDown, useReducedMotion } from 'react-native-reanimated';

import { isolate, pluralKey, type Say } from '@webui/i18n/registry';
import type { GaiaNotification } from '@webui/types/agent';
import {
    alwaysScopeWith,
    COMMAND_SUMMARY_TOOLS,
    EXACT_SUMMARY_TOOLS,
    formatCountdown,
    outsideFolderPath,
    PATH_ACCESS_TOOL,
    permissionPromptText,
    PROJECT_TRUST_TOOL,
    PROMPT_CODE_PREVIEW_LINES,
    requiresFreshConsent,
    shortScope,
    splitCommandSummary,
    summarize,
} from '@webui/utils/permissionPrompts.core';
import { calendarFacts, CALENDAR_WRITE_TOOLS } from '@webui/utils/calendarApproval.core';
import { CHANGE_PREVIEW_LINES, fileChange, fileText, fileUnseen } from '@webui/utils/permissionChange.core';

import { useT } from '@/i18n/useT';
import { Button, Disclosure, HScroll, Icon, Notice, Pressable, Row, ScrollBox, Stack, Surface, Text } from '@/ui';
import { ShieldQuestion } from '@/ui/icons';

import { useCountdown } from './useCountdown';

export type PermissionChoice = 'once' | 'always' | 'deny';

export interface PermissionPromptProps {
    prompt: GaiaNotification;
    /** Earlier allows of this same call; above zero, Always becomes the suggested answer. */
    repeats?: number;
    busy?: boolean;
    error?: string | null;
    fullAccessPending?: boolean;
    onAnswer(choice: PermissionChoice): void;
    onExpired?(): void;
}

// Width where the buttons may sit side by side.
const SIDE_BY_SIDE_MIN_WIDTH = 600;

// The body scrolls inside this share of the window, so Allow, Deny and the composer stay on screen.
const BODY_HEIGHT_SHARE = 0.35;
const MIN_BODY_HEIGHT = 96;

const mark = (text: string): string => isolate(text, 'ltr');

type CodeTone = 'primary' | 'secondary' | 'success' | 'danger';

/** Code, a path or a diff: left to right and left-aligned in any UI language; a long line scrolls sideways inside the box. */
function CodeBox({ lines, testID }: { lines: { text: string; tone?: CodeTone }[]; testID?: string }) {
    return (
        <Surface tone="recessed" radius="sm" padding="sm" border>
            <HScroll dir="ltr" hint testID={testID}>
                <View style={{ minWidth: '100%', direction: 'ltr' }}>
                    {lines.map((line, i) => (
                        <Text key={i} role="caption" mono dir="ltr" selectable tone={line.tone}>
                            {line.text}
                        </Text>
                    ))}
                </View>
            </HScroll>
        </Surface>
    );
}

/** Code or JSON, collapsed past the web's line count. */
function Collapsible({ text, limit = PROMPT_CODE_PREVIEW_LINES }: { text: string; limit?: number }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const lines = text.replace(/^\n+|\n+$/g, '').split('\n');
    const long = lines.length > limit;
    const shown = long && !expanded ? lines.slice(0, limit) : lines;
    return (
        <Stack gap="xs">
            <CodeBox lines={shown.map((text) => ({ text }))} />
            {long ? (
                <Pressable
                    onPress={() => setExpanded((v) => !v)}
                    accessibilityState={{ expanded }}
                    accessibilityLabel={expanded ? t('prompt.showLess') : t('prompt.showAllLines', { n: lines.length })}
                >
                    <Text role="label" tone="accent" weight="medium">
                        {expanded ? t('prompt.showLess') : t('prompt.showAllLines', { n: lines.length })}
                    </Text>
                </Pressable>
            ) : null}
        </Stack>
    );
}

/** The lines an edit changes, or the text a write saves, as they will land. */
function ChangePreview({ args }: { args: Record<string, unknown> }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const change = fileChange(args);
    if (!change) return null;
    const hidden = change.kind === 'edit' ? fileUnseen(args.old_content) || fileUnseen(args.new_content) : fileUnseen(change.text);
    const long = change.kind === 'edit' && change.lines.length > CHANGE_PREVIEW_LINES;
    const lines = change.kind === 'edit' ? (long && !expanded ? change.lines.slice(0, CHANGE_PREVIEW_LINES) : change.lines) : [];
    const more = t('prompt.showAllLines', { n: change.kind === 'edit' ? change.lines.length : 0 });
    return (
        <Stack gap="xs" testID="perm-change">
            <Text role="label" weight="medium">
                {t(change.kind === 'edit' ? 'prompt.willChange' : 'prompt.willWrite')}
            </Text>
            {change.kind === 'edit' ? (
                <Stack gap="xs">
                    <CodeBox
                        testID="perm-change-scroll"
                        lines={lines.map((line) => ({
                            text: fileText(line, true),
                            tone: line.startsWith('+') ? 'success' : line.startsWith('-') ? 'danger' : 'secondary',
                        }))}
                    />
                    {long ? (
                        <Button variant="secondary" size="sm" label={expanded ? t('prompt.showLess') : more} onPress={() => setExpanded((v) => !v)} />
                    ) : null}
                </Stack>
            ) : change.text ? (
                <Collapsible text={fileText(change.text, true)} limit={CHANGE_PREVIEW_LINES} />
            ) : (
                <Text role="caption" tone="secondary">
                    {t('prompt.emptyFile')}
                </Text>
            )}
            {hidden ? (
                <Text role="caption" tone="warning" testID="perm-change-unseen">
                    {t('prompt.unseenChars')}
                </Text>
            ) : null}
        </Stack>
    );
}

/** Title, time, account and people of a calendar change, as the web card lists them. */
function CalendarDetails({ tool, args }: { tool: string; args: Record<string, unknown> }) {
    const t = useT();
    const { rows, peopleLabel, people, unseenNote } = calendarFacts(tool, args, t);
    const when = (v: (typeof rows)[number]['value']) =>
        v.kind === 'text' ? v.text : v.when.raw ?? (v.when.allDay ? `${v.when.text} (${t.bare('permission.calendar.allDay')})` : v.when.text);
    return (
        <Stack gap="xs" testID="perm-calendar">
            {rows.map(({ label, value }) => (
                <Stack key={label} gap="xs">
                    <Text role="caption" tone="muted">
                        {label}
                    </Text>
                    <Text role="label" dir="auto">
                        {when(value)}
                    </Text>
                </Stack>
            ))}
            <Text role="caption" tone="muted">
                {peopleLabel}
            </Text>
            {people.map((person) => (
                <Text key={person} role="label" dir="auto">
                    {person}
                </Text>
            ))}
            {unseenNote ? (
                <Text role="caption" tone="warning" testID="perm-calendar-unseen">
                    {unseenNote}
                </Text>
            ) : null}
        </Stack>
    );
}

/** What the call does, in the order the web card shows it. */
function CallDetails({ prompt }: { prompt: GaiaNotification }) {
    const t = useT();
    const tool = prompt.tool ?? '';
    const args = prompt.toolArgs ?? {};
    const detail = summarize(prompt);
    const riskNote = typeof args._risk_note === 'string' ? args._risk_note : '';
    const outsidePath = outsideFolderPath(riskNote);
    const folder = riskNote && typeof args.working_directory === 'string' ? args.working_directory : '';
    const code = typeof args.code === 'string' && args.code.trim() ? args.code : '';
    const exactText =
        typeof args._exact_text === 'string'
            ? args._exact_text
            : EXACT_SUMMARY_TOOLS.has(tool)
              ? (prompt.summary ?? '')
              : '';
    const hasArgs = Object.keys(args).length > 0;
    const calendar = CALENDAR_WRITE_TOOLS.has(tool);
    const [showArgs, setShowArgs] = useState(false);
    const relayed = !hasArgs && COMMAND_SUMMARY_TOOLS.has(tool) ? splitCommandSummary(prompt.summary ?? '') : null;
    // The question already names the path of a folder or trust prompt.
    const own = tool === PATH_ACCESS_TOOL || tool === PROJECT_TRUST_TOOL;
    const shownDetail = own ? '' : detail;
    const inFolder = (path: string) => (
        <Text role="caption" tone="secondary">
            {t.bare('prompt.in')} <Text mono>{mark(path)}</Text>
        </Text>
    );

    return (
        <Stack gap="sm">
            {shownDetail && riskNote ? <Collapsible text={shownDetail} /> : null}
            {shownDetail && !riskNote ? (
                <View style={{ direction: 'ltr' }}>
                    <Text role="label" tone="secondary" mono dir="ltr">
                        {shownDetail}
                    </Text>
                </View>
            ) : null}
            {folder ? inFolder(folder) : null}
            {riskNote ? (
                <Text role="caption" tone="warning" accessibilityRole="text">
                    {outsidePath ? t('prompt.outsideFolders', { path: mark(outsidePath) }) : riskNote}
                </Text>
            ) : null}
            {code ? <Collapsible text={code} /> : null}
            {exactText ? <Collapsible text={exactText} /> : null}
            {relayed ? <Collapsible text={relayed.command} /> : null}
            {relayed?.folder ? inFolder(relayed.folder) : null}
            {relayed?.extras ? (
                <Text role="caption" tone="secondary">
                    {relayed.extras}
                </Text>
            ) : null}
            {relayed?.note ? (
                <Text role="caption" tone="warning">
                    {relayed.note}
                </Text>
            ) : null}
            {calendar && hasArgs ? <CalendarDetails tool={tool} args={args} /> : null}
            {calendar && !hasArgs && prompt.summary ? <Collapsible text={prompt.summary} /> : null}
            {!detail && !code && !exactText && !relayed && !calendar && prompt.summary ? (
                <Text role="caption" tone="secondary">
                    {prompt.summary}
                </Text>
            ) : null}
            <ChangePreview args={args} />
            {hasArgs && !exactText ? (
                <Disclosure
                    title={t(fileChange(args) ? 'prompt.technicalDetails' : 'prompt.details')}
                    open={showArgs}
                    onToggle={() => setShowArgs((v) => !v)}
                    testID="perm-details"
                >
                    <Collapsible text={JSON.stringify(args, null, 2)} />
                </Disclosure>
            ) : null}
        </Stack>
    );
}

/**
 * GAIA's permission question, inline above the composer. It is not dismissible and the
 * back gesture never answers it: only a button here does, and `busy` disables all of them.
 * The caller keys it by `prompt.id`, because the deadline is fixed when it mounts.
 */
export function PermissionPrompt({
    prompt,
    repeats = 0,
    busy = false,
    error = null,
    fullAccessPending = false,
    onAnswer,
    onExpired,
}: PermissionPromptProps) {
    const t = useT();
    const { width, height } = useWindowDimensions();
    // While the person types a queued message, only the question and its answers fit above the keyboard.
    const typing = useKeyboardState((k) => k.isVisible);
    const reduced = useReducedMotion();
    const left = useCountdown(prompt.expiresAt, prompt.timeoutSeconds);
    const expired = left !== null && left <= 0;

    const expiredFired = useRef(false);
    useEffect(() => {
        if (!expired || expiredFired.current) return;
        expiredFired.current = true;
        onExpired?.();
    }, [expired, onExpired]);

    useEffect(() => {
        Keyboard.dismiss();
    }, [prompt.id]);

    const { heading, question } = permissionPromptText(prompt, t, mark);
    useEffect(() => {
        AccessibilityInfo.announceForAccessibility(heading);
    }, [heading]);

    const scope = requiresFreshConsent(prompt.tool) ? undefined : prompt.alwaysScope;
    // Isolate the file or folder name so an LTR name can't reorder the Arabic around it.
    const scopeIn = (say: Say) => alwaysScopeWith(prompt, say, (name) => `\u2068${shortScope(` ${name}`).trim()}\u2069`) ?? prompt.alwaysLabel ?? scope ?? '';
    const suggestAlways = !!scope && repeats > 0;
    const allowLabel = t(
        prompt.tool === PROJECT_TRUST_TOOL ? 'prompt.trust.allow' : prompt.tool === PATH_ACCESS_TOOL ? 'prompt.allowForChat' : 'prompt.allowOnce',
    );
    const wide = width >= SIDE_BY_SIDE_MIN_WIDTH;
    const bodyMax = Math.max(MIN_BODY_HEIGHT, Math.round(height * BODY_HEIGHT_SHARE));

    const buttons = (
        <>
            <Button
                label={allowLabel}
                variant={suggestAlways ? 'secondary' : 'primary'}
                full={!wide}
                wrap
                disabled={busy}
                onPress={() => onAnswer('once')}
                testID="perm-allow"
            />
            {scope ? (
                <Button
                    label={t.compose((say) => say('prompt.alwaysAllow', { scope: shortScope(scopeIn(say)) }))}
                    variant={suggestAlways ? 'primary' : 'secondary'}
                    full={!wide}
                    wrap
                    disabled={busy}
                    onPress={() => onAnswer('always')}
                    testID="perm-always"
                />
            ) : null}
            <Button label={t('prompt.deny')} variant="secondary" full={!wide} wrap disabled={busy} onPress={() => onAnswer('deny')} testID="perm-deny" />
        </>
    );

    return (
        <Animated.View entering={reduced ? FadeIn : FadeInDown} exiting={reduced ? FadeOut : FadeOutDown}>
            <Surface tone="card" radius="lg" padding="lg" border="accent" role="alertdialog" accessibilityLabel={heading} testID="perm-card">
                <Stack gap="md">
                    <Row gap="sm" align="start">
                        <Icon icon={ShieldQuestion} size="md" tone="accent" />
                        <Stack flex={1} gap="xs">
                            <Text role="ui" weight="semibold" accessibilityRole="header">
                                {heading}
                            </Text>
                            <Text role="label" tone="secondary">
                                {question}
                            </Text>
                        </Stack>
                    </Row>
                    {typing ? null : (
                        <ScrollBox maxHeight={bodyMax} testID="perm-details-scroll">
                            <Stack gap="md">
                                <CallDetails prompt={prompt} />
                                {fullAccessPending ? <Notice tone="info" message={t('perm.fullPendingNote')} testID="perm-full-pending" /> : null}
                                {suggestAlways && !expired ? (
                                    <Text role="caption" tone="secondary">
                                        {t.compose(
                                            (say) =>
                                                `${say(pluralKey('prompt.repeated', repeats), { n: repeats })} ${say('prompt.alwaysHint', { scope: scopeIn(say) })}`,
                                        )}
                                    </Text>
                                ) : null}
                            </Stack>
                        </ScrollBox>
                    )}
                    {expired ? (
                        <Text role="label" tone="secondary" testID="perm-expired">
                            {t('prompt.expired')}
                        </Text>
                    ) : (
                        <Stack gap="sm">
                            {wide ? (
                                <Row gap="sm" wrap>
                                    {buttons}
                                </Row>
                            ) : (
                                <Stack gap="sm">{buttons}</Stack>
                            )}
                            {left !== null && !typing ? (
                                <Text role="caption" tone="muted" testID="perm-timer">
                                    {isolate(formatCountdown(left), 'ltr')} {'·'} {t('prompt.timer')}
                                </Text>
                            ) : null}
                        </Stack>
                    )}
                    {error ? <Notice tone="danger" message={error} /> : null}
                </Stack>
            </Surface>
        </Animated.View>
    );
}
