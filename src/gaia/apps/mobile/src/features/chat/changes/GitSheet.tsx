// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from "react";

import type { GitRepo } from "@/api/client.types";
import { useT } from "@/i18n/useT";
import { Button, Notice, Row, Sheet, Stack, Text, TextField } from "@/ui";
import { isolate } from "@webui/i18n/registry";

import { failureReason } from "../header/failureText";

export type GitKind = "commit" | "pr";

export function repoName(root: string): string {
  const parts = root.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? root;
}

interface GitSheetProps {
  kind: GitKind;
  repo: GitRepo;
  onClose: () => void;
  /** Runs git on the PC; called only from the sheet's own button. Resolves to the line to show afterward. */
  onRun: (fields: { title: string; body: string }) => Promise<string>;
  onDone: (text: string) => void;
}

/** The message drafted from the chat's turns, editable. Nothing runs until the button is pressed, in any permission mode. */
export function GitSheet({
  kind,
  repo,
  onClose,
  onRun,
  onDone,
}: GitSheetProps) {
  const t = useT();
  const [title, setTitle] = useState(repo.draft.title);
  const [body, setBody] = useState(repo.draft.body);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const commit = kind === "commit";
  const files = commit ? repo.uncommitted : repo.files;
  const names = isolate(files.join(", "), "ltr");

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      onDone(await onRun({ title, body }));
    } catch (err) {
      setError(failureReason(err, t));
      setRunning(false);
    }
  };

  return (
    <Sheet
      open
      onClose={onClose}
      dismissible={!running}
      title={t(
        commit
          ? "mobileApp.changes.git.commitTitle"
          : "mobileApp.changes.git.prTitle",
      )}
      footer={
        <Stack paddingX="lg" paddingY="md">
          <Row gap="sm">
            <Button
              label={t("mobileApp.changes.cancel")}
              variant="secondary"
              disabled={running}
              onPress={onClose}
              testID="git-sheet-cancel"
            />
            <Button
              label={
                running
                  ? t("mobileApp.changes.git.working")
                  : commit
                    ? t.plural("mobileApp.changes.git.commitGo", files.length)
                    : t("mobileApp.changes.git.prGo")
              }
              variant="primary"
              busy={running}
              disabled={running || !title.trim()}
              onPress={() => void run()}
              testID="git-sheet-run"
            />
          </Row>
        </Stack>
      }
    >
      <Stack gap="md" paddingX="lg" paddingY="md">
        <Text role="caption" tone="secondary" dir="auto">
          {commit
            ? t("mobileApp.changes.git.commitNote", {
                root: isolate(repo.root, "ltr"),
                branch: isolate(repo.branch, "ltr"),
              })
            : t("mobileApp.changes.git.prNote", {
                branch: isolate(repo.branch, "ltr"),
                remote: isolate(repo.remote ?? "", "ltr"),
              })}
        </Text>
        <TextField
          label={t(
            commit
              ? "mobileApp.changes.git.summary"
              : "mobileApp.changes.git.prTitleField",
          )}
          value={title}
          onChangeText={setTitle}
          disabled={running}
          testID="git-sheet-title"
        />
        <TextField
          label={t(
            commit
              ? "mobileApp.changes.git.details"
              : "mobileApp.changes.git.prBodyField",
          )}
          value={body}
          onChangeText={setBody}
          multiline
          maxLines={8}
          disabled={running}
          testID="git-sheet-body"
        />
        <Text role="caption" tone="muted" dir="auto">
          {t.plural("mobileApp.changes.git.files", files.length, { names })}
        </Text>
        {commit && repo.ignored.length > 0 ? (
          <Text role="caption" tone="muted" dir="auto">
            {t.plural("mobileApp.changes.git.ignored", repo.ignored.length, {
              names: isolate(repo.ignored.join(", "), "ltr"),
            })}
          </Text>
        ) : null}
        {error ? (
          <Notice tone="danger" message={error} testID="git-sheet-error" />
        ) : null}
      </Stack>
    </Sheet>
  );
}
