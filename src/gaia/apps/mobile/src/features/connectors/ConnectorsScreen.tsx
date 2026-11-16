// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { scopeLabel } from '@webui/utils/connectorScopes';
import { ref } from '@webui/i18n/registry';
import { useRouter } from 'expo-router';
import { useMemo, useState, type ReactNode } from 'react';
import { View } from 'react-native';

import { PC_ONLY_REFUSAL } from '@/api/constants';
import type { AgentMcpServer, ConnectorRow, UnavailableConnectorRow } from '@/api/client.types';
import {
    useAgentMcps,
    useAgents,
    useChangeConnectorAccess,
    useConnectorActivations,
    useConnectorGrants,
    useConnectors,
    useConnectorServerAction,
} from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { ChipButton } from '@/features/schedules/ChipButton';
import { SettingsGroup } from '@/features/skills/SettingsGroup';
import { describeError, describeErrorWith, useT } from '@/i18n/useT';
import {
    ActionSheet,
    EmptyState,
    ErrorState,
    Icon,
    IconButton,
    Pressable,
    ListSkeleton,
    Notice,
    Row,
    Screen,
    Stack,
    Surface,
    Switch,
    Text,
    TextField,
    Ltr,
    useTheme,
} from '@/ui';
import { ChevronDown, ChevronRight, CircleCheck, Ellipsis, Pause, Play, Trash2 } from '@/ui/icons';

import {
    activatableAgents,
    flagshipAgentId,
    grantAgents,
    MCP_DEFAULT_GRANT_SCOPES,
    nextScopes,
    personalizeRoute,
    personalizeSourceKind,
    scopesForActivation,
    soleMcpAgent,
} from './connectorAccess';
import {
    agentMcpKey,
    connectorDescription,
    disconnectNotice,
    GOOGLE_CONNECTOR_ID,
    groupConnectors,
    oauthClientStatus,
    SEARCH_ABOVE,
    tileStatus,
    unavailableHint,
} from './connectorList';

type Translator = ReturnType<typeof useT>;

export function ConnectorsScreen() {
    const t = useT();
    return (
        <Screen scroll header={<ScreenHeader title={t('settings.section.connectors')} />}>
            <ConnectorsBody />
        </Screen>
    );
}

/** The connector list, for the screen and for the Settings pane. */
export function ConnectorsBody() {
    const t = useT();
    const connectors = useConnectors();
    const agentMcps = useAgentMcps();
    const [query, setQuery] = useState('');
    const [expanded, setExpanded] = useState<string | null>(null);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const [notice, setNotice] = useState<string | null>(null);

    const rows = connectors.data?.connectors ?? [];
    const servers = agentMcps.data?.agent_mcps ?? [];
    const q = query.trim();
    const groups = useMemo(() => groupConnectors(rows, query, t), [rows, query, t]);

    const failure = connectors.error ?? agentMcps.error;
    const loading = connectors.isPending || agentMcps.isPending;
    const toggle = (id: string) => setExpanded((prev) => (prev === id ? null : id));
    const groupOpen = (id: string, byDefault: boolean) => !!q || (openGroups[id] ?? byDefault);
    const setGroup = (id: string, byDefault: boolean) => () =>
        setOpenGroups((prev) => ({ ...prev, [id]: !(prev[id] ?? byDefault) }));
    const noMatch = !!q && groups.unavailable.length + groups.connected.length + groups.accounts.length + groups.servers.length === 0;

    function renderGroup(id: string, title: string, count: number, byDefault: boolean, hint: string | null, body: ReactNode) {
        if (count === 0) return null;
        return (
            <SettingsGroup
                key={id}
                title={title}
                hint={hint}
                count={count}
                open={groupOpen(id, byDefault)}
                onToggle={setGroup(id, byDefault)}
            >
                {body}
            </SettingsGroup>
        );
    }

    const tiles = (list: ConnectorRow[]) =>
        list.map((c) => (
            <ConnectorTile key={c.id} connector={c} expanded={expanded === c.id} onToggle={() => toggle(c.id)} onDisconnected={setNotice} />
        ));

    return (
        <>
            <Stack gap="md" paddingX="lg" paddingY="md">
                <Text role="ui" tone="secondary">{t('settings.connectors.lede')}</Text>
                {notice ? <Notice tone="info" message={notice} /> : null}
                {failure ? (
                    <ErrorState
                        title={describeError(failure, t)}
                        onRetry={() => {
                            void connectors.refetch();
                            void agentMcps.refetch();
                        }}
                    />
                ) : loading ? (
                    <ListSkeleton rows={6} />
                ) : (
                    <>
                        {rows.length > SEARCH_ABOVE ? (
                            <TextField
                                value={query}
                                onChangeText={setQuery}
                                placeholder={t.bare('settings.connectors.search')}
                                accessibilityLabel={t('settings.connectors.search')}
                            />
                        ) : null}
                        {rows.length === 0 ? <EmptyState title={t('settings.connectors.none')} /> : null}
                        {noMatch ? <Text tone="muted">{t('settings.connectors.noMatch')}</Text> : null}
                        {renderGroup(
                            'unavailable',
                            t('settings.connectors.unavailable'),
                            groups.unavailable.length,
                            true,
                            unavailableHint(groups.unavailable, t),
                            groups.unavailable.map((c) => <UnavailableTile key={c.id} connector={c} />),
                        )}
                        {renderGroup('connected', t('settings.connectors.connected'), groups.connected.length, true, null, tiles(groups.connected))}
                        {renderGroup(
                            'accounts',
                            t('settings.connectors.accounts'),
                            groups.accounts.length,
                            groups.nothingConnected,
                            t('settings.connectors.accountsHint'),
                            tiles(groups.accounts),
                        )}
                        {renderGroup(
                            'servers',
                            t('settings.connectors.servers'),
                            groups.servers.length,
                            false,
                            t('settings.connectors.serversHint'),
                            tiles(groups.servers),
                        )}
                        {!q
                            ? renderGroup(
                                  'agentServers',
                                  t('settings.connectors.agentServers'),
                                  servers.length,
                                  false,
                                  null,
                                  servers.map((s) => (
                                      <AgentMcpTile
                                          key={agentMcpKey(s)}
                                          server={s}
                                          expanded={expanded === agentMcpKey(s)}
                                          onToggle={() => toggle(agentMcpKey(s))}
                                      />
                                  )),
                              )
                            : null}
                    </>
                )}
            </Stack>
        </>
    );
}

/** The desktop's `.connector-tile`: a bordered row that opens a detail panel under a rule. */
function Tile({ header, open, children }: { header: ReactNode; open?: boolean; children?: ReactNode }) {
    const { colors, space } = useTheme();
    return (
        <Surface tone="secondary" radius="md" border>
            {header}
            {open ? (
                <View style={{ borderTopWidth: 1, borderTopColor: colors.borderLight, paddingHorizontal: space.lg, paddingVertical: space.lg }}>
                    {children}
                </View>
            ) : null}
        </Surface>
    );
}

function TileHeader({ name, trailing, onPress, open, menu }: { name: string; trailing?: ReactNode; onPress?: () => void; open?: boolean; menu?: ReactNode }) {
    const { space } = useTheme();
    const body = (
        <View style={{ paddingHorizontal: space.lg, paddingVertical: space.md }}>
            <Row gap="sm" justify="between">
                <Row gap="sm" wrap flex={1}>
                    <Text role="ui" weight="semibold" dir="auto">{name}</Text>
                    {trailing}
                </Row>
                {onPress ? <Chevron open={!!open} /> : null}
            </Row>
        </View>
    );
    const main = onPress ? (
        <Pressable onPress={onPress} accessibilityLabel={name} accessibilityState={{ expanded: !!open }}>
            {body}
        </Pressable>
    ) : (
        body
    );
    if (!menu) return main;
    return (
        <Row>
            <View style={{ flex: 1 }}>{main}</View>
            <View style={{ paddingEnd: space.sm }}>{menu}</View>
        </Row>
    );
}

/** An outlined pill: the type tag (`.connector-tile-type`) and the paused tag (`.connector-status.disabled`). */
function Pill({ label, bordered = true }: { label: string; bordered?: boolean }) {
    const { colors, radius } = useTheme();
    return (
        <View
            style={{
                backgroundColor: colors.bgTertiary,
                borderColor: colors.borderLight,
                borderWidth: bordered ? 1 : 0,
                borderRadius: radius.full,
                paddingHorizontal: 8,
                paddingVertical: 1,
            }}
        >
            <Text role="caption" tone="muted" numberOfLines={1}>{label}</Text>
        </View>
    );
}

function UnavailableTile({ connector }: { connector: UnavailableConnectorRow }) {
    const t = useT();
    return <Tile header={<TileHeader name={connector.display_name} trailing={<Pill label={t('settings.connectors.unavailableStatus')} />} />} />;
}

function StatusText({ connector, t }: { connector: ConnectorRow; t: Translator }) {
    const status = tileStatus(connector);
    switch (status.kind) {
        case 'connected':
            return (
                <Row gap="xs">
                    <Icon icon={CircleCheck} size="sm" tone="success" />
                    <Text role="label" tone="success" numberOfLines={1}>
                        {status.accountId ? <Ltr>{status.accountId}</Ltr> : t('settings.connectors.connected')}
                    </Text>
                </Row>
            );
        case 'paused':
            return <Pill label={t('settings.connectors.paused')} />;
        case 'setupRequired':
            return <Text role="label" tone="muted">{t('settings.connectors.setupRequired')}</Text>;
        default:
            return null;
    }
}

function Chevron({ open }: { open: boolean }) {
    return <Icon icon={open ? ChevronDown : ChevronRight} size="sm" tone="muted" />;
}

function ConnectorTile({
    connector,
    expanded,
    onToggle,
    onDisconnected,
}: {
    connector: ConnectorRow;
    expanded: boolean;
    onToggle: () => void;
    onDisconnected: (notice: string | null) => void;
}) {
    const t = useT();
    const [menuOpen, setMenuOpen] = useState(false);
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [message, setMessage] = useState<{ tone: 'info' | 'danger'; text: string } | null>(null);
    const action = useConnectorServerAction(connector.id, (outcome) => onDisconnected(disconnectNotice(outcome, connector, t)));
    const google = connector.id === GOOGLE_CONNECTOR_ID;
    const server = connector.type === 'mcp_server';
    const paused = connector.enabled === false;

    // A result shown inside the tile opens it, so a menu pick is never silent.
    function show(next: { tone: 'info' | 'danger'; text: string }) {
        setMessage(next);
        if (!expanded) onToggle();
    }
    const refuse = (detailKey: 'checkPcOnly' | 'codePcOnly' | 'configurePcOnly' | 'connectPcOnly' | 'enablePcOnly') =>
        show({ tone: 'info', text: `${PC_ONLY_REFUSAL} ${t(`mobileApp.connectors.${detailKey}`)}` });
    function run(kind: 'disable' | 'disconnect') {
        setMessage(null);
        if (kind === 'disconnect') onDisconnected(null);
        action.mutate(kind, {
            onError: (e) => show({ tone: 'danger', text: describeError(e, t) }),
        });
    }

    return (
        <Tile
            open={expanded}
            header={
                <TileHeader
                    name={connector.display_name}
                    onPress={onToggle}
                    open={expanded}
                    menu={
                        server && connector.configured ? (
                            <IconButton
                                icon={Ellipsis}
                                size="sm"
                                label={t('settings.connectors.actions')}
                                disabled={action.isPending}
                                onPress={() => setMenuOpen(true)}
                            />
                        ) : undefined
                    }
                    trailing={
                        <>
                            {connector.configured ? <Pill label={connector.type === 'oauth_pkce' ? 'OAuth' : 'MCP'} /> : null}
                            <StatusText connector={connector} t={t} />
                        </>
                    }
                />
            }
        >
            <Stack gap="sm">
                <Text role="label" tone="secondary">{connectorDescription(t, connector)}</Text>
                {!connector.configured ? (
                    google ? (
                        <Notice tone="info" message={t('mobileApp.connectors.googlePcOnly')} />
                    ) : server ? (
                        <>
                            <ChipButton label={t('settings.key.connect')} variant="accent" onPress={() => refuse('configurePcOnly')} />
                            {message ? <Notice tone={message.tone} message={message.text} /> : null}
                        </>
                    ) : !connector.configurable && connector.oauth_setup_fields.length > 0 ? (
                        <Notice tone="info" message={t('mobileApp.connectors.setupPcOnly', { name: connector.display_name })} />
                    ) : (
                        <>
                            <Row gap="sm" wrap>
                                <ChipButton label={t('settings.key.connect')} variant="accent" onPress={() => refuse('connectPcOnly')} />
                                {connector.supports_device_code ? (
                                    <ChipButton label={t('settings.connectors.code')} onPress={() => refuse('codePcOnly')} />
                                ) : null}
                            </Row>
                            {message ? <Notice tone={message.tone} message={message.text} /> : null}
                        </>
                    )
                ) : (
                    <>
                        <Row gap="sm" wrap>
                            {server ? <ChipButton label={t('settings.connectors.check')} onPress={() => refuse('checkPcOnly')} /> : null}
                            <ChipButton
                                label={t('settings.connectors.disconnect')}
                                variant="danger"
                                disabled={action.isPending}
                                onPress={() => setConfirmOpen(true)}
                            />
                        </Row>
                        {oauthClientStatus(connector, t) ? (
                            <Stack gap="xs">
                                <Text role="label" weight="semibold" tone="muted">{t('settings.connectors.client')}</Text>
                                <Text role="label" tone="secondary">{oauthClientStatus(connector, t)}</Text>
                                <Text role="label" tone="muted">{t('mobileApp.connectors.clientPcOnly')}</Text>
                            </Stack>
                        ) : null}
                        {message ? <Notice tone={message.tone} message={message.text} /> : null}
                        <ConnectorAccess
                            connector={connector}
                            serverSwitch={
                                server
                                    ? {
                                          busy: action.isPending,
                                          onDisable: () => run('disable'),
                                          onEnableRefused: () => refuse('enablePcOnly'),
                                      }
                                    : undefined
                            }
                        />
                    </>
                )}
            </Stack>
            {server ? (
                <ActionSheet
                    open={menuOpen}
                    onClose={() => setMenuOpen(false)}
                    title={connector.display_name}
                    actions={[
                        paused
                            ? { label: t('settings.connectors.enable'), icon: Play, onPress: () => refuse('enablePcOnly') }
                            : { label: t('settings.connectors.disable'), icon: Pause, onPress: () => run('disable') },
                        { label: t('settings.connectors.disconnect'), icon: Trash2, destructive: true, onPress: () => setConfirmOpen(true) },
                    ]}
                />
            ) : null}
            <ActionSheet
                open={confirmOpen}
                onClose={() => setConfirmOpen(false)}
                title={t('mobileApp.connectors.disconnectConfirm', {
                    name: connector.account_id ? `${connector.display_name} (${connector.account_id})` : connector.display_name,
                })}
                actions={[{ label: t('settings.connectors.disconnect'), icon: Trash2, destructive: true, onPress: () => run('disconnect') }]}
            />
        </Tile>
    );
}

interface ServerSwitch {
    busy: boolean;
    onDisable: () => void;
    onEnableRefused: () => void;
}

function ConnectorAccess({ connector, serverSwitch }: { connector: ConnectorRow; serverSwitch?: ServerSwitch }) {
    const t = useT();
    const agents = useAgents();
    const grants = useConnectorGrants(connector.id, true);
    const activations = useConnectorActivations(connector.id, true);
    const change = useChangeConnectorAccess(connector.id);

    const failure = agents.error ?? grants.error ?? activations.error;
    if (failure) return <Notice tone="danger" message={describeError(failure, t)} />;
    if (!agents.data || !grants.data || !activations.data) return null;

    const list = agents.data.agents;
    const granted = grants.data.grants;
    const active = activations.data.activations;
    const busy = change.isPending;
    const sole = soleMcpAgent(list, connector);
    const perScope = sole ? [] : grantAgents(list, connector.id);
    const perAgent = sole ? [] : activatableAgents(list, connector);

    return (
        <Stack gap="sm">
            {connector.enabled !== false ? <PersonalizeOffer connector={connector} /> : null}
            {sole ? (
                <Switch
                    label={t('settings.connectors.agentCanUse', { agent: sole.name, name: connector.display_name })}
                    description={connector.enabled === false ? t('settings.connectors.disabledHelp') : undefined}
                    value={connector.enabled !== false && !!active[sole.namespaced_agent_id]}
                    disabled={busy || connector.enabled === false}
                    onValueChange={(on) =>
                        change.mutate(
                            on
                                ? {
                                      kind: 'activate',
                                      agentId: sole.namespaced_agent_id,
                                      scopes: granted[sole.namespaced_agent_id]?.length ? undefined : [...MCP_DEFAULT_GRANT_SCOPES],
                                  }
                                : { kind: 'deactivate', agentId: sole.namespaced_agent_id },
                        )
                    }
                />
            ) : null}
            {perScope.length > 0 ? (
                <Stack gap="xs">
                    <Text role="label" weight="semibold" tone="muted">{t('settings.connectors.grants')}</Text>
                    {perScope.map((agent) => {
                        const held = granted[agent.namespaced_agent_id] ?? [];
                        const scopes = agent.required_connections?.find((rc) => rc.connector_id === connector.id)?.scopes ?? [];
                        return (
                            <Surface key={agent.namespaced_agent_id} tone="secondary" radius="sm" border padding="md">
                              <Stack gap="xs">
                                <Text role="label" weight="semibold">{agent.name}</Text>
                                {scopes.map((scope) => (
                                    <Switch
                                        key={scope}
                                        label={scopeLabel(scope, t)}
                                        value={held.includes(scope)}
                                        disabled={busy}
                                        onValueChange={(on) =>
                                            change.mutate({
                                                kind: 'grant',
                                                agentId: agent.namespaced_agent_id,
                                                scopes: nextScopes(held, scope, on),
                                            })
                                        }
                                    />
                                ))}
                              </Stack>
                            </Surface>
                        );
                    })}
                </Stack>
            ) : null}
            {perAgent.length > 0 ? (
                <Stack gap="xs">
                    <Text role="label" weight="semibold" tone="muted">{t('settings.connectors.activeFor')}</Text>
                    {perAgent.map((agent) => {
                        const on = !!active[agent.namespaced_agent_id];
                        return (
                            <Switch
                                key={agent.namespaced_agent_id}
                                label={agent.name}
                                description={t(on ? 'settings.connectors.toolsVisible' : 'settings.connectors.toolsHidden', {
                                    name: connector.display_name,
                                })}
                                value={on}
                                disabled={busy}
                                onValueChange={(next) =>
                                    change.mutate(
                                        next
                                            ? {
                                                  kind: 'activate',
                                                  agentId: agent.namespaced_agent_id,
                                                  scopes: scopesForActivation(agent, connector.id, granted[agent.namespaced_agent_id] ?? []),
                                              }
                                            : { kind: 'deactivate', agentId: agent.namespaced_agent_id },
                                    )
                                }
                            />
                        );
                    })}
                </Stack>
            ) : null}
            {serverSwitch && !sole ? (
                <Switch
                    label={t(connector.enabled === false ? 'settings.connectors.enableServer' : 'settings.connectors.disableServer')}
                    description={connector.enabled === false ? t('settings.connectors.disabledHelp') : undefined}
                    value={connector.enabled !== false}
                    disabled={serverSwitch.busy}
                    onValueChange={(on) => (on ? serverSwitch.onEnableRefused() : serverSwitch.onDisable())}
                />
            ) : null}
            {change.error ? <Notice tone="danger" message={describeError(change.error, t)} /> : null}
        </Stack>
    );
}

function PersonalizeOffer({ connector }: { connector: ConnectorRow }) {
    const t = useT();
    const router = useRouter();
    const agents = useAgents();
    const change = useChangeConnectorAccess(connector.id);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const kind = personalizeSourceKind(connector.id);
    const source = kind
        ? ref(`settings.connectors.personalize.source.${kind}`)
        : ref('settings.connectors.personalize.source.other', { name: connector.display_name });

    async function start() {
        setBusy(true);
        setError(null);
        try {
            if (connector.type === 'mcp_server') {
                const flagship = flagshipAgentId(agents.data?.agents ?? []);
                if (!flagship) throw new Error(t('settings.connectors.personalize.noAgent', { name: connector.display_name }));
                if (!connector.activations?.[flagship]) {
                    await change.mutateAsync({ kind: 'activate', agentId: flagship, scopes: [...MCP_DEFAULT_GRANT_SCOPES] });
                }
            }
            router.push(personalizeRoute(connector));
        } catch (e) {
            setError(t.compose((say) => say('settings.connectors.personalize.failed', { error: describeErrorWith(e, say) })));
        } finally {
            setBusy(false);
        }
    }

    return (
        <Stack gap="xs">
            <Text role="label" tone="secondary">
                {t('settings.connectors.personalize.tile', { source, name: connector.display_name })}
            </Text>
            <ChipButton label={t('settings.connectors.personalize.button')} onPress={() => void start()} disabled={busy} />
            {error ? <Notice tone="danger" message={error} /> : null}
        </Stack>
    );
}

function AgentMcpTile({ server, expanded, onToggle }: { server: AgentMcpServer; expanded: boolean; onToggle: () => void }) {
    const t = useT();
    const command = [server.command, ...server.args].join(' ');
    return (
        <Tile
            open={expanded}
            header={
                <TileHeader
                    name={server.server_name}
                    onPress={onToggle}
                    open={expanded}
                    trailing={
                        <>
                            <Pill label={server.disabled ? t('settings.connectors.paused') : t('settings.connectors.active')} />
                            <Pill label={t('settings.connectors.readOnly')} />
                        </>
                    }
                />
            }
        >
            <Stack gap="xs">
                <Text role="label" tone="secondary">{t('settings.connectors.via', { name: server.agent_name })}</Text>
                {command ? <Ltr>{command}</Ltr> : null}
                <Ltr>{server.config_path}</Ltr>
                <Text role="label" tone="muted">{t('settings.connectors.editConfig')}</Text>
            </Stack>
        </Tile>
    );
}
