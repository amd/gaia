---
name: file-ops
description: Read, write, and edit files on disk safely — locate the right file, change only what was asked, and confirm before anything destructive. Use when the user wants a file read, created, modified, or searched for by name or content.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - read_file
      - write_file
      - edit_file
      - find_files
      - search_file_content
      - get_file_info
      - request_user_input
    provenance:
      source: starter-pack
---

# File Ops

Touching a file on someone's disk is not like answering a question — a wrong
guess doesn't just look bad, it overwrites something they cannot get back. Slow
down in proportion to how hard the change is to undo.

## Procedure

1. **Find it before you touch it.** Don't guess a path from memory or the
   user's description. Use `find_files(query)` for "the report from last week"
   style requests, or `search_file_content(pattern)` when you know a string
   that must be inside the file but not which file it's in. Confirm the exact
   path with `get_file_info` before acting on it — a near-miss filename is
   easy to write to by accident.
2. **Read before you write or edit.** `read_file(file_path)` first, every
   time — even for a file you edited two turns ago. It may have changed since,
   and `edit_file` only succeeds against the file's *current* exact content.
3. **Prefer `edit_file` over `write_file` for existing files.** `edit_file`
   replaces one exact string (`old_content` → `new_content`); it only changes
   what you targeted. `write_file` replaces the entire file — reach for it only
   when creating a new file or when you genuinely mean to replace everything.
4. **When `edit_file` fails with "content not found," re-read the file.**
   Do not retry with a guessed variation of the string. The failure means your
   copy of the file is stale or the whitespace doesn't match byte-for-byte —
   rewriting from memory to force it through is how you silently discard
   whatever changed since you read it.
5. **Confirm before anything destructive.** Overwriting a file whose existing
   content is unrelated to the new content, replacing most of a file in one
   `edit_file` call, or writing to a path that already holds something
   important — stop and ask with `request_user_input` first. A backup gets
   created automatically on overwrite/edit, but "recoverable with effort" is
   not the same as "the user expected this."
6. **Report what actually changed.** State the path, and for edits, name what
   changed rather than pasting the whole diff back at the user unless they ask
   for it.

## Guardrails are not bugs

`write_file` and `edit_file` refuse system directories, dotfiles like `.env`
or `.ssh/*`, and anything above a 10 MB size limit — these are the platform's
sandbox, not something to route around. If a write is denied, that is the
answer; report it and ask the user where they'd actually like the file, don't
look for another tool that skips the check.

## What this skill does not cover

`read_file` is for text — Python, Markdown, config, plain text. It refuses
binary document formats (PDF, DOCX, XLSX, and similar) outright, because
reading them raw produces garbage the model then reasons over as if it were
real content. Those need `index_document` + `query_documents` instead — see
the `document-brief` skill.

## Fork this

Pin step 1 to a project's actual file layout (config always lives at `X`,
generated output always goes to `Y`) and the search step collapses to a direct
path, turning this into a fast in-place editor for one codebase.
