package hub

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
)

// sized builds a HubModel with a window size so the list has real dimensions.
func sized(t *testing.T) HubModel {
	t.Helper()
	m := NewHubModel(catalog.NewCatalog(), false)
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	return updated.(HubModel)
}

func key(m HubModel, msg tea.KeyMsg) HubModel {
	updated, _ := m.Update(msg)
	return updated.(HubModel)
}

// Reproduces #2481: scrolling in a long tab then switching to a shorter one
// left the cursor out of range, so nothing was selected on the destination tab.
func TestTabSwitchKeepsSelection(t *testing.T) {
	m := sized(t)

	// Installed → Available (long list), scroll down twice.
	m = key(m, tea.KeyMsg{Type: tea.KeyTab})
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})

	// shift+tab back to the short Installed tab.
	m = key(m, tea.KeyMsg{Type: tea.KeyShiftTab})

	if _, ok := m.list.SelectedItem().(catalog.Agent); !ok {
		t.Fatalf("expected a selected agent after tab switch, got none (index=%d, items=%d)",
			m.list.Index(), len(m.list.VisibleItems()))
	}
	if got := m.list.Index(); got != 0 {
		t.Errorf("expected cursor reset to first row on tab switch, got index %d", got)
	}
}

// Landing on any tab must always leave a valid selection.
func TestEveryTabHasSelection(t *testing.T) {
	m := sized(t)
	// Scroll down on the current tab first to move the cursor off row 0.
	m = key(m, tea.KeyMsg{Type: tea.KeyTab})
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})
	m = key(m, tea.KeyMsg{Type: tea.KeyDown})

	for i := 0; i < len(m.tabs); i++ {
		m = key(m, tea.KeyMsg{Type: tea.KeyTab})
		if _, ok := m.list.SelectedItem().(catalog.Agent); !ok {
			if len(m.list.VisibleItems()) == 0 {
				continue // an empty tab legitimately has nothing to select
			}
			t.Errorf("tab %d (%s): non-empty list but nothing selected (index=%d)",
				m.activeTab, m.tabs[m.activeTab], m.list.Index())
		}
	}
}
