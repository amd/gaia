---
name: gaia-harness-engineering
description: Help a developer report a GAIA TUI, WebUI or agent problem, explicitly share a snapshot with Claude Code or Codex through MCP, and validate isolated worktree fixes.
---

# Developer handoff

This skill is enabled by the host only in developer mode. Start only when the
developer reports a problem or requests an improvement. Do not automatically
assess friction or start coding. Keep GAIA available for daily work.

1. Clarify expected and observed behavior. Use `engineering_status` without a
   job ID to inspect source-cache initialization and coding-app setup. Surface
   failures and connection instructions; do not claim connection or task launch
   merely because the app exists.
2. Select `claude` or `codex` with the developer. Prepare a concise summary and
   selected evidence. Do not collect entire histories, unrelated files or
   credentials. `share_engineering_context` shows the exact summary and context
   and requests fresh approval before the selected coding app can read them.
   The app may use a cloud provider; its existing filesystem permissions remain
   independent of this bridge. Never use shell, file tools or another MCP tool
   to bypass this approval. Bypass-permissions mode cannot approve sharing.
3. Give the developer the job ID and connection instructions. Use
   `open_engineering_app` only when asked to open the app. An opened app or
   prefilled composer is not a submitted or running task. The developer may
   interact directly in their existing coding app.
   For Codex specifically, opening only brings the app forward: it does not
   create a task, select a folder or prefill a composer. Say this explicitly,
   then show the returned directory and exact prompt to paste into a new task.
   Never say "posted to Codex", "sent", or "look for the prefilled composer".
   A generated MCP config is not an installed or verified client connection.
4. Diagnose before editing: reproduce safely using synthetic or read-only
   inputs, inspect configuration/model limits and actual build identities,
   check releases and existing issues/PRs. Do not replay destructive tools or
   external sends. Recommend a model/configuration change when evidence supports
   it; ask before changing the user's model. Unknown root cause stays unknown.
5. After diagnosis, use `approve_engineering_code` to request worktree scope.
   All edits must be in an isolated worktree from the managed GAIA cache.
   Private snapshots stay outside Git; they must not become fixtures or commits.
6. Have the coding app launch a separate agent/TUI/WebUI preview with isolated
   writable state, ports and explicit build revision. A worktree is not a
   security sandbox. Let the developer test and iterate. Share additional
   feedback only through `append_engineering_context`, which asks again.
7. Use `engineering_status` for reported progress and preview details. Clearly
   distinguish app-reported results from locally verified checks. Native app
   conversations and detailed coding remain in that app.
8. Review all outgoing public text, code and commit history. Generalize private
   examples. Workflow approval does not make raw context safe to publish.
   Follow repository contribution/review requirements; maintainers own merge.
9. `revoke_engineering_context` stops future bridge reads. Already delivered
   data cannot be recalled. Stable release updates are a separate feature;
   never replace the running GAIA installation from a preview worktree.
