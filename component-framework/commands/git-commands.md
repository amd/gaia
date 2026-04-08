---
template_id: git-commands
template_type: commands
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Git command patterns for branch, commit, and remote operations
schema_version: "1.0"
---

# Git Command Patterns

## Purpose

This template provides reusable Git command patterns for common version control operations including branching, committing, and remote synchronization.

## Branch Operations

### Create and Switch to New Branch

```bash
# Pattern: Create new branch and switch to it
command_template: |
  git checkout -b {{BRANCH_NAME}}
example: |
  git checkout -b feature/new-agent
variables:
  - BRANCH_NAME: Name of the new branch
notes: "Use descriptive branch names (feature/, bugfix/, hotfix/)"
```

### List Branches

```bash
# Pattern: List local branches
command_template: |
  git branch {{FLAGS}}
example: |
  git branch -a
variables:
  - FLAGS: -a (all), -r (remote), -v (verbose)
```

### Delete Branch

```bash
# Pattern: Delete a branch
command_template: |
  git branch -{{FLAGS}} {{BRANCH_NAME}}
example: |
  git branch -d feature/old-feature
variables:
  - BRANCH_NAME: Name of branch to delete
  - FLAGS: d (safe delete), D (force delete)
```

### Rename Branch

```bash
# Pattern: Rename current branch
command_template: |
  git branch -m {{NEW_NAME}}
example: |
  git branch -m feature/better-name
variables:
  - NEW_NAME: New branch name
```

### Switch Branch

```bash
# Pattern: Switch to existing branch
command_template: |
  git switch {{BRANCH_NAME}}
example: |
  git switch main
variables:
  - BRANCH_NAME: Name of branch to switch to
```

## Commit Operations

### Stage All Changes

```bash
# Pattern: Stage all modified files
command_template: |
  git add {{PATHS}}
example: |
  git add -A
variables:
  - PATHS: -A (all), . (current dir), or specific files
```

### Stage Specific File

```bash
# Pattern: Stage specific file
command_template: |
  git add {{FILE_PATH}}
example: |
  git add src/gaia/agents/base/agent.py
variables:
  - FILE_PATH: Path to file to stage
```

### Commit Changes

```bash
# Pattern: Commit staged changes
command_template: |
  git commit -m "{{COMMIT_MESSAGE}}"
example: |
  git commit -m "feat: add new agent class"
variables:
  - COMMIT_MESSAGE: Commit message (use conventional commits)
notes: "Follow conventional commits: feat:, fix:, docs:, chore:, etc."
```

### Commit with Extended Message

```bash
# Pattern: Commit with subject and body
command_template: |
  git commit -m "{{SUBJECT}}" -m "{{BODY}}"
example: |
  git commit -m "feat: add agent tool registry" -m "Implements thread-safe tool registration with per-agent scoping."
variables:
  - SUBJECT: Short commit subject
  - BODY: Detailed commit body
```

### Amend Last Commit

```bash
# Pattern: Amend the most recent commit
command_template: |
  git commit --amend {{FLAGS}}
example: |
  git commit --amend --no-edit
variables:
  - FLAGS: --no-edit (keep message), -m "new message"
warning: "Only amend local commits that haven't been pushed"
```

### View Commit History

```bash
# Pattern: View commit log
command_template: |
  git log {{FLAGS}}
example: |
  git log --oneline -10
variables:
  - FLAGS: --oneline, --graph, --all, -n (count)
```

### Show Specific Commit

```bash
# Pattern: Show commit details
command_template: |
  git show {{COMMIT_HASH}}
example: |
  git show abc123
variables:
  - COMMIT_HASH: SHA hash of commit to show
```

## Remote Operations

### Clone Repository

```bash
# Pattern: Clone a remote repository
command_template: |
  git clone {{REPO_URL}} {{DIRECTORY}}
example: |
  git clone https://github.com/amd/gaia.git
variables:
  - REPO_URL: URL of repository to clone
  - DIRECTORY: Optional target directory name
```

### Fetch Remote Changes

```bash
# Pattern: Fetch from remote
command_template: |
  git fetch {{REMOTE}} {{BRANCH}}
example: |
  git fetch origin main
variables:
  - REMOTE: Remote name (usually origin)
  - BRANCH: Branch to fetch
```

### Pull Remote Changes

```bash
# Pattern: Pull and merge from remote
command_template: |
  git pull {{FLAGS}} {{REMOTE}} {{BRANCH}}
example: |
  git pull origin main
variables:
  - REMOTE: Remote name
  - BRANCH: Branch to pull
  - FLAGS: --rebase, --ff-only
```

### Push to Remote

```bash
# Pattern: Push to remote
command_template: |
  git push {{FLAGS}} {{REMOTE}} {{BRANCH}}
example: |
  git push origin feature/new-feature
variables:
  - REMOTE: Remote name
  - BRANCH: Branch to push
  - FLAGS: -u (set upstream), --force (force push)
warning: "Avoid --force on shared branches"
```

### Set Upstream Branch

```bash
# Pattern: Set upstream for current branch
command_template: |
  git push -u {{REMOTE}} {{BRANCH}}
example: |
  git push -u origin feature/new-feature
variables:
  - REMOTE: Remote name
  - BRANCH: Branch name
```

### View Remote Status

```bash
# Pattern: Check remote tracking status
command_template: |
  git status
example: |
  git status -sb
variables:
  - FLAGS: -s (short), -b (branch)
```

## Diff Operations

### View Unstaged Changes

```bash
# Pattern: Show unstaged changes
command_template: |
  git diff {{FLAGS}}
example: |
  git diff HEAD
variables:
  - FLAGS: --cached (staged), HEAD (all changes)
```

### Compare Branches

```bash
# Pattern: Show differences between branches
command_template: |
  git diff {{BRANCH1}}..{{BRANCH2}}
example: |
  git diff main..feature/new-feature
variables:
  - BRANCH1: Base branch
  - BRANCH2: Comparison branch
```

## Stash Operations

### Stash Changes

```bash
# Pattern: Stash current changes
command_template: |
  git stash {{FLAGS}}
example: |
  git stash save "WIP: agent integration"
variables:
  - FLAGS: save "message", -u (include untracked)
```

### Apply Stash

```bash
# Pattern: Apply stashed changes
command_template: |
  git stash pop {{STASH_ID}}
example: |
  git stash pop stash@{0}
variables:
  - STASH_ID: Stash identifier (e.g., stash@{0})
```

## Related Components

- [[component-framework/commands/shell-commands.md]] - For general shell operations
- [[component-framework/commands/build-commands.md]] - For build operations
- [[component-framework/documents/status-report.md]] - For reporting Git-based progress
