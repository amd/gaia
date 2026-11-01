// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"context"
	"fmt"
	"io"
	"math"
	"os"
	"regexp"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/setupapi"
)

// clipboardOut is where the OSC 52 copy goes: the terminal. A var for tests.
var clipboardOut io.Writer = os.Stdout

func (m Model) key(msg tea.KeyMsg) (Model, tea.Cmd) {
	k := msg.String()
	if m.ask != askNone {
		return m.answer(k)
	}
	if m.tooSmall() && k != "esc" {
		return m, nil
	}
	if k == "d" && m.scr != scrGuide {
		m.details = !m.details
		return m, nil
	}
	if m.details && k == "esc" {
		m.details = false
		return m, nil
	}
	switch m.scr {
	case scrLoading:
		if k == "esc" {
			return m.leave()
		}
	case scrLoadError, scrNoServer:
		switch k {
		case "r":
			m.scr, m.busy, m.busySince, m.loadErr = scrLoading, readingModels, m.opts.Now(), nil
			return m, tea.Batch(m.loadCmd(), m.tickCmd())
		case "u":
			if m.scr == scrLoadError && m.opts.Mode == ModeFirstRun {
				return m.useRecommended()
			}
		case "enter":
			if m.scr == scrNoServer && m.opts.OfferInstall {
				return m, func() tea.Msg { return InstallServerMsg{} }
			}
		case "esc":
			return m.leave()
		}
	case scrModels:
		return m.modelsKey(k)
	case scrChatPick:
		return m.pickKey(k)
	case scrGPU:
		return m.gpuKey(k)
	case scrGuide:
		return m.guideKey(k)
	case scrDownload:
		return m.downloadKey(k)
	case scrReady:
		switch k {
		case "enter":
			if m.opts.Mode == ModeFirstRun {
				return m, func() tea.Msg { return FinishedMsg{} }
			}
			return m.leave()
		case "m":
			m = m.toModels()
			return m, nil
		case "esc":
			m = m.toModels()
			return m, nil
		}
	}
	return m, nil
}

// leave is esc on a screen with nothing behind it in this component.
func (m Model) leave() (Model, tea.Cmd) {
	if m.opts.Mode == ModeFirstRun {
		return m, func() tea.Msg { return BackMsg{} }
	}
	busy, _ := m.Downloading()
	return m, func() tea.Msg { return ClosedMsg{Downloading: busy} }
}

// answer handles the footer's y/n question. esc is always no.
func (m Model) answer(k string) (Model, tea.Cmd) {
	yes := k == "y"
	no := k == "n" || k == "esc"
	if !yes && !no {
		return m, nil
	}
	ask := m.ask
	m.ask = askNone
	backend := m.askAck
	m.askAck = nil
	switch ask {
	case askNoFit:
		if no {
			// "Pick another": the cursor goes to the model in question.
			m.focusOn(m.askRow.id)
			return m, nil
		}
		m.ackedFit[m.askRow.id] = true
		return m.proceed()
	case askBackendNoFit:
		if no {
			return m, nil
		}
		m.ackedFit[m.sel.chat] = true
		return m.save()
	case askLowDisk:
		if no {
			return m, nil
		}
		m.ackedDisk = true
		if backend != nil {
			return m.startDownload()
		}
		return m.proceed()
	case askCancel:
		if no {
			return m, nil
		}
		return m.cancel()
	case askRemove:
		if no {
			return m, nil
		}
		m.busy, m.busySince = "Removing "+m.askRow.name+"…", m.opts.Now()
		api, id := m.api, m.askRow.id
		return m, tea.Batch(func() tea.Msg {
			ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
			defer cancel()
			p, err := api.Remove(ctx, id)
			return removedMsg{plan: p, err: err}
		}, m.tickCmd())
	case askGPUConsent:
		if no {
			return m, nil
		}
		m.gpuPhase = gpuWaiting
		m.busy, m.busySince = gpuWaitingText, m.opts.Now()
		m.gpuNote = ""
		return m, tea.Batch(m.gpuCmd("apply", m.targetGB()), m.tickCmd())
	}
	return m, nil
}

func (m *Model) focusOn(id string) {
	for i, it := range m.items() {
		if !it.group && sameID(it.row.id, id) {
			m.focus = i
			return
		}
	}
}

func (m Model) modelsKey(k string) (Model, tea.Cmd) {
	items := m.items()
	switch k {
	case "up", "k":
		if m.focus > 0 {
			m.focus--
		}
	case "down", "j":
		if m.focus < len(items)-1 {
			m.focus++
		}
	case " ", "space":
		if m.focus < len(items) {
			if items[m.focus].group {
				m.showAll = !m.showAll
			} else {
				m.note = ""
				if r := items[m.focus].row; !m.sel.toggle(r.id) {
					m.note = r.name + " is on this PC and setup no longer downloads it, so there is nothing to untick. /models can remove it (x)."
				}
				// A changed selection is a new question about the disk.
				m.ackedDisk = false
			}
		}
	case "l":
		m.showAll = !m.showAll
		m.clampFocus()
	case "a":
		if m.opts.Mode == ModeModels {
			return m.proceed()
		}
		chat := ""
		if m.plan.RecommendedModel != nil {
			chat = *m.plan.RecommendedModel
		}
		m.sel.resetToRecommended(chat)
		m.ackedDisk = false
	case "c":
		if cands := m.sel.chatCandidates(); len(cands) > 0 && !m.sel.cloud {
			m.pick = 0
			for i, c := range cands {
				if sameID(c.id, m.sel.chat) {
					m.pick = i
				}
			}
			m.back, m.scr = scrModels, scrChatPick
		}
	case "t":
		m.keepTry = !m.keepTry
	case "x":
		if r, ok := m.focusedRow(); ok && m.opts.Mode == ModeModels && r.downloaded {
			m.ask, m.askRow = askRemove, r
		}
	case "enter":
		if m.focus < len(items) && items[m.focus].group {
			m.showAll = !m.showAll
			return m, nil
		}
		return m.proceed()
	case "esc":
		return m.leave()
	}
	return m, nil
}

func (m Model) pickKey(k string) (Model, tea.Cmd) {
	cands := m.sel.chatCandidates()
	switch k {
	case "up", "k":
		if m.pick > 0 {
			m.pick--
		}
	case "down", "j":
		if m.pick < len(cands)-1 {
			m.pick++
		}
	case "enter", " ", "space":
		if m.pick < len(cands) {
			m.sel.chat = cands[m.pick].id
		}
		m.scr = m.back
	case "esc":
		m.scr = m.back
	}
	return m, nil
}

func (m Model) gpuKey(k string) (Model, tea.Cmd) {
	if m.gpuPhase == gpuWaiting {
		return m, nil
	}
	switch k {
	case "left":
		if m.gpuTarget > 0 {
			m.gpuTarget--
			m.gpuNote = ""
		}
	case "right":
		if m.gpu != nil && m.gpuTarget < len(m.gpu.Presets)-1 {
			m.gpuTarget++
			m.gpuNote = ""
		}
	case "enter", "t":
		if m.gpuDone {
			return m.afterGPU()
		}
		if m.gpu == nil || len(m.gpu.Presets) == 0 {
			return m.afterGPU()
		}
		if m.gpu.Method == setupapi.MethodApply {
			m.ask = askGPUConsent
			return m, nil
		}
		return m.openGuide()
	case "h":
		return m.openGuide()
	case "s":
		return m.afterGPU()
	case "k":
		if m.gpu != nil && m.gpu.Pending != nil {
			m.busy, m.busySince = "Keeping the current setting…", m.opts.Now()
			return m, tea.Batch(m.gpuCmd("keep", 0), m.tickCmd())
		}
	case "u":
		if m.gpu != nil && m.gpu.Revert.Available && m.gpu.Method == setupapi.MethodApply {
			m.busy, m.busySince = "Undoing the change…", m.opts.Now()
			return m, tea.Batch(m.gpuCmd("revert", 0), m.tickCmd())
		}
	case "esc":
		if m.opts.Mode == ModeFirstRun {
			m = m.toModels()
			return m, nil
		}
		return m.leave()
	}
	return m, nil
}

// afterGPU moves past the GPU step: on to the download in setup, or closed.
func (m Model) afterGPU() (Model, tea.Cmd) {
	m.gpuDone = true
	if m.opts.Mode == ModeFirstRun {
		return m.startDownload()
	}
	return m.leave()
}

func (m Model) openGuide() (Model, tea.Cmd) {
	m.back, m.scr = scrGPU, scrGuide
	m.busy, m.busySince = "Reading the steps…", m.opts.Now()
	return m, tea.Batch(m.gpuCmd("guide", m.targetGB()), m.tickCmd())
}

func (m Model) guideKey(k string) (Model, tea.Cmd) {
	switch k {
	case "c":
		v := m.copyValue()
		if v == "" {
			return m, nil
		}
		_, err := io.WriteString(clipboardOut, ansi.SetClipboard(ansi.SystemClipboard, v))
		if err != nil {
			m.gpuNote = "Could not copy: " + err.Error() + ". The value is " + v + "."
		} else {
			m.gpuNote = "Copied " + v + " (if nothing pastes, this terminal can't copy; type it instead)."
		}
	case "enter":
		if m.gpuDone {
			return m.afterGPU()
		}
		// Re-read the split; never trust "I've done this".
		m.busy, m.busySince = "Checking the GPU memory…", m.opts.Now()
		return m, tea.Batch(m.gpuCmd("reread", m.targetGB()), m.tickCmd())
	case "s":
		return m.afterGPU()
	case "esc":
		m.scr = scrGPU
	}
	return m, nil
}

var literalRE = regexp.MustCompile("`([^`]+)`")

// copyValue is the one literal the guided steps ask the user to type: the
// first `quoted` value in them, else the setting in the GiB AMD Software
// counts in.
func (m Model) copyValue() string {
	if m.gpu == nil {
		return ""
	}
	for _, step := range m.gpu.GuidedSteps {
		if mm := literalRE.FindStringSubmatch(step); mm != nil {
			return mm[1]
		}
	}
	gib := math.Ceil(m.targetGB() * 1e9 / (1 << 30))
	return fmt.Sprintf("%.0f", gib)
}

func (m Model) downloadKey(k string) (Model, tea.Cmd) {
	rows := m.shownRows()
	switch k {
	case "up", "k":
		if m.focus > 0 {
			m.focus--
		}
	case "down", "j":
		if m.focus < len(rows)-1 {
			m.focus++
		}
	case "c":
		if !m.dl.active {
			return m, nil
		}
		return m.askToCancel()
	case "r":
		if m.lost != nil {
			m.lost = nil
			m.polling = m.dl.active
			m.note = ""
			return m, tea.Batch(m.pollNow(), m.tickCmd())
		}
		if !m.dl.active && m.opts.Mode == ModeFirstRun && !m.focusRetryable(rows) {
			return m.resumeAll()
		}
		return m.retryFocused(rows)
	case "R":
		return m.resumeAll()
	case "s":
		if m.focus < len(rows) && rows[m.focus].State == setupapi.StateFailed && !m.dl.run {
			return m.skipFailed(rows[m.focus].ID)
		}
	case "t":
		m.keepTry = !m.keepTry
	case "enter":
		if !m.dl.active && m.allReady() {
			if m.opts.Mode == ModeFirstRun {
				m.scr = scrReady
				return m, nil
			}
			return m.leave()
		}
	case "esc":
		if m.opts.Mode == ModeFirstRun {
			if m.dl.active {
				return m.askToCancel()
			}
			m = m.toModels()
			return m, nil
		}
		return m.leave()
	}
	return m, nil
}

func (m Model) focusRetryable(rows []setupapi.ModelProgress) bool {
	if m.focus >= len(rows) {
		return false
	}
	st := rows[m.focus].State
	return st == setupapi.StateFailed || st == setupapi.StateCancelled
}

// askToCancel stops the download, asking first once over 1 GB is in.
func (m Model) askToCancel() (Model, tea.Cmd) {
	done, _ := m.overallBytes()
	if done > 1e9 {
		m.ask = askCancel
		return m, nil
	}
	return m.cancel()
}

func (m Model) cancel() (Model, tea.Cmd) {
	m.busy, m.busySince = "Stopping the download…", m.opts.Now()
	api := m.api
	return m, tea.Batch(func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		_, err := api.Cancel(ctx, "")
		return cancelledMsg{err: err}
	}, m.tickCmd())
}

// retryFocused retries a failed row or resumes a cancelled one.
func (m Model) retryFocused(rows []setupapi.ModelProgress) (Model, tea.Cmd) {
	if m.focus >= len(rows) {
		return m, nil
	}
	row := rows[m.focus]
	if row.State != setupapi.StateFailed && row.State != setupapi.StateCancelled {
		if r := m.rates[row.ID]; r != nil && row.State == setupapi.StateDownloading &&
			r.stalled(m.opts.Now()) >= stallStuck && !m.dl.run {
			// Stuck: stop that one pull and start it again.
			return m.restartOne(row.ID)
		}
		return m, nil
	}
	if m.dl.run {
		m.note = runOwnsRetry
		return m, nil
	}
	return m.restartOne(row.ID)
}

// restartOne queues a single download of id ahead of anything still queued.
func (m Model) restartOne(id string) (Model, tea.Cmd) {
	if m.dl.current != "" && !sameID(m.dl.current, id) {
		m.dl.queue = append(m.dl.queue, id)
		return m, nil
	}
	m.dl.active, m.dl.cancelled = true, false
	m.polling = true
	if !m.counts(id) {
		m.dl.ids = append(m.dl.ids, id)
	}
	delete(m.rates, id)
	m.dl.queue = append([]string{id}, m.dl.queue...)
	m.note = ""
	return m.nextSingle()
}

// resumeAll is Resume: the run again in setup (it continues from the bytes on
// disk), or every model that did not finish.
func (m Model) resumeAll() (Model, tea.Cmd) {
	if m.dl.active {
		return m, nil
	}
	if m.opts.Mode == ModeFirstRun && m.plan != nil {
		m.dl.cancelled = false
		return m.startDownload()
	}
	var ids []string
	for _, r := range m.shownRows() {
		if r.State == setupapi.StateFailed || r.State == setupapi.StateCancelled || r.State == setupapi.StateQueued {
			ids = append(ids, r.ID)
		}
	}
	if len(ids) == 0 {
		return m, nil
	}
	m.dl.active, m.dl.cancelled, m.polling = true, false, true
	m.dl.queue = ids
	m.note = ""
	return m.nextSingle()
}

// skipFailed is "skip this model": saved as skipped, out of the totals, and no
// longer holding up the ready summary.
func (m Model) skipFailed(id string) (Model, tea.Cmd) {
	kept := m.dl.ids[:0:0]
	for _, d := range m.dl.ids {
		if !sameID(d, id) {
			kept = append(kept, d)
		}
	}
	m.dl.ids = kept
	if m.status != nil {
		st := *m.status
		st.Models = nil
		for _, r := range m.status.Models {
			if !sameID(r.ID, id) {
				st.Models = append(st.Models, r)
			}
		}
		m.status = &st
	}
	delete(m.ackedFit, id)
	m.sel.ticked[id] = false
	if m.focus > 0 {
		m.focus--
	}
	api := m.api
	planned := false
	if r, ok := m.sel.find(id); ok {
		planned = r.planned
	}
	if !planned {
		return m.advance()
	}
	return m, func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		p, err := api.Skip(ctx, id, true)
		return removedMsg{plan: p, err: err}
	}
}

// useRecommended runs setup with whatever the backend plans when the model
// list could not be read. A choice the user makes, never a silent fallback.
func (m Model) useRecommended() (Model, tea.Cmd) {
	m.scr = scrDownload
	m.since = m.opts.Now()
	m.dl = download{active: true, run: true, starting: true}
	m.rates = map[string]*rate{}
	m.note = "Using the recommended set; this PC's fit couldn't be checked."
	return m, tea.Batch(m.runCmd(), m.tickCmd())
}

func (m Model) tooSmall() bool { return m.height > 0 && m.height < minH }
