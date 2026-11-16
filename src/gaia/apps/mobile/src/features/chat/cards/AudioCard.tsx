// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';

import { formatDuration, isAudioPayload, type AudioPayload } from '@webui/utils/renderCards.core';

import { GaiaApiError, GaiaConfigError } from '@/api/errors';
import { useGaiaClient } from '@/data';
import { useReadAloudStore } from '@/features/audio/readAloudStore';
import { playPcAudio, type PcAudioControls } from '@/features/audio/playPcAudio';
import { describeError, useT } from '@/i18n/useT';
import { IconButton, Ltr, Notice, ProgressLine, Row, Spinner, Stack, Surface, Text } from '@/ui';
import { Pause, Play, Square } from '@/ui/icons';

import { UnsupportedCard } from './UnsupportedCard';

type Phase = 'idle' | 'loading' | 'playing' | 'paused';

function CardAudio({ src, caption, path, duration_seconds }: AudioPayload) {
    const t = useT();
    const client = useGaiaClient();
    const [phase, setPhase] = useState<Phase>('idle');
    const [error, setError] = useState<unknown>(null);
    const [position, setPosition] = useState<{ at: number; length: number } | null>(null);
    const run = useRef<AbortController | null>(null);
    const controls = useRef<PcAudioControls | null>(null);

    useEffect(() => () => run.current?.abort(), []);

    const stop = () => {
        run.current?.abort();
        run.current = null;
        controls.current = null;
        setPhase('idle');
        setPosition(null);
    };

    const play = () => {
        if (phase === 'paused' && controls.current) {
            controls.current.resume();
            setPhase('playing');
            return;
        }
        if (!client) {
            setError(new GaiaConfigError('mobileApp.error.noConnection'));
            return;
        }
        // Read aloud would start its next piece the moment this one took over the player.
        useReadAloudStore.getState().stop();
        run.current?.abort();
        const mine = new AbortController();
        run.current = mine;
        setError(null);
        setPhase('loading');
        void (async () => {
            try {
                const audio = await client.getGeneratedAudio(src, { signal: mine.signal });
                await playPcAudio(audio, mine.signal, {
                    onPlaying: (c) => {
                        controls.current = c;
                        if (run.current === mine) setPhase('playing');
                    },
                    onProgress: (p) => run.current === mine && setPosition({ at: p.currentTime, length: p.duration }),
                });
            } catch (err) {
                if (mine.signal.aborted) return;
                console.error(`Playing the audio card ${src} failed`, err);
                if (run.current === mine) setError(err);
            }
            if (run.current === mine) {
                run.current = null;
                controls.current = null;
                setPhase('idle');
                setPosition(null);
            }
        })();
    };

    const pause = () => {
        controls.current?.pause();
        setPhase('paused');
    };

    const fileName = path ? path.split(/[\\/]/).pop() : undefined;
    const length = position?.length || duration_seconds;
    const active = phase !== 'idle';
    return (
        <Surface tone="card" radius="md" border padding="sm" testID="card-audio">
            <Stack gap="sm">
                <Row gap="sm" align="center">
                    <IconButton
                        icon={phase === 'playing' ? Pause : Play}
                        label={phase === 'playing' ? t('cards.pause') : phase === 'paused' ? t('cards.resume') : t('media.audioPlay')}
                        variant="filled"
                        disabled={phase === 'loading'}
                        onPress={phase === 'playing' ? pause : play}
                        testID="card-audio-play"
                    />
                    {active ? <IconButton icon={Square} label={t('cards.stop')} onPress={stop} testID="card-audio-stop" /> : null}
                    {phase === 'loading' ? <Spinner size="sm" /> : null}
                    {position || length !== undefined ? (
                        <Text role="caption" tone="secondary" mono>
                            <Ltr>{position ? `${formatDuration(position.at)} / ${length !== undefined ? formatDuration(length) : '-:--'}` : formatDuration(length!)}</Ltr>
                        </Text>
                    ) : null}
                </Row>
                {position && position.length > 0 ? <ProgressLine value={position.at / position.length} /> : null}
                {error !== null ? (
                    <Notice
                        tone="danger"
                        title={t('media.audioUnplayable')}
                        message={error instanceof GaiaApiError ? error.detail || undefined : describeError(error, t)}
                        onDismiss={() => setError(null)}
                        testID="card-audio-failed"
                    />
                ) : null}
                {caption ? (
                    <Text role="body" dir="auto">
                        {caption}
                    </Text>
                ) : null}
                {fileName ? (
                    <Text role="caption" tone="secondary" numberOfLines={1}>
                        <Ltr>{fileName}</Ltr>
                    </Text>
                ) : null}
            </Stack>
        </Surface>
    );
}

export function AudioCard({ data }: { data: unknown }) {
    if (!isAudioPayload(data)) return <UnsupportedCard variant="invalid" render="audio" data={data} />;
    return <CardAudio src={data.src} caption={data.caption} path={data.path} duration_seconds={data.duration_seconds} />;
}
