// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package phonepanel is /phone: phone alerts' provider, phone book, rules and
// alerts, on one full screen. Every change goes to the daemon's phone routes;
// nothing is kept here. See docs/guides/phone-alerts.mdx.
package phonepanel

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

	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// CloseMsg asks the host to leave the panel.
type CloseMsg struct{}

// Msg is every message the panel's own commands produce; a host forwards
// these, keys, mouse and resizes, and nothing else.
type Msg interface{ phonePanelMsg() }

type loadedMsg struct {
	status   phone.Status
	settings phone.Settings
	alerts   []phone.Alert
	sim      *phone.SimLog
	err      error
}

type actionMsg struct {
	flow    string
	notice  string
	err     error
	started *phone.Started
}

func (loadedMsg) phonePanelMsg() {}
func (actionMsg) phonePanelMsg() {}

const callTimeout = 90 * time.Second

type section int

const (
	secSetup section = iota
	secBook
	secRules
	secAlerts
)

var sectionNames = []string{"Setup", "Phone book", "Rules", "Alerts"}

type item struct {
	id    string
	label string
	value string
}

// form is a short sequence of fields: provider keys, a number, a code.
type form struct {
	kind   string
	title  string
	note   string
	labels []string
	fields []textinput.Model
	focus  int
	vid    string
}

// Model is the panel.
type Model struct {
	api      *phone.Client
	width    int
	height   int
	loaded   bool
	loadErr  error
	status   phone.Status
	settings phone.Settings
	alerts   []phone.Alert
	sim      *phone.SimLog

	sec    section
	cursor int
	form   *form
	busy   string
	notice string
	errMsg string
}

// New builds the panel over api.
func New(api *phone.Client, width, height int) Model {
	return Model{api: api, width: width, height: height}
}

// Init loads everything.
func (m Model) Init() tea.Cmd { return m.load() }

func (m Model) load() tea.Cmd {
	api := m.api
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		var out loadedMsg
		if out.status, out.err = api.Status(ctx); out.err != nil {
			return out
		}
		if out.settings, out.err = api.Settings(ctx); out.err != nil {
			return out
		}
		if out.alerts, out.err = api.Alerts(ctx, false); out.err != nil {
			return out
		}
		if out.status.Provider == "simulated" {
			sim, err := api.SimulatedLog(ctx)
			if err != nil {
				out.err = err
				return out
			}
			out.sim = &sim
		}
		return out
	}
}

// act runs one change, then the panel reloads.
func (m Model) act(flow, busy string, run func(context.Context, *phone.Client) (string, *phone.Started, error)) (Model, tea.Cmd) {
	m.busy, m.errMsg, m.notice = busy, "", ""
	api := m.api
	return m, func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		notice, started, err := run(ctx, api)
		return actionMsg{flow: flow, notice: notice, err: err, started: started}
	}
}

func (m Model) patch(notice string, p map[string]any) (Model, tea.Cmd) {
	return m.act("settings", "Saving…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
		_, err := api.PutSettings(ctx, p)
		return notice, nil, err
	})
}

// Update handles keys, mouse, resizes and the panel's own results.
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		return m, nil
	case loadedMsg:
		m.loaded = true
		m.loadErr = msg.err
		if msg.err == nil {
			m.status, m.settings, m.alerts, m.sim = msg.status, msg.settings, msg.alerts, msg.sim
		}
		m.clampCursor()
		return m, nil
	case actionMsg:
		return m.finished(msg)
	case tea.KeyMsg:
		if m.form != nil {
			return m.formKey(msg)
		}
		return m.key(msg)
	case tea.MouseMsg:
		return m.mouse(msg)
	}
	if m.form != nil {
		var cmd tea.Cmd
		f := m.form
		f.fields[f.focus], cmd = f.fields[f.focus].Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m Model) finished(msg actionMsg) (tea.Model, tea.Cmd) {
	m.busy = ""
	if msg.err != nil {
		m.errMsg = phone.ErrorText(msg.err)
		// A wrong code keeps its form open for another try; anything that
		// means "start over" closes it.
		if m.form != nil && !keepFormOn(msg.err) {
			m.form = nil
		}
		return m, m.load()
	}
	m.notice = msg.notice
	m.form = nil
	if msg.started != nil {
		switch msg.flow {
		case "add":
			m.form = codeForm("code_add", msg.started)
		case "remove":
			m.form = codeForm("code_remove", msg.started)
		}
	}
	return m, m.load()
}

// keepFormOn reports an error the user can fix in the same form.
func keepFormOn(err error) bool {
	pe, ok := err.(*phone.Error)
	if !ok {
		return true
	}
	switch pe.Code {
	case "verification_expired", "not_found", "rate_limited":
		return false
	case "verification_failed":
		return !strings.Contains(pe.Message, "Start again")
	}
	return true
}

func (m *Model) clampCursor() {
	n := len(m.items())
	if m.cursor >= n {
		m.cursor = max(n-1, 0)
	}
}

// ---------------------------------------------------------------- rows

func onOff(b bool) string {
	if b {
		return "on"
	}
	return "off"
}

func (m Model) items() []item {
	st, set := m.status, m.settings
	var out []item
	switch m.sec {
	case secSetup:
		out = append(out, item{"power", "Phone alerts", onOff(st.Enabled)})
		sim := ""
		if st.Provider == "simulated" && st.Configured {
			sim = "in use"
		}
		out = append(out, item{"simulated", "Use the Simulated phone", sim})
		tw := ""
		if st.Provider == "twilio" && st.Configured {
			tw = "in use"
		}
		out = append(out, item{"twilio", "Set up Twilio keys", tw})
		if st.Provider == "twilio" {
			out = append(out, item{"forget", "Forget the Twilio keys", ""})
		}
		out = append(out, item{"test", "Send a test alert", ""})
	case secBook:
		for _, c := range st.Contacts {
			label := c.Number
			if c.Label != "" {
				label += "  " + c.Label
			}
			value := ""
			if c.Primary {
				value = "first to ring"
			}
			out = append(out, item{fmt.Sprintf("contact:%d", c.ID), label, value})
		}
		out = append(out, item{"add", "Add a number", ""})
	case secRules:
		for _, name := range phone.Rules {
			r := set.Rules[name]
			value := onOff(r.On)
			if r.On {
				value += " · " + phone.StepsLabel(r.Steps)
				if r.Urgent {
					value += " · urgent"
				}
				if name == "approval_waiting" && r.AfterMinutes > 0 {
					value += fmt.Sprintf(" · after %d min", r.AfterMinutes)
				}
			}
			out = append(out, item{"rule:" + name, phone.RuleLabel(name), value})
		}
		quiet := "off"
		if q := set.QuietHours; q != nil {
			quiet = q.Start + "–" + q.End
		}
		out = append(out,
			item{"quiet", "Quiet hours", quiet},
			item{"texts", "Texts", onOff(set.TextsEnabled)},
			item{"awake", "Keep this PC awake", onOff(set.KeepAwake)},
			item{"limits", "Daily limits", fmt.Sprintf("%d calls · %d texts · %d agent calls",
				set.Limits.CallsPerDay, set.Limits.TextsPerDay, set.Limits.AgentCallsPerDay)},
		)
	case secAlerts:
		for _, a := range m.alerts {
			value := a.State
			if a.Count > 1 {
				value = fmt.Sprintf("%s · %d times", value, a.Count)
			}
			out = append(out, item{fmt.Sprintf("alert:%d", a.ID), a.Title, value})
		}
	}
	return out
}

func idNumber(id, prefix string) int {
	n, _ := strconv.Atoi(strings.TrimPrefix(id, prefix))
	return n
}

// ---------------------------------------------------------------- keys

func (m Model) key(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	n := len(m.items())
	switch msg.String() {
	case "esc", "q":
		return m, func() tea.Msg { return CloseMsg{} }
	case "tab", "right":
		return m.goSection((int(m.sec) + 1) % len(sectionNames)), nil
	case "shift+tab", "left":
		return m.goSection((int(m.sec) + len(sectionNames) - 1) % len(sectionNames)), nil
	case "1", "2", "3", "4":
		return m.goSection(int(msg.String()[0] - '1')), nil
	case "up", "k":
		if m.cursor > 0 {
			m.cursor--
		}
		return m, nil
	case "down", "j":
		if m.cursor < n-1 {
			m.cursor++
		}
		return m, nil
	case "r":
		m.notice, m.errMsg = "", ""
		return m, m.load()
	}
	if m.busy != "" || n == 0 || !m.loaded || m.loadErr != nil {
		return m, nil
	}
	it := m.items()[m.cursor]
	switch msg.String() {
	case "enter", " ":
		return m.activate(it)
	case "a":
		if m.sec == secBook {
			m.form = addForm()
			return m, textinput.Blink
		}
	case "x", "delete":
		if strings.HasPrefix(it.id, "contact:") {
			return m.activate(it)
		}
	case "s":
		if name, ok := strings.CutPrefix(it.id, "rule:"); ok {
			r := m.settings.Rules[name]
			next := phone.Steps[(indexOf(phone.Steps, r.Steps)+1)%len(phone.Steps)]
			return m.patch(phone.RuleLabel(name)+": "+phone.StepsLabel(next)+".",
				map[string]any{"rules": map[string]any{name: map[string]any{"steps": next}}})
		}
	case "u":
		if name, ok := strings.CutPrefix(it.id, "rule:"); ok {
			urgent := !m.settings.Rules[name].Urgent
			return m.patch(phone.RuleLabel(name)+": urgent "+onOff(urgent)+".",
				map[string]any{"rules": map[string]any{name: map[string]any{"urgent": urgent}}})
		}
	case "+", "=", "-":
		if it.id == "rule:approval_waiting" {
			after := m.settings.Rules["approval_waiting"].AfterMinutes
			if msg.String() == "-" {
				after = max(after-5, 1)
			} else {
				after = min(after+5, 240)
			}
			return m.patch(fmt.Sprintf("Approvals alert after %d minutes.", after),
				map[string]any{"rules": map[string]any{"approval_waiting": map[string]any{"after_minutes": after}}})
		}
	}
	return m, nil
}

func indexOf(list []string, s string) int {
	for i, v := range list {
		if v == s {
			return i
		}
	}
	return -1
}

func (m Model) goSection(s int) Model {
	m.sec = section(s)
	m.cursor = 0
	return m
}

func (m Model) activate(it item) (tea.Model, tea.Cmd) {
	switch {
	case it.id == "power":
		on := !m.status.Enabled
		return m.patch("Phone alerts are "+onOff(on)+".", map[string]any{"enabled": on})
	case it.id == "simulated":
		return m.act("simulated", "Switching…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			_, err := api.UseSimulated(ctx)
			return "Using the Simulated phone. Nothing is really sent.", nil, err
		})
	case it.id == "twilio":
		m.form = twilioForm()
		return m, textinput.Blink
	case it.id == "forget":
		return m.act("forget", "Forgetting the keys…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			_, err := api.ForgetKeys(ctx)
			return "The Twilio keys are gone, and phone alerts are off.", nil, err
		})
	case it.id == "test":
		return m.act("test", "Sending…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			_, err := api.Test(ctx)
			return "Test alert sent. It shows under Alerts.", nil, err
		})
	case it.id == "add":
		m.form = addForm()
		return m, textinput.Blink
	case strings.HasPrefix(it.id, "contact:"):
		id := idNumber(it.id, "contact:")
		return m.act("remove", "Sending a code…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			started, err := api.StartRemove(ctx, id)
			if err != nil {
				return "", nil, err
			}
			return "", &started, nil
		})
	case strings.HasPrefix(it.id, "rule:"):
		name := strings.TrimPrefix(it.id, "rule:")
		on := !m.settings.Rules[name].On
		return m.patch(phone.RuleLabel(name)+": "+onOff(on)+".",
			map[string]any{"rules": map[string]any{name: map[string]any{"on": on}}})
	case it.id == "quiet":
		m.form = quietForm(m.settings.QuietHours)
		return m, textinput.Blink
	case it.id == "texts":
		on := !m.settings.TextsEnabled
		return m.patch("Texts are "+onOff(on)+".", map[string]any{"texts_enabled": on})
	case it.id == "awake":
		on := !m.settings.KeepAwake
		return m.patch("Keep awake is "+onOff(on)+".", map[string]any{"keep_awake": on})
	case it.id == "limits":
		m.form = limitsForm(m.settings.Limits)
		return m, textinput.Blink
	case strings.HasPrefix(it.id, "alert:"):
		id := idNumber(it.id, "alert:")
		if it.value != "open" && !strings.HasPrefix(it.value, "open ·") {
			m.notice = "That alert is already handled."
			return m, nil
		}
		return m.act("handled", "Marking it handled…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			_, err := api.Handled(ctx, id)
			return "Marked handled. No more texts or calls for it.", nil, err
		})
	}
	return m, nil
}

// ---------------------------------------------------------------- forms

func field(placeholder string, limit int) textinput.Model {
	in := textinput.New()
	in.Placeholder = placeholder
	in.CharLimit = limit
	in.Width = 40
	return in
}

func newForm(kind, title, note string, labels []string, fields []textinput.Model) *form {
	f := &form{kind: kind, title: title, note: note, labels: labels, fields: fields}
	f.fields[0].Focus()
	return f
}

func twilioForm() *form {
	token := field("from the Twilio console", 128)
	// Never readable off the screen, including through the control API.
	token.EchoMode = textinput.EchoPassword
	token.EchoCharacter = '•'
	return newForm("twilio", "Twilio keys",
		"GAIA checks them with Twilio before saving. Use an account just for GAIA.",
		[]string{"Account SID", "Auth Token", "Twilio number"},
		[]textinput.Model{field("AC…", 64), token, field("+1 555 010 0000", 32)})
}

func addForm() *form {
	via := field("call or text", 4)
	via.SetValue("call")
	return newForm("add", "Add a number",
		"GAIA sends a 6-digit code to it. Only numbers you confirm get alerts.",
		[]string{"Number", "Label", "Send the code by"},
		[]textinput.Model{field("+1 555 123 4567", 32), field("e.g. Mobile", 40), via})
}

func codeForm(kind string, s *phone.Started) *form {
	labels := []string{"Code sent to " + s.Number}
	fields := []textinput.Model{field("6 digits", 6)}
	title, note := "Confirm the number", "Type the code within 10 minutes."
	if kind == "code_remove" {
		title = "Remove " + s.Number
		note = "GAIA called " + s.Number + " with a code. Type it to remove the number."
	}
	if s.NeedsConfirmCode {
		labels = append(labels, "Code sent to "+s.ConfirmSentTo)
		fields = append(fields, field("6 digits", 6))
		note = "A second code went to " + s.ConfirmSentTo + ", the number already in the phone book."
	}
	f := newForm(kind, title, note, labels, fields)
	f.vid = s.VerificationID
	return f
}

func quietForm(q *phone.Quiet) *form {
	start, end := field("22:00", 5), field("07:00", 5)
	if q != nil {
		start.SetValue(q.Start)
		end.SetValue(q.End)
	}
	return newForm("quiet", "Quiet hours",
		"Alerts wait until quiet hours end, except urgent ones. Leave both empty for none.",
		[]string{"From", "Until"}, []textinput.Model{start, end})
}

func limitsForm(l phone.Limits) *form {
	calls, texts, agent := field("0–20", 2), field("0–50", 2), field("0–5", 1)
	calls.SetValue(strconv.Itoa(l.CallsPerDay))
	texts.SetValue(strconv.Itoa(l.TextsPerDay))
	agent.SetValue(strconv.Itoa(l.AgentCallsPerDay))
	return newForm("limits", "Daily limits", "They reset at local midnight.",
		[]string{"Calls a day", "Texts a day", "Agent calls a day"},
		[]textinput.Model{calls, texts, agent})
}

func (m Model) formKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	f := m.form
	switch msg.Type {
	case tea.KeyEsc:
		m.form = nil
		m.errMsg = ""
		return m, nil
	case tea.KeyTab, tea.KeyDown, tea.KeyShiftTab, tea.KeyUp:
		f.fields[f.focus].Blur()
		step := 1
		if msg.Type == tea.KeyShiftTab || msg.Type == tea.KeyUp {
			step = len(f.fields) - 1
		}
		f.focus = (f.focus + step) % len(f.fields)
		f.fields[f.focus].Focus()
		return m, textinput.Blink
	case tea.KeyEnter:
		if f.focus < len(f.fields)-1 {
			f.fields[f.focus].Blur()
			f.focus++
			f.fields[f.focus].Focus()
			return m, textinput.Blink
		}
		if m.busy != "" {
			return m, nil
		}
		return m.submit()
	}
	var cmd tea.Cmd
	f.fields[f.focus], cmd = f.fields[f.focus].Update(msg)
	return m, cmd
}

func (m Model) value(i int) string { return strings.TrimSpace(m.form.fields[i].Value()) }

func (m Model) submit() (tea.Model, tea.Cmd) {
	f := m.form
	switch f.kind {
	case "twilio":
		sid, token, number := m.value(0), m.value(1), m.value(2)
		if sid == "" || token == "" || number == "" {
			m.errMsg = "Fill in all three: the Account SID, the Auth Token and your Twilio number."
			return m, nil
		}
		// The token lives in this closure only, not on the model.
		f.fields[1].SetValue("")
		return m.act("keys", "Checking the keys with Twilio…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			_, err := api.SetKeys(ctx, sid, token, number)
			return "Twilio keys saved. Next, add your number in Phone book.", nil, err
		})
	case "add":
		number, label, via := m.value(0), m.value(1), strings.ToLower(m.value(2))
		if number == "" {
			m.errMsg = "Type the number to add."
			return m, nil
		}
		if via != "call" && via != "text" {
			m.errMsg = "Send the code by call or text."
			return m, nil
		}
		return m.act("add", "Sending a code…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			started, err := api.AddNumber(ctx, number, label, via)
			if err != nil {
				return "", nil, err
			}
			return "", &started, nil
		})
	case "code_add", "code_remove":
		codes := make([]string, len(f.fields))
		for i := range f.fields {
			codes[i] = m.value(i)
			if len(codes[i]) != 6 || strings.Trim(codes[i], "0123456789") != "" {
				m.errMsg = "Codes are 6 digits."
				return m, nil
			}
		}
		vid := f.vid
		if f.kind == "code_remove" {
			return m.act("remove_confirm", "Checking the code…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
				_, err := api.ConfirmRemove(ctx, vid, codes[0])
				return "Number removed. The numbers left were told by phone.", nil, err
			})
		}
		confirm := ""
		if len(codes) > 1 {
			confirm = codes[1]
		}
		return m.act("add_confirm", "Checking the code…", func(ctx context.Context, api *phone.Client) (string, *phone.Started, error) {
			c, err := api.ConfirmAdd(ctx, vid, codes[0], confirm)
			return c.Number + " is in the phone book.", nil, err
		})
	case "quiet":
		start, end := m.value(0), m.value(1)
		if start == "" && end == "" {
			return m.patch("Quiet hours are off.", map[string]any{"quiet_hours": nil})
		}
		return m.patch("Quiet hours "+start+"–"+end+".",
			map[string]any{"quiet_hours": map[string]any{"start": start, "end": end}})
	case "limits":
		keys := []string{"calls_per_day", "texts_per_day", "agent_calls_per_day"}
		was := []int{m.settings.Limits.CallsPerDay, m.settings.Limits.TextsPerDay, m.settings.Limits.AgentCallsPerDay}
		changed := map[string]any{}
		for i, k := range keys {
			n, err := strconv.Atoi(m.value(i))
			if err != nil {
				m.errMsg = f.labels[i] + " must be a whole number."
				return m, nil
			}
			if n != was[i] {
				changed[k] = n
			}
		}
		if len(changed) == 0 {
			m.form = nil
			return m, nil
		}
		return m.patch("Limits saved.", map[string]any{"limits": changed})
	}
	return m, nil
}

// ---------------------------------------------------------------- mouse

func (m Model) mouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	ev := tea.MouseEvent(msg)
	if m.form != nil {
		return m, nil
	}
	if ev.IsWheel() {
		if ev.Button == tea.MouseButtonWheelUp && m.cursor > 0 {
			m.cursor--
		} else if ev.Button == tea.MouseButtonWheelDown && m.cursor < len(m.items())-1 {
			m.cursor++
		}
		return m, nil
	}
	if ev.Action != tea.MouseActionPress || ev.Button != tea.MouseButtonLeft {
		return m, nil
	}
	_, hits := m.render()
	for _, h := range hits {
		if h.line != ev.Y || ev.X < h.x0 || ev.X >= h.x1 {
			continue
		}
		if h.tab {
			return m.goSection(h.index), nil
		}
		if h.index == m.cursor {
			return m.activate(m.items()[h.index])
		}
		m.cursor = h.index
		return m, nil
	}
	return m, nil
}

// ---------------------------------------------------------------- view

var (
	titleStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	dimStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	textStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	onStyle     = lipgloss.NewStyle().Foreground(theme.Success).Bold(true)
	offStyle    = lipgloss.NewStyle().Foreground(theme.Dim).Bold(true)
	errStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
	warnStyle   = lipgloss.NewStyle().Foreground(theme.Warning)
	noticeStyle = lipgloss.NewStyle().Foreground(theme.Success)
	selStyle    = lipgloss.NewStyle().Foreground(theme.Selected).Bold(true)
	tabOnStyle  = lipgloss.NewStyle().Foreground(theme.OnFill).Background(theme.AccentFillBG).Bold(true)
	divStyle    = lipgloss.NewStyle().Foreground(theme.Divider)
)

type hit struct {
	line, x0, x1 int
	tab          bool
	index        int
}

// View draws exactly the window.
func (m Model) View() string {
	lines, _ := m.render()
	return strings.Join(lines, "\n")
}

func (m Model) render() ([]string, []hit) {
	width := max(m.width, 30)
	var lines []string
	var hits []hit
	add := func(s string) { lines = append(lines, ansi.Truncate(s, width, "…")) }
	// Sentences people must read in full wrap instead of losing their end.
	para := func(style lipgloss.Style, text string) []string {
		var out []string
		for _, l := range strings.Split(ansi.Wordwrap(text, max(width-2, 10), ""), "\n") {
			out = append(out, " "+style.Render(l))
		}
		return out
	}

	add(" " + titleStyle.Render("Phone alerts"))
	if !m.loaded {
		add(" " + dimStyle.Render("Reading phone settings…"))
		return m.fit(lines, width), nil
	}
	if m.loadErr != nil {
		add("")
		lines = append(lines, para(errStyle, phone.ErrorText(m.loadErr))...)
		add("")
		add(" " + dimStyle.Render("r try again · Esc back"))
		return m.fit(lines, width), nil
	}
	lines = append(lines, m.header(width)...)

	// Section tabs.
	var tabs strings.Builder
	x := 1
	tabs.WriteString(" ")
	for i, name := range sectionNames {
		if i == int(secAlerts) && m.status.OpenAlerts > 0 {
			name = fmt.Sprintf("%s (%d)", name, m.status.OpenAlerts)
		}
		label := " " + name + " "
		style := dimStyle
		if section(i) == m.sec {
			style = tabOnStyle
		}
		tabs.WriteString(style.Render(label) + " ")
		hits = append(hits, hit{line: len(lines), x0: x, x1: x + lipgloss.Width(label), tab: true, index: i})
		x += lipgloss.Width(label) + 1
	}
	add(tabs.String())
	add(divStyle.Render(strings.Repeat("─", width)))

	if m.form != nil {
		lines = append(lines, m.formLines(width)...)
	} else {
		for i, it := range m.items() {
			mark, label := "  ", textStyle.Render(it.label)
			if i == m.cursor {
				mark, label = selStyle.Render("▸ "), selStyle.Render(it.label)
			}
			row := " " + mark + label
			if it.value != "" {
				pad := max(28-lipgloss.Width(it.label), 2)
				row += strings.Repeat(" ", pad) + valueStyle(it.value).Render(it.value)
			}
			hits = append(hits, hit{line: len(lines), x0: 0, x1: width, index: i})
			add(row)
			if m.sec == secAlerts {
				for _, d := range m.alertDetail(i) {
					add("     " + dimStyle.Render(d))
				}
			}
		}
		if m.sec == secAlerts && len(m.alerts) == 0 {
			add("   " + dimStyle.Render("No alerts yet. Send a test alert from Setup to see one."))
		}
		if m.sec == secBook && len(m.status.Contacts) == 0 {
			add("   " + dimStyle.Render("GAIA only calls or texts numbers in this list."))
		}
		if m.sec == secSetup && m.sim != nil {
			lines = append(lines, m.simLines(width)...)
		}
	}

	var tail []string
	if m.busy != "" {
		tail = append(tail, " "+warnStyle.Render(m.busy))
	}
	if m.errMsg != "" {
		tail = append(tail, para(errStyle, m.errMsg)...)
	}
	if m.notice != "" {
		tail = append(tail, para(noticeStyle, m.notice)...)
	}
	tail = append(tail, divStyle.Render(strings.Repeat("─", width)), " "+dimStyle.Render(m.keysHint()))
	body := m.height - len(tail)
	if m.height > 0 && len(lines) > body {
		lines = lines[:max(body, 0)]
	}
	for m.height > 0 && len(lines) < body {
		lines = append(lines, "")
	}
	for _, t := range tail {
		add(t)
	}
	return m.fit(lines, width), hits
}

func valueStyle(v string) lipgloss.Style {
	switch {
	case v == "on" || strings.HasPrefix(v, "on ·") || v == "in use" || strings.HasPrefix(v, "open"):
		return onStyle
	case v == "off":
		return offStyle
	}
	return dimStyle
}

func (m Model) fit(lines []string, width int) []string {
	for i := range lines {
		lines[i] = ansi.Truncate(lines[i], width, "…")
	}
	if m.height > 0 && len(lines) > m.height {
		lines = lines[:m.height]
	}
	return lines
}

func (m Model) header(width int) []string {
	st, set := m.status, m.settings
	state := offStyle.Render("○ Off")
	if st.Enabled {
		state = onStyle.Render("● On")
	}
	parts := []string{phone.ProviderLabel(st.Provider)}
	if st.FromNumber != "" {
		parts = append(parts, "from "+st.FromNumber)
	} else if !st.Configured {
		parts = append(parts, "not set up")
	}
	parts = append(parts, fmt.Sprintf("today %d/%d calls · %d/%d texts",
		st.CallsToday, set.Limits.CallsPerDay, st.TextsToday, set.Limits.TextsPerDay))
	out := []string{" " + state + "  " + dimStyle.Render(strings.Join(parts, " · "))}
	second := "Keep awake: " + phone.AwakeText(st.Awake)
	if st.OpenAlerts > 0 {
		noun := "alert"
		if st.OpenAlerts > 1 {
			noun = "alerts"
		}
		second = fmt.Sprintf("%d open %s · ", st.OpenAlerts, noun) + second
	}
	out = append(out, " "+dimStyle.Render(second))
	if e := st.LastError; e != nil {
		text := e.Message
		if text == "" {
			text = phone.CodeText(e.Code)
		}
		out = append(out, " "+errStyle.Render("Last problem: "+text))
	}
	return out
}

func (m Model) alertDetail(i int) []string {
	if i >= len(m.alerts) {
		return nil
	}
	a := m.alerts[i]
	var out []string
	if a.Reason != "" {
		out = append(out, a.Reason)
	}
	if h := phone.HeldText(a.Held); h != "" {
		out = append(out, h)
	}
	for _, d := range a.Deliveries {
		line := d.Channel + " to " + d.Number + ": " + d.State
		if o := phone.OutcomeText(d.Outcome, d.Reached); o != "" {
			line += " (" + o + ")"
		}
		if d.Error != "" {
			line += " · " + d.Error
		}
		out = append(out, line)
	}
	return out
}

func (m Model) simLines(width int) []string {
	out := []string{"", " " + textStyle.Render("Simulated phone") + dimStyle.Render(" · what would have been sent")}
	var rows []string
	for _, c := range lastN(m.sim.Calls, 3) {
		rows = append(rows, "call to "+phone.Mask(c.To)+": "+c.Script)
	}
	for _, t := range lastN(m.sim.Texts, 3) {
		rows = append(rows, "text to "+phone.Mask(t.To)+": "+t.Body)
	}
	if len(rows) == 0 {
		rows = append(rows, "Nothing yet.")
	}
	for _, r := range rows {
		out = append(out, "   "+dimStyle.Render(ansi.Truncate(r, max(width-4, 8), "…")))
	}
	return out
}

func lastN[T any](list []T, n int) []T {
	if len(list) > n {
		return list[len(list)-n:]
	}
	return list
}

func (m Model) formLines(width int) []string {
	f := m.form
	out := []string{" " + titleStyle.Render(f.title)}
	if f.note != "" {
		out = append(out, " "+dimStyle.Render(f.note))
	}
	out = append(out, "")
	for i, label := range f.labels {
		mark := "  "
		if i == f.focus {
			mark = selStyle.Render("▸ ")
		}
		in := f.fields[i]
		in.Width = max(min(40, width-26), 8)
		out = append(out, " "+mark+textStyle.Render(fmt.Sprintf("%-22s", label))+in.View())
	}
	return out
}

func (m Model) keysHint() string {
	if m.form != nil {
		return "Enter next / save · Tab move · Esc cancel"
	}
	switch m.sec {
	case secBook:
		return "a add · Enter or x remove · Tab section · Esc back"
	case secRules:
		return "Enter on/off · s steps · u urgent · +/- minutes · Tab section · Esc back"
	case secAlerts:
		return "Enter mark handled · r refresh · Tab section · Esc back"
	}
	return "Enter choose · ↑/↓ move · Tab section · r refresh · Esc back"
}
