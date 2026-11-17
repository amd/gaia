// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package hybrid is the /hybrid screen: templates, the models hybrid routes
// between, its policy and custom rules, a preview of how a request would be
// routed, and how past turns were.
//
// Everything comes from and goes to GAIA's background service (hybridapi):
// it owns the presets, the validation and which models and templates can run
// here. This screen renders its answers and sends the user's changes back.
package hybrid

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// ClosedMsg asks the host to close the screen.
type ClosedMsg struct{}

type settingsMsg struct {
	settings hybridapi.Settings
	err      error
}

type savedMsg struct {
	settings hybridapi.Settings
	err      error
}

type templatesMsg struct {
	templates []hybridapi.Template
	err       error
}

type appliedMsg struct {
	name     string
	settings hybridapi.Settings
	err      error
}

type statsMsg struct {
	stats hybridapi.Stats
	err   error
}

type previewMsg struct {
	seq   int
	route event.CanonicalRoute
	err   error
}

// NewAPI builds the backend the screen talks to. A var so tests script it.
var NewAPI = func() hybridapi.API { return hybridapi.New() }

// customPolicy is the policy whose rules are the user's own table.
const customPolicy = "custom"

// maxDifficulty is the top of the classifier's difficulty scale, the most a
// rule's stepper offers; the backend checks the value on save.
const maxDifficulty = 5

// Field rows, top to bottom below the templates. The rules table sits between
// policy and the preview.
const (
	rowCheap = iota
	rowStrong
	rowClassifier
	rowTimeout
	rowPolicy
	rowRules
)

// pick is a model chosen on this screen and not saved yet. An empty id means
// GAIA's default, saved as null.
type pick struct {
	set bool
	id  string
}

// choice is one row of the model picker: a provider heading, a note, or a model.
type choice struct {
	id        string
	heading   string
	reason    string
	isModel   bool
	isDefault bool
	provider  string
}

// Model is the /hybrid screen.
type Model struct {
	ctx    context.Context
	cancel context.CancelFunc
	api    hybridapi.API

	loaded  bool
	loadErr error
	// saved is the backend's last answer: what config.json holds now.
	saved      hybridapi.Settings
	savedRules hybridapi.Rules
	rulesErr   error
	presets    []hybridapi.Preset
	presetsErr error

	// The edit: models picked, the policy, and the custom table.
	picks  [3]pick
	policy string
	rules  hybridapi.Rules

	templates    []hybridapi.Template
	templatesErr error
	applying     bool
	saving       bool

	stats    string
	statsErr error

	// focus counts the template rows first, then the field rows.
	focus   int
	timeout textinput.Model
	try     textinput.Model

	// picking is the field row whose list is open, or -1.
	picking      int
	pickSearch   string
	pickCursor   int
	previewSeq   int
	previewBusy  bool
	preview      *event.CanonicalRoute
	previewErr   error
	note, errMsg string
	// moved is set once the user has moved the focus.
	moved bool
	// discardArmed is set by esc with unsaved changes; a second esc discards.
	discardArmed  bool
	width, height int
}

// New builds the screen.
func New(width, height int) Model {
	ctx, cancel := context.WithCancel(context.Background())
	timeout := textinput.New()
	timeout.Prompt = ""
	timeout.CharLimit = 6
	timeout.Width = 5
	try := textinput.New()
	try.Prompt = "› "
	try.Placeholder = "Type a request to see where it would run"
	try.CharLimit = 4000
	return Model{ctx: ctx, cancel: cancel, api: NewAPI(), timeout: timeout, try: try,
		picking: -1, width: width, height: height}
}

// Init reads the settings, the templates and the stats together.
func (m Model) Init() tea.Cmd {
	ctx, a := m.ctx, m.api
	return tea.Batch(
		func() tea.Msg {
			s, err := a.Settings(ctx)
			return settingsMsg{settings: s, err: err}
		},
		func() tea.Msg {
			t, err := a.Templates(ctx)
			return templatesMsg{templates: t, err: err}
		},
		func() tea.Msg {
			s, err := a.Stats(ctx)
			return statsMsg{stats: s, err: err}
		},
	)
}

// Close stops anything still in flight.
func (m Model) Close() { m.cancel() }

// rowTryAt is the preview row: after one rule row per kind.
func (m Model) rowTryAt() int { return rowRules + len(m.saved.Kinds) }

// field is the focused field row, or -1 on a template.
func (m Model) field() int {
	if m.focus < len(m.templates) {
		return -1
	}
	return m.focus - len(m.templates)
}

// adopt makes s the saved settings and drops every edit.
func (m *Model) adopt(s hybridapi.Settings) {
	m.saved = s
	m.picks = [3]pick{}
	m.policy = string(s.Policy)
	m.savedRules, m.rulesErr = s.RuleTable()
	m.rules = nil
	if m.policy == customPolicy {
		m.rules = m.savedRules.Clone()
	}
	m.presets, m.presetsErr = s.PresetList()
	m.timeout.SetValue(string(s.ClassifierTimeout))
}

func (m Model) savedModel(row int) string {
	switch row {
	case rowCheap:
		return string(m.saved.CheapModel)
	case rowStrong:
		return string(m.saved.StrongModel)
	}
	return string(m.saved.ClassifierModel)
}

// modelFields are the request fields for the model rows.
var modelFields = [3]string{"cheap_model", "strong_model", "classifier_model"}

// changes are the fields the user changed, as the backend takes them. A
// timeout that isn't a number is sent as typed, so the backend's refusal
// names the range.
func (m Model) changes() map[string]any {
	out := map[string]any{}
	for row, p := range m.picks {
		switch {
		case !p.set:
		case p.id == "":
			out[modelFields[row]] = nil
		case p.id != m.savedModel(row):
			out[modelFields[row]] = p.id
		}
	}
	if typed := strings.TrimSpace(m.timeout.Value()); typed != string(m.saved.ClassifierTimeout) {
		if n, err := strconv.Atoi(typed); err == nil {
			out["classifier_timeout"] = n
		} else {
			out["classifier_timeout"] = typed
		}
	}
	if m.policy != string(m.saved.Policy) {
		out["policy"] = m.policy
	}
	if m.policy == customPolicy && (m.policy != string(m.saved.Policy) || !m.rules.Equal(m.savedRules)) {
		out["rules"] = m.rules
	}
	return out
}

func (m Model) dirty() bool { return m.loaded && m.loadErr == nil && len(m.changes()) > 0 }

func (m Model) presetRules(name string) hybridapi.Rules {
	for _, p := range m.presets {
		if p.Name == name {
			return p.Rules
		}
	}
	return nil
}

// policies are the presets in the backend's order, then custom.
func (m Model) policies() []string {
	out := make([]string, 0, len(m.presets)+1)
	for _, p := range m.presets {
		out = append(out, p.Name)
	}
	return append(out, customPolicy)
}

// effectiveRules is the table the chosen policy stands for.
func (m Model) effectiveRules() hybridapi.Rules {
	if m.policy == customPolicy {
		return m.rules
	}
	return m.presetRules(m.policy)
}

// keepFocus holds the focus on the same field when the template rows above
// it change; before any key it starts on the first row.
func (m *Model) keepFocus(before int) {
	if !m.moved {
		m.focus = 0
		return
	}
	m.focus = max(0, m.focus+len(m.templates)-before)
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = v.Width, v.Height
		return m, nil
	case settingsMsg:
		if errors.Is(v.err, context.Canceled) {
			return m, nil
		}
		m.loaded, m.loadErr = true, v.err
		if v.err == nil {
			m.adopt(v.settings)
		}
		return m, nil
	case savedMsg:
		m.saving = false
		switch {
		case errors.Is(v.err, context.Canceled):
		case v.err != nil:
			m.errMsg = "Not saved: " + sentence(v.err)
		default:
			m.adopt(v.settings)
			m.note = "Saved. A chat already on hybrid picks this up after /model hybrid."
		}
		return m, nil
	case templatesMsg:
		if errors.Is(v.err, context.Canceled) {
			return m, nil
		}
		before := len(m.templates)
		m.templates, m.templatesErr = v.templates, v.err
		m.keepFocus(before)
		return m, nil
	case appliedMsg:
		m.applying = false
		switch {
		case errors.Is(v.err, context.Canceled):
		case v.err != nil:
			m.errMsg = "Not applied: " + sentence(v.err)
		default:
			m.adopt(v.settings)
			m.note = v.name + " applied. A chat already on hybrid picks this up after /model hybrid."
		}
		return m, nil
	case statsMsg:
		switch {
		case errors.Is(v.err, context.Canceled):
		case v.err != nil:
			m.statsErr = v.err
		default:
			m.stats = v.stats.Line()
		}
		return m, nil
	case previewMsg:
		if v.seq != m.previewSeq || errors.Is(v.err, context.Canceled) {
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
	switch m.field() {
	case rowTimeout:
		m.timeout, cmd = m.timeout.Update(msg)
	case m.rowTryAt():
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
	if k.String() == "esc" {
		if m.dirty() && !m.discardArmed {
			m.discardArmed = true
			m.note = "You have unsaved changes. ctrl+s saves them; esc again leaves without saving."
			return m, nil
		}
		m.cancel()
		return m, func() tea.Msg { return ClosedMsg{} }
	}
	if !m.loaded || m.loadErr != nil {
		return m, nil
	}
	switch k.String() {
	case "ctrl+s":
		return m.save()
	case "up", "shift+tab":
		return m.move(-1)
	case "down", "tab":
		return m.move(1)
	}
	f := m.field()
	switch {
	case f < 0:
		if k.String() == "enter" && !m.busy() {
			return m.applyTemplate(m.templates[m.focus])
		}
		return m, nil
	case f <= rowClassifier:
		if k.String() == "enter" {
			m.picking, m.pickSearch, m.pickCursor = f, "", 0
			m.errMsg = ""
		}
		return m, nil
	case f == rowTimeout:
		if k.Type == tea.KeyRunes && !isNumber(string(k.Runes)) {
			return m, nil
		}
		return m.forward(k)
	case f == rowPolicy:
		switch k.String() {
		case "left", "h":
			m.setPolicy(-1)
		case "right", "l", " ", "enter":
			m.setPolicy(1)
		default:
			m.startFromKey(k.String())
		}
		return m, nil
	case f < m.rowTryAt():
		return m.handleRuleKey(k), nil
	default:
		if k.String() == "enter" {
			return m.runPreview()
		}
		return m.forward(k)
	}
}

// busy is a save or a template apply in flight; a second change waits for it.
func (m Model) busy() bool { return m.saving || m.applying }

// isNumber is typed text that can be part of a number of seconds; the
// backend says which numbers it takes.
func isNumber(s string) bool {
	for _, r := range s {
		if (r < '0' || r > '9') && r != '.' {
			return false
		}
	}
	return s != ""
}

func (m Model) move(delta int) (tea.Model, tea.Cmd) {
	m.moved = true
	rows := len(m.templates) + m.rowTryAt() + 1
	m.focus = (m.focus + delta + rows) % rows
	m.timeout.Blur()
	m.try.Blur()
	switch m.field() {
	case rowTimeout:
		return m, m.timeout.Focus()
	case m.rowTryAt():
		return m, m.try.Focus()
	}
	return m, nil
}

// applyTemplate asks the backend to make the template the settings; it
// refuses one that can't run here, with why.
func (m Model) applyTemplate(t hybridapi.Template) (tea.Model, tea.Cmd) {
	m.note, m.errMsg = "", ""
	if !t.Available {
		m.errMsg = t.Name + " can't be used yet: " + strings.TrimSuffix(t.Why(), ".") + "."
		return m, nil
	}
	m.applying = true
	ctx, a := m.ctx, m.api
	return m, func() tea.Msg {
		s, err := a.ApplyTemplate(ctx, t.ID)
		return appliedMsg{name: t.Name, settings: s, err: err}
	}
}

// setPolicy steps through the policies. Custom with no table yet starts from
// the preset it was reached from.
func (m *Model) setPolicy(delta int) {
	policies := m.policies()
	i := 0
	for j, p := range policies {
		if p == m.policy {
			i = j
		}
	}
	from := m.policy
	m.policy = policies[(i+delta+len(policies))%len(policies)]
	if m.policy == customPolicy && m.rules == nil {
		m.rules = m.presetRules(from).Clone()
		if m.rules == nil && len(m.presets) > 0 {
			m.rules = m.presets[0].Rules.Clone()
		}
	}
	m.note, m.errMsg = "", ""
}

// startFromKey replaces the custom table with the preset whose initial is key
// (q for quality, b for balanced), turning Custom on.
func (m *Model) startFromKey(key string) bool {
	for _, p := range m.presets {
		if len(key) == 1 && strings.HasPrefix(p.Name, key) {
			m.policy = customPolicy
			m.rules = p.Rules.Clone()
			m.note = "Custom rules now match " + titleCase(p.Name) + ". Edit them, then ctrl+s to save."
			m.errMsg = ""
			return true
		}
	}
	return false
}

// handleRuleKey edits the focused kind's rule. Editing a preset's table turns
// Custom on, starting from that preset, and says so.
func (m Model) handleRuleKey(k tea.KeyMsg) Model {
	kind := m.saved.Kinds[m.field()-rowRules]
	switch k.String() {
	case "left", "h", "right", "l", " ", "enter":
	default:
		m.startFromKey(k.String())
		return m
	}
	if m.policy != customPolicy {
		from := m.policy
		m.policy = customPolicy
		m.rules = m.presetRules(from).Clone()
		m.note = "Switched to Custom, starting from " + titleCase(from) + "."
	} else {
		m.note = ""
	}
	if m.rules == nil {
		m.rules = hybridapi.Rules{}
	}
	m.errMsg = ""
	rule := m.rules[kind]
	switch k.String() {
	case "left", "h":
		rule.CheapMaxDifficulty = max(0, rule.CheapMaxDifficulty-1)
	case "right", "l":
		rule.CheapMaxDifficulty = min(maxDifficulty, rule.CheapMaxDifficulty+1)
	default:
		rule.AllowHighStakes = !rule.AllowHighStakes
	}
	m.rules[kind] = rule
	return m
}

// save sends the changes; the backend validates them and answers with what
// it saved, or why it refused.
func (m Model) save() (tea.Model, tea.Cmd) {
	m.note, m.errMsg = "", ""
	if m.busy() {
		return m, nil
	}
	changes := m.changes()
	if len(changes) == 0 {
		m.note = "Nothing to save."
		return m, nil
	}
	m.saving = true
	ctx, a := m.ctx, m.api
	return m, func() tea.Msg {
		s, err := a.Update(ctx, changes)
		return savedMsg{settings: s, err: err}
	}
}

func (m Model) runPreview() (tea.Model, tea.Cmd) {
	text := strings.TrimSpace(m.try.Value())
	if text == "" {
		m.previewErr = errors.New("type a request first")
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

// roleValue is a model row's value: the pick when there is one, else saved.
// An empty id is the default.
func (m Model) roleValue(row int) string {
	if p := m.picks[row]; p.set {
		return p.id
	}
	return m.savedModel(row)
}

func (m Model) modelInfo(id string) (hybridapi.Model, bool) {
	for _, mo := range m.saved.Models {
		if strings.EqualFold(mo.ID, id) {
			return mo, true
		}
	}
	return hybridapi.Model{}, false
}

// choices are the picker's rows: the default first, then each provider's
// models under its heading, as the backend lists them.
func (m Model) choices() []choice {
	out := []choice{{isModel: true, isDefault: true}}
	if m.saved.ModelsError != nil {
		out = append(out, choice{reason: "Models couldn't be listed: " + *m.saved.ModelsError})
	} else if len(m.saved.Models) == 0 {
		out = append(out, choice{reason: "No provider set up here offers a model. Add one with /provider or /gateway."})
	}
	last := ""
	for _, mo := range m.saved.Models {
		if mo.Provider != last {
			out = append(out, choice{heading: providerLabel(mo.Provider)})
			last = mo.Provider
		}
		c := choice{id: mo.ID, isModel: true, provider: mo.Provider}
		if !mo.Available {
			c.reason = "can't be used here"
			if mo.Reason != nil && *mo.Reason != "" {
				c.reason = *mo.Reason
			}
		}
		out = append(out, c)
	}
	needle := strings.ToLower(m.pickSearch)
	if needle == "" {
		return out
	}
	var filtered []choice
	for _, c := range out {
		if c.isModel && !c.isDefault && strings.Contains(strings.ToLower(c.id), needle) {
			filtered = append(filtered, c)
		}
	}
	return filtered
}

// selectable are the indexes of choices that can be picked.
func selectable(cs []choice) []int {
	var out []int
	for i, c := range cs {
		if c.isModel {
			out = append(out, i)
		}
	}
	return out
}

func (m Model) handlePickKey(k tea.KeyMsg) (tea.Model, tea.Cmd) {
	rows := selectable(m.choices())
	switch k.String() {
	case "esc":
		m.picking = -1
		return m, nil
	case "up":
		m.pickCursor = max(0, m.pickCursor-1)
	case "down", "tab":
		m.pickCursor = max(0, min(len(rows)-1, m.pickCursor+1))
	case "enter":
		if m.pickCursor >= len(rows) {
			return m, nil
		}
		c := m.choices()[rows[m.pickCursor]]
		if c.reason != "" {
			m.errMsg = c.id + " can't be used: " + strings.TrimSuffix(c.reason, ".") + "."
			m.picking = -1
			return m, nil
		}
		m.picks[m.picking] = pick{set: true, id: c.id}
		m.note, m.errMsg = "", ""
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

// sentence is an error as one sentence: a trailing period added only when the
// message lacks one, so none reads "..".
func sentence(err error) string {
	text := strings.TrimSpace(err.Error())
	if strings.HasSuffix(text, ".") {
		return text
	}
	return text + "."
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
	warnStyle   = lipgloss.NewStyle().Foreground(theme.Warning)
	okStyle     = lipgloss.NewStyle().Foreground(theme.Success)
	focusStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	chosenStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Highlight)
)

// modelLabel is a role's value as shown: the id and its provider, or the default.
func (m Model) modelLabel(row int) string {
	v := m.roleValue(row)
	if v == "" {
		if row == rowClassifier {
			return "same as the cheap model"
		}
		return "GAIA's default"
	}
	if mo, ok := m.modelInfo(v); ok && mo.Provider != "" {
		return v + " · " + providerLabel(mo.Provider)
	}
	return v
}

// notes are the backend's cautions for the saved pair.
func (m Model) notes() []string {
	if m.dirty() {
		return nil
	}
	var out []string
	for _, n := range []*string{m.saved.MeasuredNote, m.saved.ClassifierNote} {
		if n != nil && *n != "" {
			out = append(out, *n)
		}
	}
	return out
}

func (m Model) View() string {
	w := max(20, m.width-4)
	lines := []string{titleStyle.Render("Hybrid routing")}
	switch {
	case !m.loaded:
		lines = append(lines, "", "Reading the hybrid settings…")
		return m.frame(lines, "esc close", w, -1)
	case m.loadErr != nil:
		lines = append(lines, "", errStyle.Render(sentence(m.loadErr)))
		return m.frame(lines, "esc close", w, -1)
	case m.picking >= 0:
		return m.pickView(w)
	}
	lines = append(lines, dimStyle.Render(
		"Each turn runs on the cheap or the strong model; the classifier decides. "+
			"Unclassified turns always go to the strong model."))
	switch {
	case m.statsErr != nil:
		lines = append(lines, dimStyle.Render(sentence(m.statsErr)))
	case m.stats != "":
		lines = append(lines, dimStyle.Render(m.stats))
	}
	if m.saved.ConfigError != nil {
		lines = append(lines, errStyle.Render("Your saved settings can't be used: "+
			strings.TrimSuffix(strings.TrimSpace(*m.saved.ConfigError), ".")+
			". Change what it names and press ctrl+s, or apply a template."))
	}

	focusLine := -1
	lines = append(lines, "", dimStyle.Render("Templates"))
	switch {
	case m.templatesErr != nil:
		lines = append(lines, errStyle.Render("  Templates couldn't be listed: "+sentence(m.templatesErr)))
	case m.templates == nil:
		lines = append(lines, dimStyle.Render("  Checking what this PC offers…"))
	}
	for i, t := range m.templates {
		text := t.Name
		if t.Measured {
			text += " · measured"
		} else {
			text += " · not yet measured"
		}
		detail := t.Description
		if !t.Available {
			detail = "Can't use yet: " + t.Why()
		}
		switch {
		case i == m.focus:
			focusLine = len(lines)
			lines = append(lines, focusStyle.Render("› "+text), "    "+detail)
		case !t.Available:
			lines = append(lines, dimStyle.Render("  "+text), dimStyle.Render("    "+detail))
		default:
			lines = append(lines, "  "+text, dimStyle.Render("    "+detail))
		}
	}
	lines = append(lines, "")

	row := func(i int, label, value string) string {
		text := fmt.Sprintf("%-18s %s", label, value)
		if i == m.field() {
			focusLine = len(lines)
			return focusStyle.Render("› " + text)
		}
		return "  " + text
	}
	lines = append(lines,
		row(rowCheap, "Cheap model", m.modelLabel(rowCheap)),
		row(rowStrong, "Strong model", m.modelLabel(rowStrong)),
		row(rowClassifier, "Classifier", m.modelLabel(rowClassifier)))
	timeout := m.timeout.View()
	if m.field() != rowTimeout {
		timeout = m.timeout.Value()
	}
	lines = append(lines, row(rowTimeout, "Classifier limit", timeout+" seconds"))

	var policies []string
	for _, p := range m.policies() {
		name := titleCase(p)
		if p == m.policy {
			name = chosenStyle.Render("[" + name + "]")
		}
		policies = append(policies, name)
	}
	lines = append(lines, row(rowPolicy, "Policy", strings.Join(policies, "  ")))
	if m.presetsErr != nil {
		lines = append(lines, errStyle.Render("  "+sentence(m.presetsErr)))
	}
	for _, n := range m.notes() {
		lines = append(lines, warnStyle.Render("  "+n))
	}

	lines = append(lines, "", dimStyle.Render(fmt.Sprintf("  %-18s %-16s %s", "Request kind", "Cheap up to", "High stakes")))
	if m.rulesErr != nil && m.policy == customPolicy {
		lines = append(lines, errStyle.Render("  "+sentence(m.rulesErr)))
	}
	rules := m.effectiveRules()
	for i, kind := range m.saved.Kinds {
		r, ok := rules[kind]
		limit, stakes := "Never", "strong only"
		if ok && r.CheapMaxDifficulty > 0 {
			limit = "difficulty " + strconv.Itoa(r.CheapMaxDifficulty)
		}
		if ok && r.AllowHighStakes {
			stakes = "cheap allowed"
		}
		if !ok {
			limit, stakes = "no rule", ""
		}
		text := fmt.Sprintf("%-18s %-16s %s", kindLabel(kind), limit, stakes)
		switch {
		case rowRules+i == m.field():
			focusLine = len(lines)
			lines = append(lines, focusStyle.Render("› "+text))
		case m.policy != customPolicy:
			lines = append(lines, dimStyle.Render("  "+text))
		default:
			lines = append(lines, "  "+text)
		}
	}

	lines = append(lines, "", row(m.rowTryAt(), "Try a request", ""))
	try := m.try
	try.Width = max(10, w-4)
	lines = append(lines, "  "+try.View())
	switch {
	case m.previewBusy:
		lines = append(lines, dimStyle.Render("  Asking the classifier…"))
	case m.previewErr != nil:
		lines = append(lines, errStyle.Render("  "+sentence(m.previewErr)))
	case m.preview != nil:
		lines = append(lines, "  "+okStyle.Render(PreviewLine(*m.preview)))
		if m.preview.Reason != "" {
			lines = append(lines, dimStyle.Render("  "+m.preview.Reason))
		}
		if m.preview.Fix != "" {
			lines = append(lines, warnStyle.Render("  "+m.preview.Fix))
		}
		if m.dirty() {
			lines = append(lines, dimStyle.Render("  That used your saved settings; ctrl+s to try these."))
		}
	}

	switch {
	case m.applying:
		lines = append(lines, "", dimStyle.Render("Applying the template…"))
	case m.saving:
		lines = append(lines, "", dimStyle.Render("Saving…"))
	}
	if m.note != "" {
		lines = append(lines, "", okStyle.Render(m.note))
	}
	if m.errMsg != "" {
		lines = append(lines, "", errStyle.Render(m.errMsg))
	}
	return m.frame(lines, m.hint(), w, focusLine)
}

func (m Model) hint() string {
	f := m.field()
	switch {
	case f < 0:
		return "↑/↓ move · enter apply template · ctrl+s save · esc close"
	case f <= rowClassifier:
		return "↑/↓ move · enter choose model · ctrl+s save · esc close"
	case f == rowTimeout:
		return "type seconds · ↑/↓ move · ctrl+s save · esc close"
	case f == rowPolicy:
		return "←/→ policy · " + m.presetHint() + "ctrl+s save · esc close"
	case f < m.rowTryAt():
		return "←/→ cheap up to · space high stakes · " + m.presetHint() + "ctrl+s save · esc close"
	}
	return "type a request · enter try it · ↑/↓ move · ctrl+s save · esc close"
}

// presetHint names the keys that start Custom from each preset, as
// startFromKey reads them: a preset's initial.
func (m Model) presetHint() string {
	if len(m.presets) == 0 {
		return ""
	}
	keys := make([]string, 0, len(m.presets))
	names := make([]string, 0, len(m.presets))
	for _, p := range m.presets {
		if p.Name == "" {
			continue
		}
		keys = append(keys, p.Name[:1])
		names = append(names, titleCase(p.Name))
	}
	return strings.Join(keys, "/") + " start from " + strings.Join(names, "/") + " · "
}

func (m Model) pickView(w int) string {
	names := map[int]string{rowCheap: "cheap", rowStrong: "strong", rowClassifier: "classifier"}
	lines := []string{titleStyle.Render("Hybrid routing · " + names[m.picking] + " model"),
		"Models from every provider set up here. Type to search.", "Search: " + m.pickSearch, ""}
	cs := m.choices()
	rows := selectable(cs)
	focused := -1
	if m.pickCursor < len(rows) {
		focused = rows[m.pickCursor]
	}
	focusLine := -1
	budget := max(3, m.height-10)
	start := max(0, focused-budget+1)
	current := m.roleValue(m.picking)
	for i := start; i < min(len(cs), start+budget); i++ {
		c := cs[i]
		switch {
		case c.heading != "":
			lines = append(lines, titleStyle.Render(c.heading))
			continue
		case !c.isModel:
			lines = append(lines, dimStyle.Render("    "+c.reason))
			continue
		}
		label := c.id
		if c.isDefault {
			label = "GAIA's default"
			if m.picking == rowClassifier {
				label = "Same as the cheap model"
			}
		}
		if !c.isDefault && strings.EqualFold(c.id, current) {
			label += " · current"
		}
		if c.reason != "" {
			label += " · can't use: " + c.reason
		}
		label = ansi.Truncate(label, w-2, "…")
		switch {
		case i == focused:
			focusLine = len(lines)
			lines = append(lines, focusStyle.Render("› "+label))
		case c.reason != "":
			lines = append(lines, dimStyle.Render("  "+label))
		default:
			lines = append(lines, "  "+label)
		}
	}
	if len(rows) == 0 {
		lines = append(lines, "No model matches. Backspace to change the search.")
	}
	return m.frame(lines, "type to search · ↑/↓ choose · enter select · esc back", w, focusLine)
}

// frame wraps lines to the width and, when too tall, keeps the title and a
// window that holds the focused line.
func (m Model) frame(lines []string, hint string, w, focusLine int) string {
	var wrapped []string
	focusAt := 0
	for i, line := range lines {
		if i == focusLine {
			focusAt = len(wrapped)
		}
		wrapped = append(wrapped, strings.Split(ansi.Wrap(line, w, ""), "\n")...)
	}
	if budget := m.height - 4; m.height > 0 && len(wrapped) > budget {
		keep := max(1, budget-1)
		start := len(wrapped) - keep
		if focusLine >= 0 {
			// From the top while the focus fits there, so a notice above the
			// first row is never cut in half.
			start = min(max(1, focusAt-keep/3), len(wrapped)-keep)
			if focusAt < keep+1 {
				start = 1
			}
		}
		wrapped = append(wrapped[:1], wrapped[start:start+keep]...)
	}
	wrapped = append(wrapped, "", dimStyle.Render(ansi.Truncate(hint, w, "…")))
	return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(wrapped, "\n"))
}
