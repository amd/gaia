# Vendored dependencies

Patched copies of upstream modules, wired in with `replace` in `tui/go.mod`.
Each one exists because the upstream bug reaches the screen and the project
has no maintained release that fixes it. Drop the copy and its `replace` line
as soon as upstream ships the fix.

| Module | Version | Patch | Why |
|---|---|---|---|
| `github.com/muesli/reflow` | v0.3.0 | `wordwrap/wordwrap.go`: the hyphen breakpoint is counted toward the line length | Glamour wraps every paragraph with this writer. Upstream wrote a `-` without counting it, so each hyphen made a line one column over its limit; glamour then re-wrapped the over-long line and stranded words on short rows ("python -" / "m" / "pytest"). |
