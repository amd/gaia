package ui

import (
	"fmt"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// RunHub launches the Agent Hub TUI — the main entry point for browsing and launching agents.
// If mockAgent is non-empty, all agent binary paths are overridden with it for testing.
func RunHub(debug bool, mockAgent string) error {
	cat := catalog.NewCatalog()
	if mockAgent != "" {
		cat.SetMockBinary(mockAgent)
	} else {
		cat.DiscoverBinaries()
	}
	model := root.NewRootModel(cat, debug)

	p := tea.NewProgram(model, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return fmt.Errorf("TUI error: %w", err)
	}
	return nil
}

// RunChat launches the chat TUI directly with a subprocess agent (standalone mode).
//
// subprocess is a command line, so it is split with quoting honoured — a binary
// path containing a space must be quoted, not silently torn in two.
func RunChat(subprocess string, query string, debug bool) error {
	argv, err := client.SplitCommandLine(subprocess)
	if err != nil {
		return fmt.Errorf("invalid --subprocess command: %w", err)
	}

	c := client.NewSubprocessClient(argv[0], argv[1:], debug)
	defer c.Close()

	model := chat.NewChatModel(c, agentNameFromPath(argv[0]), query, debug)

	p := tea.NewProgram(model, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return fmt.Errorf("TUI error: %w", err)
	}
	return nil
}

func agentNameFromPath(path string) string {
	name := filepath.Base(path)
	return strings.TrimSuffix(name, ".exe")
}
