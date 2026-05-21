package hub

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/vote"
)

// LaunchAgentMsg signals the root model to switch to chat with this agent.
type LaunchAgentMsg struct {
	Agent catalog.Agent
}

type HubModel struct {
	catalog   *catalog.Catalog
	list      list.Model
	activeTab int
	tabs      []catalog.Section
	confirm   *components.ConfirmModel
	debug     bool
	width     int
	height    int
	status    string // ephemeral status messages
}

func NewHubModel(cat *catalog.Catalog, debug bool) HubModel {
	tabs := catalog.AllSections()

	delegate := newAgentDelegate()
	l := list.New(agentsToItems(cat.BySection(tabs[0])), delegate, 80, 20)
	l.Title = ""
	l.SetShowHelp(false)
	l.SetShowStatusBar(false)
	l.SetShowTitle(false)
	l.SetFilteringEnabled(true)
	l.DisableQuitKeybindings()

	return HubModel{
		catalog:   cat,
		list:      l,
		activeTab: 0,
		tabs:      tabs,
		debug:     debug,
	}
}

func (m HubModel) Init() tea.Cmd {
	return nil
}

func (m HubModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	// If confirmation dialog is active, route everything to it
	if m.confirm != nil {
		return m.handleConfirm(msg)
	}

	switch msg := msg.(type) {
	case tea.KeyMsg:
		// Don't intercept keys when filtering (typing in search)
		if m.list.FilterState() == list.Filtering {
			var cmd tea.Cmd
			m.list, cmd = m.list.Update(msg)
			return m, cmd
		}
		return m.handleKey(msg)

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resizeList()
		return m, nil

	case vote.VoteResultMsg:
		// Vote was already incremented locally in the key handler
		if msg.Err != nil {
			m.status = fmt.Sprintf("Voted for %s (offline)", msg.AgentID)
		}
		m.refreshList()
		return m, nil

	case components.ConfirmMsg:
		if msg.Result == components.ConfirmYes {
			m.catalog.Remove(msg.ID)
			m.status = fmt.Sprintf("Removed %s", msg.ID)
			m.refreshList()
		}
		m.confirm = nil
		return m, nil
	}

	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	return m, cmd
}

func (m HubModel) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "q", "ctrl+c":
		return m, tea.Quit

	case "enter":
		selected, ok := m.list.SelectedItem().(catalog.Agent)
		if !ok {
			return m, nil
		}
		if selected.Status.IsLaunchable() {
			return m, func() tea.Msg {
				return LaunchAgentMsg{Agent: selected}
			}
		}
		if selected.Status == catalog.StatusAvailable {
			m.status = fmt.Sprintf("%s is not installed yet", selected.Name)
		} else if selected.Status == catalog.StatusComingSoon {
			m.status = fmt.Sprintf("%s is coming soon — press 'v' to vote", selected.Name)
		}
		return m, nil

	case "tab":
		m.activeTab = (m.activeTab + 1) % len(m.tabs)
		m.refreshList()
		m.status = ""
		return m, nil

	case "shift+tab":
		m.activeTab = (m.activeTab - 1 + len(m.tabs)) % len(m.tabs)
		m.refreshList()
		m.status = ""
		return m, nil

	case "d", "delete", "backspace":
		selected, ok := m.list.SelectedItem().(catalog.Agent)
		if !ok {
			return m, nil
		}
		if selected.Status == catalog.StatusInstalled || selected.Status == catalog.StatusIdle {
			confirm := components.NewConfirmModel(
				selected.ID,
				fmt.Sprintf("Uninstall \"%s\"?", selected.Name),
				"This will remove the agent and clear its cache.\nYou can reinstall it later.",
			)
			m.confirm = &confirm
		}
		return m, nil

	case "v":
		selected, ok := m.list.SelectedItem().(catalog.Agent)
		if !ok {
			return m, nil
		}
		if selected.Status == catalog.StatusComingSoon {
			// Increment locally immediately for responsive UX
			m.catalog.IncrementVotes(selected.ID)
			m.refreshList()
			m.status = fmt.Sprintf("Voted for %s! (vote sent to amd-gaia.ai)", selected.Name)
			// Fire HTTP POST — sends only agent_id, no personal data
			return m, vote.CastVote(selected.ID)
		}
		return m, nil

	case "?":
		return m, func() tea.Msg { return components.HelpContext(components.HelpContextHub) }

	case "r":
		// Request a new agent — opens a text input for the user's idea
		m.status = "Agent requests coming soon — share ideas at github.com/amd/gaia/issues"
		return m, nil
	}

	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	return m, cmd
}

func (m HubModel) handleConfirm(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		updated, cmd := m.confirm.Update(msg)
		m.confirm = &updated
		return m, cmd
	case components.ConfirmMsg:
		if msg.Result == components.ConfirmYes {
			m.catalog.Remove(msg.ID)
			m.status = fmt.Sprintf("Removed %s", msg.ID)
			m.refreshList()
		}
		m.confirm = nil
		return m, nil
	}
	return m, nil
}

func (m HubModel) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	header := m.renderHeader()
	dashboard := m.renderDashboard()
	tabBar := m.renderTabs()
	divider := dividerStyle.Render(strings.Repeat("─", m.width))

	listView := m.list.View()

	statusLine := ""
	if m.status != "" {
		statusLine = lipgloss.NewStyle().
			Foreground(lipgloss.Color("214")).
			Padding(0, 1).
			Render(m.status)
	}

	footer := m.renderFooter()

	view := lipgloss.JoinVertical(lipgloss.Left,
		header,
		dashboard,
		tabBar,
		divider,
		listView,
		statusLine,
		footer,
	)

	if m.confirm != nil {
		return m.confirm.Overlay(view, m.width, m.height)
	}

	return view
}

func (m HubModel) renderHeader() string {
	logo := lipgloss.NewStyle().
		Foreground(lipgloss.Color("212")).
		Bold(true).
		Render("  ██████╗  █████╗ ██╗ █████╗\n" +
			"  ██╔════╝ ██╔══██╗██║██╔══██╗\n" +
			"  ██║  ███╗███████║██║███████║\n" +
			"  ██║   ██║██╔══██║██║██╔══██║\n" +
			"  ╚██████╔╝██║  ██║██║██║  ██║\n" +
			"   ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝")

	subtitle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("243")).
		Italic(true).
		Render("  Local AI Agent Hub — by AMD")

	return logo + "\n" + subtitle
}

func (m HubModel) renderDashboard() string {
	installed, active, idle := m.catalog.DashboardStats()

	parts := []string{
		installedLabel.Render(fmt.Sprintf("  Installed: %d", installed)),
	}
	if active > 0 {
		parts = append(parts, activeLabel.Render(fmt.Sprintf("● Active: %d", active)))
	}
	if idle > 0 {
		parts = append(parts, idleLabel.Render(fmt.Sprintf("● Idle: %d", idle)))
	}

	return dashboardStyle.Render(strings.Join(parts, "   "))
}

func (m HubModel) renderTabs() string {
	var tabs []string
	for i, section := range m.tabs {
		label := string(section)
		count := len(m.catalog.BySection(section))
		label = fmt.Sprintf("%s (%d)", label, count)
		if i == m.activeTab {
			tabs = append(tabs, tabActive.Render(label))
		} else {
			tabs = append(tabs, tabInactive.Render(label))
		}
	}
	return strings.Join(tabs, "")
}

func (m HubModel) renderFooter() string {
	hint := "Enter=launch  /=search  Tab=category  d=delete  v=vote  r=request  ?=help  q=quit"
	return statusBarStyle.Width(m.width).Render(" " + hint)
}

func (m *HubModel) refreshList() {
	items := agentsToItems(m.catalog.BySection(m.tabs[m.activeTab]))
	m.list.SetItems(items)
}

func (m *HubModel) resizeList() {
	overhead := 13 // logo(6) + subtitle(1) + dashboard(1) + tabs(1) + divider(1) + status(1) + footer(1) + padding(1)
	h := m.height - overhead
	if h < 5 {
		h = 5
	}
	m.list.SetSize(m.width, h)
}

func agentsToItems(agents []catalog.Agent) []list.Item {
	items := make([]list.Item, len(agents))
	for i, a := range agents {
		items[i] = a
	}
	return items
}
