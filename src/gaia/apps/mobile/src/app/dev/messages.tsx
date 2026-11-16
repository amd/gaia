// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect, useLocalSearchParams } from 'expo-router';
import { devGalleryEnabled } from '@/features/dev/devGallery';

import { MessagesGallery, type MessagesGalleryState } from '@/features/chat/messages/MessagesGallery';
import { FIXTURES } from '@/features/chat/messages/__fixtures__/messages';
import { SessionPanelGallery, type SessionPanelGalleryState } from '@/features/chat/session-panel/SessionPanelGallery';
import { UIProvider } from '@/ui';

const OPEN_STATES = ['actions', 'delete', 'edit', 'guided'];
const PANEL_STATES = ['loaded', 'empty', 'error', 'notRunning', 'ctxMeasuring', 'ctxNormal', 'ctxEstimated', 'ctxFilling', 'ctxChat', 'ctxNoTurn', 'envFolders', 'envRemove', 'envOffline', 'envFull', 'envEmpty', 'chgList', 'chgConfirm', 'chgConflict', 'chgGit', 'chgPending', 'chgError', 'chgReview', 'ansFull', 'ansPending', 'ansNoLive', 'ansError', 'outItems', 'outEmpty', 'envPick', 'envPickRefused'];

function isState(state?: string): state is MessagesGalleryState {
    return !!state && (state in FIXTURES || OPEN_STATES.includes(state));
}

export default function MessagesGalleryRoute() {
    const { state } = useLocalSearchParams<{ state?: string }>();
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    const panel = state?.startsWith('panel-') ? state.slice('panel-'.length) : undefined;
    if (panel && PANEL_STATES.includes(panel)) {
        return (
            <UIProvider>
                <SessionPanelGallery state={panel as SessionPanelGalleryState} />
            </UIProvider>
        );
    }
    const fixture = isState(state) ? state : 'long';
    return (
        <UIProvider>
            <MessagesGallery state={fixture} />
        </UIProvider>
    );
}
