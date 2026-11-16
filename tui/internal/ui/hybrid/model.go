// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package hybrid is the /hybrid screen: templates, the models hybrid routes
// between (from every provider set up here), its policy and custom rules, a
// preview of how a request would be routed, and how past turns were.
//
// Settings are read from and saved to ~/.gaia/config.json with the checks
// GaiaConfig applies, the way every other TUI setting is saved. The preview, the
// stats and the server's own template list need the Agent UI server; without
// it the templates are judged here from what this PC's providers offer.
package hybrid

import (
	"context"
	"errors"
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
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// ClosedMsg asks the host to close the screen.
type ClosedMsg struct{}

type settingsMsg struct {
	settings lemonade.HybridSettings
	err      error
}

type catalogMsg struct{ catalog lemonade.HybridCatalog }

type serverTemplatesMsg struct {
	templates []lemonade.HybridTemplate
	err       error
}

type appliedMsg struct {
	name string
	err  error
}

type notesMsg struct {
	notes serverNotes
	err   error
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

// loadCatalog lists what each provider offers here. A var so tests reach no
// server.
var loadCatalog = func(ctx context.Context, base string) lemonade.HybridCatalog {
	c := lemonade.HybridCatalog{Models: map[string][]string{}, Errs: map[string]error{},
		ClaudeKey: preflight.ClaudeCredentialConfigured(), LocalOnly: lemonade.LocalOnly()}
	lc := lemonade.New(base)
	for _, p := range []string{lemonade.HybridFromLocal, lemonade.HybridFromGateway, lemonade.HybridFromFireworks} {
		models, err := lc.Models(ctx, p)
		if err != nil {
			c.Errs[p] = err
			continue
		}
		for _, m := range models {
			c.Models[p] = append(c.Models[p], m.ID)
		}
	}
	if c.ClaudeKey {
		c.Models[lemonade.HybridFromClaude] = lemonade.HybridClaudeModels
	}
	local, err := lemonade.DefaultLocalChatModel()
	if err != nil {
		c.Errs[lemonade.HybridFromLocal] = err
	}
	c.DefaultLocal = local
	return c
}

// Settings I/O, vars so tests touch no real config.
var (
	readSettings = lemonade.ReadHybridSettings
	saveSettings = lemonade.SaveHybridSettings
)

const loadTimeout = 30 * time.Second

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

func rowTry() int { return rowRules + len(lemonade.HybridKinds) }

// choice is one row of the model picker: a provider heading, or a model.
type choice struct {
	id       string
	heading  string
	reason   string
	isModel  bool
	provider string
}

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

	catalog       lemonade.HybridCatalog
	catalogLoaded bool

	// templates are the server's when it answered, else judged here.
	templates     []lemonade.HybridTemplate
	fromServer    bool
	serverChecked bool
	templatesErr  error
	applying      bool

	stats    string
	statsErr error

	// server is the Agent UI's notes for the saved settings; nil when it
	// isn't running, so the notes are worked out here.
	server *serverNotes

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

// New builds the screen for the Lemonade at base, which lists the models.
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

// Init reads the settings, every provider's models, the server's templates
// and the stats together.
func (m Model) Init() tea.Cmd {
	ctx, base, a := m.ctx, m.base, m.api
	return tea.Batch(
		readSettingsCmd(),
		m.readNotesCmd(),
		func() tea.Msg {
			c, cancel := context.WithTimeout(ctx, loadTimeout)
			defer cancel()
			return catalogMsg{catalog: loadCatalog(c, base)}
		},
		func() tea.Msg {
			c, cancel := context.WithTimeout(ctx, loadTimeout)
			defer cancel()
			t, err := a.Templates(c)
			return serverTemplatesMsg{templates: t, err: err}
		},
		func() tea.Msg {
			c, cancel := context.WithTimeout(ctx, loadTimeout)
			defer cancel()
			s, err := a.Stats(c)
			return statsMsg{stats: s, err: err}
		},
	)
}

func readSettingsCmd() tea.Cmd {
	return func() tea.Msg {
		s, err := readSettings()
		return settingsMsg{settings: s, err: err}
	}
}

func (m Model) readNotesCmd() tea.Cmd {
	ctx, a := m.ctx, m.api
	return func() tea.Msg {
		c, cancel := context.WithTimeout(ctx, loadTimeout)
		defer cancel()
		n, err := a.Notes(c)
		return notesMsg{notes: n, err: err}
	}
}

// Close stops anything still in flight.
func (m Model) Close() { m.cancel() }

// field is the focused field row, or -1 on a template.
func (m Model) field() int {
	if m.focus < len(m.templates) {
		return -1
	}
	return m.focus - len(m.templates)
}

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

// refreshTemplates judges the built-in templates here while the server's
// list is not in use.
func (m *Model) refreshTemplates() {
	if m.fromServer || !m.catalogLoaded || !m.serverChecked {
		return
	}
	before := len(m.templates)
	m.templates = lemonade.BuiltinHybridTemplates(m.catalog)
	m.keepFocus(before)
}

// keepFocus holds the focus on the same field when the template rows above
// it change; before any key it starts on the first template.
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
		m.loaded = true
		m.loadErr = v.err
		if v.err != nil {
			// Shown, and the screen opens on the defaults so the bad value can be
			// replaced; saving writes every hybrid key.
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
	case catalogMsg:
		m.catalog, m.catalogLoaded = v.catalog, true
		m.refreshTemplates()
		return m, nil
	case serverTemplatesMsg:
		m.serverChecked = true
		var down *ServerDownError
		var missing *RouteMissingError
		switch {
		case v.err == nil:
			before := len(m.templates)
			m.templates, m.fromServer = v.templates, true
			m.keepFocus(before)
		case errors.As(v.err, &down), errors.As(v.err, &missing):
			m.refreshTemplates()
		default:
			m.templatesErr = v.err
		}
		return m, nil
	case appliedMsg:
		m.applying = false
		if v.err != nil {
			m.errMsg = "Not applied: " + v.err.Error()
			return m, nil
		}
		m.note = v.name + " applied. A chat already on hybrid picks this up after /model hybrid."
		return m, tea.Batch(readSettingsCmd(), m.readNotesCmd())
	case notesMsg:
		m.server = nil
		if v.err == nil {
			n := v.notes
			m.server = &n
		} else if !IsServerAbsent(v.err) {
			m.errMsg = "The Agent UI server's hybrid notes couldn't be read: " + v.err.Error()
		}
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
	switch m.field() {
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
		m = m.save()
		return m, m.readNotesCmd()
	case "up", "shift+tab":
		return m.move(-1)
	case "down", "tab":
		return m.move(1)
	}
	if !m.loaded {
		return m, nil
	}
	f := m.field()
	switch {
	case f < 0:
		if k.String() == "enter" {
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
		if k.Type == tea.KeyRunes && !isDigits(string(k.Runes)) {
			return m, nil
		}
		return m.forward(k)
	case f == rowPolicy:
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
	case f < rowTry():
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
	m.moved = true
	rows := len(m.templates) + rowTry() + 1
	m.focus = (m.focus + delta + rows) % rows
	m.timeout.Blur()
	m.try.Blur()
	switch m.field() {
	case rowTimeout:
		return m, m.timeout.Focus()
	case rowTry():
		return m, m.try.Focus()
	}
	return m, nil
}

// applyTemplate sets a template's models and policy: through the server when
// the list came from it, else by saving config.json here.
func (m Model) applyTemplate(t lemonade.HybridTemplate) (tea.Model, tea.Cmd) {
	m.note, m.errMsg = "", ""
	if !t.Available {
		m.errMsg = t.Name + " can't be used yet: " + t.Reason + "."
		return m, nil
	}
	if m.fromServer {
		m.applying = true
		ctx, a := m.ctx, m.api
		return m, func() tea.Msg {
			return appliedMsg{name: t.Name, err: a.ApplyTemplate(ctx, t.ID)}
		}
	}
	s := t.Settings(m.saved)
	if m.loadErr != nil || s.Timeout == 0 {
		s.Timeout = lemonade.DefaultHybridTimeout
	}
	if err := saveSettings(s); err != nil {
		m.errMsg = "Not applied: " + err.Error()
		return m, nil
	}
	m.note = t.Name + " applied. A chat already on hybrid picks this up after /model hybrid."
	return m, tea.Batch(readSettingsCmd(), m.readNotesCmd())
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
	kind := lemonade.HybridKinds[m.field()-rowRules]
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

// offered checks every model s routes to against the providers set up here,
// as the backend accepts a model any configured provider offers.
func (m Model) offered(s lemonade.HybridSettings) error {
	if !m.catalogLoaded {
		return errors.New("the models each provider offers are still loading; try again in a moment")
	}
	h := s.Models()
	for _, r := range h.Roles() {
		if _, err := m.catalog.Find(r.Model); err != nil {
			return fmt.Errorf("the %s model %s can't be used: %v. %s", r.Role, r.Model, err, m.catalog.Checked())
		}
	}
	return nil
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
	if err := s.Validate(); err != nil {
		m.errMsg = "Not saved: " + err.Error()
		return m
	}
	if err := m.offered(s); err != nil {
		m.errMsg = "Not saved: " + err.Error()
		return m
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

// choices are the picker's rows: the default first, then each provider's
// models under its heading. A provider with nothing to offer says why, and the
// current choice stays listed with the reason when no provider offers it.
func (m Model) choices() []choice {
	current := m.roleValue(m.picking)
	out := []choice{{isModel: true}}
	placed := current == ""
	for _, p := range lemonade.HybridProviders {
		out = append(out, choice{heading: lemonade.HybridProviderLabel(p)})
		if err := m.catalog.Errs[p]; err != nil {
			out = append(out, choice{reason: "couldn't list them: " + err.Error()})
		} else if len(m.catalog.Models[p]) == 0 {
			out = append(out, choice{reason: m.emptyReason(p)})
		}
		for _, id := range m.catalog.Models[p] {
			out = append(out, choice{id: id, isModel: true, provider: p})
			placed = placed || strings.EqualFold(id, current)
		}
		if !placed && lemonade.HybridProviderOf(current) == p {
			_, err := m.catalog.Find(current)
			out = append(out, choice{id: current, isModel: true, provider: p, reason: reasonOf(err)})
			placed = true
		}
	}
	needle := strings.ToLower(m.pickSearch)
	if needle == "" {
		return out
	}
	var filtered []choice
	for _, c := range out {
		if c.isModel && c.id != "" && strings.Contains(strings.ToLower(c.id), needle) {
			filtered = append(filtered, c)
		}
	}
	return filtered
}

func reasonOf(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func (m Model) emptyReason(p string) string {
	if !m.catalogLoaded {
		return "loading…"
	}
	return lemonade.HybridSetupHint(p)
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
			m.errMsg = c.id + " can't be used: " + c.reason + "."
			m.picking = -1
			return m, nil
		}
		m.setRole(m.picking, c.id)
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

// modelLabel is a role's value as shown: the id, or what the default is.
func (m Model) modelLabel(row int) string {
	v := m.roleValue(row)
	h := m.edit.Models()
	switch {
	case v != "":
	case row == rowCheap:
		return h.Cheap + " (default)"
	case row == rowStrong:
		return h.Strong + " (default)"
	default:
		return "same as the cheap model"
	}
	return v + " · " + lemonade.HybridProviderLabel(lemonade.HybridProviderOf(v))
}

// notes are the cautions the pair earns: the server's for the saved pair when
// it answers, else worked out here in the same words.
func (m Model) notes() []string {
	var out []string
	if m.server != nil && !m.dirty() {
		for _, n := range []*string{m.server.MeasuredNote, m.server.ClassifierNote} {
			if n != nil && *n != "" {
				out = append(out, *n)
			}
		}
		return out
	}
	_, measured, classifier := lemonade.HybridNotes(m.current())
	for _, n := range []string{measured, classifier} {
		if n != "" {
			out = append(out, n)
		}
	}
	return out
}

func (m Model) View() string {
	w := max(20, m.width-4)
	lines := []string{titleStyle.Render("Hybrid routing")}
	if !m.loaded {
		lines = append(lines, "", "Reading the hybrid settings…")
		return m.frame(lines, "esc close", w, -1)
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
			". Showing the defaults; ctrl+s or a template replaces them."))
	}

	focusLine := -1
	lines = append(lines, "", m.templatesHeading())
	if m.templatesErr != nil {
		lines = append(lines, errStyle.Render("  Templates couldn't be listed: "+m.templatesErr.Error()))
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
			detail = "Can't use yet: " + t.Reason
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
	for _, p := range lemonade.HybridPolicies {
		name := titleCase(p)
		if p == m.edit.Policy {
			name = chosenStyle.Render("[" + name + "]")
		}
		policies = append(policies, name)
	}
	lines = append(lines, row(rowPolicy, "Policy", strings.Join(policies, "  ")))
	for _, n := range m.notes() {
		lines = append(lines, warnStyle.Render("  "+n))
	}

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
		case rowRules+i == m.field():
			focusLine = len(lines)
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

	if m.applying {
		lines = append(lines, "", dimStyle.Render("Applying the template…"))
	}
	if m.note != "" {
		lines = append(lines, "", okStyle.Render(m.note))
	}
	if m.errMsg != "" {
		lines = append(lines, "", errStyle.Render(m.errMsg))
	}
	return m.frame(lines, m.hint(), w, focusLine)
}

func (m Model) templatesHeading() string {
	switch {
	case m.fromServer:
		return dimStyle.Render("Templates")
	case !m.serverChecked || !m.catalogLoaded:
		return dimStyle.Render("Templates (checking what this PC offers…)")
	}
	return dimStyle.Render("Templates (judged on this PC; the Agent UI server isn't running)")
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
	f := m.field()
	switch {
	case f < 0:
		return "↑/↓ move · enter apply template · ctrl+s save · esc close"
	case f <= rowClassifier:
		return "↑/↓ move · enter choose model · ctrl+s save · esc close"
	case f == rowTimeout:
		return "type seconds (1-120) · ↑/↓ move · ctrl+s save · esc close"
	case f == rowPolicy:
		return "←/→ policy · q/b start from Quality/Balanced · ctrl+s save · esc close"
	case f < rowTry():
		return "←/→ cheap up to · space high stakes · q/b start from Quality/Balanced · ctrl+s save · esc close"
	}
	return "type a request · enter try it · ↑/↓ move · ctrl+s save · esc close"
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
		if label == "" {
			switch m.picking {
			case rowCheap:
				label = "Default (" + lemonade.DefaultHybridCheapModel + ")"
			case rowStrong:
				label = "Default (" + lemonade.DefaultHybridStrongModel + ")"
			default:
				label = "Same as the cheap model"
			}
		}
		if c.id == current {
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
		if focusLine >= 0 && focusAt < start+1 {
			start = max(1, focusAt-keep/3)
		}
		wrapped = append(wrapped[:1], wrapped[start:min(len(wrapped), start+keep)]...)
	}
	wrapped = append(wrapped, "", dimStyle.Render(ansi.Truncate(hint, w, "…")))
	return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(wrapped, "\n"))
}
