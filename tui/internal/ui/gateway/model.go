package gateway

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// stage is where the user is in the connect flow. The screen walks
// URL -> token -> models, but jumps straight to models when Lemonade already
// has a registered, authenticated provider.
type stage int

const (
	stageURL stage = iota
	stageToken
	stageModels
)

// savingNotice is shown while the token is on its way to the credential store,
// and is the only notice the success path is allowed to overwrite.
const savingNotice = "Saving it..."

// CloseMsg asks the root model to leave the gateway screen.
type CloseMsg struct{}

// statusMsg carries the initial provider probe.
type statusMsg struct {
	status Status
	err    error
}

// probeMsg carries the result of testing a base URL.
type probeMsg struct {
	count int
	err   error
}

// installedMsg carries the result of registering the provider.
type installedMsg struct {
	result installResult
	err    error
}

// authedMsg carries the result of handing Lemonade a token.
type authedMsg struct {
	result installResult
	err    error
}

// persistedMsg reports whether the accepted token reached the credential
// store. It never carries the token.
type persistedMsg struct {
	err error
}

// modelsMsg carries the discovered gateway models.
type modelsMsg struct {
	models []Model
	err    error
}

// GatewayModel is the connect-to-the-gateway screen.
type GatewayModel struct {
	client *Client
	state  State

	stage  stage
	models []Model
	cursor int

	urlInput   textinput.Model
	tokenInput textinput.Model

	status   Status
	warnings []string
	busy     string
	errMsg   string
	notice   string
	width    int
	height   int
	initErr  error
	haveInit bool

	// pendingPersist saves the token once the gateway has accepted it. It is a
	// closure over the token, never the token itself.
	pendingPersist tea.Cmd
}

// New builds the screen. A nil client means Lemonade could not be reached; the
// screen says so rather than pretending it can do anything.
func New(client *Client, clientErr error) GatewayModel {
	url := textinput.New()
	url.Placeholder = DefaultBaseURL
	url.CharLimit = 512
	url.Width = 60

	token := textinput.New()
	token.Placeholder = "paste your gateway token"
	// The token must never be readable off the screen — including from the
	// loopback control API, which returns the rendered frame verbatim.
	token.EchoMode = textinput.EchoPassword
	token.EchoCharacter = '•'
	token.CharLimit = 512
	token.Width = 60

	state, stateErr := LoadState()
	if stateErr != nil && clientErr == nil {
		clientErr = stateErr
	}
	url.SetValue(state.BaseURL)

	return GatewayModel{
		client:     client,
		state:      state,
		stage:      stageURL,
		urlInput:   url,
		tokenInput: token,
		initErr:    clientErr,
	}
}

func (m GatewayModel) Init() tea.Cmd {
	if m.client == nil {
		return nil
	}
	return m.fetchStatus()
}

// -- commands ---------------------------------------------------------------

func (m GatewayModel) fetchStatus() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		// Lemonade forgets its token on restart, so replay a stored one before
		// reading status — otherwise a user who already authenticated is asked
		// again on every launch. Failure here is not an error to report: it
		// just means there is nothing stored, and the token stage handles that.
		restoreToken()
		status, err := client.Status()
		return statusMsg{status: status, err: err}
	}
}

func (m GatewayModel) probe(baseURL string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		count, err := client.Probe(baseURL)
		return probeMsg{count: count, err: err}
	}
}

func (m GatewayModel) install(baseURL string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		result, err := client.Install(baseURL)
		return installedMsg{result: result, err: err}
	}
}

// authenticate takes the token by value so it is not captured off the model,
// and the caller clears the input immediately after.
func (m GatewayModel) authenticate(token string) tea.Cmd {
	client := m.client
	baseURL := m.state.BaseURL
	return func() tea.Msg {
		result, err := client.SetToken(token, baseURL)
		return authedMsg{result: result, err: err}
	}
}

// persist holds the token in a closure rather than on the model, so it never
// reaches rendered state or a message the root model forwards. Reaching the
// daemon can mean starting one, so it runs after the auth result is already on
// screen instead of blocking it.
func (m GatewayModel) persist(token string) tea.Cmd {
	return func() tea.Msg { return persistedMsg{err: rememberToken(token)} }
}

func (m GatewayModel) fetchModels() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		models, err := client.ListModels()
		return modelsMsg{models: models, err: err}
	}
}

// -- update -----------------------------------------------------------------

func (m GatewayModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case statusMsg:
		m.haveInit = true
		if msg.err != nil {
			m.errMsg = msg.err.Error()
			return m, nil
		}
		m.status = msg.status
		if msg.status.BaseURL != "" {
			m.urlInput.SetValue(msg.status.BaseURL)
		}
		if len(msg.status.Warnings) > 0 {
			m.warnings = msg.status.Warnings
		}
		// Skip straight to the models when there is nothing left to set up.
		if msg.status.Installed && msg.status.Authenticated() {
			m.stage = stageModels
			m.busy = "Loading models..."
			return m, m.fetchModels()
		}
		m.urlInput.Focus()
		return m, textinput.Blink

	case probeMsg:
		m.busy = ""
		if msg.err != nil {
			m.errMsg = msg.err.Error()
			return m, nil
		}
		m.errMsg = ""
		if msg.count > 0 {
			m.notice = fmt.Sprintf("Gateway reachable — %d model(s) advertised.", msg.count)
		} else {
			m.notice = "Gateway reachable. It needs a token before it lists models."
		}
		m.busy = "Registering with Lemonade..."
		return m, m.install(m.urlInput.Value())

	case installedMsg:
		m.busy = ""
		if msg.err != nil {
			m.errMsg = msg.err.Error()
			return m, nil
		}
		m.errMsg = ""
		m.state.BaseURL = strings.TrimRight(m.urlInput.Value(), "/")
		if err := m.state.Save(); err != nil {
			m.errMsg = err.Error()
			return m, nil
		}
		if msg.result.AuthState.EnvVarSet || msg.result.AuthState.RuntimeKeySet {
			m.notice = fmt.Sprintf("Registered. %d model(s) discovered.", msg.result.ModelsDiscovered)
			m.stage = stageModels
			m.busy = "Loading models..."
			return m, m.fetchModels()
		}
		m.notice = "Registered. Lemonade needs a token before it can list models."
		m.stage = stageToken
		m.urlInput.Blur()
		m.tokenInput.Focus()
		return m, textinput.Blink

	case authedMsg:
		m.busy = ""
		if msg.err != nil {
			m.errMsg = msg.err.Error()
			return m, nil
		}
		m.errMsg = ""
		m.notice = fmt.Sprintf(
			"Token accepted — %d model(s) discovered. %s",
			msg.result.ModelsDiscovered, savingNotice)
		m.stage = stageModels
		m.tokenInput.Blur()
		m.busy = "Loading models..."
		save := m.pendingPersist
		m.pendingPersist = nil
		return m, tea.Batch(m.fetchModels(), save)

	case persistedMsg:
		if msg.err != nil {
			// Always shown: the user has to know they will be asked again, and
			// the remedy. This outranks whatever the model list had to say.
			m.notice = fmt.Sprintf(
				"Token accepted, but it could not be saved, so you will be "+
					"asked again next launch: %v. Set %s to avoid that.",
				msg.err, APIKeyEnv)
			return m, nil
		}
		// The model list resolves first when saving is slow, and which model is
		// now the default is the more useful thing to be reading. Only the
		// "saving" placeholder gets replaced.
		if strings.Contains(m.notice, savingNotice) {
			m.notice = "Token saved to your OS credential store — GAIA will reuse it."
		}
		return m, nil

	case modelsMsg:
		m.busy = ""
		if msg.err != nil {
			m.errMsg = msg.err.Error()
			return m, nil
		}
		m.errMsg = ""
		m.models = msg.models
		if m.cursor >= len(m.models) {
			m.cursor = 0
		}
		// Land on a working model rather than an empty selection. Discovery
		// returns 76 models and a new user has no way to know which of them
		// streams; without this they leave the screen with nothing active and
		// their next agent turn fails on whatever the old default was.
		// An existing choice is never overridden.
		if m.state.ActiveModel == "" && len(m.models) > 0 {
			chosen := m.models[0].ID // preference-ranked: the default model first
			m.state = m.state.SetActive(chosen)
			if err := m.state.Save(); err != nil {
				m.errMsg = err.Error()
				return m, nil
			}
			if err := setDefaultModel(chosen); err != nil {
				m.errMsg = err.Error()
				return m, nil
			}
			m.notice = chosen + " selected as the default. Press enter on another to change it."
		}
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}

	return m.forwardToInput(msg)
}

func (m GatewayModel) forwardToInput(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	switch m.stage {
	case stageURL:
		m.urlInput, cmd = m.urlInput.Update(msg)
	case stageToken:
		m.tokenInput, cmd = m.tokenInput.Update(msg)
	}
	return m, cmd
}

func (m GatewayModel) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		return m, func() tea.Msg { return CloseMsg{} }
	case "ctrl+c":
		return m, tea.Quit
	}

	if m.client == nil {
		return m, nil
	}

	switch m.stage {
	case stageURL:
		if msg.String() == "enter" {
			url := strings.TrimSpace(m.urlInput.Value())
			if url == "" {
				m.errMsg = "Enter the gateway's OpenAI-compatible base URL."
				return m, nil
			}
			m.errMsg = ""
			m.notice = ""
			m.busy = "Testing " + url + " ..."
			return m, m.probe(url)
		}

	case stageToken:
		switch msg.String() {
		case "enter":
			token := m.tokenInput.Value()
			if strings.TrimSpace(token) == "" {
				m.errMsg = fmt.Sprintf(
					"Enter a token, or set %s in Lemonade's environment and "+
						"restart it.", APIKeyEnv)
				return m, nil
			}
			// Clear the field before the request goes out; the value is now
			// only in the command closure, which ends with the request.
			m.tokenInput.SetValue("")
			m.errMsg = ""
			m.notice = ""
			m.busy = "Sending token to Lemonade..."
			m.pendingPersist = m.persist(token)
			return m, m.authenticate(token)
		case "tab":
			m.stage = stageURL
			m.tokenInput.Blur()
			m.urlInput.Focus()
			return m, textinput.Blink
		}

	case stageModels:
		switch msg.String() {
		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
			}
			return m, nil
		case "down", "j":
			if m.cursor < len(m.models)-1 {
				m.cursor++
			}
			return m, nil
		case " ":
			if len(m.models) == 0 {
				return m, nil
			}
			m.state = m.state.Toggle(m.models[m.cursor].ID)
			if err := m.state.Save(); err != nil {
				m.errMsg = err.Error()
				return m, nil
			}
			m.notice = ""
			return m, nil
		case "enter":
			if len(m.models) == 0 {
				return m, nil
			}
			id := m.models[m.cursor].ID
			m.state = m.state.SetActive(id)
			if err := m.state.Save(); err != nil {
				m.errMsg = err.Error()
				return m, nil
			}
			// Without this the pick would change nothing an agent can see.
			if err := setDefaultModel(id); err != nil {
				m.errMsg = err.Error()
				return m, nil
			}
			m.notice = id + " is now the default model for GAIA."
			return m, nil
		case "r":
			m.busy = "Reloading models..."
			return m, m.fetchModels()
		case "t":
			m.stage = stageToken
			m.tokenInput.Focus()
			return m, textinput.Blink
		case "u":
			m.stage = stageURL
			m.urlInput.Focus()
			return m, textinput.Blink
		}
	}

	return m.forwardToInput(msg)
}

// -- view -------------------------------------------------------------------

var (
	titleStyle    = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	labelStyle    = lipgloss.NewStyle().Foreground(theme.Text)
	dimStyle      = lipgloss.NewStyle().Foreground(theme.Dim)
	errorStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
	noticeStyle   = lipgloss.NewStyle().Foreground(theme.Success)
	busyStyle     = lipgloss.NewStyle().Foreground(theme.Warning)
	activeStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	selectedStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Highlight)
	keyStyle      = lipgloss.NewStyle().Foreground(theme.Info)
)

func (m GatewayModel) View() string {
	var b strings.Builder
	b.WriteString(titleStyle.Render("AMD LLM Gateway"))
	b.WriteString("\n")
	b.WriteString(dimStyle.Render(
		"Run GAIA agents on gateway-hosted models. Your token goes to " +
			"Lemonade and\nto your OS credential store — encrypted, never a GAIA file."))
	b.WriteString("\n\n")

	if m.initErr != nil {
		b.WriteString(errorStyle.Render(m.initErr.Error()))
		b.WriteString("\n\n")
		b.WriteString(keyStyle.Render("esc") + dimStyle.Render(" back"))
		return m.fit(b.String())
	}
	if !m.haveInit {
		b.WriteString(busyStyle.Render("Checking Lemonade..."))
		return m.fit(b.String())
	}

	var tail strings.Builder
	if m.busy != "" {
		tail.WriteString("\n" + busyStyle.Render(m.busy) + "\n")
	}
	if m.notice != "" {
		tail.WriteString("\n" + noticeStyle.Render(m.notice) + "\n")
	}
	if m.errMsg != "" {
		tail.WriteString("\n" + errorStyle.Render(m.errMsg) + "\n")
	}
	tail.WriteString("\n")
	tail.WriteString(m.renderKeys())

	inputWidth := 60
	if m.width > 0 {
		// Prompt and cursor take three cells beside the text.
		inputWidth = max(1, min(inputWidth, m.width-3))
	}
	switch m.stage {
	case stageURL:
		b.WriteString(labelStyle.Render("Gateway base URL"))
		b.WriteString("\n")
		b.WriteString(dimStyle.Render("The OpenAI-compatible endpoint, e.g. https://llm-api.amd.com/Unified/v1"))
		b.WriteString("\n")
		in := m.urlInput
		in.Width = inputWidth
		b.WriteString(in.View())
		b.WriteString("\n")
	case stageToken:
		b.WriteString(labelStyle.Render("Gateway API token"))
		b.WriteString("\n")
		b.WriteString(dimStyle.Render(
			"Saved to your OS credential store, so you are not asked again.\n" +
				"Set " + APIKeyEnv + " in Lemonade's environment to use that instead."))
		b.WriteString("\n")
		in := m.tokenInput
		in.Width = inputWidth
		b.WriteString(in.View())
		b.WriteString("\n")
	case stageModels:
		used := len(m.wrap(b.String())) + len(m.wrap(tail.String())) - 1
		b.WriteString(m.renderModels(m.height - used))
	}

	b.WriteString(tail.String())
	return m.fit(b.String())
}

// wrap splits s into the lines it takes at the screen width.
func (m GatewayModel) wrap(s string) []string {
	if m.width > 0 {
		s = ansi.Wrap(s, m.width, "")
	}
	return strings.Split(s, "\n")
}

// fit wraps the frame to the window and, if it is still too tall, keeps the
// title and the bottom — where the input, list and keys are.
func (m GatewayModel) fit(s string) string {
	lines := m.wrap(s)
	if m.height > 0 && len(lines) > m.height {
		lines = append(lines[:1], lines[len(lines)-m.height+1:]...)
	}
	return strings.Join(lines, "\n")
}

// renderModels lists the models in at most budget lines (no limit when the
// window size is unknown), scrolled so the cursor row stays visible.
func (m GatewayModel) renderModels(budget int) string {
	var b strings.Builder
	if len(m.models) == 0 {
		b.WriteString(dimStyle.Render(
			"No gateway models discovered yet.\n" +
				"Lemonade only lists them once it has a working token — press t to " +
				"enter one, or r to reload."))
		return b.String()
	}

	b.WriteString(labelStyle.Render(fmt.Sprintf("%d gateway model(s)", len(m.models))))
	b.WriteString("\n\n")
	blocks := make([]string, len(m.models))
	for i, model := range m.models {
		var row strings.Builder
		cursor := "  "
		if i == m.cursor {
			cursor = selectedStyle.Render("> ")
		}
		mark := "[ ]"
		if m.state.IsEnabled(model.ID) {
			mark = "[x]"
		}
		name := model.ID
		if model.ID == m.state.ActiveModel {
			name = activeStyle.Render(model.ID + " (active)")
		}
		star := ""
		if model.Recommended() {
			star = dimStyle.Render(" *")
		}
		row.WriteString(cursor + mark + " " + name + star + "\n")

		details := append([]string{}, model.Labels...)
		if model.CtxSize > 0 {
			details = append(details, fmt.Sprintf("%dK ctx", model.CtxSize/1024))
		}
		if len(details) > 0 {
			row.WriteString("      " + dimStyle.Render(strings.Join(details, ", ")) + "\n")
		}
		blocks[i] = row.String()
	}
	if m.height <= 0 {
		b.WriteString(strings.Join(blocks, ""))
		return b.String()
	}
	// A block ends in a newline, so it takes one line fewer than wrap reports.
	height := func(i int) int { return len(m.wrap(blocks[i])) - 1 }
	avail := max(1, budget-2)
	start, end := m.cursor, m.cursor+1
	used := height(m.cursor)
	for start > 0 && used+height(start-1) <= avail {
		start--
		used += height(start)
	}
	for end < len(blocks) && used+height(end) <= avail {
		used += height(end)
		end++
	}
	b.WriteString(strings.Join(blocks[start:end], ""))
	return b.String()
}

func (m GatewayModel) renderKeys() string {
	key := func(k, desc string) string {
		return keyStyle.Render(k) + dimStyle.Render(" "+desc)
	}
	var keys []string
	switch m.stage {
	case stageURL:
		keys = []string{key("enter", "test & register")}
	case stageToken:
		keys = []string{key("enter", "submit"), key("tab", "edit URL")}
	case stageModels:
		keys = []string{
			key("space", "enable/disable"),
			key("enter", "set active"),
			key("r", "reload"),
			key("t", "token"),
			key("u", "URL"),
		}
	}
	keys = append(keys, key("esc", "back"))
	// Break between keys, never inside one, when the row is too narrow.
	sep := dimStyle.Render("  ·  ")
	var rows []string
	line := keys[0]
	for _, k := range keys[1:] {
		if m.width > 0 && ansi.StringWidth(line+sep+k) > m.width {
			rows = append(rows, line)
			line = k
			continue
		}
		line += sep + k
	}
	return strings.Join(append(rows, line), "\n")
}

// ActiveModel is the gateway model currently selected, for the status bar.
func (m GatewayModel) ActiveModel() string { return m.state.ActiveModel }
