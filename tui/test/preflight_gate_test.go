package test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// These tests cover the seam between the hub and the readiness gate: that a
// launch goes through the gate, that the gate's three answers land where they
// should, and that a failing precondition never reaches chat.

// --- a preflight transport under test control -------------------------------

// gateTransport answers the four calls the gate makes, from canned bodies. It
// exists so the seam can be driven without a daemon, a sidecar, or Lemonade.
type gateTransport struct {
	attachErr error
	// agentState is the state reported for the agent in /daemon/v1/agents.
	// Empty means the agent is not listed at all.
	agentState string
	// initStatus and initBody are the answer to GET /v1/<agent>/init.
	initStatus int
	initBody   map[string]any
	// connectors is the answer to GET /v1/<agent>/connectors.
	connectors map[string]any

	starts  int
	ensures int
}

func readyGateTransport() *gateTransport {
	return &gateTransport{
		agentState: "running",
		initStatus: http.StatusOK,
		initBody: map[string]any{
			"ready": true,
			"lemonade": map[string]any{
				"reachable": true, "base_url": "http://localhost:8000/api/v1",
				"version": "8.2.0", "min_version": "8.1.0", "compatible": true,
			},
			"model": map[string]any{
				"id": "Gemma-4-E4B-it-GGUF", "present": true, "ctx_size": 32768,
			},
		},
		connectors: map[string]any{
			"agent_id": "email",
			"providers": []map[string]any{
				{"provider": "google", "connected": true, "account_email": "user@gmail.com",
					"can_send": true, "scopes": []string{"gmail.send"}},
			},
		},
	}
}

func (g *gateTransport) Attach(context.Context) (preflight.DaemonInfo, error) {
	if g.attachErr != nil {
		return preflight.DaemonInfo{}, g.attachErr
	}
	return preflight.DaemonInfo{PID: 4242, Port: 13337, APIVersion: "1.1"}, nil
}

func (g *gateTransport) Start(ctx context.Context) (preflight.DaemonInfo, error) {
	g.starts++
	g.attachErr = nil
	return g.Attach(ctx)
}

func (g *gateTransport) EnsureAgent(context.Context, string) error {
	g.ensures++
	g.agentState = "running"
	return nil
}

func (g *gateTransport) Do(_ context.Context, _, path string, _ []byte) (preflight.Response, error) {
	switch {
	case path == daemon.APIPrefix+"/agents":
		agents := []map[string]any{}
		if g.agentState != "" {
			pid := 5150
			agents = append(agents, map[string]any{
				"agent_id": "email", "state": g.agentState, "pid": pid,
				"agent_version": "0.5.0", "api_version": "1.0",
			})
		}
		return jsonResponse(http.StatusOK, map[string]any{"agents": agents})
	case strings.HasSuffix(path, "/init"):
		return jsonResponse(g.initStatus, g.initBody)
	case strings.HasSuffix(path, "/connectors"):
		return jsonResponse(http.StatusOK, g.connectors)
	}
	return preflight.Response{}, fmt.Errorf("gateTransport: no canned answer for %s", path)
}

func (g *gateTransport) Stream(context.Context, string, string, []byte) (preflight.Stream, error) {
	return preflight.Stream{}, fmt.Errorf("gateTransport: streaming is not wired in this test")
}

func jsonResponse(status int, body map[string]any) (preflight.Response, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return preflight.Response{}, err
	}
	return preflight.Response{Status: status, Body: raw}, nil
}

// modelMissing turns the transport into the commonest real failure: everything
// up and running, the model never downloaded.
func (g *gateTransport) modelMissing() *gateTransport {
	g.initStatus = http.StatusServiceUnavailable
	g.initBody = map[string]any{
		"ready": false,
		"lemonade": map[string]any{
			"reachable": true, "base_url": "http://localhost:8000/api/v1",
			"version": "8.2.0", "min_version": "8.1.0", "compatible": true,
		},
		"model": map[string]any{"id": "Gemma-4-E4B-it-GGUF", "present": false},
		"hint":  "the model is not downloaded yet",
	}
	return g
}

// mailboxMissing leaves every generic row green with no mailbox connected — the
// state that asks the host to open a connector flow.
func (g *gateTransport) mailboxMissing() *gateTransport {
	g.connectors = map[string]any{"agent_id": "email", "providers": []map[string]any{}}
	return g
}

// --- a root driver ----------------------------------------------------------

type rootDriver struct {
	t   *testing.T
	m   root.RootModel
	cat *catalog.Catalog
}

// newRootDriver builds a root model whose gate is pointed at g, with the ready
// hold squeezed to keep the tests fast.
func newRootDriver(t *testing.T, g preflight.Transport, w, h int) *rootDriver {
	t.Helper()
	cat := catalog.NewCatalog()
	cat.MarkInstalled("email", "0.5.0")
	m := root.NewRootModelWithHub(cat, nil, false).
		WithPreflight(g, preflight.Options{ReadyHold: time.Millisecond})
	d := &rootDriver{t: t, m: m, cat: cat}
	d.send(windowSize(w, h))
	return d
}

func (d *rootDriver) send(msg tea.Msg) {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(root.RootModel)
	d.pump(cmd)
}

// pump runs every command the model produced, feeding results back in.
func (d *rootDriver) pump(cmd tea.Cmd) {
	d.t.Helper()
	queue := []tea.Cmd{cmd}
	for steps := 0; len(queue) > 0; steps++ {
		if steps > maxPumpSteps {
			d.t.Fatalf("command loop did not settle after %d steps", maxPumpSteps)
		}
		next := queue[0]
		queue = queue[1:]
		if next == nil {
			continue
		}
		msg := next()
		if msg == nil {
			continue
		}
		if batch, ok := msg.(tea.BatchMsg); ok {
			queue = append(queue, batch...)
			continue
		}
		// A spinner re-arms its own tick for as long as the screen is busy, and
		// the cursor blinks forever. Real Bubble Tea keeps ticking; a test loop
		// must not. Routing of a spinner tick is asserted on its own below.
		if isCursorBlink(msg) || isSpinnerTick(msg) {
			continue
		}
		updated, follow := d.m.Update(msg)
		d.m = updated.(root.RootModel)
		queue = append(queue, follow)
	}
}

func isSpinnerTick(msg tea.Msg) bool {
	_, ok := msg.(spinner.TickMsg)
	return ok
}

func (d *rootDriver) screen() string { return stripAnsi(d.m.View()) }

// flat is the screen with every run of whitespace collapsed, so an assertion is
// about what the screen says rather than where a line happened to wrap.
func (d *rootDriver) flat() string {
	return strings.Join(strings.Fields(d.screen()), " ")
}

func (d *rootDriver) view() string { return d.m.ControlSnapshot().View }

// launchEmail sends the message the hub sends when the user presses enter on an
// installed, daemon-backed agent.
func (d *rootDriver) launchEmail() {
	d.t.Helper()
	agent := d.cat.Get("email")
	if agent == nil {
		d.t.Fatal("the email agent is missing from the catalog")
	}
	if agent.Transport != catalog.TransportDaemon {
		d.t.Fatalf("email transport = %s, want daemon — the gate only guards relayed agents", agent.Transport)
	}
	d.send(hub.LaunchAgentMsg{Agent: *agent})
}

// --- the seam ---------------------------------------------------------------

// The bug this whole change exists to fix: a launch used to go straight to chat.
func TestLaunchingAnAgentShowsPreflightNotChat(t *testing.T) {
	// A gate that is still checking: the daemon answer is what the screen is
	// waiting on, so the first frame must be the gate.
	g := readyGateTransport()
	d := newRootDriver(t, g, 80, 24)

	// Deliberately unpumped: the point is the FIRST frame after the launch, not
	// where the checks eventually land.
	updated, _ := d.m.Update(hub.LaunchAgentMsg{Agent: *d.cat.Get("email")})
	d.m = updated.(root.RootModel)

	if got := d.view(); got != "preflight" {
		t.Fatalf("view after launch = %q, want preflight", got)
	}
	screen := d.flat()
	if !strings.Contains(screen, "Getting Email ready") {
		t.Errorf("the gate is not what got rendered:\n%s", d.screen())
	}
	if strings.Contains(screen, "Welcome to GAIA") {
		t.Error("chat opened without passing the gate")
	}
	if snap := d.m.ControlSnapshot(); snap.Agent != "email" {
		t.Errorf("snapshot agent = %q, want email", snap.Agent)
	}
}

// An all-green gate hands off on its own. It must reach chat — through the same
// launch path the hub used to call directly.
func TestAnAllGreenGateReachesChat(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 80, 24)
	d.launchEmail()

	if got := d.view(); got != "chat" {
		t.Fatalf("view after an all-green gate = %q, want chat\n%s", got, d.screen())
	}
	if snap := d.m.ControlSnapshot(); snap.Agent != "email" {
		t.Errorf("chat agent = %q, want email", snap.Agent)
	}
	if !strings.Contains(d.flat(), "Email") {
		t.Errorf("the chat view does not name the agent:\n%s", d.screen())
	}
	if got := d.cat.Get("email").Status; got != catalog.StatusActive {
		t.Errorf("catalog status after launch = %s, want active", got)
	}
}

// A proved-broken precondition must hold the user on the gate with something to
// do about it — and must not start the agent behind that failure.
func TestAFailingPreconditionKeepsTheUserOnTheGate(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)
	d.launchEmail()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view with a missing model = %q, want preflight\n%s", got, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"AI model",          // the row
		"not downloaded",    // what is wrong
		"run: gaia init",    // the remedy command
		"f download it now", // the one-key fix
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the gate does not show %q:\n%s", want, d.screen())
		}
	}

	// enter must not talk its way past a real blocker.
	d.send(keyEnter())
	if got := d.view(); got != "preflight" {
		t.Fatalf("enter past a blocked gate landed on %q", got)
	}
	if !strings.Contains(d.flat(), "cannot start yet") {
		t.Errorf("enter on a blocked gate said nothing:\n%s", d.screen())
	}
}

// esc backs out to a hub that still has a highlighted row (#2481), and says why
// the launch did not happen.
func TestCancellingTheGateReturnsToAUsableHub(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)

	// Drive the hub the way a user does, so the selection under test is a real
	// one rather than one the test set up.
	d.selectInHub("email")
	before := d.m.ControlSnapshot()
	d.send(keyEnter())
	if got := d.view(); got != "preflight" {
		t.Fatalf("enter in the hub landed on %q, want preflight", got)
	}

	d.send(keyEsc())
	snap := d.m.ControlSnapshot()
	if snap.View != "hub" {
		t.Fatalf("esc from the gate landed on %q, want hub\n%s", snap.View, d.screen())
	}
	if snap.SelectedAgentID == "" {
		t.Error("the hub came back with nothing selected")
	}
	if snap.SelectedAgentID != before.SelectedAgentID {
		t.Errorf("selection moved across the gate: %q → %q", before.SelectedAgentID, snap.SelectedAgentID)
	}
	if snap.HubTabIndex != before.HubTabIndex {
		t.Errorf("tab moved across the gate: %d → %d", before.HubTabIndex, snap.HubTabIndex)
	}
	if !strings.Contains(d.flat(), "did not start") {
		t.Errorf("the hub does not say why the launch stopped:\n%s", d.screen())
	}
	if got := d.cat.Get("email").Status; got == catalog.StatusActive {
		t.Error("a cancelled launch left the agent marked active")
	}
}

// selectInHub moves the hub cursor onto agentID using only keys and the control
// snapshot — the same surface automation drives.
func (d *rootDriver) selectInHub(agentID string) {
	d.t.Helper()
	for tab := 0; tab < 6; tab++ {
		snap := d.m.ControlSnapshot()
		for i, id := range snap.VisibleAgentIDs {
			if id != agentID {
				continue
			}
			for j := 0; j < i; j++ {
				d.send(keyDown())
			}
			if got := d.m.ControlSnapshot().SelectedAgentID; got != agentID {
				d.t.Fatalf("selecting %q landed on %q", agentID, got)
			}
			return
		}
		d.send(keyTab())
	}
	d.t.Fatalf("no tab in the hub lists %q", agentID)
}

// A gate that has been left must not launch the agent afterwards: the hold tick
// it scheduled can still be in flight.
func TestALateProceedFromAnAbandonedGateLaunchesNothing(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)
	d.launchEmail()
	d.send(keyEsc())
	if got := d.view(); got != "hub" {
		t.Fatalf("esc landed on %q, want hub", got)
	}

	d.send(preflight.ProceedMsg{AgentID: "email"})
	if got := d.view(); got != "hub" {
		t.Fatalf("a late ProceedMsg opened %q", got)
	}
	if got := d.cat.Get("email").Status; got == catalog.StatusActive {
		t.Error("a late ProceedMsg launched the agent anyway")
	}
}

// A subprocess agent has no daemon relay, so the gate has nothing to probe and
// must not invent four failures over a launch that works.
func TestASubprocessAgentIsNotGated(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 80, 24)
	d.send(hub.LaunchAgentMsg{Agent: catalog.Agent{
		ID: "bash", Name: "Bash", Status: catalog.StatusInstalled,
		Transport: catalog.TransportSubprocess, BinaryPath: "/bin/echo",
	}})
	if got := d.view(); got != "chat" {
		t.Fatalf("a subprocess launch landed on %q, want chat\n%s", got, d.screen())
	}
}

// --- the mailbox hand-off ---------------------------------------------------

// ConnectMailboxMsg must produce a real instruction. The TUI has no connector
// screen, so the honest answer is the exact command plus a way back.
func TestConnectMailboxNamesTheCommandToRun(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().mailboxMissing(), 80, 24)
	d.launchEmail()

	if got := d.view(); got != "preflight" {
		t.Fatalf("an unconnected mailbox landed on %q, want preflight\n%s", got, d.screen())
	}
	d.send(key("f"))

	snap := d.m.ControlSnapshot()
	if snap.Overlay != "connect-mailbox" {
		t.Fatalf("f on the mailbox row produced overlay %q\n%s", snap.Overlay, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"Connect a mailbox for Email",
		"gaia connectors connect google",
		"--grant-agent installed:email",
		"gmail.send",
		"Outlook",
		"https://amd-gaia.ai/docs/connectors/microsoft",
		"r re-check",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the hand-off does not show %q:\n%s", want, d.screen())
		}
	}
	// The gate's own advice for this row is "press f to choose" — repeating it
	// here would send the user back around the loop they just came through.
	if strings.Contains(screen, "press f to choose") {
		t.Errorf("the hand-off tells the user to press f again:\n%s", d.screen())
	}
	if strings.Contains(strings.ToLower(screen), "coming soon") {
		t.Errorf("the hand-off is a dead end:\n%s", d.screen())
	}
}

// A mailbox that is connected but cannot send is a different failure with a
// different command, and the hand-off must name that provider's own command
// rather than a chooser.
func TestConnectMailboxForAConnectedProviderShowsThatProvider(t *testing.T) {
	g := readyGateTransport()
	g.connectors = map[string]any{
		"agent_id": "email",
		"providers": []map[string]any{
			{"provider": "microsoft", "connected": true, "account_email": "user@outlook.com",
				"can_send": false, "scopes": []string{"Mail.Read"}},
		},
	}
	d := newRootDriver(t, g, 80, 24)
	d.launchEmail()
	d.send(key("f"))

	screen := d.flat()
	if !strings.Contains(screen, "Reconnect Outlook for Email") {
		t.Errorf("the hand-off does not name the connected provider:\n%s", d.screen())
	}
	if !strings.Contains(screen, "gaia connectors connect microsoft") {
		t.Errorf("the hand-off does not name the Outlook command:\n%s", d.screen())
	}
	if strings.Contains(screen, "connect google") {
		t.Errorf("the hand-off offers Gmail to an Outlook user:\n%s", d.screen())
	}
}

// esc dismisses the hand-off back to the gate — not out of the launch behind it.
func TestEscFromTheHandoffReturnsToTheGate(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().mailboxMissing(), 80, 24)
	d.launchEmail()
	d.send(key("f"))
	d.send(keyEsc())

	snap := d.m.ControlSnapshot()
	if snap.View != "preflight" || snap.Overlay != "" {
		t.Fatalf("esc from the hand-off left view=%q overlay=%q, want the bare gate",
			snap.View, snap.Overlay)
	}
	if !strings.Contains(d.flat(), "Getting Email ready") {
		t.Errorf("esc did not come back to the gate:\n%s", d.screen())
	}
}

// r from the hand-off re-checks, so a user who just connected a mailbox in
// another terminal is not told to press anything else.
func TestRFromTheHandoffRechecksAndProceeds(t *testing.T) {
	g := readyGateTransport().mailboxMissing()
	d := newRootDriver(t, g, 80, 24)
	d.launchEmail()
	d.send(key("f"))

	// The user connects the mailbox in another terminal.
	g.connectors = readyGateTransport().connectors
	d.send(key("r"))

	if got := d.view(); got != "chat" {
		t.Fatalf("re-checking a fixed mailbox landed on %q, want chat\n%s", got, d.screen())
	}
}

// --- routing and layout ----------------------------------------------------

// The gate is a spinner-driven screen. If ticks and resizes do not reach it, it
// freezes mid-check and a resized terminal renders at the old size.
func TestTheGateGetsSpinnerAndResizeMessages(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 80, 24)

	// Unpumped on purpose: this is the gate mid-check, which is the state whose
	// spinner the user actually watches.
	updated, _ := d.m.Update(hub.LaunchAgentMsg{Agent: *d.cat.Get("email")})
	d.m = updated.(root.RootModel)
	if !strings.Contains(d.flat(), "checking") {
		t.Fatalf("the gate is not mid-check:\n%s", d.screen())
	}

	before := d.screen()
	spun := false
	for i := 0; i < 12 && !spun; i++ {
		updated, _ := d.m.Update(spinner.TickMsg{Time: time.Now()})
		d.m = updated.(root.RootModel)
		spun = d.screen() != before
	}
	if !spun {
		t.Errorf("spinner ticks do not reach the gate — it renders frozen:\n%s", d.screen())
	}

	d.send(windowSize(120, 40))
	for _, line := range strings.Split(d.screen(), "\n") {
		if w := ansi.StringWidth(line); w > 120 {
			t.Fatalf("after a resize a line is %d columns wide", w)
		}
	}
	if !strings.Contains(d.screen(), strings.Repeat("─", 100)) {
		t.Errorf("the gate did not widen with the terminal:\n%s", d.screen())
	}
}

// The gate defaults to 80x24 internally, so a launch that forgets to hand it the
// real terminal size renders narrow on a wide terminal and nothing else fails.
func TestTheGateIsBornAtTheTerminalSize(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 100, 30)
	d.launchEmail()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view = %q, want preflight", got)
	}
	if !strings.Contains(d.screen(), strings.Repeat("─", 96)) {
		t.Errorf("the gate rendered narrower than the 100-column terminal:\n%s", d.screen())
	}
}

// The hub was fixed to fit the minimum terminal; the gate in front of it has to
// fit too, in every state a user can be in.
func TestEveryGateStateFitsEightyByTwentyFour(t *testing.T) {
	cases := []struct {
		name  string
		build func() *gateTransport
		keys  []tea.KeyMsg
	}{
		{"all ready", readyGateTransport, nil},
		{"model missing", func() *gateTransport { return readyGateTransport().modelMissing() }, nil},
		{"model missing, details", func() *gateTransport { return readyGateTransport().modelMissing() },
			[]tea.KeyMsg{key("d")}},
		{"mailbox missing", func() *gateTransport { return readyGateTransport().mailboxMissing() }, nil},
		{"mailbox hand-off", func() *gateTransport { return readyGateTransport().mailboxMissing() },
			[]tea.KeyMsg{key("f")}},
		{"daemon down", func() *gateTransport {
			g := readyGateTransport()
			g.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			return g
		}, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := newRootDriver(t, tc.build(), 80, 24)
			d.launchEmail()
			for _, k := range tc.keys {
				d.send(k)
			}
			lines := strings.Split(d.screen(), "\n")
			if len(lines) > 24 {
				t.Errorf("renders %d lines into 24 rows:\n%s", len(lines), d.screen())
			}
			for i, line := range lines {
				if w := ansi.StringWidth(line); w > 80 {
					t.Errorf("line %d is %d columns wide: %q", i, w, line)
				}
			}
		})
	}
}
