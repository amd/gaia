package hub

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/vote"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// compactHeightRows is the terminal height below which the ASCII logo is
// dropped for a one-line wordmark. The logo alone is 20 rows; keeping it on an
// 80x24 terminal left the list with nothing and pushed the footer off screen.
const compactHeightRows = 32

// LaunchAgentMsg signals the root model to switch to chat with this agent.
type LaunchAgentMsg struct {
	Agent catalog.Agent
}

// catalogState is how much the hub currently knows about the remote catalog.
type catalogState int

const (
	catalogLoading catalogState = iota
	catalogReady
	catalogUnavailable
)

type HubModel struct {
	catalog *catalog.Catalog
	// hub talks to the daemon's Agent Hub control plane. Nil in tests and in
	// any session started without one — install/uninstall then fail loudly
	// rather than pretending to work.
	hub       *catalog.HubClient
	list      list.Model
	activeTab int
	tabs      []catalog.Section
	confirm   *components.ConfirmModel
	trust     *trustModel
	install   *installState
	dev     bool
	width     int
	height    int
	status    string // ephemeral status messages

	catalogState catalogState
	catalogNote  string
}

func NewHubModel(cat *catalog.Catalog, hc *catalog.HubClient, dev bool) HubModel {
	tabs := catalog.AllSections()

	delegate := newAgentDelegate()
	l := list.New(nil, delegate, 80, 20)
	l.Title = ""
	l.SetShowHelp(false)
	l.SetShowStatusBar(false)
	l.SetShowTitle(false)
	l.SetFilteringEnabled(true)
	l.DisableQuitKeybindings()

	m := HubModel{
		catalog:      cat,
		hub:          hc,
		list:         l,
		tabs:         tabs,
		dev:        dev,
		catalogState: catalogLoading,
	}
	// A fresh machine has an empty Installed tab. Opening on it shows a new
	// user nothing at all, so open on the first tab that has rows.
	m.activeTab = m.firstPopulatedTab()
	m.refreshList()
	return m
}

// firstPopulatedTab returns the index of the first tab with at least one agent,
// or 0 when the catalog is entirely empty.
func (m HubModel) firstPopulatedTab() int {
	for i, section := range m.tabs {
		if len(m.catalog.BySection(section)) > 0 {
			return i
		}
	}
	return 0
}

func (m HubModel) Init() tea.Cmd {
	if m.hub == nil {
		return nil
	}
	// start=false: opening a browser screen must not spawn a background daemon
	// the user never asked for. `r` refreshes with start=true.
	return loadCatalogCmd(m.hub, false, false)
}

func (m HubModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		first := m.width == 0
		m.width = msg.Width
		m.height = msg.Height
		m.resizeList()
		if first {
			// An unreadable install root or a corrupt sentinel makes an
			// installed agent silently vanish from this screen. Say it on the
			// first frame that can show anything.
			if warnings := m.catalog.Warnings(); len(warnings) > 0 && m.status == "" {
				m.setStatus(warnings[0])
			}
		}
		return m, nil

	case catalogLoadedMsg:
		return m.handleCatalogLoaded(msg)

	case catalogFailedMsg:
		m.catalogState = catalogUnavailable
		m.catalogNote = msg.Detail
		return m, nil

	case installTrustRequiredMsg:
		return m.handleTrustRequired(msg)

	case trustDecisionMsg:
		return m.handleTrustDecision(msg)

	case installQueuedMsg:
		if m.install != nil && m.install.agentID == msg.AgentID {
			m.install.status = catalog.InstallQueued
		}
		return m, pollInstallCmd(m.hub, msg.AgentID, 0)

	case installProgressMsg:
		return m.handleInstallProgress(msg)

	case installFailedMsg:
		return m.handleInstallFailed(msg)

	case uninstallDoneMsg:
		m.catalog.Remove(msg.AgentID)
		m.setStatus(fmt.Sprintf("Uninstalled %s", msg.AgentID))
		m.refreshList()
		return m, nil

	case uninstallFailedMsg:
		m.setStatus(msg.Detail)
		return m, nil

	case vote.VoteResultMsg:
		// The key handler incremented the count optimistically so the row reacts
		// at once. Nothing queues a failed vote for later, so a failure has to
		// take that increment back — a count that only ever went up locally is
		// the UI telling the user their vote was recorded when it was not.
		if msg.Err != nil {
			m.catalog.DecrementVotes(msg.AgentID)
			m.setStatus(fmt.Sprintf(
				"Could not send your vote for %s (%v) — nothing was recorded. Try again when you are online",
				msg.AgentID, msg.Err))
			m.refreshRows()
			return m, nil
		}
		// The server's count is the real one; the optimistic +1 was a guess.
		if msg.Votes > 0 {
			m.catalog.SetVotes(msg.AgentID, msg.Votes)
		}
		m.setStatus(fmt.Sprintf("Voted for %s — thanks!", msg.AgentID))
		m.refreshRows()
		return m, nil

	case components.ConfirmMsg:
		return m.handleConfirmResult(msg)
	}

	// Modals own every key while they are up.
	if key, ok := msg.(tea.KeyMsg); ok {
		// ctrl+c always quits. A modal that swallows it leaves the user with no
		// way out of a terminal app.
		if key.String() == "ctrl+c" {
			return m, tea.Quit
		}
		switch {
		case m.trust != nil:
			updated, cmd := m.trust.Update(key)
			m.trust = &updated
			return m, cmd
		case m.install != nil:
			return m.handleInstallModalKey(key)
		case m.confirm != nil:
			updated, cmd := m.confirm.Update(key)
			m.confirm = &updated
			return m, cmd
		}
		// Don't intercept keys when filtering (typing in search)
		if m.list.FilterState() == list.Filtering {
			var cmd tea.Cmd
			m.list, cmd = m.list.Update(key)
			m.clampCursor()
			return m, cmd
		}
		return m.handleKey(key)
	}

	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	m.clampCursor()
	return m, cmd
}

// SetStatus shows an ephemeral message in the hub, used to surface a launch
// failure without leaving the user staring at an unchanged screen.
func (m *HubModel) SetStatus(status string) { m.setStatus(status) }

func (m *HubModel) setStatus(status string) {
	m.status = status
}

func (m HubModel) handleCatalogLoaded(msg catalogLoadedMsg) (tea.Model, tea.Cmd) {
	m.catalog.ApplyHubCatalog(msg.Catalog)
	m.catalogState = catalogReady
	m.catalogNote = ""
	// Clear "Refreshing…" — a progress message that outlives its operation
	// reads as a hung refresh.
	if strings.HasPrefix(m.status, "Refreshing") {
		m.status = ""
	}
	if msg.Catalog != nil && msg.Catalog.Offline {
		m.catalogNote = "showing a cached agent list — the Agent Hub was unreachable"
	}
	// The tab that was open may have been the only populated one before the
	// merge, or may have just emptied; re-pick so the first frame after a load
	// is never a blank tab.
	if len(m.catalog.BySection(m.tabs[m.activeTab])) == 0 {
		m.activeTab = m.firstPopulatedTab()
	}
	m.refreshList()
	return m, nil
}

func (m HubModel) handleTrustRequired(msg installTrustRequiredMsg) (tea.Model, tea.Cmd) {
	m.install = nil
	agent := m.catalog.Get(msg.AgentID)
	if agent == nil {
		m.setStatus(msg.Detail)
		return m, nil
	}
	// The 403 becomes a question, never a retry. installCmd is not called again
	// until trustDecisionMsg{Approved: true} arrives from the user.
	trust := newTrustModel(*agent, msg.Detail)
	m.trust = &trust
	return m, nil
}

func (m HubModel) handleTrustDecision(msg trustDecisionMsg) (tea.Model, tea.Cmd) {
	// An approval is only honoured while the gate it answers is actually up and
	// is for this agent. Without this, a second `y` inside one frame emits a
	// second decision (the daemon then 409s the duplicate and the UI paints a
	// failure over a succeeding install) — and, structurally, the gate would
	// rest on "only a keypress can build this message" staying true forever.
	if m.trust == nil || m.trust.agentID != msg.AgentID {
		return m, nil
	}
	m.trust = nil
	if !msg.Approved {
		m.setStatus(fmt.Sprintf("Did not install %s", msg.AgentID))
		return m, nil
	}
	agent := m.catalog.Get(msg.AgentID)
	if agent == nil {
		m.setStatus(fmt.Sprintf("%s is no longer in the catalog", msg.AgentID))
		return m, nil
	}
	m.beginInstall(*agent)
	return m, installCmd(m.hub, msg.AgentID, msg.Version, true)
}

func (m HubModel) handleInstallProgress(msg installProgressMsg) (tea.Model, tea.Cmd) {
	p := msg.Progress
	if m.install == nil || m.install.agentID != p.AgentID {
		return m, nil
	}
	m.install.status = p.Status
	m.install.phase = p.Phase
	m.install.percent = p.Percent
	m.install.failure = p.Error
	if p.Version != "" {
		m.install.version = p.Version
	}

	switch p.Status {
	case catalog.InstallCompleted:
		m.install.percent = 100
		m.catalog.MarkInstalled(p.AgentID, m.install.version)
		m.setStatus(fmt.Sprintf("Installed %s", m.install.name))
		m.refreshList()
		return m, nil
	case catalog.InstallFailed:
		if m.install.failure == "" {
			m.install.failure = "the daemon reported the install failed but recorded no reason. " +
				"Check `gaia daemon logs`."
		}
		return m, nil
	}
	return m, pollInstallCmd(m.hub, p.AgentID, msg.Tick+1)
}

func (m HubModel) handleInstallFailed(msg installFailedMsg) (tea.Model, tea.Cmd) {
	if m.install != nil && m.install.agentID == msg.AgentID {
		m.install.status = catalog.InstallFailed
		m.install.failure = msg.Detail
		return m, nil
	}
	m.setStatus(msg.Detail)
	return m, nil
}

// handleInstallModalKey handles keys while the install progress/result box is
// up. It never cancels an in-flight install — the daemon owns that work and a
// half-removed install directory is worse than waiting.
func (m HubModel) handleInstallModalKey(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	finished := m.install.done()
	switch key.String() {
	case "esc", "q", "enter", "n":
		if key.String() == "enter" && m.install.status == catalog.InstallCompleted {
			agent := m.catalog.Get(m.install.agentID)
			m.install = nil
			if agent != nil && agent.Status.IsLaunchable() {
				launch := *agent
				return m, func() tea.Msg { return LaunchAgentMsg{Agent: launch} }
			}
			return m, nil
		}
		m.install = nil
		if !finished {
			m.setStatus("Install still running in the background — press r to refresh")
		}
		return m, nil
	case "i", "r":
		if m.install.status == catalog.InstallFailed {
			agent := m.catalog.Get(m.install.agentID)
			if agent == nil {
				m.install = nil
				return m, nil
			}
			// A retry is a fresh install request with trusted=false, so a
			// non-verified agent goes back through the trust gate.
			m.beginInstall(*agent)
			// The SAME resolved version the box is showing — passing
			// agent.LatestVersion here asked the daemon for its own latest
			// while the box named a different one.
			return m, installCmd(m.hub, agent.ID, m.install.version, false)
		}
	}
	return m, nil
}

func (m *HubModel) beginInstall(agent catalog.Agent) {
	version := agent.LatestVersion
	if version == "" {
		version = agent.Version
	}
	m.install = &installState{
		agentID: agent.ID,
		name:    agent.Name,
		version: version,
		size:    agent.DownloadSizeBytes,
		status:  catalog.InstallQueued,
		phase:   catalog.InstallQueued,
	}
	m.status = ""
}

func (m HubModel) handleConfirmResult(msg components.ConfirmMsg) (tea.Model, tea.Cmd) {
	m.confirm = nil
	if msg.Result != components.ConfirmYes {
		return m, nil
	}
	agent := m.catalog.Get(msg.ID)
	if agent != nil && agent.Uninstallable() {
		m.setStatus(fmt.Sprintf("Uninstalling %s…", agent.Name))
		return m, uninstallCmd(m.hub, msg.ID)
	}
	// A local/seed entry the daemon never installed: only this session's view
	// of it changes.
	m.catalog.Remove(msg.ID)
	m.setStatus(fmt.Sprintf("Removed %s", msg.ID))
	m.refreshList()
	return m, nil
}

func (m HubModel) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "q", "ctrl+c":
		return m, tea.Quit

	case "enter":
		return m.handleEnter()

	case "i":
		return m.handleInstallKey()

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

	// `backspace` is deliberately NOT bound: every user presses it meaning "go
	// back", and this opens a destructive-action dialog.
	case "d", "delete":
		return m.handleUninstallKey()

	case "v":
		selected, ok := m.list.SelectedItem().(catalog.Agent)
		if !ok {
			return m, nil
		}
		if selected.Status == catalog.StatusComingSoon {
			// Optimistic: the count moves now, and VoteResultMsg takes it back if
			// the send fails. The wording says what is happening, not what has
			// already happened — the request has not been answered yet.
			m.catalog.IncrementVotes(selected.ID)
			m.refreshRows()
			m.setStatus(fmt.Sprintf("Sending your vote for %s to amd-gaia.ai…", selected.Name))
			// Fire HTTP POST — sends only agent_id, no personal data
			return m, vote.CastVote(selected.ID)
		}
		return m, nil

	case "?":
		return m, func() tea.Msg { return components.HelpContext(components.HelpContextHub) }

	case "r":
		if m.hub == nil {
			m.setStatus("This session has no daemon connection, so the catalog cannot be refreshed")
			return m, nil
		}
		m.catalogState = catalogLoading
		m.setStatus("Refreshing the agent list…")
		return m, loadCatalogCmd(m.hub, true, true)
	}

	var cmd tea.Cmd
	m.list, cmd = m.list.Update(msg)
	m.clampCursor()
	return m, cmd
}

func (m HubModel) handleEnter() (tea.Model, tea.Cmd) {
	selected, ok := m.list.SelectedItem().(catalog.Agent)
	if !ok {
		return m, nil
	}
	if selected.Status.IsLaunchable() {
		return m, func() tea.Msg {
			return LaunchAgentMsg{Agent: selected}
		}
	}
	switch {
	case selected.Installable():
		m.setStatus(fmt.Sprintf("%s is not installed — press i to install it (%s)",
			selected.Name, catalog.FormatSize(selected.DownloadSizeBytes)))
	case selected.Status == catalog.StatusAvailable:
		m.setStatus(fmt.Sprintf("%s is not installed yet", selected.Name))
	case selected.NotOfferedReason != "":
		m.setStatus(fmt.Sprintf("%s: %s", selected.Name, selected.NotOfferedReason))
	case selected.Status == catalog.StatusComingSoon:
		m.setStatus(fmt.Sprintf("%s is coming soon — press 'v' to vote", selected.Name))
	}
	return m, nil
}

func (m HubModel) handleInstallKey() (tea.Model, tea.Cmd) {
	selected, ok := m.list.SelectedItem().(catalog.Agent)
	if !ok {
		return m, nil
	}
	if m.hub == nil {
		m.setStatus("This session has no daemon connection, so nothing can be installed")
		return m, nil
	}
	if m.catalogState != catalogReady {
		m.setStatus("The agent list has not loaded — press r to load it from the Agent Hub, then i")
		return m, nil
	}
	switch {
	case selected.Installable(), selected.UpdateAvailable:
	case selected.NotOfferedReason != "":
		m.setStatus(fmt.Sprintf("%s cannot be installed: %s", selected.Name, selected.NotOfferedReason))
		return m, nil
	case selected.Status.IsLaunchable():
		m.setStatus(fmt.Sprintf("%s is already installed — press enter to run it", selected.Name))
		return m, nil
	default:
		m.setStatus(fmt.Sprintf("%s is not offered by the Agent Hub", selected.Name))
		return m, nil
	}

	m.beginInstall(selected)
	// trusted=false, always. The daemon answers 403 for anything it has not
	// verified, and that refusal is what opens the trust gate.
	return m, installCmd(m.hub, selected.ID, selected.LatestVersion, false)
}

func (m HubModel) handleUninstallKey() (tea.Model, tea.Cmd) {
	selected, ok := m.list.SelectedItem().(catalog.Agent)
	if !ok {
		return m, nil
	}
	if !selected.Status.IsLaunchable() {
		// A key that does nothing and says nothing reads as a broken UI.
		m.setStatus(fmt.Sprintf("%s is not installed, so there is nothing to remove", selected.Name))
		return m, nil
	}
	detail := "This will remove the agent and clear its cache.\nYou can reinstall it later."
	if selected.Uninstallable() {
		where := catalog.InstallRoot()
		if where == "" {
			where = "its install directory"
		}
		detail = fmt.Sprintf(
			"Removes %s from %s.\nYou can reinstall it from the Agent Hub later.",
			selected.InstalledVersion, where)
	}
	confirm := components.NewConfirmModel(
		selected.ID,
		fmt.Sprintf("Uninstall \"%s\"?", selected.Name),
		detail,
	)
	m.confirm = &confirm
	return m, nil
}

func (m HubModel) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	view := lipgloss.JoinVertical(lipgloss.Left,
		m.renderHeader(),
		m.renderDashboard(),
		m.renderTabs(),
		m.line(strings.Repeat("─", m.width), dividerStyle),
		m.renderBody(),
		m.renderStatus(),
		m.renderFooter(),
	)

	switch {
	case m.trust != nil:
		return m.trust.Overlay(view, m.width, m.height)
	case m.install != nil:
		return m.install.Overlay(view, m.width, m.height)
	case m.confirm != nil:
		return m.confirm.Overlay(view, m.width, m.height)
	}
	return view
}

// compact reports whether the terminal is too short for the ASCII logo.
func (m HubModel) compact() bool {
	return m.height > 0 && m.height < compactHeightRows
}

// line renders exactly one row, truncated to the terminal width so a long hint
// can never wrap and push the layout off-screen.
func (m HubModel) line(s string, style lipgloss.Style) string {
	if m.width <= 0 {
		return style.Render(s)
	}
	return style.Render(ansi.Truncate(s, m.width, "…"))
}

func (m HubModel) renderHeader() string {
	if m.compact() {
		wordmark := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright).Render(" G A I A")
		sub := lipgloss.NewStyle().Foreground(theme.Dim).Italic(true).
			Render("  Local AI Agent Hub — by AMD")
		return m.line(wordmark+sub, lipgloss.NewStyle())
	}

	logo := colorizeRobotLogo()

	gaiaText := lipgloss.NewStyle().
		Bold(true).
		Foreground(theme.AccentBright).
		Render("  G A I A")

	subtitle := lipgloss.NewStyle().
		Foreground(theme.Dim).
		Italic(true).
		Render("  Local AI Agent Hub — by AMD")

	return logo + "\n" + gaiaText + "  " + subtitle + "\n"
}

// colorizeRobotLogo renders the GAIA robot ASCII art with colors matching the mascot PNG.
func colorizeRobotLogo() string {
	// Colors from the GAIA mascot: green body, cyan eyes, dark background
	bright := lipgloss.NewStyle().Foreground(theme.ArtBright) // body highlights
	body := lipgloss.NewStyle().Foreground(theme.ArtBody)     // solid green
	mid := lipgloss.NewStyle().Foreground(theme.ArtMid)       // mid-tone
	detail := lipgloss.NewStyle().Foreground(theme.ArtDetail) // detail
	shadow := lipgloss.NewStyle().Foreground(theme.ArtShadow) // shading
	eye := lipgloss.NewStyle().Foreground(theme.ArtEye)       // eyes

	lines := []string{
		"               +=-------                 ",
		"           =====++======-----            ",
		"        ++======+*=========-----         ",
		"      +++++++++++===========------       ",
		"    ++++*#*++++++++============-=--      ",
		"  +***+++==#+++++++++==============--    ",
		" +#####*++=*%+++++##+++==============    ",
		" *##%#%##++*%*++*%%%%%%%%%#########+=-   ",
		" +#%#%%*#+*#%++#%%%#######%########%%%+  ",
		"  *+**#####%++*%%%##*--+##%%%%%##++##%++ ",
		"   +#####%%*+*#%%%##+--+*##%%%##*--*##** ",
		"    +##%##++*#%%%%###++###%%%%%#*==##*#   ",
		"    +**##+*++#%%%%%%####%%%%%%%%####*    ",
		"     +*%%%**#+*%##%%%%%%%%%%%%%%%%%#+    ",
		"       *##*###**+*####%%%%%%%%%%###=     ",
		"         ==+*#######+*##########+=       ",
		"               +#############*=          ",
		"             +=***%%%%#**                 ",
		"           %%%##*##**##***==              ",
		"              #*+++++++**+=              ",
	}

	var result strings.Builder
	for _, line := range lines {
		for _, ch := range line {
			switch ch {
			case '%':
				result.WriteString(bright.Render(string(ch)))
			case '#':
				result.WriteString(body.Render(string(ch)))
			case '*':
				result.WriteString(mid.Render(string(ch)))
			case '+':
				result.WriteString(detail.Render(string(ch)))
			case '=':
				result.WriteString(shadow.Render(string(ch)))
			case '-':
				// Eye sockets — use cyan for the dash markers inside the face area
				result.WriteString(eye.Render(string(ch)))
			case ' ':
				result.WriteByte(' ')
			default:
				result.WriteString(shadow.Render(string(ch)))
			}
		}
		result.WriteByte('\n')
	}
	return result.String()
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
	parts = append(parts, m.catalogBadge())

	return m.line(strings.Join(parts, "   "), dashboardStyle)
}

// catalogBadge says where the agent list came from. A stale or missing list is
// never presented as if it were live.
func (m HubModel) catalogBadge() string {
	switch m.catalogState {
	case catalogLoading:
		return idleLabel.Render("[..] loading list")
	case catalogUnavailable:
		// Not "hub offline": the list can fail to load for local reasons too,
		// and the badge must not pick a culprit the status row may contradict.
		return idleLabel.Render("[!] list unavailable")
	default:
		if m.catalogNote != "" {
			return idleLabel.Render("[!] cached list")
		}
		return ""
	}
}

func (m HubModel) renderTabs() string {
	var tabs []string
	for i, section := range m.tabs {
		label := fmt.Sprintf("%s (%d)", string(section), len(m.catalog.BySection(section)))
		if i == m.activeTab {
			tabs = append(tabs, tabActive.Render(label))
		} else {
			tabs = append(tabs, tabInactive.Render(label))
		}
	}
	return m.line(strings.Join(tabs, ""), lipgloss.NewStyle())
}

// renderBody is the list, or a real empty state when the tab has no rows.
// bubbles' own "No items." tells a first-time user nothing about what to do.
func (m HubModel) renderBody() string {
	if len(m.list.VisibleItems()) > 0 {
		return m.list.View()
	}
	return lipgloss.NewStyle().Height(m.list.Height()).Render(m.emptyState())
}

func (m HubModel) emptyState() string {
	dim := lipgloss.NewStyle().Foreground(theme.Dim)
	accent := lipgloss.NewStyle().Foreground(theme.AccentBright)

	if m.list.FilterState() != list.Unfiltered {
		return dim.Render("\n  Nothing matches that search.  esc clears it.")
	}

	var lines []string
	switch m.tabs[m.activeTab] {
	case catalog.SectionInstalled:
		lines = []string{
			accent.Render("  No agents installed yet."),
			"",
			dim.Render("  Press " + accent.Render("tab") + dim.Render(" for Available, pick one, then ") +
				accent.Render("i") + dim.Render(" to install it.")),
		}
	case catalog.SectionAvailable:
		if m.catalogState == catalogUnavailable {
			// The headline stays neutral: the service may be unreachable, or
			// reachable but too old for this build. The note below says which,
			// and claiming the wrong one sends the user after the wrong thing.
			lines = []string{
				accent.Render("  The agent list can't be loaded."),
				"",
				dim.Render("  " + firstLine(m.catalogNote)),
				"",
				dim.Render("  Press " + accent.Render("r") + dim.Render(" to retry.")),
			}
		} else {
			lines = []string{
				accent.Render("  Nothing else to install right now."),
				"",
				dim.Render("  Press " + accent.Render("r") + dim.Render(" to refresh from the Agent Hub.")),
			}
		}
	default:
		lines = []string{dim.Render("  Nothing here yet.")}
	}
	return "\n" + strings.Join(lines, "\n")
}

func (m HubModel) renderStatus() string {
	// One row is always reserved so the list height never changes underneath
	// the user when a message appears or clears.
	// The specific note beats the generic line: it carries the actual diagnosis
	// (an old daemon, a cached list), and the generic one guessed "can't reach".
	text := m.status
	if text == "" && m.catalogNote != "" {
		text = m.catalogNote
	}
	if text == "" && m.catalogState == catalogUnavailable {
		text = "Agent list unavailable — press r to start the background service and retry"
	}
	if text == "" {
		return ""
	}
	// firstLine, not just truncate: ansi.Truncate does not collapse newlines,
	// and a multi-line message (a daemon start failure quotes its launcher
	// output) would silently break chromeHeight's one-row budget.
	return m.line(" "+firstLine(text), lipgloss.NewStyle().Foreground(theme.Warning))
}

func (m HubModel) renderFooter() string {
	hint := "enter run · i install · d remove · / search · tab category · " +
		"v vote · r refresh · ? help · q quit"
	if m.width < 100 {
		hint = "enter run · i install · d remove · / search · r refresh · ? help · q quit"
	}
	if m.width < 76 {
		hint = "enter run · i install · d remove · / search · ? help · q quit"
	}
	return m.line(" "+hint, statusBarStyle.Width(m.width))
}

// refreshList rebuilds the rows and sends the cursor back to the top. Use it
// whenever which agents are shown can change.
func (m *HubModel) refreshList() {
	m.setRows()
	// The visible set just changed, so the old cursor index means nothing.
	// bubbles' SetItems leaves it where it was, which is how `tab, down, down,
	// shift+tab` used to land on a tab with nothing selected (#2481).
	m.list.ResetSelected()
}

// refreshRows re-renders the same agents in place, keeping the cursor. Used
// where only a row's contents changed (a vote count), so the user is not
// yanked back to the top of the list for pressing `v`.
func (m *HubModel) refreshRows() {
	m.setRows()
	m.clampCursor()
}

func (m *HubModel) setRows() {
	m.list.SetItems(agentsToItems(m.catalog.BySection(m.tabs[m.activeTab])))
}

// clampCursor keeps the cursor inside the visible set. The `/` filter narrows
// the list without moving the cursor, which is the same #2481 failure by a
// different route: SelectedItem() goes nil and enter silently does nothing.
func (m *HubModel) clampCursor() {
	n := len(m.list.VisibleItems())
	if n == 0 {
		m.list.Select(0)
		return
	}
	if idx := m.list.Index(); idx >= n {
		m.list.Select(n - 1)
	} else if idx < 0 {
		m.list.Select(0)
	}
}

// resizeList gives the list every row the chrome does not need. The chrome is
// measured, not assumed: the old hardcoded 26-row budget rendered 34 lines into
// an 80x24 terminal.
func (m *HubModel) resizeList() {
	if m.width <= 0 || m.height <= 0 {
		return
	}
	h := m.height - m.chromeHeight()
	if h < 3 {
		h = 3
	}
	m.list.SetSize(m.width, h)
}

func (m HubModel) chromeHeight() int {
	// Divider, status row, and footer are one line each by construction.
	return lipgloss.Height(m.renderHeader()) +
		lipgloss.Height(m.renderDashboard()) +
		lipgloss.Height(m.renderTabs()) +
		3
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}

func agentsToItems(agents []catalog.Agent) []list.Item {
	items := make([]list.Item, len(agents))
	for i, a := range agents {
		items[i] = a
	}
	return items
}
