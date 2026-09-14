# GAIA Terminal UI

`gaia-tui` opens the flagship agent in a terminal. Local chat uses Lemonade;
optional cloud providers send conversation history to the selected service.

- [Install and use the TUI](../docs/guides/terminal-hub.mdx)
- [Architecture, transports, state, and debugging](../docs/reference/tui.mdx)
- [CLI reference](../docs/reference/cli.mdx#terminal-ui-gaia-tui)
- [Control API and MCP](../docs/guides/mcp/tui.mdx)

## Build

Use the Go version in [go.mod](go.mod):

```bash
cd tui
make build
./bin/gaia-tui --help
go test ./... -count=1
go vet ./...
```

The build creates the terminal client only. Install or build `gaia-agent`
separately to run the flagship. For deterministic UI development:

```bash
make mock-agent
./bin/gaia-tui --mock ./bin/mock-agent --control
```

`--dev` shows detailed activity; `--trace=run.jsonl` records agent events.
See the linked architecture guide before debugging a different checkout or
switching between subprocess and daemon transports.

## Theme contributions

Add colors by role in `internal/ui/theme`. Render styles after `theme.Init()`;
rendering at package initialization freezes the initial palette. Use
`GAIA_TUI_THEME=light` or `dark` to inspect both modes.
