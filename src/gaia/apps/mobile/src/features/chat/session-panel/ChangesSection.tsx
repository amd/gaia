// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { groupUntracked, untrackedWarning } from '@webui/utils/untrackedWork';
import { isolate } from '@webui/i18n/registry';

import type { GitRepo, SessionChanges, UndoAllResult } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { useChangesActions, useSessionChanges, useSessionGit } from '@/data';
import { describeErrorWith, useT, type Translate } from '@/i18n/useT';
import { Button, Notice, Row, Stack, Text } from '@/ui';

import { ChatChangesReview } from '../changes/ChatChangesReview';
import { countsText, filesRef, summaryText, totalText } from '../changes/changesText';
import { GitSheet, repoName, type GitKind } from '../changes/GitSheet';
import { failureReason, failureReasonWith } from '../header/failureText';
import { PanelSection } from './PanelSection';
import { useSessionPanelStore } from './panelStore';
import type { SectionProps } from './sections';

type UndoPhase = 'idle' | 'confirm' | 'working' | 'conflict' | 'done';

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

function undoneText(result: UndoAllResult, t: Translate): string {
    const parts: string[] = [];
    if (result.restored.length) parts.push(t.plural('mobileApp.changes.done.restored', result.restored.length));
    if (result.removed.length) parts.push(t.plural('mobileApp.changes.done.removed', result.removed.length));
    const text = t('mobileApp.changes.done', { parts: parts.join(t('mobileApp.changes.done.join')) || t('mobileApp.changes.done.none') });
    return result.kept_folders.length ? `${text} ${t.plural('mobileApp.changes.done.kept', result.kept_folders.length)}` : text;
}

/** What this chat changed on disk: totals, a review, Undo all, and in a git folder Commit and Create PR. Shows nothing for a chat that changed no files. */
export function ChangesSection({ sessionId, live }: SectionProps) {
    const t = useT();
    const { data: changes, error } = useSessionChanges(sessionId, live);
    // Kept here: after Undo all the list empties, and the result must stay readable.
    const [undone, setUndone] = useState<string | null>(null);
    const empty = !changes || changes.files.length === 0;
    if (!error && empty && !undone) return null;
    return (
        <PanelSection
            id="changes"
            title={t('chg.title')}
            summary={
                changes && !empty
                    ? summaryText(changes, t)
                    : undefined
            }
        >
            <Stack gap="md">
                {error ? <Notice tone="danger" message={t.compose((say) => say('chg.error', { error: failureReasonWith(error, say) }))} testID="changes-error" /> : null}
                {changes && !empty ? <ChangesBody sessionId={sessionId} live={live} changes={changes} onUndone={setUndone} /> : null}
                {empty && undone ? <Notice tone="success" message={undone} testID="changes-undone" /> : null}
                {changes && !empty ? <GitActions sessionId={sessionId} live={live} /> : null}
            </Stack>
        </PanelSection>
    );
}

function ChangesBody({ sessionId, live, changes, onUndone }: { sessionId: string; live: boolean; changes: SessionChanges; onUndone: (text: string) => void }) {
    const t = useT();
    const { undoAll } = useChangesActions(sessionId);
    const seed = useSessionPanelStore((s) => s.changesSeed);
    const [phase, setPhase] = useState<UndoPhase>(seed && 'phase' in seed ? seed.phase : 'idle');
    const [message, setMessage] = useState<string | null>(seed && 'phase' in seed ? (seed.message ?? null) : null);
    const [reviewing, setReviewing] = useState(Boolean(seed && 'review' in seed));
    const count = changes.files.length;
    const groups = groupUntracked(changes.untracked);

    const run = async (force: boolean) => {
        setPhase('working');
        setMessage(null);
        try {
            const text = undoneText(await undoAll.mutateAsync(force), t);
            setMessage(text);
            setPhase('done');
            onUndone(text);
        } catch (err) {
            const conflict = err instanceof GaiaApiError && err.status === 409 && err.body?.status === 'conflict';
            setMessage(failureReason(err, t));
            setPhase(conflict ? 'conflict' : 'idle');
        }
    };

    return (
        <Stack gap="md" testID="changes-body">
            <Stack gap="xs">
                <Text weight="semibold" dir="auto">
                    {totalText(changes, t)}
                    {changes.totals.uncounted > 0 ? ` · ${t.plural('chg.uncounted', changes.totals.uncounted)}` : ''}
                </Text>
                {changes.pending ? <Text role="caption" tone="muted">{t('chg.pending')}</Text> : null}
            </Stack>
            <Stack gap="md" accessibilityRole="list">
                {changes.files.map((file) => (
                    <Stack key={file.path} gap="xs" testID={`changes-file-${file.path}`}>
                        <Text weight="medium">{isolate(baseName(file.path), 'ltr')}</Text>
                        <Text role="caption" tone="muted" dir="auto">
                            {t(`chg.${file.change}`)}
                            {file.additions != null ? ` · ${countsText(file.additions, file.deletions ?? 0)}` : ''}
                            {file.message_ids.length > 1 ? ` · ${t('chg.turns', { n: file.message_ids.length })}` : ''}
                        </Text>
                        {file.changed_since ? <Text role="caption" tone="warning" dir="auto">{t('chg.outside', { when: file.changed_since })}</Text> : null}
                    </Stack>
                ))}
            </Stack>
            {changes.incomplete ? (
                <Notice
                    tone="warning"
                    role="note"
                    message={
                        untrackedWarning(changes.untracked, { inChat: true, undo: t('chg.undoAll') }) +
                        groups.map((g) => `\n${g.title}${g.entries.length > 1 ? `: ${g.entries.map((e) => `${e.label || 'No description'}${e.count > 1 ? ` ×${e.count}` : ''}`).join('; ')}` : ''}`).join('')
                    }
                />
            ) : null}
            <Row gap="sm" wrap>
                <Button size="sm" variant="secondary" label={t('chg.review')} onPress={() => setReviewing(true)} testID="changes-review" />
                {phase !== 'confirm' && phase !== 'conflict' ? (
                    <Button
                        size="sm"
                        variant="secondary"
                        label={phase === 'working' ? t('chg.undoing') : t('chg.undoAll')}
                        busy={phase === 'working'}
                        disabled={phase === 'working' || live}
                        onPress={() => setPhase('confirm')}
                        testID="changes-undo-all"
                    />
                ) : null}
            </Row>
            {live && phase !== 'confirm' && phase !== 'conflict' ? <Text role="caption" tone="muted">{t('chg.wait')}</Text> : null}
            {phase === 'confirm' ? (
                <Stack gap="sm" accessibilityRole="summary" accessibilityLabel={t('chg.confirmLabel')}>
                    <Text role="caption" dir="auto">{t(count === 1 ? 'chg.confirm.one' : 'chg.confirm.other', { files: filesRef(count) })}</Text>
                    <Row gap="sm" wrap>
                        <Button size="sm" variant="danger" label={t('chg.undoAll')} disabled={live} onPress={() => void run(false)} testID="changes-undo-confirm" />
                        <Button size="sm" variant="secondary" label={t('mobileApp.changes.cancel')} onPress={() => setPhase('idle')} />
                    </Row>
                </Stack>
            ) : null}
            {message ? <Notice tone={phase === 'done' ? 'success' : 'danger'} message={message} testID="changes-undo-message" /> : null}
            {phase === 'conflict' ? (
                <Row gap="sm" wrap>
                    <Button size="sm" variant="danger" label={t('chg.undoAnyway')} onPress={() => void run(true)} testID="changes-undo-force" />
                    <Button
                        size="sm"
                        variant="secondary"
                        label={t('chg.keep')}
                        onPress={() => {
                            setPhase('idle');
                            setMessage(null);
                        }}
                    />
                </Row>
            ) : null}
            {reviewing ? <ChatChangesReview changes={changes} onClose={() => setReviewing(false)} /> : null}
        </Stack>
    );
}

/** The repo line with Commit… and Create PR…; hidden when the chat's files are in no repository. */
function GitActions({ sessionId, live }: { sessionId: string; live: boolean }) {
    const t = useT();
    const { data: git, error } = useSessionGit(sessionId, live);
    const { commit, pullRequest } = useChangesActions(sessionId);
    const [dialog, setDialog] = useState<{ kind: GitKind; repo: GitRepo } | null>(null);
    const [done, setDone] = useState<string | null>(null);

    if (error) return <Notice tone="danger" message={t.compose((say) => say('chg.gitError', { error: describeErrorWith(error, say) }))} testID="changes-git-error" />;
    if (!git || !git.available || git.repos.length === 0) return null;

    const open = (kind: GitKind, repo: GitRepo) => {
        setDone(null);
        setDialog({ kind, repo });
    };
    const nothingToCommit = (repo: GitRepo) => repo.uncommitted.length === 0;
    return (
        <Stack gap="md" testID="changes-git">
            {git.repos.map((repo) => (
                <Stack key={repo.root} gap="sm" testID={`changes-repo-${repo.root}`}>
                    <Text role="caption" tone="muted" dir="auto">
                        {`${isolate(repoName(repo.root), 'ltr')} · ${isolate(repo.branch, 'ltr')}`}
                        {nothingToCommit(repo) && repo.files.length > 0 ? ` · ${t('mobileApp.changes.git.committed')}` : ''}
                    </Text>
                    <Row gap="sm" wrap>
                        <Button
                            size="sm"
                            variant="secondary"
                            label={t('mobileApp.changes.git.commit')}
                            disabled={live || nothingToCommit(repo)}
                            onPress={() => open('commit', repo)}
                            testID="changes-commit"
                        />
                        <Button
                            size="sm"
                            variant="secondary"
                            label={t('mobileApp.changes.git.pr')}
                            disabled={live || repo.pr_blocked != null}
                            onPress={() => open('pr', repo)}
                            testID="changes-pr"
                        />
                    </Row>
                    {live ? <Text role="caption" tone="muted">{t('chg.wait')}</Text> : null}
                    {!live && nothingToCommit(repo) ? <Text role="caption" tone="muted">{t('mobileApp.changes.git.nothingToCommit')}</Text> : null}
                    {repo.pr_blocked ? <Text role="caption" tone="muted" dir="auto">{repo.pr_blocked}</Text> : null}
                </Stack>
            ))}
            {done ? <Notice tone="success" message={done} testID="changes-git-done" /> : null}
            {dialog ? (
                <GitSheet
                    kind={dialog.kind}
                    repo={dialog.repo}
                    onClose={() => setDialog(null)}
                    onDone={(text) => {
                        setDialog(null);
                        setDone(text);
                    }}
                    onRun={async ({ title, body }) => {
                        if (dialog.kind === 'commit') {
                            const res = await commit.mutateAsync({ root: dialog.repo.root, message: `${title.trim()}\n\n${body.trim()}`.trim() });
                            return t.plural('mobileApp.changes.git.committedAs', res.files.length, { hash: isolate(res.commit, 'ltr') });
                        }
                        const res = await pullRequest.mutateAsync({ root: dialog.repo.root, title, body });
                        return t('mobileApp.changes.git.opened', { url: isolate(res.url, 'ltr') });
                    }}
                />
            ) : null}
        </Stack>
    );
}
