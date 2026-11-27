// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiaprogram"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// /agent gaiacpp on a PC without gaiacpp offers the download instead of
// refusing: check the release, ask, download with progress, then switch.

type fetchStage int

const (
	fetchChecking fetchStage = iota
	fetchOffered
	fetchDownloading
)

// programFetch is gaiacpp being offered or downloaded.
type programFetch struct {
	stage  fetchStage
	status gaiaprogram.Status
	cancel context.CancelFunc
	events chan tea.Msg
	// line is the progress line now in the transcript.
	line string
}

type programStatusMsg struct {
	status gaiaprogram.Status
	err    error
}

type programProgressMsg gaiaprogram.Progress

type programDownloadedMsg struct {
	path string
	err  error
}

const nothingChanged = "Nothing was changed: this session is still on %s and your saved choice is as it was."

// downloadable reports whether err is a gaiacpp this release can replace: one
// that is not on this PC, or one that failed its check. why is the sentence
// the offer opens with.
func downloadable(err error) (why string, ok bool) {
	var missing *catalog.MissingProgramError
	if errors.As(err, &missing) && missing.Binary == catalog.NativeAgentBinary && missing.Lookup.Unverified == "" {
		return catalog.NativeAgentBinary + " (Beta) is not on this PC yet.", true
	}
	var broken *catalog.NativeIntegrityError
	if errors.As(err, &broken) {
		return fmt.Sprintf("The %s on this PC was not run: %s.", catalog.NativeAgentBinary, broken.Reason), true
	}
	return "", false
}

func (m FlagshipModel) checkProgramCmd() tea.Cmd {
	check := m.checkProgram
	if check == nil {
		check = gaiaprogram.Check
	}
	return func() tea.Msg {
		st, err := check(context.Background())
		return programStatusMsg{status: st, err: err}
	}
}

// offerProgram starts the offer: it says why gaiacpp cannot run and asks the
// release whether it has one for this PC.
func (m FlagshipModel) offerProgram(why string) (tea.Model, tea.Cmd) {
	current := programOf(m.agent)
	if reason := m.nativeRefusal(true); reason != "" {
		m.chat.AppendStatus(reason + " " + fmt.Sprintf(nothingChanged, current))
		return m, nil
	}
	if m.programFetch != nil && m.programFetch.stage == fetchDownloading {
		m.chat.AppendStatus(catalog.NativeAgentBinary + " is already downloading. Esc cancels it.")
		return m, nil
	}
	m.programFetch = &programFetch{stage: fetchChecking}
	m.chat.AppendStatus(why + " Checking whether this GAIA release has it…")
	return m, m.checkProgramCmd()
}

func (m FlagshipModel) programStatus(msg programStatusMsg) (tea.Model, tea.Cmd) {
	if m.programFetch == nil || m.programFetch.stage != fetchChecking || m.chat == nil {
		return m, nil
	}
	current := programOf(m.agent)
	unchanged := " " + fmt.Sprintf(nothingChanged, current)
	switch {
	case msg.err != nil:
		m.programFetch = nil
		m.chat.AppendStatus(fmt.Sprintf("Could not check for %s: %v.", catalog.NativeAgentBinary, msg.err) + unchanged)
	case !msg.status.CanDownload && msg.status.Retry:
		m.programFetch = nil
		m.chat.AppendStatus(fmt.Sprintf(
			"GAIA could not reach its release to look for %s: %s. Check your connection, then choose it again with /agent.",
			catalog.NativeAgentBinary, msg.status.Reason) + unchanged)
	case !msg.status.CanDownload:
		m.programFetch = nil
		m.chat.AppendStatus(fmt.Sprintf("%s can't be downloaded here: %s",
			catalog.NativeAgentBinary, msg.status.Reason) + unchanged)
	default:
		m.programFetch.stage = fetchOffered
		m.programFetch.status = msg.status
		m.chat.AppendStatus(programOfferText(msg.status))
	}
	return m, nil
}

func programOfferText(st gaiaprogram.Status) string {
	size := "a download"
	if st.SizeBytes > 0 {
		size = "a " + gaiaprogram.HumanSize(st.SizeBytes) + " download"
	}
	where := ""
	if st.Target != "" {
		where = " in " + filepath.Dir(st.Target)
	}
	return fmt.Sprintf(
		"This GAIA release has %s %s (Beta): %s, checked against the release's checksums before it is installed%s.\n"+
			"Press Enter to download it and switch, or Esc to cancel.",
		catalog.NativeAgentBinary, st.Version, size, where)
}

// programFetchKey answers the offer or cancels the download. It reports
// whether it took the key; a key the chat is using is never taken.
func (m FlagshipModel) programFetchKey(key tea.KeyMsg) (tea.Model, tea.Cmd, bool) {
	f := m.programFetch
	if f == nil || m.activeView != viewChat || m.chat == nil || m.sessionsPanel != nil || !m.chat.Idle() {
		return m, nil, false
	}
	switch {
	case f.stage == fetchOffered && key.String() == "enter":
		model, cmd := m.startProgramDownload()
		return model, cmd, true
	case f.stage == fetchOffered && key.String() == "esc":
		m.programFetch = nil
		m.chat.AppendStatus(fmt.Sprintf("%s was not downloaded. ", catalog.NativeAgentBinary) +
			fmt.Sprintf(nothingChanged, programOf(m.agent)))
		return m, nil, true
	case f.stage == fetchDownloading && key.String() == "esc":
		f.cancel()
		return m, nil, true
	}
	return m, nil, false
}

func (m FlagshipModel) startProgramDownload() (tea.Model, tea.Cmd) {
	download := m.downloadProgram
	if download == nil {
		download = gaiaprogram.Download
	}
	ctx, cancel := context.WithCancel(context.Background())
	events := make(chan tea.Msg, 8)
	f := &programFetch{stage: fetchDownloading, status: m.programFetch.status, cancel: cancel, events: events}
	f.line = programProgressText(gaiaprogram.Progress{Total: f.status.SizeBytes})
	m.programFetch = f
	m.chat.AppendStatus(f.line)
	go func() {
		path, err := download(ctx, func(p gaiaprogram.Progress) {
			// Progress may be dropped; the result below never is.
			select {
			case events <- programProgressMsg(p):
			default:
			}
		})
		cancel()
		events <- programDownloadedMsg{path: path, err: err}
	}()
	return m, waitProgramFetch(events)
}

func waitProgramFetch(events chan tea.Msg) tea.Cmd {
	return func() tea.Msg { return <-events }
}

func programProgressText(p gaiaprogram.Progress) string {
	name := catalog.NativeAgentBinary
	switch {
	case p.Total > 0:
		return fmt.Sprintf("Downloading %s… %s of %s (%d%%). Esc cancels.",
			name, gaiaprogram.HumanSize(p.Done), gaiaprogram.HumanSize(p.Total), p.Done*100/p.Total)
	case p.Done > 0:
		return fmt.Sprintf("Downloading %s… %s. Esc cancels.", name, gaiaprogram.HumanSize(p.Done))
	}
	return fmt.Sprintf("Downloading %s… Esc cancels.", name)
}

func (m FlagshipModel) programProgress(msg programProgressMsg) (tea.Model, tea.Cmd) {
	f := m.programFetch
	if f == nil || f.stage != fetchDownloading {
		return m, nil
	}
	if m.chat != nil {
		next := programProgressText(gaiaprogram.Progress(msg))
		m.chat.ReplaceStatus(f.line, next)
		f.line = next
	}
	return m, waitProgramFetch(f.events)
}

// programDownloaded ends the download: on success gaiacpp is on this PC, so
// the switch that asked for it runs again and now finds it.
func (m FlagshipModel) programDownloaded(msg programDownloadedMsg) (tea.Model, tea.Cmd) {
	f := m.programFetch
	if f == nil || f.stage != fetchDownloading {
		return m, nil
	}
	m.programFetch = nil
	if m.chat == nil {
		return m, nil
	}
	current := programOf(m.agent)
	if msg.err != nil {
		m.chat.ReplaceStatus(f.line, fmt.Sprintf("%s was not installed: %v. ", catalog.NativeAgentBinary, msg.err)+
			fmt.Sprintf(nothingChanged, current))
		return m, nil
	}
	m.chat.ReplaceStatus(f.line, fmt.Sprintf(
		"Downloaded %s %s and checked it against the release's checksum. Installed at %s.",
		catalog.NativeAgentBinary, f.status.Version, msg.path))
	m.programFetched = true
	model, cmd := m.switchProgram(preflight.FlagshipAgentNative)
	if fm, ok := model.(FlagshipModel); ok {
		fm.programFetched = false
		return fm, cmd
	}
	return model, cmd
}

// WithProgramFetch replaces the calls that check for and download gaiacpp.
// Tests use it to stay off the network; a real session asks the gaia CLI.
func (m FlagshipModel) WithProgramFetch(
	check func(context.Context) (gaiaprogram.Status, error),
	download func(context.Context, func(gaiaprogram.Progress)) (string, error),
) FlagshipModel {
	m.checkProgram, m.downloadProgram = check, download
	return m
}
