// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { bracketParts } from '@webui/i18n/registry';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { PermissionMode } from '@/api/client.types';
import { useCreateSession, useDocuments, useGaiaClient, useStartScreen, useSystemStatus } from '@/data';
import { FlagshipAgentBanner } from '@/features/banners/FlagshipAgentBanner';
import { Composer, FolderChip, ModelChip, PermissionChip, PrivateChip, useAttachments, useComposerQueue } from '@/features/composer';
import { useNextChatDocs } from '@/features/documents';
import { QueuedMessages } from '@/features/composer/QueuedMessages';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { useT } from '@/i18n/useT';
import { useLastChatStore } from '@/state/lastChat';
import { useRouteNoticeStore } from '@/state/routeNotice';
import { Disclosure, IconButton, Notice, ProgressLine, Row, Screen, Stack, Surface, Text } from '@/ui';
import { DotGrid } from '@/ui/controls/DotGrid';
import { X } from '@/ui/icons';

import { DocumentBriefCard } from './DocumentBriefCard';
import { LibraryDocuments } from './LibraryDocuments';
import { greetingKey } from './greeting';
import { setupError } from './setupFailed';
import { showWarmUp, startingReason } from './starting';
import { TryChips } from './TryChips';
import { withExample } from './tryExamples';
import { useAssistantNotice, useFirstSend } from './useNewChat';
import { shortModelName, warmUpLine, warmUpProgress } from './warmUp';

export interface NewChatScreenProps {
    // Opens the chat once its first message is under way.
    onOpen: (sessionId: string) => void;
    // Opens the document library, to choose what the new chat searches.
    onChooseDocuments: () => void;
    initialText?: string;
    // A skill's prompt goes in the box under any unsent draft; a new `id` adds it again.
    prompt?: { id: string; text: string };
}

export function NewChatScreen({ onOpen, onChooseDocuments, initialText = '', prompt }: NewChatScreenProps) {
    const t = useT();
    const status = useSystemStatus().data;
    const createSession = useCreateSession();
    const client = useGaiaClient();
    const startScreen = useStartScreen().data;
    const attachments = useAttachments();
    // The chat doesn't exist yet: these are applied as it is made.
    const [isPrivate, setPrivate] = useState(false);
    const [mode, setMode] = useState<PermissionMode>('ask');
    const documents = useDocuments().data?.documents;
    const attachedIds = useNextChatDocs((s) => s.ids);
    const holdDocuments = useNextChatDocs((s) => s.add);
    const dropDocument = useNextChatDocs((s) => s.remove);
    const queue = useComposerQueue(null);
    const { state, setText, submit } = useFirstSend(
        {
            createSession: async () => {
                const session = await createSession.mutateAsync({ document_ids: useNextChatDocs.getState().ids, private: isPrivate });
                useNextChatDocs.getState().clear();
                if (mode !== 'ask') await client?.setPermissionMode(session.id, mode);
                return session;
            },
            deliver: queue.deliver,
            flush: queue.flush,
            queueHeld: queue.queueHeld,
            takeHeld: () => queue.takeHeld().map((item) => item.text),
        },
        initialText,
    );
    const assistant = useAssistantNotice();
    const routeNotice = useRouteNoticeStore((s) => s.notice);
    const dismissRouteNotice = useRouteNoticeStore((s) => s.dismiss);
    const [detailsOpen, setDetailsOpen] = useState(false);

    // A message sent while GAIA is still starting waits here, then goes out on its own.
    const starting = startingReason(status, t) !== null;
    const [held, setHeld] = useState<{ text: string } | null>(null);
    const sentHeld = useRef<{ text: string } | null>(null);
    const heldOpening = starting ? (held?.text ?? null) : null;
    const send = useCallback(
        (message: string) => {
            if (starting) setHeld({ text: message });
            else void submit(message, queue.hold);
        },
        [starting, submit, queue.hold],
    );
    useEffect(() => {
        if (starting || held === null || sentHeld.current === held) return;
        sentHeld.current = held;
        void submit(held.text, queue.hold);
    }, [starting, held, submit, queue.hold]);

    // Text in the box means the person started here, so the launch restore must not move them.
    const hasText = state.text !== '';
    useEffect(() => {
        if (hasText) useLastChatStore.getState().markNewChatChosen();
    }, [hasText]);

    useEffect(() => {
        if (state.open) onOpen(state.open);
    }, [state.open, onOpen]);

    const promptId = prompt?.id;
    const promptText = prompt?.text;
    useEffect(() => {
        if (promptText) setText((draft) => (draft.trim() ? `${draft}\n\n${promptText}` : promptText));
    }, [promptId, promptText, setText]);

    const lastExample = useRef('');
    const fillExample = useCallback(
        (example: string) => {
            setText((draft) => withExample(draft, lastExample.current, example));
            lastExample.current = example;
        },
        [setText],
    );

    const briefCard = startScreen?.cards.find((c) => c.skill === 'document-brief');
    const indexed = (documents ?? []).filter((d) => d.indexing_status === 'complete');
    // Same as the web UI's first-task pick: the prompt goes out at once, or waits in the box when it needs a subject.
    const askAboutDocuments = () => {
        if (!briefCard) return;
        holdDocuments(indexed.map((d) => d.id));
        if (briefCard.needs_input) setText(briefCard.prompt);
        else send(briefCard.prompt);
    };
    const failedSetup = setupError(status);
    const warming = warmUpProgress(status?.warm_up);
    const [greeting, gloss] = bracketParts(t(greetingKey(new Date())));

    return (
        <Screen
            keyboard="avoid"
            scroll
            footerBleed
            backdrop={<DotGrid />}
            header={<ScreenHeader />}
            footer={
                <>
                    {heldOpening !== null && (
                        <Stack paddingX="lg" testID="held-opening" accessibilityRole="list">
                            <Surface tone="secondary" radius="md" padding="sm" border>
                                <Stack gap="xs">
                                    <Row gap="sm" align="center">
                                        <Stack flex={1}>
                                            <Text role="ui" numberOfLines={2} dir="auto">
                                                {heldOpening}
                                            </Text>
                                        </Stack>
                                        <IconButton
                                            icon={X}
                                            size="sm"
                                            label={t('queue.removeAria', { text: heldOpening })}
                                            onPress={() => setHeld(null)}
                                            testID="held-opening-remove"
                                        />
                                    </Row>
                                    <Text role="caption" tone="secondary" accessibilityLabel={t('composer.heldLabel')}>
                                        {t('composer.heldUntilStarted')}
                                    </Text>
                                </Stack>
                            </Surface>
                        </Stack>
                    )}
                    <QueuedMessages items={queue.held} replying={false} onRemove={queue.remove} onRetry={queue.retry} />
                    <Composer
                        value={state.text}
                        onChange={setText}
                        onSend={send}
                        disabledReason={startingReason(status, t) ?? undefined}
                        attachments={attachments}
                        leftControls={
                            <>
                                <PermissionChip mode={mode} onChange={setMode} />
                                <PrivateChip on={isPrivate} onChange={setPrivate} />
                            </>
                        }
                        rightControls={<ModelChip />}
                    />
                </>
            }
        >
            <Stack gap="lg" paddingX="lg" paddingY="xl" flex={1} justify="center">
                {!state.sending && (
                    <Stack gap="xs">
                        <Text role="display" accessibilityRole="header">
                            {greeting}
                        </Text>
                        {gloss !== undefined && (
                            <Text role="caption" tone="secondary">
                                {`\u200F${gloss}`}
                            </Text>
                        )}
                    </Stack>
                )}
                <FlagshipAgentBanner />
                {failedSetup !== null && (
                    <Notice
                        tone="danger"
                        title={t('newChat.setupFailed.title')}
                        message={`${failedSetup}\n\n${t('newChat.setupFailed.hint')}`}
                    />
                )}
                {!state.sending && <FolderChip folder={startScreen?.working_folder} />}
                {documents && !state.sending && (
                    <LibraryDocuments
                        documents={documents}
                        attachedIds={attachedIds}
                        onAttach={holdDocuments}
                        onRemove={dropDocument}
                        onChoose={onChooseDocuments}
                    />
                )}
                {briefCard && indexed.length > 0 && !state.sending && (
                    <DocumentBriefCard count={indexed.length} onPress={askAboutDocuments} />
                )}
                {warming !== null && showWarmUp(status) && (
                    <ProgressLine label={warmUpLine(warming, t, shortModelName)} />
                )}
                {routeNotice !== null && (
                    <Notice
                        tone="info"
                        message={t(routeNotice)}
                        action={{ label: t('banner.dismiss'), onPress: dismissRouteNotice }}
                    />
                )}
                {state.sending && state.opening !== null && (
                    <Stack self="end" maxWidth="85%" testID="opening-message">
                        <Surface tone="userMsg" radius="lg" padding="md">
                            <Text role="body" dir="auto">
                                {state.opening}
                            </Text>
                        </Surface>
                    </Stack>
                )}
                {state.error !== null && <Notice tone="danger" message={state.error} />}
                {!state.sending && startingReason(status, t) === null && <TryChips onPick={fillExample} />}
                {assistant.problem !== null && (
                    <Stack gap="sm">
                        <Notice
                            tone="warning"
                            message={t('newChat.assistantUnavailable')}
                            action={{ label: t('newChat.tryAgain'), onPress: assistant.retry }}
                        />
                        <Disclosure
                            title={t('newChat.details')}
                            open={detailsOpen}
                            onToggle={() => setDetailsOpen((v) => !v)}
                        >
                            <Text role="caption" tone="secondary" mono selectable>
                                {assistant.problem}
                            </Text>
                        </Disclosure>
                    </Stack>
                )}
            </Stack>
        </Screen>
    );
}
