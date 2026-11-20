// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View, type ViewStyle } from 'react-native';

import { bracketParts } from '@webui/i18n/registry';
import { MAX_MESSAGE_CHARS } from '@webui/utils/messageLimits';

import { useT } from '@/i18n/useT';
import { IconButton, Notice, Row, Stack, Surface, TextField } from '@/ui';
import { ArrowUp, Square } from '@/ui/icons';


export interface ComposerProps {
    /** Give `value` and `onChange` together to control the text; leave both out to let the composer keep it. */
    value?: string;
    onChange?(value: string): void;
    /** Returns false to keep the text in the box, as when an answer matches none of a question's choices. */
    onSend(text: string): boolean | void;
    onStop?(): void;
    /** A reply is running: Send queues the next message and Stop appears. */
    running?: boolean;
    /** Replaces the placeholder: `composer.queueNext` while running, else `composer.placeholder`. */
    placeholder?: string;
    /** Why the box can't be used right now; shown as the hint and the placeholder. */
    disabledReason?: string;
    /** GAIA waits on a question: Send answers it instead of queueing. */
    answering?: boolean;
    /** The chat is being created: the text stays editable and Send waits. */
    starting?: boolean;
}

// About the web box's 240 px cap at the body text size.
const MAX_LINES = 9;

// Row flow follows the layout direction, so Arabic mirrors without left/right.
const FIXED: ViewStyle = { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', flexGrow: 1, gap: 8 };

export function Composer({
    value,
    onChange,
    onSend,
    onStop,
    placeholder: placeholderText,
    running = false,
    answering = false,
    disabledReason,
    starting = false,
}: ComposerProps) {
    const t = useT();
    const [own, setOwn] = useState('');
    const [stopping, setStopping] = useState(false);
    const text = value ?? own;

    // Reset during render, so a later reply never starts out as Stopping.
    if (!running && stopping) setStopping(false);

    const setText = (next: string) => {
        if (value === undefined) setOwn(next);
        onChange?.(next);
    };

    const sent = text.trim();
    const tooLong = sent.length > MAX_MESSAGE_CHARS;
    const hasContent = sent.length > 0;
    const canSend = !disabledReason && !starting && hasContent && !tooLong;

    // Clears without blurring, so the keyboard stays up for the next message.
    const send = () => {
        if (!canSend) return;
        if (onSend(sent) !== false) setText('');
    };

    const stop = () => {
        setStopping(true);
        onStop?.();
    };

    // While a reply runs, an empty draft has nothing to queue, so Stop stands alone; a waiting question keeps Send.
    const showSend = !running || hasContent || !!answering;
    const sendLabel = answering ? t('composer.sendAnswer') : running && sent ? t('composer.queue') : t('composer.send');
    const spoken = disabledReason ?? placeholderText ?? (running ? t('composer.queueNext') : t('composer.placeholder'));
    // A placeholder can't draw the muted English half, so it shows the Arabic alone; screen readers get both.
    const [placeholder] = bracketParts(spoken);

    return (
        <Stack gap="sm" padding="md">
            {tooLong ? (
                <Notice
                    tone="danger"
                    title={t('composer.tooLong', {
                        sent: sent.length.toLocaleString(),
                        max: MAX_MESSAGE_CHARS.toLocaleString(),
                    })}
                    message={t('composer.tooLongHint')}
                    testID="composer-too-long"
                />
            ) : null}
            <Surface tone="card" border radius="xl" padding="sm">
                <Stack gap="xs">
                    <TextField
                        value={text}
                        onChangeText={setText}
                        placeholder={placeholder}
                        accessibilityHint={spoken}
                        hint={disabledReason}
                        accessibilityLabel={t('composer.message')}
                        multiline
                        maxLines={MAX_LINES}
                        disabled={!!disabledReason}
                        bare
                        testID="composer-input"
                    />
                    <Row gap="sm" align="center">
                        <View style={FIXED} testID="composer-actions">
                            {running ? (
                                <IconButton
                                    variant="danger"
                                    icon={Square}
                                    label={stopping ? t('composer.stopping') : t('composer.stop')}
                                    onPress={stop}
                                    disabled={stopping}
                                    testID="composer-stop"
                                />
                            ) : null}
                            {showSend ? (
                                <IconButton
                                    variant="primary"
                                    icon={ArrowUp}
                                    label={sendLabel}
                                    onPress={send}
                                    busy={starting}
                                    disabled={!canSend}
                                    testID="composer-send"
                                />
                            ) : null}
                        </View>
                    </Row>
                </Stack>
            </Surface>
        </Stack>
    );
}
