// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package hybrid is the /hybrid screen: the models hybrid routes between, its
// policy and custom rules, a preview of how a request would be routed, and how
// past turns were.
//
// Settings are read from and saved to ~/.gaia/config.json with the checks
// GaiaConfig applies, the way every other TUI setting is saved. The preview and
// the stats need the real classifier and the spending record, which only the
// Agent UI server has, so those two go to its /api/hybrid routes.
package hybrid

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// ClosedMsg asks the host to close the screen.
type ClosedMsg struct{}

type settingsMsg struct {
	settings lemonade.HybridSettings
	err      error
}

type modelsMsg struct {
	ids []string
	err error
}

type statsMsg struct {
	stats Stats
	err   error
}

type previewMsg struct {
	seq   int
	route event.CanonicalRoute
	err   error
}

// listGatewayModels lists the AMD gateway's chat models from Lemonade. A var so
// tests reach no server.
var listGatewayModels = func(ctx context.Context, base string) ([]string, error) {
	models, err := lemonade.New(base).Models(ctx, "amd")
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(models))
	for _, m := range models {
		ids = append(ids, m.ID)
	}
	return ids, nil
}

// Settings I/O, vars so tests touch no real config.
var (
	readSettings = lemonade.ReadHybridSettings
	saveSettings = lemonade.SaveHybridSettings
)

const loadTimeout = 30 * time.Second

// Rows, top to bottom. The rules table sits between policy and the preview.
const (
	rowCheap = iota
	rowStrong
	rowClassifier
	rowTimeout
	rowPolicy
	rowRules
)

func rowTry() int { return rowRules + len(lemonade.HybridKinds) }

// Model is the /hybrid screen.
type Model struct {
	ctx    context.Context
	cancel context.CancelFunc
	base   string
	api    api

	loaded  bool
	saved   lemonade.HybridSettings
	edit    lemonade.HybridSettings
	loadErr error

	models    []string
	modelsErr error

	stats    string
	statsErr error

	focus   int
	timeout textinput.Model
	try     textinput.Model

	// picking is the model row whose list is open, or -1.
	picking      int
	pickSearch   string
	pickCursor   int
	previewSeq   int
	previewBusy  bool
	preview      *event.CanonicalRoute
	previewErr   error
	note, errMsg string
	// discardArmed is set by esc with unsaved changes; a second esc discards.
	discardArmed  bool
	width, height int
}

// New builds the screen for the Lemonade at base, which lists gateway models.
func New(base string, width, height int) Model {
	ctx, cancel := context.WithCancel(context.Background())
	timeout := textinput.New()
	timeout.Prompt = ""
	timeout.CharLimit = 3
	timeout.Width = 5
	try := textinput.New()
	try.Prompt = "› "
	try.Placeholder = "Type a request to see where it would run"
	try.CharLimit = 4000
	return Model{ctx: ctx, cancel: cancel, base: base, api: newAPI(), timeout: timeout, try: try,
		picking: -1, width: width, height: height}
}

// Init reads the settings, the gateway's models and the stats together.
func (m Model) Init() tea.Cmd {
	ctx, base, a := m.ctx, m.base, m.api
	return tea.Batch(
		func() tea.Msg {
			s, err := readSettings()
			return settingsMsg{settings: s, err: err}
		},
		func() tea.Msg {
			c, cancel := context.WithTimeout(ctx, loadTimeout)
			defer cancel()
			ids, err := listGatewayModels(c, base)
			return modelsMsg{ids: ids, err: err}
		},
		func() tea.Msg {
			c, cancel := context.WithTimeout(ctx, loadTimeout)
			defer cancel()
			s, err := a.Stats(c)
			return statsMsg{stats: s, err: err}
		},
	)
}

// Close stops anything still in flight.
func (m Model) Close() { m.cancel() }

func (m Model) dirty() bool {
	return m.loaded && !sameSettings(m.current(), m.saved)
}

// current is the edit with the timeout field as typed; an unparsable entry is
// 0, which Validate refuses with the range.
func (m Model) current() lemonade.HybridSettings {
	s := m.edit
	n, err := strconv.Atoi(strings.TrimSpace(m.timeout.Value()))
	if err != nil {
		n = 0
	}
	s.Timeout = n
	return s
}

func sameSettings(a, b lemonade.HybridSettings) bool {
	if a.Cheap != b.Cheap || a.Strong != b.Strong || a.Classifier != b.Classifier ||
		a.Timeout != b.Timeout || a.Policy != b.Policy {
		return false
	}
	if a.Policy != lemonade.HybridPolicyCustom {
		return true
	}
	if len(a.Rules) != len(b.Rules) {
		return false
	}
	for k, v := range a.Rules {
		if b.Rules[k] != v {
			return false
		}
	}
	return true
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = v.Width, v.Height
		return m, nil
	case settingsMsg:
		m.loaded = true
		if v.err != nil {
			// Shown, and the screen opens on the defaults so the bad value can be
			// replaced; saving writes every hybrid key.
			m.loadErr = v.err
			v.settings = lemonade.DefaultHybridSettings()
		}
		m.saved = v.settings
		m.edit = v.settings
		if m.edit.Rules != nil {
			m.edit.Rules = m.edit.Rules.Clone()
		}
		m.timeout.SetValue(strconv.Itoa(v.settings.Timeout))
		if m.loadErr != nil {
			// Not saved as loaded: the file still holds the bad value.
			m.saved = lemonade.HybridSettings{}
		}
		return m, nil
	case modelsMsg:
		m.models, m.modelsErr = v.ids, v.err
		return m, nil
	case statsMsg:
		if v.err != nil {
			m.statsErr = v.err
		} else {
			m.stats = v.stats.Line()
		}
		return m, nil
	case previewMsg:
		if v.seq != m.previewSeq {
			return m, nil
		}
		m.previewBusy = false
		if v.err != nil {
			m.previewErr, m.preview = v.err, nil
		} else {
			r := v.route
			m.preview, m.previewErr = &r, nil
		}
		return m, nil
	case tea.KeyMsg:
		return m.handleKey(v)
	}
	return m.forward(msg)
}

func (m Model) forward(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	switch m.focus {
	case rowTimeout:
		m.timeout, cmd = m.timeout.Update(msg)
	case rowTry():
		m.try, cmd = m.try.Update(msg)
	}
	return m, cmd
}

func (m Model) handleKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	if k.String() == "ctrl+c" {
		m.cancel()
		return m, tea.Quit
	}
	if m.picking >= 0 {
		return m.handlePickKey(k)
	}
	if k.String() != "esc" {
		m.discardArmed = false
	}
	switch k.String() {
	case "esc":
		if m.dirty() && !m.discardArmed {
			m.discardArmed = true
			m.note = "You have unsaved changes. ctrl+s saves them; esc again leaves without saving."
			return m, nil
		}
		m.cancel()
		return m, func() tea.Msg { return ClosedMsg{} }
	case "ctrl+s":
		return m.save(), nil
	case "up", "shift+tab":
		return m.move(-1)
	case "down", "tab":
		return m.move(1)
	}
	if !m.loaded {
		return m, nil
	}
	switch {
	case m.focus <= rowClassifier:
		if k.String() == "enter" {
			m.picking, m.pickSearch, m.pickCursor = m.focus, "", 0
			m.errMsg = ""
		}
		return m, nil
	case m.focus == rowTimeout:
		if k.Type == tea.KeyRunes && !isDigits(string(k.Runes)) {
			return m, nil
		}
		return m.forward(k)
	case m.focus == rowPolicy:
		switch k.String() {
		case "left", "h":
			m.setPolicy(-1)
		case "right", "l", " ", "enter":
			m.setPolicy(1)
		case "q":
			m.startFrom(lemonade.HybridPolicyQuality)
		case "b":
			m.startFrom(lemonade.HybridPolicyBalanced)
		}
		return m, nil
	case m.focus < rowTry():
		return m.handleRuleKey(k), nil
	default:
		if k.String() == "enter" {
			return m.runPreview()
		}
		return m.forward(k)
	}
}

func isDigits(s string) bool {
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return s != ""
}

func (m Model) move(delta int) (tea.Model, tea.Cmd) {
	last := rowTry()
	m.focus = (m.focus + delta + last + 1) % (last + 1)
	m.timeout.Blur()
	m.try.Blur()
	switch m.focus {
	case rowTimeout:
		return m, m.timeout.Focus()
	case rowTry():
		return m, m.try.Focus()
	}
	return m, nil
}

// setPolicy steps through Quality, Balanced and Custom. Custom with no table
// yet starts from the preset it was reached from.
func (m *Model) setPolicy(delta int) {
	policies := lemonade.HybridPolicies
	i := 0
	for j, p := range policies {
		if p == m.edit.Policy {
			i = j
		}
	}
	from := m.edit.Policy
	m.edit.Policy = policies[(i+delta+len(policies))%len(policies)]
	if m.edit.Policy == lemonade.HybridPolicyCustom && m.edit.Rules == nil {
		m.edit.Rules = lemonade.HybridPreset(from).Clone()
		if m.edit.Rules == nil {
			m.edit.Rules = lemonade.HybridPreset(lemonade.HybridPolicyQuality).Clone()
		}
	}
	m.note, m.errMsg = "", ""
}

// startFrom replaces the custom table with a preset's, turning Custom on.
func (m *Model) startFrom(preset string) {
	m.edit.Policy = lemonade.HybridPolicyCustom
	m.edit.Rules = lemonade.HybridPreset(preset).Clone()
	m.note = "Custom rules now match " + titleCase(preset) + ". Edit them, then ctrl+s to save."
	m.errMsg = ""
}

// handleRuleKey edits the focused kind's rule. Editing a preset's table turns
// Custom on, starting from that preset, and says so.
func (m Model) handleRuleKey(k tea.KeyMsg) Model {
	kind := lemonade.HybridKinds[m.focus-rowRules]
	switch k.String() {
	case "q":
		m.startFrom(lemonade.HybridPolicyQuality)
		return m
	case "b":
		m.startFrom(lemonade.HybridPolicyBalanced)
		return m
	case "left", "h", "right", "l", " ", "enter":
	default:
		return m
	}
	if m.edit.Policy != lemonade.HybridPolicyCustom {
		from := m.edit.Policy
		m.edit.Policy = lemonade.HybridPolicyCustom
		m.edit.Rules = lemonade.HybridPreset(from).Clone()
		m.note = "Switched to Custom, starting from " + titleCase(from) + "."
	} else {
		m.note = ""
	}
	m.errMsg = ""
	rule := m.edit.Rules[kind]
	switch k.String() {
	case "left", "h":
		rule.CheapMaxDifficulty = max(0, rule.CheapMaxDifficulty-1)
	case "right", "l":
		rule.CheapMaxDifficulty = min(lemonade.MaxHybridDifficulty, rule.CheapMaxDifficulty+1)
	default:
		rule.AllowHighStakes = !rule.AllowHighStakes
	}
	m.edit.Rules[kind] = rule
	return m
}

func (m Model) save() Model {
	m.note, m.errMsg = "", ""
	if !m.loaded {
		return m
	}
	if strings.TrimSpace(m.timeout.Value()) == "" {
		m.errMsg = fmt.Sprintf("Not saved: enter the classifier limit in seconds (1-%d; %d is the default).",
			lemonade.MaxHybridTimeout, lemonade.DefaultHybridTimeout)
		return m
	}
	s := m.current()
	if s.Policy != lemonade.HybridPolicyCustom {
		s.Rules = nil
	}
	if err := saveSettings(s); err != nil {
		m.errMsg = "Not saved: " + err.Error()
		return m
	}
	m.saved = s
	if s.Rules != nil {
		m.saved.Rules = s.Rules.Clone()
	}
	m.loadErr = nil
	m.note = "Saved to GAIA's config. A chat already on hybrid picks this up after /model hybrid."
	return m
}

func (m Model) runPreview() (tea.Model, tea.Cmd) {
	text := strings.TrimSpace(m.try.Value())
	if text == "" {
		m.previewErr = fmt.Errorf("type a request first")
		return m, nil
	}
	m.previewSeq++
	m.previewBusy = true
	m.preview, m.previewErr = nil, nil
	seq, ctx, a := m.previewSeq, m.ctx, m.api
	return m, func() tea.Msg {
		r, err := a.Preview(ctx, text)
		return previewMsg{seq: seq, route: r, err: err}
	}
}

// pickOptions are the picker's rows: the default first, then the gateway's
// models, then the current choice if the gateway does not list it.
func (m Model) pickOptions() []string {
	opts := []string{""}
	current := m.roleValue(m.picking)
	listed := current == ""
	for _, id := range m.models {
		opts = append(opts, id)
		if id == current {
			listed = true
		}
	}
	if !listed {
		opts = append(opts, current)
	}
	needle := strings.ToLower(m.pickSearch)
	if needle == "" {
		return opts
	}
	var out []string
	for _, id := range opts {
		if id != "" && strings.Contains(strings.ToLower(id), needle) {
			out = append(out, id)
		}
	}
	return out
}

func (m Model) roleValue(row int) string {
	switch row {
	case rowCheap:
		return m.edit.Cheap
	case rowStrong:
		return m.edit.Strong
	}
	return m.edit.Classifier
}

func (m *Model) setRole(row int, id string) {
	switch row {
	case rowCheap:
		m.edit.Cheap = id
	case rowStrong:
		m.edit.Strong = id
	default:
		m.edit.Classifier = id
	}
}

func (m Model) handlePickKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	opts := m.pickOptions()
	switch k.String() {
	case "esc":
		m.picking = -1
		return m, nil
	case "up":
		m.pickCursor = max(0, m.pickCursor-1)
	case "down", "tab":
		m.pickCursor = max(0, min(len(opts)-1, m.pickCursor+1))
	case "enter":
		if m.pickCursor < len(opts) {
			m.setRole(m.picking, opts[m.pickCursor])
			m.note, m.errMsg = "", ""
		}
		m.picking = -1
	case "backspace":
		if r := []rune(m.pickSearch); len(r) > 0 {
			m.pickSearch = string(r[:len(r)-1])
			m.pickCursor = 0
		}
	default:
		if k.Type == tea.KeyRunes {
			m.pickSearch += string(k.Runes)
			m.pickCursor = 0
		}
	}
	return m, nil
}

func titleCase(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

var (
	titleStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	dimStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	errStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
	okStyle     = lipgloss.NewStyle().Foreground(theme.Success)
	focusStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	chosenStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Highlight)
)

// modelLabel is a role's value as shown: the id, or what the default is.
func (m Model) modelLabel(row int) string {
	v := m.roleValue(row)
	if v != "" {
		return v
	}
	h := m.edit.Models()
	switch row {
	case rowCheap:
		return h.Cheap + " (default)"
	case rowStrong:
		return h.Strong + " (default)"
	}
	return "same as the cheap model"
}

func (m Model) View() string {
	w := max(20, m.width-4)
	lines := []string{titleStyle.Render("Hybrid routing")}
	if !m.loaded {
		lines = append(lines, "", "Reading the hybrid settings…")
		return m.frame(lines, "esc close", w)
	}
	if m.picking >= 0 {
		return m.pickView(w)
	}
	lines = append(lines, dimStyle.Render(
		"Each turn runs on the cheap or the strong model; the classifier decides. "+
			"Unclassified turns always go to the strong model."))
	switch {
	case m.statsErr != nil:
		lines = append(lines, dimStyle.Render(m.statsErr.Error()+"."))
	case m.stats != "":
		lines = append(lines, dimStyle.Render(m.stats))
	}
	if m.loadErr != nil {
		lines = append(lines, errStyle.Render("Your saved settings can't be used: "+m.loadErr.Error()+
			". Showing the defaults; ctrl+s replaces them."))
	}
	if m.modelsErr != nil {
		lines = append(lines, errStyle.Render("Gateway models couldn't be listed ("+
			strings.TrimSuffix(m.modelsErr.Error(), ".")+"), so only the current choices can be kept."))
	}
	lines = append(lines, "")

	row := func(i int, label, value string) string {
		marker := "  "
		text := fmt.Sprintf("%-18s %s", label, value)
		if i == m.focus {
			marker = "› "
			return focusStyle.Render(marker + text)
		}
		return marker + text
	}
	lines = append(lines,
		row(rowCheap, "Cheap model", m.modelLabel(rowCheap)),
		row(rowStrong, "Strong model", m.modelLabel(rowStrong)),
		row(rowClassifier, "Classifier", m.modelLabel(rowClassifier)))
	timeout := m.timeout.View()
	if m.focus != rowTimeout {
		timeout = m.timeout.Value()
	}
	lines = append(lines, row(rowTimeout, "Classifier limit", timeout+" seconds"))

	var policies []string
	for _, p := range lemonade.HybridPolicies {
		name := titleCase(p)
		if p == m.edit.Policy {
			name = chosenStyle.Render("[" + name + "]")
		}
		policies = append(policies, name)
	}
	lines = append(lines, row(rowPolicy, "Policy", strings.Join(policies, "  ")))

	lines = append(lines, "", dimStyle.Render(fmt.Sprintf("  %-18s %-16s %s", "Request kind", "Cheap up to", "High stakes")))
	rules := m.edit.EffectiveRules()
	for i, kind := range lemonade.HybridKinds {
		r := rules[kind]
		stakes := "strong only"
		if r.AllowHighStakes {
			stakes = "cheap allowed"
		}
		limit := "Never"
		if r.CheapMaxDifficulty > 0 {
			limit = "difficulty " + strconv.Itoa(r.CheapMaxDifficulty)
		}
		text := fmt.Sprintf("%-18s %-16s %s", lemonade.HybridKindLabel(kind), limit, stakes)
		switch {
		case rowRules+i == m.focus:
			lines = append(lines, focusStyle.Render("› "+text))
		case m.edit.Policy != lemonade.HybridPolicyCustom:
			lines = append(lines, dimStyle.Render("  "+text))
		default:
			lines = append(lines, "  "+text)
		}
	}

	lines = append(lines, "", row(rowTry(), "Try a request", ""))
	try := m.try
	try.Width = max(10, w-4)
	lines = append(lines, "  "+try.View())
	switch {
	case m.previewBusy:
		lines = append(lines, dimStyle.Render("  Asking the classifier…"))
	case m.previewErr != nil:
		lines = append(lines, errStyle.Render("  "+m.previewErr.Error()+"."))
	case m.preview != nil:
		lines = append(lines, "  "+okStyle.Render(PreviewLine(*m.preview)))
		if m.preview.Reason != "" {
			lines = append(lines, dimStyle.Render("  "+m.preview.Reason))
		}
		if m.dirty() {
			lines = append(lines, dimStyle.Render("  That used your saved settings; ctrl+s to try these."))
		}
	}

	if m.note != "" {
		lines = append(lines, "", okStyle.Render(m.note))
	}
	if m.errMsg != "" {
		lines = append(lines, "", errStyle.Render(m.errMsg))
	}
	return m.frame(lines, m.hint(), w)
}

// PreviewLine is a preview's route in the chat's words, with how long the
// classifier took.
func PreviewLine(r event.CanonicalRoute) string {
	line := RouteOf(r).Line()
	if r.Seconds > 0 {
		line += fmt.Sprintf(" · %.1fs", r.Seconds)
	}
	return line
}

// RouteOf is the decision a route record carries.
func RouteOf(r event.CanonicalRoute) lemonade.HybridRoute {
	d := 0
	if r.Difficulty != nil {
		d = *r.Difficulty
	}
	return lemonade.HybridRoute{Model: r.Model, Kind: r.Kind, Difficulty: d, Stakes: r.Stakes,
		Classified: r.Classified, Sticky: r.Sticky}
}

func (m Model) hint() string {
	switch {
	case m.focus <= rowClassifier:
		return "↑/↓ move · enter choose model · ctrl+s save · esc close"
	case m.focus == rowTimeout:
		return "type seconds (1-120) · ↑/↓ move · ctrl+s save · esc close"
	case m.focus == rowPolicy:
		return "←/→ policy · q/b start from Quality/Balanced · ctrl+s save · esc close"
	case m.focus < rowTry():
		return "←/→ cheap up to · space high stakes · q/b start from Quality/Balanced · ctrl+s save · esc close"
	}
	return "type a request · enter try it · ↑/↓ move · ctrl+s save · esc close"
}

func (m Model) pickView(w int) string {
	names := map[int]string{rowCheap: "cheap", rowStrong: "strong", rowClassifier: "classifier"}
	lines := []string{titleStyle.Render("Hybrid routing · " + names[m.picking] + " model"),
		"Gateway models. Type to search.", "Search: " + m.pickSearch, ""}
	opts := m.pickOptions()
	budget := max(3, m.height-10)
	start := max(0, m.pickCursor-budget+1)
	current := m.roleValue(m.picking)
	for i := start; i < min(len(opts), start+budget); i++ {
		label := opts[i]
		if label == "" {
			label = "Default"
			switch m.picking {
			case rowCheap:
				label += " (" + lemonade.DefaultHybridCheapModel + ")"
			case rowStrong:
				label += " (" + lemonade.DefaultHybridStrongModel + ")"
			default:
				label = "Same as the cheap model"
			}
		}
		if opts[i] == current {
			label += " · current"
		}
		label = ansi.Truncate(label, w-2, "…")
		if i == m.pickCursor {
			lines = append(lines, focusStyle.Render("› "+label))
		} else {
			lines = append(lines, "  "+label)
		}
	}
	if len(opts) == 0 {
		lines = append(lines, "No gateway model matches. Backspace to change the search.")
	}
	if m.modelsErr != nil {
		lines = append(lines, "", errStyle.Render("Gateway models couldn't be listed: "+m.modelsErr.Error()))
	}
	return m.frame(lines, "type to search · ↑/↓ choose · enter select · esc back", w)
}

// frame wraps lines to the width and, when too tall, keeps the title and the
// bottom, where the focused row and keys usually are.
func (m Model) frame(lines []string, hint string, w int) string {
	var wrapped []string
	for _, line := range lines {
		wrapped = append(wrapped, strings.Split(ansi.Wrap(line, w, ""), "\n")...)
	}
	if budget := m.height - 4; m.height > 0 && len(wrapped) > budget {
		wrapped = append(wrapped[:1], wrapped[len(wrapped)-max(1, budget-1):]...)
	}
	wrapped = append(wrapped, "", dimStyle.Render(ansi.Truncate(hint, w, "…")))
	return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(wrapped, "\n"))
}
