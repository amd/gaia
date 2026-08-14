---
name: coding
description: Work on a codebase — read, search, edit and verify source files. Use when the user asks to fix a bug, add a feature, refactor, explain code, make a test pass, or change anything in a repository. Covers finding the right file, editing safely, and proving the change works before reporting it.
license: MIT
version: 0.1.0
metadata:
  gaia:
    security_tier: verified
    permissions:
      - shell:execute:pytest
    tools_required:
      - read_file
      - edit_file
      - search_file_content
      - search_code_index
      - execute_python_file
    provenance:
      source: starter-pack
---

# Coding

Changing code is different from answering a question: you can be *confidently
wrong for hours* because the answer looked plausible. Everything below exists to
close that gap.

## Find the code before you change it

You have two searches and they answer different questions:

- **`search_file_content`** — grep. Use it when you know the string: a function
  name, an error message, a config key. Fastest way to find every call site.
- **`search_code_index`** — semantic. Use it when you know the *behaviour* but
  not the name: "where do we decide which model to load". Run `index_codebase`
  once for the repo first; `get_index_status` says whether it is ready.

Grep first when you have an exact token — it is instant and exhaustive. Reach
for the semantic index when grep returns nothing useful because you are
guessing at names.

## Read the file before you edit it

Not the top of it — the part you are changing, plus enough around it to see what
else depends on it. An edit written from memory of a similar file is how you
delete someone's special case.

`edit_file` replaces an exact string. If it fails, the file is not what you
thought: **re-read it**, do not retry with a guess. Never fall back to
rewriting the whole file to get past a failed edit — that silently reverts
anything else in it and rewrites every line's endings, turning a two-line change
into a whole-file diff.

## Reproduce it first, then fix it

Run the failing thing and read the actual output before changing anything. A bug
you have not reproduced is a bug you are guessing at, and the fix for a guess
usually lands somewhere real code was fine.

This also gives you the *before* state. Without it you cannot tell whether you
fixed the problem or merely changed the symptom.

## Prove it, then say it

**A test you did not run is not a test that passed.** Tracing the logic in your
head is not verification — it is the same reasoning that produced the bug.

This skill grants `pytest`, so run it directly:

```
pytest -q tests/
pytest -x -k discount tests/test_cart.py
```

The grant is narrow on purpose. `--pdb` would hang waiting for a debugger nobody
can answer, `-p <plugin>` imports arbitrary code, and `--junitxml` writes outside
the run — all refused. If you need something the grant will not allow, say so
rather than working around it.

For a suite pytest cannot drive (npm, go, make), write a runner and use
`execute_python_file`. Then report what the run actually said. If you could not run it, say that
plainly — *"I could not execute the suite, so this is unverified"* — rather than
implying it passed.

## Do not break what was already working

Run the WHOLE suite, not just the test you were asked about. A fix that repairs
one test and breaks two is a worse state than you started in, and you will not
notice if you only look at the one.

If something else fails, that is now your problem, whether or not you caused it.
Say which of the two it is.

## Keep the diff to the change

- Fix the bug you were asked about. Do not reformat, rename, reorder imports, or
  "clean up while you're here" — every unrelated line is a line the reviewer has
  to check.
- Match the file's existing style, even where you would write it differently.
- Do not add comments narrating what the code does. A comment earns its place by
  explaining a *why* that is not obvious from the code.

## When you fix one instance, look for the others

The same mistake is rarely alone. A bad `subprocess` call, a missing encoding, a
wrong default — grep for the pattern once you understand it, and say what you
found even if you only fixed one. "Fixed here; three more in X, Y, Z" is far
more useful than a silent single fix.

## Scratch files are yours, not theirs

Runner scripts and scratch output go in the system temp directory. Writing
`create_doc.py` and `temp/` into the root of someone's repository leaves them in
the next `git status`, and they did not ask for them.

## Reporting a code change

Lead with whether it works, then what changed:

> Both failing tests pass now, and the other six still do.
>
> - `apply_discount` was subtracting `amount * percent` instead of
>   `amount * percent / 100`.
> - `format_money` used `str(round(...))`, which drops the trailing zero in
>   `$5.50`.

Name the file and line only when the reader needs it to act. Never claim a test
run you did not do.
