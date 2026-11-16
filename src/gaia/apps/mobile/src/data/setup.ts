// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import type {
    GaiaClient,
    GpuMemoryStatus,
    LemonadeFound,
    LemonadeTarget,
    PreflightReport,
    ProviderSummaryList,
    SetupCheck,
    SetupPlan,
    SetupRunOptions,
    SetupRunStatus,
} from '@/api/client.types';
import { GaiaError, GaiaNetworkError } from '@/api/errors';
import { downloadsActive } from '@webui/components/setup/downloadModel';
import { catalogOf, savedVision, skipChanges, type Selection } from '@webui/components/setup/selection';

import { useGaiaClient } from './GaiaClientProvider';
import { modelMenuKeys } from './modelMenu';
import { queryKeys, requireClient } from './queries';

/** How often to re-read a running setup, as the web UI's setup screen does. */
export const SETUP_POLL_MS = 1000;
/** How long a refused setup check counts as "still starting"; webui App.tsx BACKEND_START_WAIT_MS. */
export const BACKEND_START_WAIT_MS = 60_000;
/** How long to wait for an embedded model server's update; webui App.tsx UPGRADE_WAIT_MS. */
export const UPGRADE_WAIT_MS = 5 * 60_000;

/**
 * Where first-run setup stands, as webui's `SetupGate` (App.tsx).
 * `needed` is the first-run flow; `addingLocal` reopens it from Settings; `serverDown`
 * is a PC set up before whose model server isn't answering; `unknown` is a check that
 * could not be answered, which is not the same as "not set up".
 */
export type SetupGateState =
    | { state: 'checking' }
    | { state: 'upgrading' }
    | { state: 'ready' }
    | { state: 'needed'; check: SetupCheck }
    | { state: 'serverDown'; modelListUnread: boolean }
    | { state: 'addingLocal' }
    | { state: 'unknown'; error: unknown; upgradeFailed: boolean };

/** The decision App.tsx makes from a check that answered. */
export function gateFromCheck(check: SetupCheck): SetupGateState {
    if (check.ready) return { state: 'ready' };
    if (check.stage === 'server' && check.initialized) {
        return { state: 'serverDown', modelListUnread: !!check.model_list_unread };
    }
    return { state: 'needed', check };
}

class UpgradeFailed extends Error {}

export interface SetupCheckOptions {
    /** Only the launch check treats a refused connection as "still starting". */
    waitForStart?: boolean;
    sleep?: (ms: number) => Promise<void>;
    pollMs?: number;
    /** Reports the upgrade wait so the screen can say so. */
    onUpgrading?: () => void;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function waitForEmbeddedUpgrade(client: GaiaClient, sleep: (ms: number) => Promise<void>, pollMs: number) {
    let waited = 0;
    for (;;) {
        const status = await client.getSetupStatus();
        if (status.state === 'ready') return;
        if (status.state === 'failed' || status.state === 'cancelled') {
            throw new UpgradeFailed(status.error || undefined);
        }
        if (waited >= UPGRADE_WAIT_MS) throw new GaiaError('setup.upgrading.slow');
        await sleep(pollMs);
        waited += pollMs;
    }
}

/**
 * Asks GAIA whether it is set up and says what the app should show, as webui's `checkSetup`
 * (App.tsx) does. Never throws: a check that could not be answered is `unknown`.
 */
export async function runSetupCheck(client: GaiaClient, options: SetupCheckOptions = {}): Promise<SetupGateState> {
    const sleep = options.sleep ?? wait;
    const pollMs = options.pollMs ?? SETUP_POLL_MS;
    let waited = 0;
    try {
        let check: SetupCheck;
        for (;;) {
            try {
                check = await client.checkSetup(false);
                break;
            } catch (err) {
                const starting = err instanceof GaiaNetworkError;
                if (!options.waitForStart || !starting || waited >= BACKEND_START_WAIT_MS) throw err;
                await sleep(pollMs);
                waited += pollMs;
            }
        }
        // A new pinned embedded server is an update, not first-run setup: the models stay.
        if (!check.ready && check.stage === 'upgrade') {
            options.onUpgrading?.();
            const run = await client.getSetupStatus();
            const updating = run.upgrade_embedded && (run.state === 'running' || run.state === 'verifying');
            if (!updating) await client.startSetup(false, false, { upgradeEmbedded: true });
            await waitForEmbeddedUpgrade(client, sleep, pollMs);
            check = await client.checkSetup(false);
        }
        return gateFromCheck(check);
    } catch (err) {
        if (err instanceof UpgradeFailed) {
            const error = err.message ? err : new GaiaError('setup.upgrading.stopped');
            return { state: 'unknown', error, upgradeFailed: true };
        }
        return { state: 'unknown', error: err, upgradeFailed: false };
    }
}

/** The first-run gate: checks at launch, and again on `recheck`. `open` reopens setup from Settings. */
export function useSetupGate() {
    const client = useGaiaClient();
    const [gate, setGate] = useState<SetupGateState>({ state: 'checking' });
    const latest = useRef(0);

    const recheck = useCallback(
        async (opts: { quiet?: boolean; waitForStart?: boolean } = {}) => {
            if (!client) return;
            const ticket = ++latest.current;
            if (!opts.quiet) setGate({ state: 'checking' });
            const next = await runSetupCheck(client, {
                waitForStart: opts.waitForStart,
                onUpgrading: () => {
                    if (ticket === latest.current) setGate({ state: 'upgrading' });
                },
            });
            if (ticket === latest.current) setGate(next);
        },
        [client],
    );

    // The gate starts as `checking`, so the launch check sets nothing until it has an answer.
    useEffect(() => {
        if (!client) return undefined;
        const ticket = ++latest.current;
        let current = true;
        void runSetupCheck(client, {
            waitForStart: true,
            onUpgrading: () => {
                if (current && ticket === latest.current) setGate({ state: 'upgrading' });
            },
        }).then((next) => {
            if (current && ticket === latest.current) setGate(next);
        });
        return () => {
            current = false;
        };
    }, [client]);

    const finish = useCallback(() => setGate({ state: 'ready' }), []);
    const open = useCallback(() => setGate({ state: 'addingLocal' }), []);
    return { gate, recheck, finish, open };
}

/** The setup run: polled every second while it is running or verifying, or a single model is moving. */
export function useSetupStatus() {
    const client = useGaiaClient();
    return useQuery<SetupRunStatus>({
        queryKey: queryKeys.setupStatus,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getSetupStatus({ signal }),
        refetchInterval: (query) => (downloadsActive(query.state.data) ? SETUP_POLL_MS : false),
    });
}

/** What setup will use and download; `catalog` entries that are `partial` and `selected` offer a resume. */
export function useSetupPlan(enabled = true) {
    const client = useGaiaClient();
    return useQuery<SetupPlan>({
        queryKey: queryKeys.setupPlan,
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getSetupPlan({ signal }),
    });
}

/** This PC's hardware as setup reads it (memory, disk, GPU, NPU); a failed read stays an error for the screen to say so. */
export function useSetupPreflight() {
    const client = useGaiaClient();
    return useQuery<PreflightReport>({
        queryKey: ['setupPreflight'],
        enabled: client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).getPreflight({ signal }),
    });
}

/** The GPU memory split and what each size unlocks; `settingGb` also reads the manual steps for that size. Read only. */
export function useGpuMemory(settingGb: number | null = null, enabled = true) {
    const client = useGaiaClient();
    return useQuery<GpuMemoryStatus>({
        queryKey: ['gpuMemory', settingGb],
        enabled: enabled && client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).getGpuMemory(settingGb ?? undefined, { signal }),
    });
}

/** "I've done this" with a size, or "Keep {gb}" with none: records or drops the pending GPU memory change, then rereads the split. */
export function useGpuMemoryPending() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<GpuMemoryStatus, Error, number | null>({
        mutationFn: (settingGb) => {
            const api = requireClient(client);
            return settingGb == null ? api.dismissGpuMemoryPending() : api.recordGpuMemoryPending(settingGb);
        },
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['gpuMemory'] }),
    });
}

/** Which model server GAIA uses; `offer` stays true until someone on the PC chooses, so it is re-read while waiting. */
export function useLemonadeTarget() {
    const client = useGaiaClient();
    return useQuery<LemonadeTarget>({
        queryKey: ['lemonadeTarget'],
        enabled: client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).getLemonadeTarget({ signal }),
        refetchInterval: (query) => (query.state.data?.offer ? SETUP_POLL_MS * 3 : false),
    });
}

/** A model server the PC already runs; only asked while the PC still has to choose one. */
export function useLemonadeDetect(enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<{ found: LemonadeFound | null; unchecked?: string[] }>({
        queryKey: ['lemonadeDetect'],
        enabled: enabled && client !== null,
        retry: false,
        staleTime: Infinity,
        queryFn: ({ signal }) => requireClient(client).detectLemonade({ signal }),
    });
}

/** Starts only the model server on the PC (no models); `gaiaLemonade` switches to GAIA's own. Follow it with `useSetupStatus`. */
export function useStartSetupServer() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupRunStatus, Error, { gaiaLemonade?: boolean } | void>({
        mutationFn: (options) =>
            requireClient(client).startSetup(false, !!options?.gaiaLemonade, { serverOnly: true }),
        onSuccess: (run) => {
            queryClient.setQueryData(queryKeys.setupStatus, run);
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
        },
    });
}

/** Stops the running setup on the PC, or one model's download with `mutate(modelId)`; finished files are kept. */
export function useCancelSetup() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<{ cancelled: boolean }, Error, string | void>({
        mutationFn: (modelId) => requireClient(client).cancelSetup(modelId || undefined),
        onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus }),
    });
}

/** Every provider, each `blocked` while Local only is on and `configured` once the PC has it ready. */
export function useCloudProviders(enabled = true) {
    const client = useGaiaClient();
    return useQuery<ProviderSummaryList>({
        queryKey: ['cloudProviders'],
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).listProviderSummaries({ signal }),
    });
}

/** `mutate({ options })` starts the setup run; the status poll then follows it. */
export function useStartSetup() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupRunStatus, Error, { options?: SetupRunOptions }>({
        mutationFn: ({ options }) => requireClient(client).startSetup(false, false, options),
        onSuccess: (status) => {
            queryClient.setQueryData(queryKeys.setupStatus, status);
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
        },
    });
}

/**
 * llama.cpp runs on Vulkan, the GPU backend GAIA has tested, where the server's own pick or an earlier
 * saved one is another and this PC lists Vulkan. Returns whether it set it; an unreachable server is left alone.
 */
export async function applyTestedBackend(client: GaiaClient): Promise<boolean> {
    const server = await client.getModelServer();
    if (!server.reachable) return false;
    const vulkan = (server.available_backends ?? []).some((b) => b.name === 'vulkan');
    if (!vulkan || (server.preferred_backend ?? server.default_backend) === 'vulkan') return false;
    await client.setModelServerBackend('vulkan');
    return true;
}

/** `mutate()` puts llama.cpp on the tested backend before a local setup starts. */
export function useApplyTestedBackend() {
    const client = useGaiaClient();
    return useMutation<boolean, Error, void>({ mutationFn: () => applyTestedBackend(requireClient(client)) });
}

/** Deletes a model that downloaded but didn't load, then downloads it again; follow it with `useSetupStatus`. */
export function useRedownloadSetupModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupRunStatus, Error, string>({
        mutationFn: async (modelId) => {
            const c = requireClient(client);
            await c.removeSetupModel(modelId);
            return c.downloadSetupModel(modelId, false);
        },
        onSuccess: (run) => {
            queryClient.setQueryData(queryKeys.setupStatus, run);
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupPlan });
        },
    });
}

/** Reads the NPU again after a fix on the PC; the plan it answers replaces the one shown. */
export function useRecheckNpu() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupPlan, Error, void>({
        mutationFn: () => requireClient(client).recheckSetupNpu(),
        onSuccess: (plan) => {
            queryClient.setQueryData(queryKeys.setupPlan, plan);
            void queryClient.invalidateQueries({ queryKey: ['setupPreflight'] });
        },
    });
}

/**
 * Saves what the Customize screen ticked, in the order the web UI's setup does before it downloads:
 * the chat model (a 409 `no_fit` asks to be sent again with `acknowledgeNoFit`), the image reader, then the skip list.
 * Resolves to the plan the PC now holds.
 */
export async function saveModelChoices(
    client: GaiaClient,
    plan: SetupPlan,
    selection: Selection,
    acknowledgeNoFit: boolean,
): Promise<SetupPlan> {
    let latest = plan;
    if (selection.defaultChat && selection.defaultChat !== latest.chat_model.id) {
        latest = await client.chooseSetupChatModel(selection.defaultChat, acknowledgeNoFit);
    }
    const vision = savedVision(catalogOf(latest), selection);
    if (vision !== (latest.vision_model ?? null)) latest = await client.chooseSetupVisionModel(vision);
    for (const change of skipChanges(latest, selection)) {
        latest = await client.skipSetupModel(change.id, change.skipped);
    }
    return latest;
}

/** `mutate({ plan, selection, acknowledgeNoFit })` saves the choices on the PC and refreshes the plan. */
export function useSaveModelChoices() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupPlan, Error, { plan: SetupPlan; selection: Selection; acknowledgeNoFit: boolean }>({
        mutationFn: ({ plan, selection, acknowledgeNoFit }) =>
            saveModelChoices(requireClient(client), plan, selection, acknowledgeNoFit),
        onSuccess: (plan) => queryClient.setQueryData(queryKeys.setupPlan, plan),
    });
}

/**
 * Connects a provider that already holds a key on the PC, selects the model and starts the cloud
 * setup run, as webui's SetupScreen `connect` does. No key is sent: `wanted` is the picked model id,
 * and `wantedName` the name an error says.
 */
export function useConnectCloud() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupRunStatus, Error, { provider: string; wanted: string | null; wantedName?: string }>({
        mutationFn: async ({ provider, wanted, wantedName }) => {
            const api = requireClient(client);
            await api.connectProvider(provider);
            const { models } = await api.listProviderModels(provider);
            const pick = wanted ? models.find((m) => m.id === wanted) : (models.find((m) => m.rank === 1) ?? models[0]);
            if (!pick) {
                throw wanted
                    ? new GaiaError('setup.key.modelNotOffered', { model: wantedName ?? wanted })
                    : new GaiaError('setup.key.noModels');
            }
            await api.selectModel(pick.id);
            return api.startSetup(true, false, {});
        },
        onSuccess: (status) => {
            queryClient.setQueryData(queryKeys.setupStatus, status);
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
            void queryClient.invalidateQueries({ queryKey: ['cloudProviders'] });
            void queryClient.invalidateQueries({ queryKey: modelMenuKeys.active });
        },
    });
}

/** Starts one model's download now (Retry, Resume, or one skipped); the status poll then follows it. */
export function useDownloadSetupModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupRunStatus, Error, { modelId: string; acknowledgeLowDisk?: boolean }>({
        mutationFn: ({ modelId, acknowledgeLowDisk }) => requireClient(client).downloadSetupModel(modelId, !!acknowledgeLowDisk),
        onSuccess: (status) => {
            queryClient.setQueryData(queryKeys.setupStatus, status);
            void queryClient.invalidateQueries({ queryKey: queryKeys.setupStatus });
        },
    });
}

/** Deletes a downloaded model at the user's request; the plan it answers (with `freed_gb`) replaces the cached one. */
export function useRemoveSetupModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupPlan & { freed_gb: number }, Error, { modelId: string }>({
        mutationFn: ({ modelId }) => requireClient(client).removeSetupModel(modelId),
        onSuccess: (plan) => {
            queryClient.setQueryData(queryKeys.setupPlan, plan);
            void queryClient.invalidateQueries({ queryKey: ['providerModels'] });
        },
    });
}

/** Skips one of setup's models; the new plan replaces the cached one. */
export function useSkipSetupModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SetupPlan, Error, { modelId: string }>({
        mutationFn: ({ modelId }) => requireClient(client).skipSetupModel(modelId, true),
        onSuccess: (plan) => queryClient.setQueryData(queryKeys.setupPlan, plan),
    });
}
