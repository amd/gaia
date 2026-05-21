package test

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/root"
)

func TestHubModelRenders(t *testing.T) {
	cat := catalog.NewCatalog()
	m := hub.NewHubModel(cat, false)

	// Simulate window size
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	hubModel := updated.(hub.HubModel)

	view := hubModel.View()
	if view == "" {
		t.Fatal("hub view is empty")
	}
	if view == "Loading..." {
		t.Fatal("hub still showing loading after window size")
	}

	// Check for key content in rendered view
	// The GAIA logo uses Unicode box-drawing chars, so check for "Agent Hub" subtitle
	checks := []string{"Agent Hub", "Chat", "Bash", "Doc"}
	for _, check := range checks {
		if !contains(view, check) {
			t.Errorf("hub view missing expected content: %q", check)
		}
	}
	t.Logf("Hub view length: %d chars", len(view))
}

func TestHubTabSwitching(t *testing.T) {
	cat := catalog.NewCatalog()
	m := hub.NewHubModel(cat, false)

	// Set window size
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	m = updated.(hub.HubModel)

	// Tab to Available section
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("tab")})

	// Verify no panic
	view := updated.(hub.HubModel).View()
	if view == "" {
		t.Fatal("view empty after tab")
	}
}

func TestHubSearch(t *testing.T) {
	cat := catalog.NewCatalog()
	m := hub.NewHubModel(cat, false)

	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	m = updated.(hub.HubModel)

	// Press / to enter search mode
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("/")})

	// Verify no panic
	view := updated.(hub.HubModel).View()
	if view == "" {
		t.Fatal("view empty after search")
	}
}

func TestRootModelStartsWithHub(t *testing.T) {
	cat := catalog.NewCatalog()
	m := root.NewRootModel(cat, false)

	// Set window size
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})

	view := updated.(root.RootModel).View()
	if view == "" {
		t.Fatal("root view is empty")
	}
	if !contains(view, "Agent Hub") {
		t.Error("root view missing Agent Hub text")
	}
}

func TestChatModelWelcome(t *testing.T) {
	// Use nil client — we won't send queries
	m := chat.NewChatModel(nil, "test-agent", "", false)

	// View before window size — should show welcome
	view := m.View()
	if !contains(view, "Welcome to GAIA") {
		t.Error("chat view missing welcome message before window size")
	}

	// After window size
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	view = updated.(chat.ChatModel).View()

	if !contains(view, "Welcome to GAIA") {
		t.Error("chat view missing welcome message after window size")
	}
	if !contains(view, "test-agent") {
		t.Error("chat view missing agent name")
	}
	if !contains(view, "Ctrl+C to quit") {
		t.Error("chat view missing quit hint")
	}
}

func TestChatModelFromHub(t *testing.T) {
	m := chat.NewChatModelFromHub(nil, "bash", "Bash", false)

	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	view := updated.(chat.ChatModel).View()

	if !contains(view, "Esc to return") {
		t.Error("hub-launched chat missing 'Esc to return' hint")
	}
}

func TestDashboardStats(t *testing.T) {
	cat := catalog.NewCatalog()

	installed, active, idle := cat.DashboardStats()
	if installed != 5 {
		t.Errorf("expected 5 installed, got %d", installed)
	}
	if active != 0 {
		t.Errorf("expected 0 active, got %d", active)
	}
	if idle != 0 {
		t.Errorf("expected 0 idle, got %d", idle)
	}

	// Set one to active
	cat.SetStatus("bash", catalog.StatusActive)
	installed, active, idle = cat.DashboardStats()
	if active != 1 {
		t.Errorf("expected 1 active after SetStatus, got %d", active)
	}
	if installed != 4 {
		t.Errorf("expected 4 installed after SetStatus, got %d", installed)
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchString(s, substr)
}

func searchString(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// stripAnsi removes ANSI escape sequences from a string.
func stripAnsi(s string) string {
	var result []byte
	i := 0
	for i < len(s) {
		if s[i] == '\x1b' && i+1 < len(s) && s[i+1] == '[' {
			// Skip until we find the terminating character
			j := i + 2
			for j < len(s) && !((s[j] >= 'A' && s[j] <= 'Z') || (s[j] >= 'a' && s[j] <= 'z') || s[j] == '~') {
				j++
			}
			if j < len(s) {
				j++ // skip the terminating character
			}
			i = j
		} else {
			result = append(result, s[i])
			i++
		}
	}
	return string(result)
}
