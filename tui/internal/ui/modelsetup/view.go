// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Colour is additive, as on the readiness checklist: every state already
// reads from its text marker ([x] [ ] [!] [?] [..] [ok]) and its words, so the
// screens work on a monochrome terminal and for anyone who can't tell red from
// green.
var (
	titleStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	summaryStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	dividerStyle = lipgloss.NewStyle().Foreground(theme.Divider)
	textStyle    = lipgloss.NewStyle().Foreground(theme.Text)
	dimStyle     = lipgloss.NewStyle().Foreground(theme.Dim)
	okStyle      = lipgloss.NewStyle().Foreground(theme.Success)
	flashStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.Success)
	failStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
	warnStyle    = lipgloss.NewStyle().Foreground(theme.Warning)
	keyStyle     = lipgloss.NewStyle().Bold(true).Foreground(theme.Info)
	infoStyle    = lipgloss.NewStyle().Foreground(theme.Info)
	accentStyle  = lipgloss.NewStyle().Foreground(theme.Accent)
	focusStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.Selected)
	trackStyle   = lipgloss.NewStyle().Foreground(theme.Divider)
)

const (
	indentW = 2
	minW    = 40
	minH    = 10
	nameW   = 22
	purpW   = 12
	sizeW   = 9
)

// View renders the screen on view.
func (m Model) View() string {
	if m.tooSmall() {
		return clip([]string{pad(warnStyle.Render(tooSmall))}, m.width, m.height)
	}
	w, h := m.width, m.height
	if w < minW {
		w = minW
	}
	var title, right string
	var body, foot []string
	switch m.scr {
	case scrLoading:
		title = titleLoading
		body = []string{pad(m.busyMarker() + " " + dimStyle.Render(m.busyText()))}
		foot = m.footer(w, [][2]string{{"esc", "back"}})
	case scrLoadError:
		title = titleModels
		body = m.loadErrorLines(w)
		keys := [][2]string{{"r", "try again"}}
		if m.opts.Mode == ModeFirstRun && m.loadErr != nil && m.opts.Mode != ModeGPU {
			keys = append(keys, [2]string{"u", "use the recommended set"})
		}
		foot = m.footer(w, append(keys, [2]string{"esc", "back"}))
	case scrNoServer:
		title = titleNoServer
		keys := [][2]string{{"r", "try again"}, {"esc", "back"}}
		text := noServerBody
		if m.opts.OfferInstall {
			text = noServerInstall
			keys = append([][2]string{{"enter", "install it"}}, keys...)
		}
		body = indentAll(wrapStyled(dimStyle, text, w-2*indentW), indentW)
		foot = m.footer(w, keys)
	case scrModels:
		title = titleModels
		if m.opts.Mode == ModeModels {
			title = titleSettings
		}
		body, foot = m.modelsView(w, h)
	case scrChatPick:
		title = "Default chat model"
		body = m.pickLines(w)
		foot = m.footer(w, [][2]string{{"↑/↓", "move"}, {"enter", "choose"}, {"esc", "back"}})
	case scrGPU:
		title = titleGPU
		body, foot = m.gpuView(w)
	case scrGuide:
		title = titleGuide
		body, foot = m.guideView(w)
	case scrDownload:
		title = titleDownload
		body, foot = m.downloadView(w)
	case scrReady:
		title = titleReady
		body, foot = m.readyView(w)
	}
	if m.opts.Mode == ModeFirstRun {
		right = m.stepLabel(m.scr)
	}
	if m.details {
		body = m.detailLines(w)
		foot = m.footer(w, [][2]string{{"d", "close details"}, {"esc", "back"}})
	}

	div := strings.Repeat("─", w-2*indentW)
	up, down := "", ""
	budget := h - 4 - len(foot) + 1
	if budget < 1 {
		budget = 1
	}
	if len(body) > budget {
		body = body[:budget]
	}
	if m.scr == scrModels && !m.details {
		up, down = m.scrollHints()
	}
	out := []string{m.header(w, title, right), pad(dividerStyle.Render(withHint(div, up)))}
	out = append(out, body...)
	out = append(out, pad(dividerStyle.Render(withHint(div, down))))
	out = append(out, foot...)
	return clip(out, m.width, m.height)
}

func withHint(div, hint string) string {
	if hint == "" {
		return div
	}
	r := []rune(div)
	h := []rune(" " + hint + " ")
	if len(r) < len(h)+4 {
		return div
	}
	copy(r[len(r)-len(h)-2:], h)
	return string(r)
}

func (m Model) header(w int, left, right string) string {
	gap := w - 2*indentW - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		return pad(titleStyle.Render(truncate(left, w-2*indentW)))
	}
	return pad(titleStyle.Render(left) + strings.Repeat(" ", gap) + summaryStyle.Render(right))
}

// footer is the key hints, or the y/n question that replaces them.
func (m Model) footer(w int, keys [][2]string) []string {
	if m.ask != askNone {
		return m.askFooter(w)
	}
	return []string{fitKeys(w, m.width, keys)}
}

// fitKeys drops whole hints from the FRONT when the line is too long, so the
// last one — esc, the way out — always survives.
func fitKeys(w, real int, keys [][2]string) string {
	avail := w
	if real > 0 && real < avail {
		avail = real
	}
	avail -= 2 * indentW
	for first := 0; first < len(keys); first++ {
		line := renderKeys(keys[first:])
		if first == len(keys)-1 || lipgloss.Width(line) <= avail {
			return pad(line)
		}
	}
	return pad("")
}

func renderKeys(keys [][2]string) string {
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, keyStyle.Render(k[0])+" "+dimStyle.Render(k[1]))
	}
	return strings.Join(parts, dimStyle.Render(" · "))
}

// askFooter is the question and its keys. It is one line when it fits; a
// narrow terminal gets the question above the keys.
func (m Model) askFooter(w int) []string {
	q, yes, no := m.askText()
	keys := keyStyle.Render("y") + " " + dimStyle.Render(yes) + dimStyle.Render(" · ") +
		keyStyle.Render("n") + " " + dimStyle.Render(no)
	line := warnStyle.Render(q) + " " + keys
	avail := w - 2*indentW
	if m.width > 0 && m.width < w {
		avail = m.width - 2*indentW
	}
	if lipgloss.Width(line) <= avail {
		return []string{pad(line)}
	}
	out := indentAll(wrapStyled(warnStyle, q, avail), indentW)
	return append(out, pad(keys))
}

func (m Model) askText() (q, yes, no string) {
	switch m.ask {
	case askNoFit:
		return noFitAsk(deref(m.askRow.needGB), deref(m.askRow.haveGB)), "download anyway", "pick another"
	case askBackendNoFit:
		if m.askAck != nil {
			return m.askAck.Message, "use it anyway", "pick another"
		}
	case askLowDisk:
		msg := m.sel.shortfall(m.freeGB(), m.drive())
		if m.askAck != nil {
			msg = m.askAck.Message
		}
		return msg, "continue anyway", "change"
	case askCancel:
		return cancelAsk, "stop", "keep downloading"
	case askRemove:
		return removeAsk(m.askRow), "remove", "keep"
	case askRedownload:
		return redownloadAsk(m.askRow.name), "download again", "keep"
	case askGPUConsent:
		return gpuConsentKey, "continue", "cancel"
	}
	return "", "yes", "no"
}

func deref(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

// --- the models checklist -----------------------------------------------------

func (m Model) shortPurpose() bool { return m.width > 0 && m.width < 60 }

func (m Model) modelsView(w, h int) (body, foot []string) {
	var top []string
	if m.resume && m.opts.Mode == ModeFirstRun {
		top = append(top, pad(warnStyle.Render("[!] "+resumeBanner+" ")+dimStyle.Render("enter resume")))
	}
	if m.sel.cloud {
		top = append(top, pad(dimStyle.Render(cloudNoDownload)))
	} else if m.height >= 30 {
		top = append(top, indentAll(wrapStyled(dimStyle, introFor(m.opts.Mode == ModeModels), w-2*indentW), indentW)...)
	}

	bottom := m.modelsBottom(w)
	keys := m.modelsKeys()
	foot = m.footer(w, keys)

	budget := h - 4 - len(foot) + 1 - len(top) - len(bottom)
	if budget < 1 {
		budget = 1
	}
	list := m.listLines(w, budget)
	body = append(top, list...)
	return append(body, bottom...), foot
}

func (m Model) modelsKeys() [][2]string {
	// Least important first: a narrow window drops hints from the front.
	keys := [][2]string{{"d", "details"}}
	if m.opts.Mode == ModeFirstRun {
		keys = append(keys, [2]string{"t", "keep trying"}, [2]string{"a", "recommended"})
	}
	if m.installedCount() > 0 {
		keys = append(keys, [2]string{"l", "installed"})
	}
	if !m.sel.cloud {
		keys = append(keys, [2]string{"c", "default"})
	}
	keys = append(keys, [2]string{"space", "tick"})
	if m.opts.Mode == ModeModels {
		if r, ok := m.focusedRow(); ok && r.downloaded {
			keys = append(keys, [2]string{"x", "remove"})
		}
		keys = append(keys, [2]string{"a", "apply"})
	} else {
		label := "continue"
		if m.sel.shortfall(m.freeGB(), m.drive()) != "" {
			label = "continue anyway"
		}
		if m.resume {
			label = "resume"
		}
		if !m.sel.blocked() {
			keys = append(keys, [2]string{"enter", label})
		}
	}
	return append(keys, [2]string{"esc", "back"})
}

// listLines is the checklist with the focused row's note under it, scrolled
// so the cursor is on screen. The note shrinks before any row is hidden.
func (m Model) listLines(w, budget int) []string {
	items := m.items()
	var rows []string
	for i, it := range items {
		rows = append(rows, m.itemLine(i, it, w))
	}
	note := m.noteLines(w)
	if len(rows) == 0 {
		return nil
	}
	// The note sits under the focused row; it gives way to rows first.
	room := budget - len(rows)
	if room < 0 {
		room = 0
	}
	if len(note) > room {
		if room >= 1 {
			note = note[:room]
		} else {
			note = nil
		}
	}
	// Scroll: keep the focused row and its note in view.
	visible := budget - len(note)
	if visible < 1 {
		visible = 1
	}
	start := 0
	if m.focus >= visible {
		start = m.focus - visible + 1
	}
	end := start + visible
	if end > len(rows) {
		end = len(rows)
	}
	var out []string
	for i := start; i < end; i++ {
		out = append(out, rows[i])
		if i == m.focus {
			out = append(out, note...)
		}
	}
	return out
}

// scrollHints are the ^/v marks on the dividers when rows are off screen.
func (m Model) scrollHints() (up, down string) {
	items := m.items()
	_, foot := []string{}, m.footer(m.width, m.modelsKeys())
	budget := m.height - 4 - len(foot) + 1 - len(m.modelsBottom(max(m.width, minW)))
	if m.resume {
		budget--
	}
	if m.sel.cloud || m.height >= 30 {
		budget--
	}
	note := len(m.noteLines(max(m.width, minW)))
	if budget-len(items) < note {
		note = max(budget-len(items), 0)
	}
	visible := budget - note
	if visible < 1 {
		visible = 1
	}
	if len(items) <= visible {
		return "", ""
	}
	start := 0
	if m.focus >= visible {
		start = m.focus - visible + 1
	}
	if start > 0 {
		up = "^ more"
	}
	if start+visible < len(items) {
		down = "v more"
	}
	return up, down
}

func (m Model) itemLine(i int, it item, w int) string {
	cursor := "  "
	focused := i == m.focus
	if focused {
		cursor = focusStyle.Render(">") + " "
	}
	if it.group {
		label := fmt.Sprintf(installedGroup, m.installedCount())
		if m.showAll {
			label = installedHide
		}
		return pad(cursor + dimStyle.Render("[ ] "+label))
	}
	r := it.row
	short := m.shortPurpose()
	nw, pw := nameW, purpW
	if short {
		pw = 4
		nw = 18
	}
	tick := "[ ]"
	tickStyle := dimStyle
	switch {
	case r.installed && sameID(r.id, m.sel.chat):
		tick, tickStyle = "(•)", okStyle
	case r.installed:
		tick = "( )"
	case m.sel.ticked[r.id]:
		tick, tickStyle = "[x]", textStyle
	}
	nameStyle := textStyle
	if focused {
		nameStyle = focusStyle
	}
	size := m.sizeText(r)
	line := cursor + tickStyle.Render(tick) + " " +
		nameStyle.Render(fmt.Sprintf("%-*s", nw, truncate(r.name, nw))) + " " +
		dimStyle.Render(fmt.Sprintf("%-*s", pw, truncate(r.purposeText(short), pw))) + " " +
		dimStyle.Render(fmt.Sprintf("%-*s", m.sizeCol(), truncate(size, m.sizeCol())))
	if marks := m.marks(r, w-2*indentW-lipgloss.Width(line)-1); marks != "" {
		line += " " + marks
	}
	return pad(line)
}

// sizeCol is the size column's width: wide enough for "12.3 GB left" when a
// partly downloaded model is listed, else the usual nine.
func (m Model) sizeCol() int {
	w := sizeW
	for _, r := range m.sel.rows {
		if n := lipgloss.Width(m.sizeText(r)); n > w {
			w = n
		}
	}
	if w > 14 {
		w = 14
	}
	return w
}

func (m Model) sizeText(r rowEntry) string {
	switch {
	case r.downloaded:
		return statusOnPC
	case r.leftGB == nil:
		return statusUnknown
	case r.partial:
		return statusPartial(*r.leftGB)
	}
	return statusDownload(*r.leftGB)
}

// marks are the row's flags, in words: a warning, an unknown fit, the
// recommendation, the NPU chip. When they don't fit in avail columns the
// recommendation shortens to its "*", so the warning words survive.
func (m Model) marks(r rowEntry, avail int) string {
	var parts []string
	switch {
	case r.fits != nil && !*r.fits:
		parts = append(parts, warnStyle.Render("[!] won't fit"))
	case r.fits == nil && !r.downloaded:
		parts = append(parts, warnStyle.Render("[?] "+fitUnknownChip))
	case !r.installed && !m.sel.ticked[r.id] && skippedCaveat(r) != "":
		parts = append(parts, warnStyle.Render("[!]"))
	}
	rec := -1
	if r.recommended && (r.fits != nil || r.planned) && !r.installed {
		rec = len(parts)
		parts = append(parts, accentStyle.Render(markRecommended))
	}
	if r.npu {
		parts = append(parts, infoStyle.Render(markNPU))
	}
	out := strings.Join(parts, " ")
	if rec >= 0 && lipgloss.Width(out) > avail {
		parts[rec] = accentStyle.Render("*")
		out = strings.Join(parts, " ")
	}
	return out
}

// noteLines explain the focused row: what it is for, whether it fits, and
// what is lost without it.
func (m Model) noteLines(w int) []string {
	r, ok := m.focusedRow()
	if !ok {
		return nil
	}
	indent := indentW + 6
	width := w - indent - indentW
	var out []string
	add := func(s lipgloss.Style, text string) {
		for _, l := range wrap(text, width) {
			out = append(out, strings.Repeat(" ", indent)+s.Render(l))
		}
	}
	line := purposeLine(r)
	if r.fits != nil && *r.fits && r.needGB != nil && r.haveGB != nil {
		line += " " + fitLine(*r.needGB, *r.haveGB)
	}
	add(dimStyle, line)
	if r.partial && r.leftGB != nil {
		add(dimStyle, "Partly downloaded, "+formatGB(*r.leftGB)+" left.")
	}
	switch {
	case r.fits != nil && !*r.fits && r.needGB != nil && r.haveGB != nil:
		add(warnStyle, noFitLine(*r.needGB, *r.haveGB))
	case r.fits == nil && !r.downloaded:
		add(warnStyle, "[?] "+fitUnknownLine)
	}
	if !r.installed && !m.sel.ticked[r.id] {
		if c := skippedCaveat(r); c != "" {
			add(warnStyle, c)
		}
	}
	if r.installed && r.chatable && !sameID(r.id, m.sel.chat) {
		add(dimStyle, "Already on this PC. Press space to chat with it.")
	}
	return out
}

// modelsBottom is the default chat model, the keep-trying toggle, the disk
// totals, and any warning about the whole selection.
func (m Model) modelsBottom(w int) []string {
	out := []string{""}
	width := w - 2*indentW
	if !m.sel.cloud {
		chat := "none"
		if r, ok := m.sel.find(m.sel.chat); ok {
			chat = r.name
			if r.npu {
				chat += " (" + markNPU + ")"
			}
		}
		out = append(out, pad(dimStyle.Render("Default chat: ")+textStyle.Render(chat)+dimStyle.Render("   (c to change)")))
	}
	if m.opts.Mode == ModeFirstRun {
		out = append(out, pad(m.keepTryLine()))
	}
	out = append(out, pad(dimStyle.Render(truncate(diskSummary(m.sel.downloadGB(), m.freeGB(), m.drive()), width))))
	if msg := m.sel.shortfall(m.freeGB(), m.drive()); msg != "" {
		out = append(out, indentAll(wrapStyled(warnStyle, "[!] "+msg, width), indentW)...)
	}
	if m.sel.blocked() {
		out = append(out, indentAll(wrapStyled(warnStyle, "[!] "+zeroChat, width), indentW)...)
	}
	if m.shortPurpose() {
		out = append(out, pad(dimStyle.Render("C chat · V vision · D documents")))
	}
	if m.busy != "" {
		out = append(out, pad(m.busyMarker()+" "+dimStyle.Render(m.busyText())))
	} else if m.note != "" {
		out = append(out, indentAll(wrapStyled(warnStyle, m.note, width), indentW)...)
	}
	return out
}

func (m Model) keepTryLine() string {
	box := "[ ]"
	if m.keepTry {
		box = "[x]"
	}
	return dimStyle.Render(box+" "+keepTryingLabel+"  ") + keyStyle.Render("t")
}

func (m Model) pickLines(w int) []string {
	var out []string
	for i, r := range m.sel.chatCandidates() {
		cursor := "  "
		st := textStyle
		if i == m.pick {
			cursor, st = focusStyle.Render(">")+" ", focusStyle
		}
		mark := "( )"
		if sameID(r.id, m.sel.chat) {
			mark = "(•)"
		}
		extra := ""
		if r.installed || r.downloaded {
			extra = statusOnPC
		}
		if r.npu {
			extra = strings.TrimSpace(extra + " " + markNPU)
		}
		out = append(out, pad(cursor+dimStyle.Render(mark)+" "+st.Render(truncate(r.name, w-20))+"  "+dimStyle.Render(extra)))
	}
	return out
}

func (m Model) loadErrorLines(w int) []string {
	msg := "Couldn't read the model list from Lemonade."
	if m.loadErr != nil {
		msg = "Couldn't read the model list from Lemonade: " + strings.TrimSuffix(m.loadErr.Error(), ".") + "."
	}
	if m.opts.Mode == ModeGPU {
		msg = "Couldn't read how this PC's memory is split."
		if m.loadErr != nil {
			msg = "Couldn't read how this PC's memory is split: " + strings.TrimSuffix(m.loadErr.Error(), ".") + "."
		}
	}
	return indentAll(wrapStyled(failStyle, "[!] "+msg, w-2*indentW), indentW)
}

// --- GPU --------------------------------------------------------------------

func (m Model) gpuView(w int) (body, foot []string) {
	width := w - 2*indentW
	g := m.gpu
	if g == nil {
		return indentAll(wrapStyled(dimStyle, "GAIA couldn't read how this PC's memory is split.", width), indentW),
			m.footer(w, [][2]string{{"esc", "back"}})
	}
	if !g.Applies {
		reason := gpuNoChange
		if g.HiddenReason != nil && *g.HiddenReason != "" {
			reason = *g.HiddenReason
		}
		return indentAll(wrapStyled(dimStyle, reason, width), indentW), m.footer(w, [][2]string{{"esc", "back"}})
	}
	body = append(body, indentAll(wrapStyled(dimStyle, gpuBody(g.TotalGB, g.GPUGB), width), indentW)...)
	body = append(body, "")
	label := "Now:    " + gpuSplit(g.CPUGB, g.GPUGB)
	barW := width - lipgloss.Width(label) - 4
	if barW > 30 {
		barW = 30
	}
	line := pad(textStyle.Render(label))
	if barW >= 8 && g.TotalGB > 0 {
		line += "    " + m.splitBar(barW)
	}
	body = append(body, line)
	pool := m.targetPool()
	if len(g.Presets) > 0 {
		hint := ""
		if len(g.Presets) > 1 {
			hint = "  (←/→ to change)"
		}
		body = append(body, pad(textStyle.Render("Target: GPU "+formatGBShort(pool))+dimStyle.Render(hint)))
		body = append(body, indentAll(wrapStyled(dimStyle, fmt.Sprintf(gpuKeepsFmt, formatGBShort(g.ReserveGB), osName(g.OS)), width), indentW)...)
		if u := gpuUnlocks(pool, m.unlocks()); u != "" {
			body = append(body, pad(okStyle.Render(u)))
		}
	}
	if g.Pending != nil {
		switch g.Pending.State {
		case setupapi.PendingRestart:
			body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+gpuPendingLine(m.poolFor(g.Pending.SettingGB)), width), indentW)...)
		case setupapi.PendingTookEffect:
			body = append(body, indentAll(wrapStyled(okStyle, "[ok] "+gpuTookEffect(g.GPUGB), width), indentW)...)
		case setupapi.PendingNotApplied:
			body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+gpuNotApplied(g.GPUGB), width), indentW)...)
		}
	}
	if g.Method == setupapi.MethodGuided {
		reason := gpuGuidedNote
		if g.MethodReason != "" {
			reason = g.MethodReason
		}
		body = append(body, indentAll(wrapStyled(dimStyle, reason, width), indentW)...)
	}
	if m.ask == askGPUConsent {
		body = append(body, "")
		body = append(body, indentAll(wrapStyled(textStyle, gpuConsent(g.Setting.Name, pool, g.GPUGB), width), indentW)...)
	}
	if m.busy != "" {
		body = append(body, pad(m.busyMarker()+" "+dimStyle.Render(m.busyText())))
	} else if m.gpuNote != "" {
		st := dimStyle
		switch m.gpuPhase {
		case gpuApplied, gpuPendingSaved:
			st = okStyle
		case gpuDenied:
			st = warnStyle
		}
		body = append(body, indentAll(wrapStyled(st, m.gpuNote, width), indentW)...)
	}

	var keys [][2]string
	switch {
	case m.gpuDone:
		keys = append(keys, [2]string{"enter", "continue"})
	case m.gpuPhase == gpuDenied:
		keys = append(keys, [2]string{"t", "try again"}, [2]string{"h", "show me how"}, [2]string{"s", "skip"})
	case g.Method == setupapi.MethodApply:
		keys = append(keys, [2]string{"enter", "apply and restart"}, [2]string{"h", "show me how"})
	default:
		keys = append(keys, [2]string{"enter", "show me how"})
	}
	if g.Pending != nil && g.Pending.State == setupapi.PendingNotApplied {
		keys = append(keys, [2]string{"k", "keep " + formatGBShort(g.GPUGB)})
	}
	if g.Revert.Available && g.Method == setupapi.MethodApply && m.opts.Mode == ModeGPU {
		keys = append(keys, [2]string{"u", "undo"})
	}
	if !m.gpuDone && m.gpuPhase != gpuDenied {
		label := "skip"
		if m.opts.Mode == ModeGPU {
			label = "close"
		}
		keys = append(keys, [2]string{"s", label})
	}
	keys = append(keys, [2]string{"esc", "back"})
	return body, m.footer(w, keys)
}

// unlocks are at most two models the target lets fit, as the backend
// names them for that preset.
func (m Model) unlocks() []string {
	var out []string
	for _, id := range m.targetPreset().Unlocks {
		if m.opts.Mode != ModeGPU && !m.unlockCounts(id) {
			// Setup names only what the step is for; /gpu names everything.
			continue
		}
		out = append(out, m.nameOf(id))
		if len(out) == 2 {
			break
		}
	}
	return out
}

// unlockCounts reports whether id is a model the GPU step is offered for.
func (m Model) unlockCounts(id string) bool {
	for _, g := range m.gpu.Models {
		if sameID(g.ID, id) {
			return m.gpuHelps(g)
		}
	}
	return m.sel.wants(id)
}

// splitBar is CPU and GPU shares of the memory, with the target marked.
func (m Model) splitBar(width int) string {
	g := m.gpu
	f, t := bar(g.GPUGB/g.TotalGB, width, m.opts.Motion.ASCII)
	at := int(m.targetPool() / g.TotalGB * float64(width))
	cells := []rune(f + t)
	if at >= 0 && at < len(cells) {
		mark := '|'
		if !m.opts.Motion.ASCII {
			mark = '┃'
		}
		cells[at] = mark
	}
	filledN := len([]rune(f))
	var b strings.Builder
	b.WriteString("[")
	for i, c := range cells {
		switch {
		case i == at:
			b.WriteString(accentStyle.Render(string(c)))
		case i < filledN:
			b.WriteString(infoStyle.Render(string(c)))
		default:
			b.WriteString(trackStyle.Render(string(c)))
		}
	}
	b.WriteString("]")
	return b.String()
}

func (m Model) guideView(w int) (body, foot []string) {
	width := w - 2*indentW
	if m.gpu == nil {
		return nil, m.footer(w, [][2]string{{"esc", "back"}})
	}
	if m.busy != "" && len(m.gpu.GuidedSteps) == 0 {
		body = append(body, pad(m.busyMarker()+" "+dimStyle.Render(m.busyText())))
	} else if m.busy == "" && m.gpu.GuidedValue == nil && len(m.gpu.GuidedSteps) == 0 {
		body = append(body, indentAll(wrapStyled(dimStyle, gpuNothingToGive, width), indentW)...)
	}
	for i, step := range m.gpu.GuidedSteps {
		prefix := fmt.Sprintf("%d. ", i+1)
		lines := wrap(step, width-len(prefix))
		for j, l := range lines {
			if j == 0 {
				body = append(body, pad(textStyle.Render(prefix+l)))
			} else {
				body = append(body, pad(strings.Repeat(" ", len(prefix))+textStyle.Render(l)))
			}
		}
	}
	if v := m.copyValue(); v != "" {
		body = append(body, "", pad(dimStyle.Render("Value: ")+accentStyle.Render(v)+dimStyle.Render("   (c copies it)")))
	}
	if m.busy != "" && len(m.gpu.GuidedSteps) > 0 {
		body = append(body, pad(m.busyMarker()+" "+dimStyle.Render(m.busyText())))
	} else if m.gpuNote != "" {
		st := dimStyle
		if m.gpuDone {
			st = okStyle
		}
		body = append(body, indentAll(wrapStyled(st, m.gpuNote, width), indentW)...)
	}
	var keys [][2]string
	if m.copyValue() != "" {
		keys = append(keys, [2]string{"c", "copy value"})
	}
	if m.gpuDone {
		keys = append(keys, [2]string{"enter", "continue"})
	} else {
		keys = append(keys, [2]string{"enter", gpuIveDone}, [2]string{"s", "skip"})
	}
	return body, m.footer(w, append(keys, [2]string{"esc", "back"}))
}

// --- download -----------------------------------------------------------------

func (m Model) downloadView(w int) (body, foot []string) {
	width := w - 2*indentW
	now := m.opts.Now()
	rows := m.shownRows()
	if m.status != nil && len(m.status.Models) == 0 && m.status.State != setupapi.RunIdle && !m.dl.active {
		body = append(body, pad(dimStyle.Render(noPerModel)))
	}
	for i, r := range rows {
		body = append(body, m.progressLine(i, r, w, now))
		if i == m.focus {
			body = append(body, m.progressNote(r, width)...)
		} else if s := m.stallLine(r, now); s != "" {
			// A stall is said under its row wherever the cursor is.
			body = append(body, strings.Repeat(" ", 8)+warnStyle.Render(s))
		}
	}
	if len(rows) > 0 && m.lost == nil {
		body = append(body, m.overallLine(w, now))
	}
	if m.lost != nil {
		body = append(body, indentAll(wrapStyled(failStyle, "[!] "+lostContact, width), indentW)...)
	}
	if note := m.runStoppedNote(); note != "" {
		body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+note, width), indentW)...)
	}
	if m.dl.cancelled && !m.dl.active {
		body = append(body, pad(dimStyle.Render(setupCancelled)))
	}
	if m.busy != "" {
		body = append(body, pad(m.busyMarker()+" "+dimStyle.Render(m.busyText())))
	} else if m.note != "" {
		body = append(body, indentAll(wrapStyled(warnStyle, m.note, width), indentW)...)
	}
	body = append(body, pad(m.keepTryLine()))
	if m.dl.active {
		leaving := leavingCancels
		if m.opts.Mode != ModeFirstRun {
			leaving = leavingKeeps
		}
		body = append(body, pad(dimStyle.Render(truncate(leaving, width))))
	}

	// Least important first: a narrow window drops hints from the front.
	keys := [][2]string{{"t", "keep trying"}, {"d", "details"}}
	switch {
	case m.lost != nil:
		keys = append(keys, [2]string{"r", "reconnect"})
	default:
		if !m.dl.active && m.anyUnfinished(rows) {
			keys = append(keys, [2]string{"R", "resume all"})
		}
		if m.focusRetryable(rows) && !m.dl.run && rows[m.focus].State == setupapi.StateFailed {
			keys = append(keys, [2]string{"s", "skip this model"})
		}
		if m.focusRetryable(rows) || m.stuck(rows) {
			// One verb with the row's own line: a stopped download resumes.
			verb := "retry"
			if m.focus < len(rows) && rows[m.focus].State == setupapi.StateCancelled {
				verb = "resume"
			}
			keys = append(keys, [2]string{"r", verb})
		}
		if m.dl.active {
			keys = append(keys, [2]string{"c", "cancel"})
		} else if m.allReady() {
			keys = append(keys, [2]string{"enter", "continue"})
		}
	}
	keys = append(keys, [2]string{"esc", "back"})
	return body, m.footer(w, keys)
}

// stallLine says a download has had no data for a while, "" while it moves.
func (m Model) stallLine(r setupapi.ModelProgress, now time.Time) string {
	rt := m.rates[r.ID]
	if rt == nil || r.State != setupapi.StateDownloading || r.RetryingUntil != nil {
		return ""
	}
	switch stall := rt.stalled(now); {
	case stall >= stallStuck:
		if m.dl.run {
			return "The download seems stuck. Press c to stop it, then R to resume."
		}
		return stalledLong
	case stall >= stallNotice:
		return stalledShort
	}
	return ""
}

func (m Model) stuck(rows []setupapi.ModelProgress) bool {
	if m.focus >= len(rows) || m.dl.run {
		return false
	}
	r := m.rates[rows[m.focus].ID]
	return r != nil && rows[m.focus].State == setupapi.StateDownloading && r.stalled(m.opts.Now()) >= stallStuck
}

func (m Model) anyUnfinished(rows []setupapi.ModelProgress) bool {
	for _, r := range rows {
		switch r.State {
		case setupapi.StateFailed, setupapi.StateCancelled, setupapi.StateQueued, setupapi.StateUnchecked:
			return true
		}
	}
	return false
}

// progressLine is one model's row: marker, name, bar and numbers, each state
// named in words.
func (m Model) progressLine(i int, r setupapi.ModelProgress, w int, now time.Time) string {
	cursor := "  "
	if i == m.focus {
		cursor = focusStyle.Render(">") + " "
	}
	name := m.nameOf(r.ID)
	nw := 20
	if w < 60 {
		nw = 14
	}
	nameCol := fmt.Sprintf("%-*s", nw, truncate(name, nw))
	if m.lost != nil {
		return pad(cursor + warnStyle.Render("[?] ") + " " + dimStyle.Render(nameCol) + " " + dimStyle.Render("last known: "+r.State))
	}
	marker, st := "[ ]", dimStyle
	text := ""
	switch m.shownState(r) {
	case setupapi.StateQueued:
		text = dimStyle.Render("queued")
		if done := m.inPlanBytes(r, m.doneBytes(r)); done > 0 {
			text += dimStyle.Render(", " + formatGB(bytesGB(done)) + " already here")
		}
	case setupapi.StateDownloading:
		marker, st = "[..]", infoStyle
		text = m.downloadingText(r, w-indentW-2-5-2-nw-1, now)
		if m.dl.cancelled {
			text = dimStyle.Render(stoppingLine)
		}
	case setupapi.StateVerifying:
		marker, st = "[..]", accentStyle
		if m.othersDownloading() {
			text = accentStyle.Render(verifyWaiting)
			break
		}
		barW := 10
		text = accentStyle.Render(indeterminate(m.frame, barW, m.opts.Motion.ASCII, m.opts.Motion.Reduced)) + " " + accentStyle.Render(verifyingLine)
		if at, ok := m.verifyAt[r.ID]; ok {
			if el := now.Sub(at); el > 10*time.Second {
				text += dimStyle.Render(" " + formatElapsed(el))
			}
		}
	case setupapi.StateReady:
		marker, st = "[ok]", okStyle
		ns := dimStyle
		if at, ok := m.readyAt[r.ID]; ok && now.Sub(at) < 100*time.Millisecond && !m.opts.Motion.Reduced {
			ns = flashStyle
		}
		return pad(cursor + st.Render(marker) + "  " + ns.Render(nameCol) + " " + ns.Render("ready"))
	case setupapi.StateFailed:
		marker, st = "[!]", failStyle
		text = failStyle.Render("failed")
	case setupapi.StateCancelled:
		text = dimStyle.Render("cancelled")
		if done := m.inPlanBytes(r, m.doneBytes(r)); done > 0 {
			text += dimStyle.Render(", " + formatGB(bytesGB(done)) + " kept")
		}
	case setupapi.StateUnchecked:
		// Lined up with ready rows: a finished download, just not checked.
		return pad(cursor + okStyle.Render("[ok]") + "  " + textStyle.Render(nameCol) + " " + dimStyle.Render("downloaded · not checked yet"))
	}
	spin := "  "
	if (r.State == setupapi.StateDownloading || r.State == setupapi.StateVerifying) && !m.opts.Motion.Reduced {
		spin = st.Render(m.spinFrame()) + " "
	}
	return pad(cursor + st.Render(fmt.Sprintf("%-4s", marker)) + " " + spin + textStyle.Render(nameCol) + " " + text)
}

func (m Model) downloadingText(r setupapi.ModelProgress, room int, now time.Time) string {
	if r.RetryingUntil != nil {
		left := int(time.Until(time.Unix(*r.RetryingUntil, 0)).Seconds())
		if left < 0 {
			left = 0
		}
		return warnStyle.Render(fmt.Sprintf("connection dropped · retrying, %s left", formatElapsed(time.Duration(left)*time.Second)))
	}
	if r.BytesTotal <= 0 {
		// A pull that has not reported its size yet.
		return infoStyle.Render(startingLine)
	}
	rt := m.rates[r.ID]
	nums := fmt.Sprintf("%.1f/%s", bytesGB(m.inPlanBytes(r, r.BytesDone)), formatGB(bytesGB(m.sizeOf(r))))
	tail := ""
	if rt != nil {
		switch stall := rt.stalled(now); {
		case stall >= stallStuck:
			tail = "stuck"
		case stall >= stallNotice:
			tail = "no data"
		default:
			if rt.speed > 0 {
				tail = formatSpeed(rt.speed)
			}
			if rt.showETA(now) {
				tail += "  " + formatETA(rt.eta)
			}
		}
	}
	barW := room - lipgloss.Width(nums) - lipgloss.Width(tail) - 4
	if barW > 40 {
		barW = 40
	}
	out := ""
	if barW >= 6 {
		f, t := bar(m.fill(r.ID, r.BytesDone, r.BytesTotal), barW, m.opts.Motion.ASCII)
		out = infoStyle.Render(f) + trackStyle.Render(t) + "  "
	}
	out += warnStyle.Render(nums)
	if tail != "" && lipgloss.Width(out)+2+lipgloss.Width(tail) <= room {
		st := dimStyle
		if tail == "stuck" || tail == "no data" {
			st = warnStyle
		}
		out += "  " + st.Render(tail)
	}
	return out
}

// fill is the bar's shown fraction: eased toward the real one in full motion.
func (m Model) fill(id string, done, total int64) float64 {
	if v, ok := m.shown[id]; ok && !m.opts.Motion.Reduced {
		return v
	}
	return frac(done, total)
}

// progressNote is the focused row's detail: why it failed, what to do.
func (m Model) progressNote(r setupapi.ModelProgress, width int) []string {
	indent := 8
	var lines []string
	add := func(s lipgloss.Style, text string) {
		for _, l := range wrap(text, width-indent+indentW) {
			lines = append(lines, strings.Repeat(" ", indent)+s.Render(l))
		}
	}
	switch m.shownState(r) {
	case setupapi.StateFailed:
		kind := ""
		if r.ErrorKind != nil {
			kind = *r.ErrorKind
		}
		got := m.inPlanBytes(r, r.BytesDone)
		line := failureLine(kind, m.nameOf(r.ID), m.drive(), bytesGB(got), bytesGB(m.sizeOf(r)-got))
		if kind == setupapi.ErrVerify && r.Error != nil && *r.Error != "" {
			// The load check says which of its cases this is (would not load,
			// or setup stopped before checking), in plain words.
			line = *r.Error + " Press r to try again."
		}
		add(failStyle, "[!] "+line)
		if m.dl.run {
			add(dimStyle, runOwnsRetry)
		}
	case setupapi.StateDownloading:
		if s := m.stallLine(r, m.opts.Now()); s != "" {
			add(warnStyle, s)
		}
		if r.RetryingUntil != nil {
			left := int(time.Until(time.Unix(*r.RetryingUntil, 0)).Seconds())
			if left < 0 {
				left = 0
			}
			add(warnStyle, retryingLine(left))
		}
		if r.DownloadedBeforeGB != nil && *r.DownloadedBeforeGB > 0 {
			add(dimStyle, "Resumed: "+formatGB(*r.DownloadedBeforeGB)+" was already on this PC.")
		}
	case setupapi.StateCancelled:
		add(dimStyle, "Press r to resume from where it stopped.")
	case setupapi.StateUnchecked:
		if !m.dl.active {
			// While setup runs its check, the note above says so.
			add(dimStyle, uncheckedLine)
		}
	}
	return lines
}

// runStoppedNote says why a run stopped when no model's row does: only when
// no download failed (model_failed), in plain words, the raw error behind d.
func (m Model) runStoppedNote() string {
	if m.status == nil || m.status.State != setupapi.RunFailed || m.status.ModelFailed || m.dl.active || m.failedCount() > 0 {
		return ""
	}
	for _, r := range m.shownRows() {
		if r.State == setupapi.StateUnchecked {
			return stoppedBeforeCheck
		}
	}
	return setupStoppedNote
}

func (m Model) overallLine(w int, now time.Time) string {
	done, total := m.overallBytes()
	pct := 0
	if total > 0 {
		pct = int(done * 100 / total)
	}
	parts := []string{fmt.Sprintf("%d%%", pct), fmt.Sprintf("%.1f/%s", bytesGB(done), formatGB(bytesGB(total)))}
	switch {
	case !m.dl.active, total > 0 && done >= total:
		// Nothing left to download: no estimate to make.
	case m.total.showETA(now):
		parts = append(parts, formatETA(m.total.eta)+" left")
	default:
		parts = append(parts, estimating)
	}
	if m.opts.Motion.Reduced && m.dl.active {
		parts = append(parts, formatElapsed(now.Sub(m.since)))
	}
	if n := m.failedCount(); n > 0 {
		parts = append(parts, failStyle.Render(fmt.Sprintf("%d failed", n)))
	}
	label := "Overall "
	tail := strings.Join(parts, " · ")
	barW := w - 2*indentW - len(label) - lipgloss.Width(tail) - 2
	if barW > 50 {
		barW = 50
	}
	line := textStyle.Render(label)
	if barW >= 6 {
		f, t := bar(m.fill("", done, total), barW, m.opts.Motion.ASCII)
		line += infoStyle.Render(f) + trackStyle.Render(t) + " "
	}
	return pad(line + warnStyle.Render(tail))
}

// --- ready ------------------------------------------------------------------

func (m Model) readyView(w int) (body, foot []string) {
	width := w - 2*indentW
	if m.plan != nil {
		var chat, vision, docs string
		var missing []string
		for _, r := range m.sel.rows {
			on := r.downloaded || m.landed(r.id)
			switch {
			case sameID(r.id, m.sel.chat) && r.chatable:
				where := "on this PC"
				if r.npu {
					where = "on this PC, NPU"
				}
				chat = readyChat(r.name, where)
			}
			if r.vision && on && (m.sel.ticked[r.id] || r.installed) {
				vision = r.name
			}
			if r.embedding && on && m.sel.ticked[r.id] {
				docs = r.name
			}
			// The vision and documents models get their own "off" line.
			if r.planned && !m.sel.ticked[r.id] && !r.downloaded && !sameID(r.id, m.sel.chat) &&
				!r.vision && !r.embedding {
				missing = append(missing, r.name)
			}
		}
		if m.sel.cloud {
			body = append(body, pad(textStyle.Render("Chat       runs in the cloud")))
		} else if chat != "" {
			body = append(body, pad(textStyle.Render(chat)))
		}
		if vision != "" {
			body = append(body, pad(textStyle.Render(readyVision(vision))))
		} else if v, ok := m.visionRow(); ok {
			body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+readyVisionOff(v.name), width), indentW)...)
		}
		if docs != "" {
			body = append(body, pad(textStyle.Render(readyDocs(docs))))
		} else if d, ok := m.docsRow(); ok {
			body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+readyDocsOff(d.name), width), indentW)...)
		}
		if len(missing) > 0 {
			body = append(body, indentAll(wrapStyled(dimStyle, readyNotInstalled(missing), width), indentW)...)
		}
	}
	if g := m.gpu; g != nil && m.gpuPhase == gpuApplied {
		body = append(body, indentAll(wrapStyled(okStyle, readyGPU(m.targetPool(), m.gpuWas), width), indentW)...)
	}
	if g := m.gpu; g != nil && (m.gpuPhase == gpuPendingSaved || m.gpuPhase == gpuApplied) {
		body = append(body, indentAll(wrapStyled(warnStyle, "[!] "+gpuPendingLine(formatGBShort(m.targetPool())), width), indentW)...)
	}
	if m.runErr != "" {
		body = append(body, "")
		body = append(body, indentAll(wrapStyled(warnStyle, "[!] Setup stopped after the downloads: "+m.runErr+
			" The models are on this PC; GAIA checks that they load next. Press d for details.", width), indentW)...)
	}
	if len(m.dl.ids) == 0 && (m.status == nil || len(m.status.Models) == 0) && m.nothingLeftOut() {
		body = append(body, "", pad(dimStyle.Render(everythingHere)))
	}
	keys := [][2]string{{"m", "change models"}}
	if m.opts.Mode == ModeFirstRun {
		keys = append(keys, [2]string{"enter", "start chatting"})
	}
	return body, m.footer(w, append(keys, [2]string{"esc", "back"}))
}

// landed reports whether id finished downloading in this session.
func (m Model) landed(id string) bool {
	r, ok := m.progressFor(id)
	// shownState: a model the run downloaded but had no check for (Gemma,
	// once a chat model is saved) is on this PC too.
	return ok && m.shownState(r) == setupapi.StateReady
}

func (m Model) docsRow() (rowEntry, bool) {
	for _, r := range m.sel.rows {
		if r.embedding {
			return r, true
		}
	}
	return rowEntry{}, false
}

// nothingLeftOut reports whether every model setup offers is on this PC.
func (m Model) nothingLeftOut() bool {
	for _, r := range m.sel.rows {
		if !r.installed && (r.planned || r.vision || r.embedding) && !r.downloaded && !m.landed(r.id) {
			return false
		}
	}
	return true
}

func (m Model) visionRow() (rowEntry, bool) {
	for _, r := range m.sel.rows {
		if r.vision {
			return r, true
		}
	}
	return rowEntry{}, false
}

// --- details ------------------------------------------------------------------

func (m Model) detailLines(w int) []string {
	width := w - 2*indentW
	var raw []string
	if p := m.plan; p != nil {
		if p.Lemonade.URL != nil {
			raw = append(raw, "Lemonade: "+*p.Lemonade.URL+" ("+p.Lemonade.Mode+")")
		}
		if p.CapacityGB != nil {
			src := ""
			if p.MemorySource != nil {
				src = " (" + *p.MemorySource + ")"
			}
			raw = append(raw, fmt.Sprintf("Memory for models: %s%s", formatGBShort(*p.CapacityGB), src))
		} else {
			raw = append(raw, "Memory for models: unknown")
		}
		if p.Disk.Path != nil {
			free := "unknown"
			if p.Disk.FreeGB != nil {
				free = formatGB(*p.Disk.FreeGB)
			}
			raw = append(raw, "Model drive: "+*p.Disk.Path+", free "+free)
		}
	}
	if m.scr == scrModels {
		if r, ok := m.focusedRow(); ok {
			for _, e := range m.plan.Catalog {
				if sameID(e.ID, r.id) && e.FitReason != nil {
					raw = append(raw, r.id+": "+*e.FitReason)
				}
			}
		}
	}
	if m.status != nil {
		if m.status.Text != "" {
			raw = append(raw, "Setup: "+m.status.Text)
		}
		for _, r := range m.status.Models {
			if r.Error != nil {
				raw = append(raw, r.ID+": "+*r.Error)
			}
		}
		if m.status.Error != nil {
			raw = append(raw, "Error: "+*m.status.Error)
		}
		raw = append(raw, m.status.LogTail...)
	}
	if m.gpuErr != nil {
		raw = append(raw, "GPU memory: "+m.gpuErr.Error())
	}
	if m.lost != nil {
		raw = append(raw, "Lost contact with GAIA:")
		raw = append(raw, strings.Split(m.lost.Error(), "\n")...)
	}
	if len(raw) == 0 {
		raw = []string{noDetails}
	}
	var out []string
	for _, l := range raw {
		l, ok := cleanLogLine(l)
		if !ok {
			continue
		}
		out = append(out, indentAll(wrapStyled(dimStyle, l, width), indentW)...)
	}
	// The newest lines matter most: keep the tail when it is long.
	if budget := m.height - 5; budget > 0 && len(out) > budget {
		out = out[len(out)-budget:]
	}
	return out
}

// boxRunes are the frame characters `gaia init` draws its panels with. In the
// details they arrive cut to the log's width, as rules and corners on their
// own, so they are dropped and the words kept.
const boxRunes = "─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬╭╮╯╰━┃┏┓┗┛"

// cleanLogLine is one line of setup output as plain words: no colour codes, no
// panel frame, no tabs. ok is false for a line with nothing left to read.
func cleanLogLine(l string) (string, bool) {
	l = ansi.Strip(l)
	l = strings.ReplaceAll(l, "\t", "    ")
	l = strings.Trim(l, boxRunes+" ")
	return l, strings.TrimSpace(l) != ""
}

// --- shared bits --------------------------------------------------------------

// busyMarker is a spinner, or a static "[..]" without motion.
func (m Model) busyMarker() string {
	if m.opts.Motion.Reduced {
		return keyStyle.Render("[..]")
	}
	return keyStyle.Render(m.spinFrame())
}

func (m Model) spinFrame() string {
	frames := spinner.Dot.Frames
	return strings.TrimSpace(frames[m.frame%len(frames)])
}

// busyText adds the elapsed time once a wait passes 3 s, so no spinner runs
// long without saying for how long.
func (m Model) busyText() string {
	el := m.opts.Now().Sub(m.busySince)
	if el >= 3*time.Second {
		return m.busy + " " + formatElapsed(el)
	}
	return m.busy
}

func pad(s string) string { return strings.Repeat(" ", indentW) + s }

func indentAll(lines []string, n int) []string {
	prefix := strings.Repeat(" ", n)
	out := make([]string, 0, len(lines))
	for _, l := range lines {
		out = append(out, prefix+l)
	}
	return out
}

func wrapStyled(s lipgloss.Style, text string, w int) []string {
	lines := wrap(text, w)
	for i, l := range lines {
		lines[i] = s.Render(l)
	}
	return lines
}

// truncate cuts a plain string to w columns.
func truncate(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= w {
		return s
	}
	runes := []rune(s)
	for len(runes) > 0 && lipgloss.Width(string(runes))+1 > w {
		runes = runes[:len(runes)-1]
	}
	return string(runes) + "…"
}

// wrap breaks text on word boundaries into lines of at most w columns.
func wrap(s string, w int) []string {
	if w < 8 {
		w = 8
	}
	var out []string
	for _, para := range strings.Split(s, "\n") {
		line := ""
		for _, word := range strings.Fields(para) {
			for lipgloss.Width(word) > w {
				if line != "" {
					out = append(out, line)
					line = ""
				}
				runes := []rune(word)
				cut := len(runes)
				for cut > 0 && lipgloss.Width(string(runes[:cut])) > w {
					cut--
				}
				out = append(out, string(runes[:cut]))
				word = string(runes[cut:])
			}
			switch {
			case line == "":
				line = word
			case lipgloss.Width(line)+1+lipgloss.Width(word) <= w:
				line += " " + word
			default:
				out = append(out, line)
				line = word
			}
		}
		if line != "" {
			out = append(out, line)
		}
	}
	if len(out) == 0 {
		return []string{""}
	}
	return out
}

// clip is the last pass of every frame: no line wider than the terminal and no
// frame taller, so a small window never wraps or scrolls and corrupts the
// session. Height comes out of the body; the footer, with esc, stays.
func clip(lines []string, cols, rows int) string {
	if cols > 0 {
		for i, l := range lines {
			if ansi.StringWidth(l) > cols {
				lines[i] = ansi.Truncate(l, cols, "…")
			}
		}
	}
	if rows > 0 && len(lines) > rows {
		if rows < 3 {
			lines = lines[len(lines)-rows:]
		} else {
			lines = append(append([]string{}, lines[:rows-2]...), lines[len(lines)-2:]...)
		}
	}
	return strings.Join(lines, "\n")
}
