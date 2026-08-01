package hub

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// pollInterval paces install-status polling. The daemon writes progress from a
// worker thread, so this is a read of local state on loopback — cheap enough to
// keep a progress bar honest without hammering it.
const pollInterval = 400 * time.Millisecond

// maxPollTicks bounds the poll loop at ~10 minutes. The daemon always forces a
// terminal state, so hitting this cap means the daemon itself stopped answering
// — reported loudly rather than spinning forever.
const maxPollTicks = int(10 * time.Minute / pollInterval)

// --- messages ---------------------------------------------------------------

type catalogLoadedMsg struct{ Catalog *catalog.HubCatalog }

type catalogFailedMsg struct{ Detail string }

// installTrustRequiredMsg is the daemon's 403. It carries no retry: the only
// path from here to a successful install goes through the user answering the
// trust gate.
type installTrustRequiredMsg struct {
	AgentID string
	Detail  string
}

type installQueuedMsg struct{ AgentID string }

type installFailedMsg struct {
	AgentID string
	Detail  string
}

type installProgressMsg struct {
	Progress catalog.InstallProgress
	Tick     int
}

type uninstallDoneMsg struct{ AgentID string }

type uninstallFailedMsg struct {
	AgentID string
	Detail  string
}

// --- commands ---------------------------------------------------------------

// loadCatalogCmd fetches the merged hub catalog.
//
// start=false on the hub's own first load: opening a browser UI must not spawn
// a background daemon the user did not ask for. An explicit refresh or install
// passes start=true.
func loadCatalogCmd(hc *catalog.HubClient, start, refresh bool) tea.Cmd {
	return func() tea.Msg {
		if hc == nil {
			return catalogFailedMsg{Detail: "this session was started without a daemon connection"}
		}
		cat, err := hc.Catalog(context.Background(), start, false, refresh)
		if err != nil {
			return catalogFailedMsg{Detail: err.Error()}
		}
		return catalogLoadedMsg{Catalog: cat}
	}
}

// installCmd asks the daemon to install agentID.
//
// trusted MUST only ever be true when it came from the user answering the trust
// gate. Nothing in this file turns a 403 into a retry.
func installCmd(hc *catalog.HubClient, agentID, version string, trusted bool) tea.Cmd {
	return func() tea.Msg {
		if hc == nil {
			return installFailedMsg{
				AgentID: agentID,
				Detail:  "this session was started without a daemon connection, so nothing can be installed",
			}
		}
		err := hc.Install(context.Background(), agentID, version, trusted)
		if err == nil {
			return installQueuedMsg{AgentID: agentID}
		}
		var trustErr *catalog.TrustRequiredError
		if errors.As(err, &trustErr) {
			return installTrustRequiredMsg{AgentID: agentID, Detail: trustErr.Detail}
		}
		return installFailedMsg{AgentID: agentID, Detail: err.Error()}
	}
}

// pollInstallCmd waits one interval and reads install progress once.
func pollInstallCmd(hc *catalog.HubClient, agentID string, tick int) tea.Cmd {
	return tea.Tick(pollInterval, func(time.Time) tea.Msg {
		if hc == nil {
			return installFailedMsg{AgentID: agentID, Detail: "no daemon connection to poll"}
		}
		if tick > maxPollTicks {
			return installFailedMsg{
				AgentID: agentID,
				Detail: fmt.Sprintf(
					"the daemon stopped reporting progress for '%s' after %s. "+
						"Check `gaia daemon logs`, then retry the install", agentID, 10*time.Minute),
			}
		}
		progress, err := hc.InstallStatus(context.Background(), agentID)
		if err != nil {
			return installFailedMsg{AgentID: agentID, Detail: err.Error()}
		}
		return installProgressMsg{Progress: *progress, Tick: tick}
	})
}

func uninstallCmd(hc *catalog.HubClient, agentID string) tea.Cmd {
	return func() tea.Msg {
		if hc == nil {
			return uninstallFailedMsg{
				AgentID: agentID,
				Detail:  "this session was started without a daemon connection, so nothing can be uninstalled",
			}
		}
		if err := hc.Uninstall(context.Background(), agentID); err != nil {
			return uninstallFailedMsg{AgentID: agentID, Detail: err.Error()}
		}
		return uninstallDoneMsg{AgentID: agentID}
	}
}

// --- progress modal ---------------------------------------------------------

// installState tracks one in-flight (or just-finished) install for the modal.
type installState struct {
	agentID string
	name    string
	version string
	size    int64
	status  string
	phase   string
	percent float64
	failure string
	tick    int
}

func (s installState) done() bool {
	return s.status == catalog.InstallCompleted || s.status == catalog.InstallFailed
}

var (
	installBorder = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(theme.Accent).
			Padding(1, 2)

	installFailBorder = lipgloss.NewStyle().
				Border(lipgloss.RoundedBorder()).
				BorderForeground(theme.Danger).
				Padding(1, 2)

	barFill  = lipgloss.NewStyle().Foreground(theme.Accent)
	barEmpty = lipgloss.NewStyle().Foreground(theme.Divider)
	failText = lipgloss.NewStyle().Foreground(theme.Danger)
)

func (s installState) View(width int) string {
	boxWidth := width - 8
	if boxWidth > 64 {
		boxWidth = 64
	}
	if boxWidth < 34 {
		boxWidth = 34
	}

	var b strings.Builder
	if s.status == catalog.InstallFailed {
		b.WriteString(failText.Bold(true).Render("Install failed — " + s.name))
		b.WriteString("\n\n")
		b.WriteString(failText.Render(wrap(orUnknown(s.failure), boxWidth-4)))
		b.WriteString("\n\n")
		b.WriteString(trustHintStyle.Render("Nothing was installed. i retry · esc dismiss"))
		return installFailBorder.Width(boxWidth).Render(b.String())
	}

	title := "Installing " + s.name
	if s.status == catalog.InstallCompleted {
		title = "Installed " + s.name
	}
	b.WriteString(trustTitleStyle.Render(title))
	if s.version != "" {
		b.WriteString(trustHintStyle.Render("  " + s.version))
	}
	b.WriteString("\n\n")
	b.WriteString(renderBar(s.percent, boxWidth-16))
	b.WriteString(fmt.Sprintf("  %3.0f%%", s.percent))
	b.WriteString("\n\n")
	b.WriteString(trustKeyStyle.Render("  " + s.phaseLabel()))
	if s.size > 0 {
		b.WriteString(trustHintStyle.Render("   " + catalog.FormatSize(s.size)))
	}
	b.WriteString("\n\n")
	if s.status == catalog.InstallCompleted {
		b.WriteString(trustHintStyle.Render("enter run it · esc dismiss"))
	} else {
		b.WriteString(trustHintStyle.Render("esc run it in the background"))
	}
	return installBorder.Width(boxWidth).Render(b.String())
}

// phaseLabel turns the daemon's phase word into something a person can read.
// The cases are the phases gaia.hub.installer actually emits; anything else is
// shown verbatim rather than guessed at.
func (s installState) phaseLabel() string {
	switch s.phase {
	case "", catalog.InstallQueued:
		return "queued"
	case "resolving":
		return "resolving the version to install"
	case "checking":
		return "checking compatibility"
	case "downloading":
		return "downloading the package"
	case "installing":
		return "verifying the checksum and unpacking"
	case "registering":
		return "registering the agent"
	case "completed":
		return "done"
	case "failed":
		return "failed"
	default:
		return s.phase
	}
}

func (s installState) Overlay(background string, width, height int) string {
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, s.View(width))
}

func renderBar(percent float64, width int) string {
	if width < 8 {
		width = 8
	}
	if percent < 0 {
		percent = 0
	}
	if percent > 100 {
		percent = 100
	}
	filled := int(float64(width) * percent / 100)
	return barFill.Render(strings.Repeat("█", filled)) +
		barEmpty.Render(strings.Repeat("░", width-filled))
}
