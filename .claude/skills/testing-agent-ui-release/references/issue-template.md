# Issue template

Voice and layout follow CLAUDE.md "How You Communicate". The first lines say what a
person sees go wrong, in plain words. Mechanics go in the collapsed block. Never name
competitors, never credit Claude, and never paste content from the tester's own files.

Title: `<area>: <what goes wrong, in user terms>`. Under ~70 characters, no
conventional-commit prefix. For example: `Model picker: choosing an undownloaded model
never switches to it`.

```markdown
Picking a model that isn't downloaded yet downloads it, but the chat stays on the old
model, so the person thinks the switch worked and it didn't.

**Steps** (fresh profile, desktop app)
1. Open the model picker in the composer.
2. Pick Qwen3.6 35B (not downloaded).
3. Wait for the download to finish.

**Expected:** Qwen becomes the active model.
**Actual:** the download finishes and Gemma stays selected.

**Build:** v0.25.4-rc3 Windows installer · Strix Halo 128 GB · Windows 11
**Found by:** release test of v0.25.4-rc3, finding F3

![after the download, Gemma still selected](https://raw.githubusercontent.com/<owner>/<repo>/<evidence-branch>/testing/<run>/015_still_gemma.png)

<details>
<summary>🔍 Technical details</summary>

- Backend `GET /api/sessions/<id>`: `effective_model` stays `Gemma-4-E4B-it-GGUF`
- Suspected area, if known (one line)
- Related PRs in this release: #NNNN
</details>
```

Labels: `bug`, plus the area label if one exists. `bug` starts the auto-fix workflow
(see SKILL.md §7). Severity goes in the body, not as an invented label. Add `p1` only
for critical/high when the repo uses that label.
