package test

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// windowSize is the resize message every hub test starts with.
func windowSize(w, h int) tea.WindowSizeMsg {
	return tea.WindowSizeMsg{Width: w, Height: h}
}

func TestHubModelRenders(t *testing.T) {
	cat := catalog.NewCatalog()
	m := hub.NewHubModel(cat, nil, false)

	// Simulate window size
	updated, _ := m.Update(windowSize(120, 40))
	hubModel := updated.(hub.HubModel)

	view := hubModel.View()
	if view == "" {
		t.Fatal("hub view is empty")
	}
	if view == "Loading..." {
		t.Fatal("hub still showing loading after window size")
	}

	// Check for key content in rendered view
	// Only Bash is installed — it should appear in the default Installed tab
	checks := []string{"Agent Hub", "Bash"}
	for _, check := range checks {
		if !contains(view, check) {
			t.Errorf("hub view missing expected content: %q", check)
		}
	}
	t.Logf("Hub view length: %d chars", len(view))
}

// This used to send the three runes t, a, b — not the Tab key — and then assert
// only that nothing panicked (§5 bug 8).
func TestHubTabSwitching(t *testing.T) {
	d := newDriver(t, nil, 120, 40)

	_, first := d.m.ActiveTab()
	firstRows := d.m.VisibleAgentIDs()

	d.send(keyTab())

	idx, second := d.m.ActiveTab()
	if second == first {
		t.Fatalf("Tab did not change the active tab (still %q)", first)
	}
	if idx != 1 {
		t.Errorf("Tab moved to index %d, want 1", idx)
	}
	secondRows := d.m.VisibleAgentIDs()
	if sameIDs(firstRows, secondRows) {
		t.Errorf("tab %q shows the same rows as %q: %v", second, first, secondRows)
	}
	if !contains(stripAnsi(d.m.View()), second) {
		t.Errorf("view does not show the active tab %q", second)
	}

	d.send(keyShiftTab())
	if _, back := d.m.ActiveTab(); back != first {
		t.Errorf("Shift+Tab landed on %q, want back on %q", back, first)
	}
}

// This used to send the three runes /, and then assert only "didn't panic".
func TestHubSearch(t *testing.T) {
	d := newDriver(t, nil, 120, 40)
	d.gotoTab("Coming Soon")

	before := len(d.m.VisibleAgentIDs())
	if before < 2 {
		t.Fatalf("need at least 2 rows to prove filtering narrows them, got %d", before)
	}

	d.send(key("/"))
	if !d.m.IsFiltering() {
		t.Fatal("/ did not enter filter mode")
	}
	for _, r := range "browser" {
		d.send(key(string(r)))
	}
	d.send(keyEnter())

	after := d.m.VisibleAgentIDs()
	if len(after) >= before {
		t.Fatalf("filtering for 'browser' left %d of %d rows visible", len(after), before)
	}
	if len(after) == 0 || after[0] != "browser" {
		t.Fatalf("filtered rows = %v, want browser first", after)
	}

	d.send(keyEsc())
	if d.m.IsFiltering() {
		t.Error("esc did not clear the filter")
	}
	if got := len(d.m.VisibleAgentIDs()); got != before {
		t.Errorf("clearing the filter left %d rows, want the original %d", got, before)
	}
}

func sameIDs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
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

	if !contains(view, "Esc back") {
		t.Error("hub-launched chat missing 'Esc back' hint")
	}
}

func TestBinaryDiscovery(t *testing.T) {
	cat := catalog.NewCatalog()
	cat.DiscoverBinaries()

	bash := cat.Get("bash")
	if bash == nil {
		t.Fatal("bash agent not found")
	}
	// If gaia-bash.exe exists in the repo, discovery should find it
	if bash.BinaryPath != "gaia-bash" {
		// Discovery found something — verify it's a real path
		t.Logf("Discovered bash binary: %s", bash.BinaryPath)
	} else {
		t.Logf("Binary discovery did not find gaia-bash (expected if not built)")
	}
}

func TestDashboardStats(t *testing.T) {
	cat := catalog.NewCatalog()

	installed, active, idle := cat.DashboardStats()
	if installed != 1 {
		t.Errorf("expected 1 installed (bash only), got %d", installed)
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
	if installed != 0 {
		t.Errorf("expected 0 installed after SetStatus (bash now active), got %d", installed)
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
