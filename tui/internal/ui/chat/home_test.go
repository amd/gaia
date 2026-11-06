// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/homeassistant"
	"github.com/amd/gaia/tui/internal/ui/components"
)

const homeTestToken = "eyJsecret-token-value"

// fakeHome scripts the sidecar and records what /home sent.
type fakeHome struct {
	status       homeassistant.Status
	connect      homeassistant.Status
	connectErr   error
	test         homeassistant.TestResult
	disconnect   homeassistant.Status
	connectURL   string
	connectToken string
	tests        int
	disconnects  int
}

func (f *fakeHome) Status(context.Context) (homeassistant.Status, error) { return f.status, nil }
func (f *fakeHome) Connect(_ context.Context, url, token string) (homeassistant.Status, error) {
	f.connectURL, f.connectToken = url, token
	return f.connect, f.connectErr
}
func (f *fakeHome) Test(context.Context) (homeassistant.TestResult, error) {
	f.tests++
	return f.test, nil
}
func (f *fakeHome) Disconnect(context.Context) (homeassistant.Status, error) {
	f.disconnects++
	return f.disconnect, nil
}

func homeModel(t *testing.T, f *fakeHome) ChatModel {
	t.Helper()
	prev := newHomeAPI
	newHomeAPI = func() homeassistant.API { return f }
	t.Cleanup(func() { newHomeAPI = prev })
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 100, 30
	return m
}

// runHome delivers msg, then follows the /home messages its commands return.
// Other commands (cursor blink, spinner) are dropped: they only sleep.
func runHome(t *testing.T, m ChatModel, msg tea.Msg) ChatModel {
	t.Helper()
	// update, not Update: the wrapper batches housekeeping commands in.
	updated, cmd := m.update(msg)
	m = updated.(ChatModel)
	for cmd != nil {
		next := cmd()
		switch next.(type) {
		case homeStatusMsg, homeConnectedMsg, homeTestedMsg, homeDisconnectedMsg:
		default:
			return m
		}
		updated, cmd = m.update(next)
		m = updated.(ChatModel)
	}
	return m
}

// homeKey sends one key to the open question, routing a committed answer
// through the model the way Bubble Tea would.
func homeKey(t *testing.T, m ChatModel, key tea.KeyMsg) ChatModel {
	t.Helper()
	if m.question == nil {
		t.Fatal("no question is open")
	}
	q, cmd := m.question.Update(key)
	m.question = &q
	m.updateViewport() // as handleKey does, so View shows the keystroke
	// Only Enter and an option's digit commit; anything else returns the
	// cursor blink, which just sleeps.
	commits := key.Type == tea.KeyEnter ||
		(key.Type == tea.KeyRunes && len(key.Runes) == 1 && key.Runes[0] >= '1' && key.Runes[0] <= '9')
	if cmd == nil || !commits {
		return m
	}
	if ans, ok := cmd().(components.QuestionAnsweredMsg); ok {
		return runHome(t, m, ans)
	}
	return m
}

func openHome(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	updated, cmd := m.submit("/home")
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("/home started no status check")
	}
	return runHome(t, m, cmd())
}

// answer types text into the open question and presses Enter.
func homeAnswer(t *testing.T, m ChatModel, text string) ChatModel {
	t.Helper()
	if m.question == nil {
		t.Fatal("no question is open")
	}
	if text != "" {
		m = homeKey(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(text)})
	}
	return homeKey(t, m, tea.KeyMsg{Type: tea.KeyEnter})
}

// clearInput empties a prefilled free-text answer.
func homeClearInput(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	q := m.question.WithValue("")
	m.question = &q
	return m
}

func homeTranscript(m ChatModel) string {
	var sb strings.Builder
	for _, msg := range m.messages {
		sb.WriteString(msg.Content)
		sb.WriteString("\n")
	}
	return sb.String()
}

func TestHomeNotConnectedShowsTheStepsAndTheAddressField(t *testing.T) {
	m := openHome(t, homeModel(t, &fakeHome{}))
	text := homeTranscript(m)
	for _, want := range []string{
		"not connected", "Model Context Protocol Server", "Voice assistants › Expose", "Long-lived access tokens",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("setup steps are missing %q:\n%s", want, text)
		}
	}
	if m.question == nil || m.question.RequestID() != homeQAddress {
		t.Fatalf("want the address question, got %+v", m.question)
	}
	if got := m.question.Value(); got != homeassistant.DefaultAddress {
		t.Errorf("address starts as %q, want %q", got, homeassistant.DefaultAddress)
	}
	if m.question.Sensitive() {
		t.Error("the address is not a secret")
	}
}

func TestHomeTokenIsMaskedAndSubmitConnects(t *testing.T) {
	f := &fakeHome{connect: homeassistant.Status{
		Connected: true, URL: "http://ha.lan:8123", Active: true,
		ToolCount: 28, Detail: "Connected: 28 Home Assistant tools.",
	}}
	m := openHome(t, homeModel(t, f))
	m = homeClearInput(t, m)
	m = homeAnswer(t, m, "http://ha.lan:8123")
	if m.question == nil || m.question.RequestID() != homeQToken || !m.question.Sensitive() {
		t.Fatal("the token must be asked for in a masked field")
	}
	if strings.Contains(m.View(), homeTestToken) {
		t.Fatal("not yet typed, but already on screen?")
	}

	// Typing shows only the mask.
	m = homeKey(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(homeTestToken)})
	if strings.Contains(m.View(), homeTestToken) {
		t.Fatal("the token is visible while typed")
	}
	if !strings.Contains(m.View(), strings.Repeat("*", 8)) {
		t.Fatal("typing the token shows no mask, so the test is not looking at the field")
	}

	// Submitting goes to connecting before the answer arrives.
	q, commit := m.question.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m.question = &q
	updated, cmd := m.update(commit())
	m = updated.(ChatModel)
	if m.home == nil || m.home.step != homeStepConnecting {
		t.Fatalf("want connecting, got %+v", m.home)
	}
	if !strings.Contains(homeTranscript(m), "Connecting to Home Assistant at http://ha.lan:8123") {
		t.Errorf("no connecting note:\n%s", homeTranscript(m))
	}
	m = runHome(t, m, cmd())

	if f.connectURL != "http://ha.lan:8123" || f.connectToken != homeTestToken {
		t.Errorf("sent url=%q token=%q", f.connectURL, f.connectToken)
	}
	text := homeTranscript(m)
	if !strings.Contains(text, "Connected: 28 Home Assistant tools.") || !strings.Contains(text, "/clear") {
		t.Errorf("success must show the detail and how to start a new chat:\n%s", text)
	}
	if m.home == nil || m.home.step != homeStepConnected {
		t.Fatalf("want the connected panel, got %+v", m.home)
	}
	assertNoToken(t, m)
}

func TestHomeRefusalShowsTheDetailAndKeepsTheAddress(t *testing.T) {
	const detail = "Home Assistant at http://ha.lan:8123 has no MCP server yet. Add the Model Context Protocol Server integration in Home Assistant (Settings › Devices & services › Add integration), then connect again."
	f := &fakeHome{connectErr: &homeassistant.StatusError{Op: "connect Home Assistant", Status: 400, Detail: detail}}
	m := openHome(t, homeModel(t, f))
	m = homeClearInput(t, m)
	m = homeAnswer(t, m, "http://ha.lan:8123")
	m = homeAnswer(t, m, homeTestToken)

	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || last.Content != detail {
		t.Errorf("want the detail verbatim as an error, got %v %q", last.Role, last.Content)
	}
	if m.question == nil || m.question.RequestID() != homeQAddress {
		t.Fatal("a refusal must put the address question back up")
	}
	if got := m.question.Value(); got != "http://ha.lan:8123" {
		t.Errorf("address = %q, want what the user typed", got)
	}
	assertNoToken(t, m)
}

func TestHomeConnectedShowsTheAddressTestAndDisconnect(t *testing.T) {
	f := &fakeHome{
		status: homeassistant.Status{Connected: true, URL: "http://ha.lan:8123", Active: true},
		test:   homeassistant.TestResult{OK: true, Detail: "Home Assistant answered with 28 tools."},
	}
	m := openHome(t, homeModel(t, f))
	if !strings.Contains(homeTranscript(m), "connected at http://ha.lan:8123") {
		t.Errorf("no address in the status:\n%s", homeTranscript(m))
	}
	if m.question == nil || m.question.RequestID() != homeQConnected {
		t.Fatal("want the connected panel")
	}
	view := m.View()
	for _, want := range []string{"Test", "Disconnect"} {
		if !strings.Contains(view, want) {
			t.Errorf("the panel does not offer %q:\n%s", want, view)
		}
	}

	m = homeKey(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("1")})
	if f.tests != 1 || !strings.Contains(homeTranscript(m), "answered with 28 tools") {
		t.Errorf("test ran %d times; transcript:\n%s", f.tests, homeTranscript(m))
	}
	if m.question == nil || m.question.RequestID() != homeQConnected {
		t.Fatal("after a test the panel comes back")
	}
}

func TestHomeFailedTestShowsTheDetailAsAnError(t *testing.T) {
	f := &fakeHome{
		status: homeassistant.Status{Connected: true, URL: "http://ha.lan:8123", Active: true},
		test:   homeassistant.TestResult{OK: false, Detail: "Home Assistant isn't answering at http://ha.lan:8123."},
	}
	m := openHome(t, homeModel(t, f))
	m = homeKey(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("1")})
	var found bool
	for _, msg := range m.messages {
		if msg.Role == RoleError && msg.Content == "Home Assistant isn't answering at http://ha.lan:8123." {
			found = true
		}
	}
	if !found {
		t.Errorf("the failed test's detail is missing:\n%s", homeTranscript(m))
	}
}

func TestHomeDisconnect(t *testing.T) {
	f := &fakeHome{
		status:     homeassistant.Status{Connected: true, URL: "http://ha.lan:8123", Active: true},
		disconnect: homeassistant.Status{Detail: "GAIA forgot this Home Assistant."},
	}
	m := openHome(t, homeModel(t, f))
	m = homeKey(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("2")})
	if f.disconnects != 1 {
		t.Fatalf("disconnect ran %d times", f.disconnects)
	}
	if m.home != nil || m.question != nil {
		t.Error("the panel must close after disconnecting")
	}
	if !strings.Contains(homeTranscript(m), "GAIA forgot this Home Assistant.") {
		t.Errorf("no disconnect detail:\n%s", homeTranscript(m))
	}
}

func TestHomeEscClosesWithoutSaving(t *testing.T) {
	f := &fakeHome{}
	m := openHome(t, homeModel(t, f))
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if m.home != nil || m.question != nil {
		t.Fatal("Esc must close /home")
	}
	if f.connectURL != "" {
		t.Error("Esc must not connect")
	}
	if !strings.Contains(homeTranscript(m), "nothing was saved") {
		t.Errorf("no close note:\n%s", homeTranscript(m))
	}
}

func TestHomeIsOnlyForTheFlagship(t *testing.T) {
	prev := newHomeAPI
	newHomeAPI = func() homeassistant.API { t.Fatal("must not call the sidecar"); return nil }
	t.Cleanup(func() { newHomeAPI = prev })
	m, _ := newTestModel(t) // the email agent
	updated, cmd := m.submit("/home")
	if cmd != nil {
		t.Fatal("a non-flagship agent must not start a check")
	}
	m = updated.(ChatModel)
	if m.messages[len(m.messages)-1].Role != RoleError {
		t.Error("want a refusal")
	}
	if m.availableCommandSet()["/home"] {
		t.Error("the palette must not offer /home to the email agent")
	}
}

func TestHomeIsOfferedInTheCommandPalette(t *testing.T) {
	m := homeModel(t, &fakeHome{})
	if !m.availableCommandSet()["/home"] {
		t.Error("the flagship's palette must offer /home")
	}
}

func assertNoToken(t *testing.T, m ChatModel) {
	t.Helper()
	if strings.Contains(homeTranscript(m), homeTestToken) {
		t.Error("the token reached the transcript")
	}
	if strings.Contains(m.View(), homeTestToken) {
		t.Error("the token is on screen")
	}
}
