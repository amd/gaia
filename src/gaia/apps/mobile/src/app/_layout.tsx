// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Drawer } from 'expo-router/drawer';
import { useEffect, useMemo, useState } from 'react';
import { AppState, Platform, useWindowDimensions } from 'react-native';

import { createHttpClient } from '@/api/http';
import { shouldRetryQuery } from '@/data/queryRetry';
import { GaiaClientProvider } from '@/data/GaiaClientProvider';
import { AppErrorBoundary } from '@/features/errors/AppErrorBoundary';
import { ChatAlertFeed } from '@/features/banners/ChatAlertFeed';
import { ConnectionBanner } from '@/features/banners/ConnectionBanner';
import { ModelNeededHost } from '@/features/model';
import { AppPolls } from '@/features/notifications/AppPolls';
import { NotificationFeed } from '@/features/notifications';
import { AppDrawer } from '@/features/nav/AppDrawer';
import { RoutingEffects } from '@/features/nav/RoutingEffects';
import { INITIAL_ROUTE, PERMANENT_DRAWER_MIN_WIDTH } from '@/features/nav/navigation';
import { sweepTempFiles } from '@/features/files/shareTemp';
import { installPinnedPairing, PairingGate } from '@/features/pairing';
import { SetupGate } from '@/features/setup/SetupGate';
import { useDirection, useLocaleCode } from '@/i18n/useT';
import { applyDirection } from '@/platform/direction';
import { platformFetch } from '@/platform/fetch';
import { useConnectionStore } from '@/state/connection';
import { useLocaleStore } from '@/state/locale';
import { ScreenBannerProvider, UIProvider } from '@/ui';

// Before the first render, so the pairing screens and the saved PC use the pinned transport.
installPinnedPairing();

// Files a crash or a kill left in the cache (shared images, opened PC files, played audio).
if (Platform.OS !== 'web') {
    try {
        sweepTempFiles();
    } catch (error) {
        console.error('Clearing leftover temporary files failed', error);
    }
}

// A chat opened cold (deep link, notification) has the home screen beneath it.
export const unstable_settings = { anchor: INITIAL_ROUTE };

// Expo Router renders only the screens a layout declares.
const ROUTES = [INITIAL_ROUTE, 'new', 'connectors', 'schedules', 'settings', 'documents', 'file-preview', 'files', 'logs', 'notifications', 'skills', 'memory', 'pair', 'pair/manual', 'pair/pcs', 'chat/[id]', 'dev/ui', 'dev/ui-lists', 'dev/composer', 'dev/file-preview', 'dev/files', 'dev/forms', 'dev/markdown', 'dev/memory-gallery', 'dev/messages', 'dev/permission', 'dev/setup'];

export default function RootLayout() {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: shouldRetryQuery } } }));
    const dir = useDirection();
    const lang = useLocaleCode();
    const { width } = useWindowDimensions();
    const connection = useConnectionStore((s) => s.connection);
    const client = useMemo(
        () => (connection ? createHttpClient({ ...connection, fetch: platformFetch }) : null),
        [connection],
    );

    // Coming back to the foreground re-reads what's stale, such as the PC's status.
    useEffect(() => {
        const sub = AppState.addEventListener('change', (state) => focusManager.setFocused(state === 'active'));
        return () => sub.remove();
    }, []);

    const localeLoaded = useLocaleStore((s) => s.hydrated);
    const picked = useLocaleStore((s) => s.locale !== null);

    // Before the saved pick loads, dir is the phone's and would overwrite the pick's flags.
    useEffect(() => {
        if (localeLoaded) applyDirection(dir, lang, picked);
    }, [dir, lang, picked, localeLoaded]);

    if (!localeLoaded) return null;

    return (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <UIProvider>
                    <AppErrorBoundary variant="window">
                    <ScreenBannerProvider banner={<ConnectionBanner />}>
                        <PairingGate>
                        <SetupGate>
                            <RoutingEffects />
                            <NotificationFeed />
                            <AppPolls />
                            <ChatAlertFeed />
                            <Drawer
                                drawerContent={(props) => (
                                    <AppErrorBoundary variant="compact">
                                        <AppDrawer {...props} />
                                    </AppErrorBoundary>
                                )}
                                backBehavior="initialRoute"
                                screenOptions={{
                                    headerShown: false,
                                    freezeOnBlur: true,
                                    drawerType: width >= PERMANENT_DRAWER_MIN_WIDTH ? 'permanent' : 'front',
                                    // react-navigation reads I18nManager, which stays LTR on web, so the closed drawer would sit inside the page.
                                    drawerPosition: Platform.OS === 'web' && dir === 'rtl' ? 'right' : undefined,
                                }}
                            >
                                {ROUTES.map((name) => (
                                    <Drawer.Screen key={name} name={name} />
                                ))}
                            </Drawer>
                            <ModelNeededHost />
                        </SetupGate>
                        </PairingGate>
                    </ScreenBannerProvider>
                    </AppErrorBoundary>
                </UIProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
}
