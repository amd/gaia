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
	}
	model := root.NewRootModel(cat, debug)

	p := tea.NewProgram(model, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return fmt.Errorf("TUI error: %w", err)
	}
	return nil
}

// RunChat launches the chat TUI directly with a subprocess agent (standalone mode).
func RunChat(subprocess string, query string, debug bool) error {
	c := client.NewSubprocessClient(subprocess, debug)
	defer c.Close()

	agentName := extractAgentName(subprocess)
	model := chat.NewChatModel(c, agentName, query, debug)

	p := tea.NewProgram(model, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return fmt.Errorf("TUI error: %w", err)
	}
	return nil
}

func extractAgentName(cmdLine string) string {
	parts := strings.Fields(cmdLine)
	if len(parts) == 0 {
		return "agent"
	}
	name := filepath.Base(parts[0])
	name = strings.TrimSuffix(name, ".exe")
	return name
}
