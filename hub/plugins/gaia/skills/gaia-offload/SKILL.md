---
name: gaia-offload
description: Hand easy, read-mostly work to GAIA, the AI agent running on this PC's local model, to save cloud tokens. Use when a task is summarizing long files or logs, triaging many files, extracting or classifying, answering questions about documents, or drafting prose, and the gaia_task tool is available.
---

# Offloading to local GAIA

GAIA runs on this PC's own model through the `gaia_task` tool. It is free to call and
keeps data on the machine, but it is a smaller model than you. Give it the reading;
keep the judgment.

## Hand it

- Summaries of long files, logs, transcripts or diffs
- Triage across many files: "which of these mention X", "list the ones that fail"
- Extraction and classification: pull fields out of text, label items, turn prose into a table
- Questions about documents in the project folder
- First drafts of prose you will then review: release notes, descriptions, emails

## Keep it

- Anything you will act on without checking: code changes, commands, final answers
- Design decisions, debugging, security review
- Work outside the folder Claude Code was started in. GAIA can only read inside it.
- Anything that changes files. GAIA cannot edit or delete files, so make the change yourself from its answer.

## Write the task

Each call starts fresh, so the task must stand alone:

- Name the files by path and say what to do with them.
- Say the shape you want back: "a bullet per file", "a JSON list with name and line".
- Keep one task to one job. Split a big job into several calls; tasks stop after 300 seconds.

## Use the answer

The answer ends with a line naming the model, steps and time. Read the answer before
using it: spot-check a claim against the file when it matters. If GAIA did not finish,
the result says why. Do the task yourself rather than retrying the same call.

## When GAIA isn't there

If `gaia_task` is missing, GAIA isn't installed on this PC. Tell the user once and point
them to https://amd-gaia.ai/docs/guides/install, then do the work yourself. If
`gaia_status` says it is starting, the model is still loading. If it says failed, pass its
error to the user in plain words.
