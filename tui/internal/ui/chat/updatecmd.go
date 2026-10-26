// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/update"
)

// `/update` checks for, and installs, GAIA releases. Installing closes the
// TUI first — the installer replaces this binary — and runs in the terminal
// it gives back (update.RunPending, called by the CLI after the TUI exits).
const updateCommandPrefix = "/update"

const updateUsage = "Usage: /update (check) · /update now (install the newest) · " +
	"/update v0.25.0 (install that exact version and pin it) · /update list · " +
	"/update auto on|off · /update rc on|off (release candidates, needs auto) · /update unpin · /update cancel"

// updateCheckTimeout bounds a check: GitHub not answering must not leave
// "Checking…" up forever.
const updateCheckTimeout = 20 * time.Second

func isUpdateCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == updateCommandPrefix || strings.HasPrefix(trimmed, updateCommandPrefix+" ")
}

func updateCommandArg(query string) string {
	return strings.Join(strings.Fields(strings.TrimPrefix(strings.TrimSpace(query), updateCommandPrefix)), " ")
}

// updateCheckedMsg carries a check's result. launch marks the check every
// launch runs; then is what the user asked to do with the answer.
type updateCheckedMsg struct {
	status update.Status
	err    error
	launch bool
	then   string // "", "now" or "list"
}

// updatePlannedMsg is an install ready to run once the TUI exits.
type updatePlannedMsg struct {
	plan          update.Plan
	err           error
	scheduledOnly bool // auto-update: install on quit, do not quit now
}

type updateSavedMsg struct {
	text string
	err  error
}

func checkUpdateCmd(launch bool, then string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), updateCheckTimeout)
		defer cancel()
		st, err := update.Check(ctx)
		return updateCheckedMsg{status: st, err: err, launch: launch, then: then}
	}
}

func planUpdateCmd(r update.Release, pin, scheduledOnly bool) tea.Cmd {
	return func() tea.Msg {
		p, err := update.NewPlan(r, pin)
		return updatePlannedMsg{plan: p, err: err, scheduledOnly: scheduledOnly}
	}
}

func planTagCmd(tag string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), updateCheckTimeout)
		defer cancel()
		r, err := update.GetRelease(ctx, tag)
		if err != nil {
			return updatePlannedMsg{err: err}
		}
		p, err := update.NewPlan(r, true /* an exact version stays put */)
		return updatePlannedMsg{plan: p, err: err}
	}
}

func saveUpdateSettingCmd(text string, save func() error) tea.Cmd {
	return func() tea.Msg {
		return updateSavedMsg{text: text, err: save()}
	}
}

// WithUpdateCheck makes Init check for a newer release once. Off in tests and
// in sessions that are not the user's own launch.
func (m ChatModel) WithUpdateCheck() ChatModel {
	m.updateCheckOnLaunch = !update.Disabled() && !update.IsDevBuild()
	return m
}

func (m ChatModel) runUpdateCommand(arg string) (tea.Model, tea.Cmd) {
	if update.Disabled() {
		return m.statusNote("Updates are turned off for this install (GAIA_DISABLE_UPDATE=1)."), nil
	}
	switch {
	case arg == "":
		return m.statusNote("Checking for updates…"), checkUpdateCmd(false, "")
	case arg == "now" || arg == "latest":
		return m.statusNote("Checking for the newest release…"), checkUpdateCmd(false, "now")
	case arg == "list":
		return m.statusNote("Listing releases…"), checkUpdateCmd(false, "list")
	case arg == "auto on":
		return m, saveUpdateSettingCmd("Auto-update is on: a newer release installs when you quit GAIA. "+
			"The desktop app follows the same setting.", func() error { return update.SetAutoUpdate(true) })
	case arg == "auto off":
		return m, saveUpdateSettingCmd("Auto-update is off: GAIA tells you when a release is out and waits for /update now.",
			func() error {
				// A release the launch check queued for quit no longer installs.
				update.Cancel()
				return update.SetAutoUpdate(false)
			})
	case arg == "rc on":
		return m, saveUpdateSettingCmd("Auto-update now includes release candidates (vX.Y.Z-rcN). "+
			"/update rc off goes back to final releases only.", func() error { return update.SetPrereleases(true) })
	case arg == "rc off":
		return m, saveUpdateSettingCmd("Auto-update takes final releases only.",
			func() error { return update.SetPrereleases(false) })
	case arg == "unpin":
		return m, saveUpdateSettingCmd("Unpinned: GAIA offers new releases again.", func() error { return update.SetPin("") })
	case arg == "cancel":
		if _, ok := update.Pending(); !ok {
			return m.statusNote("No update is waiting to install."), nil
		}
		update.Cancel()
		return m.statusNote("The pending update will not install when you quit."), nil
	case update.ValidTag(arg):
		if update.SameVersion(arg, update.Current) {
			return m.statusNote("GAIA " + arg + " is the version already running."), nil
		}
		return m.statusNote("Looking up GAIA " + arg + "…"), planTagCmd(arg)
	}
	m.messages = append(m.messages, Message{Role: RoleError, Content: fmt.Sprintf("%q is not an /update option. %s", arg, updateUsage)})
	m.updateViewport()
	return m, nil
}

func (m ChatModel) handleUpdateChecked(msg updateCheckedMsg) (tea.Model, tea.Cmd) {
	st := msg.status
	if msg.err != nil {
		return m.errorNote("Could not check for GAIA updates: " + msg.err.Error()), nil
	}
	pinned := st.Config.PinnedVersion != ""
	if msg.launch {
		if !st.Available || pinned {
			return m, nil
		}
		if st.Config.AutoUpdate {
			return m, planUpdateCmd(st.Latest, false, true)
		}
		return m.statusNote(fmt.Sprintf("GAIA %s is available (you have %s). Type /update now to install it — "+
			"GAIA closes, installs it, and you reopen it. /update for more options.", st.Latest.Tag, st.Current)), nil
	}
	switch msg.then {
	case "now":
		if update.IsDevBuild() {
			return m.statusNote(st.Summary() + " Install a release with /update v0.25.0."), nil
		}
		if !st.Available {
			return m.statusNote(st.Summary()), nil
		}
		return m.statusNote("Preparing GAIA " + st.Latest.Tag + "…"), planUpdateCmd(st.Latest, false, false)
	case "list":
		return m.statusNote(releaseList(st)), nil
	}
	text := st.Summary()
	if st.Available && !pinned {
		text += " Type /update now to install it."
	}
	return m.statusNote(text + "\n" + updateUsage), nil
}

func (m ChatModel) handleUpdatePlanned(msg updatePlannedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.errorNote("Cannot install that GAIA release: " + msg.err.Error()), nil
	}
	if msg.scheduledOnly {
		update.Schedule(msg.plan)
		return m.statusNote(fmt.Sprintf("GAIA %s will install when you quit (auto-update is on). "+
			"/update cancel skips it; /update auto off turns this off.", msg.plan.Tag)), nil
	}
	if m.streaming {
		return m.statusNote("GAIA is still answering — installing closes it. Let the answer finish (or press Esc), then run /update again."), nil
	}
	if m.setupRunning {
		return m.statusNote("Setup is still running — installing closes GAIA. Let it finish (or press Esc), then run /update again."), nil
	}
	saved, err := m.saveTranscriptForUpdate()
	if err == nil && saved != "" {
		err = m.saveForResume(msg.plan.Tag)
	}
	if err != nil {
		return m.errorNote("Not installing: this conversation could not be saved first, so closing GAIA would lose it. " + err.Error()), nil
	}
	update.Schedule(msg.plan)
	how := "GAIA closes now and the installer runs in this terminal; reopen GAIA when it finishes."
	if saved != "" {
		how += " This conversation reopens with it, and is also saved to " + saved + "."
	}
	if msg.plan.Pin {
		how += " It stays on " + msg.plan.Tag + " until you /update unpin."
	}
	return m.statusNote("Installing GAIA " + msg.plan.Tag + ". " + how), tea.Quit
}

// saveTranscriptForUpdate writes the conversation on screen to
// ~/.gaia/transcripts before an update closes the TUI. "" when there is
// nothing to keep (no message from the user yet).
func (m ChatModel) saveTranscriptForUpdate() (string, error) {
	if !m.hasUserMessage() {
		return "", nil
	}
	cfg, err := update.ConfigPath()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(filepath.Dir(cfg), "transcripts")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("cannot create %s: %w", dir, err)
	}
	path := filepath.Join(dir, "before-update-"+time.Now().Format("20060102-150405")+".md")
	if err := os.WriteFile(path, []byte(transcriptMarkdown(m.messages, m.agentName)), 0o600); err != nil {
		return "", fmt.Errorf("cannot write %s: %w", path, err)
	}
	return path, nil
}

func (m ChatModel) handleUpdateSaved(msg updateSavedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.errorNote("Could not save the update setting: " + msg.err.Error()), nil
	}
	return m.statusNote(msg.text), nil
}

func (m ChatModel) errorNote(text string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleError, Content: text})
	m.updateViewport()
	return m
}

// releaseList is /update list: recent releases, the running one and the pin marked.
func releaseList(st update.Status) string {
	if update.IsDevBuild() && len(st.Releases) == 0 {
		return st.Summary()
	}
	var b strings.Builder
	b.WriteString("Recent GAIA releases (install one with /update <version>):")
	shown := 0
	for _, r := range st.Releases {
		if shown == 10 {
			break
		}
		shown++
		line := "\n  " + r.Tag
		if r.Prerelease {
			line += "  (release candidate)"
		}
		if update.SameVersion(r.Tag, st.Current) {
			line += "  ← running"
		}
		if r.Tag == st.Config.PinnedVersion {
			line += "  (pinned)"
		}
		b.WriteString(line)
	}
	b.WriteString("\nOlder releases: " + update.ReleasesPage)
	return b.String()
}
