# Vendored dependencies

Patched copies of upstream modules, wired in with `replace` in `tui/go.mod`.
Each one exists because the upstream bug reaches the screen and the project
has no maintained release that fixes it. Drop the copy and its `replace` line
as soon as upstream ships the fix.

| Module | Version | Patch | Why |
|---|---|---|---|
| `github.com/muesli/reflow` | v0.3.0 | `wordwrap/wordwrap.go`: the hyphen breakpoint is counted toward the line length | Glamour wraps every paragraph with this writer. Upstream wrote a `-` without counting it, so each hyphen made a line one column over its limit; glamour then re-wrapped the over-long line and stranded words on short rows ("python -" / "m" / "pytest"). |
| `github.com/charmbracelet/bubbletea` | v1.3.10 | `key_windows.go`: Ctrl+Backspace reports `ctrl+h` and Ctrl+Delete reports a new `KeyCtrlDelete`; `key.go`: adds `KeyCtrlDelete` (`ctrl+delete`) and maps `ESC[3;5~` / `ESC[3;7~` to it | The Windows console reader dropped the Ctrl state on Backspace and Delete, so Ctrl+Backspace and Ctrl+Delete deleted one character in the composer instead of a word (#5084). v1 has no Ctrl+Delete key at all on any OS. Patched lines are marked `GAIA patch`. Upstream's own key tests pass against the copy. |
