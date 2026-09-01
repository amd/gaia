package test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
	"github.com/amd/gaia/tui/internal/ui/status"
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
	// initByAgent overrides the init answer per agent id, so one gate can be
	// green while another is blocked.
	initByAgent map[string]initAnswer
	// connectors is the answer to GET /v1/<agent>/connectors.
	connectors map[string]any
	// healthStatus and healthBody are the answer to the sidecar row's liveness
	// probe, GET /v1/<agent>/health. The daemon's listing only proves a PROCESS
	// exists, so the gate asks the agent itself before probing anything below it.
	healthStatus int
	healthBody   map[string]any
	// searchStatus and searchBody are the answer to the mailbox row's credential
	// probe, POST /v1/<agent>/search. A connector list that says "connected and
	// granted" is not evidence the mailbox works, so the gate reads it — and this
	// fake has to answer, or the row reports "could not be verified".
	searchStatus int
	searchBody   map[string]any

	starts   int
	ensures  int
	healths  int
	searches int
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
				// At the profile window: below it the AI model row reports a shortfall,
				// which is a real answer but not the "everything ready" this fixture means.
				"id": "Gemma-4-E4B-it-GGUF", "present": true, "ctx_size": 65536,
			},
		},
		// The real shape: connector_routes returns the NAMESPACED grant id, and
		// flow.py stores scopes as full URIs — which is what the mailbox row
		// compares `can_send` against.
		connectors: map[string]any{
			"agent_id": "installed:email",
			"providers": []map[string]any{
				{"provider": "google", "connected": true, "account_email": "user@gmail.com",
					"can_send": true, "scopes": []string{
						"https://www.googleapis.com/auth/gmail.modify",
						"https://www.googleapis.com/auth/gmail.send",
					}},
			},
		},
		healthStatus: http.StatusOK,
		healthBody:   map[string]any{"status": "ok"},
		searchStatus: http.StatusOK,
		// EmailSearchResponse: `count` is required there, so a body without it is
		// not the shape the sidecar serializes.
		searchBody: map[string]any{
			"schema_version": "2.5", "count": 1,
			"messages": []map[string]any{
				{"id": "18f0", "subject": "Welcome", "from": "a@b.com", "label_ids": []string{"INBOX"}},
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
			for _, id := range []string{"email", "analyst"} {
				agents = append(agents, map[string]any{
					"agent_id": id, "state": g.agentState, "pid": pid,
					"agent_version": "0.5.0", "api_version": "1.0",
				})
			}
		}
		return jsonResponse(http.StatusOK, map[string]any{"agents": agents})
	case strings.HasSuffix(path, "/health"):
		g.healths++
		return jsonResponse(g.healthStatus, g.healthBody)
	case strings.HasSuffix(path, "/init"):
		if answer, ok := g.initByAgent[agentFromPath(path)]; ok {
			return jsonResponse(answer.status, answer.body)
		}
		return jsonResponse(g.initStatus, g.initBody)
	case strings.HasSuffix(path, "/connectors"):
		return jsonResponse(http.StatusOK, g.connectors)
	case strings.HasSuffix(path, "/search"):
		g.searches++
		return jsonResponse(g.searchStatus, g.searchBody)
	}
	return preflight.Response{}, fmt.Errorf("gateTransport: no canned answer for %s", path)
}

func (g *gateTransport) Stream(context.Context, string, string, []byte) (preflight.Stream, error) {
	return preflight.Stream{}, fmt.Errorf("gateTransport: streaming is not wired in this test")
}

// initAnswer is one agent's canned /init reply.
type initAnswer struct {
	status int
	body   map[string]any
}

// agentFromPath pulls the agent id out of "/v1/<agent>/init".
func agentFromPath(path string) string {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) >= 2 {
		return parts[1]
	}
	return ""
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

// sidecarWedged is the state the daemon's agent listing cannot see: the process
// is alive, registered and "running", and its event loop is parked, so it
// answers nothing. The daemon's pre-relay probe reports it on the next relayed
// call. VERBATIM from sidecars/manager.check_responsive.
func (g *gateTransport) sidecarWedged() *gateTransport {
	g.healthStatus = http.StatusServiceUnavailable
	g.healthBody = map[string]any{
		"detail": "email sidecar (pid 41999) is alive but did not answer " +
			"http://127.0.0.1:51234/health within 2.0s (ReadTimeout: ). It passed its " +
			"startup health check, so it has stopped serving since — typically a blocked " +
			"event loop or a hung dependency.",
	}
	return g
}

// mailboxCredentialsRejected is the state the connector list cannot see: linked,
// granted, and the first read refused. The gate must stop here.
func (g *gateTransport) mailboxCredentialsRejected() *gateTransport {
	g.searchStatus = http.StatusBadGateway
	g.searchBody = map[string]any{
		"detail": "no forwarded 'google' credential is available to the email sidecar. " +
			"The connection may not be granted to this agent, or it was revoked/withdrawn.",
	}
	return g
}

// ctxShortfall leaves every generic row green except the model, loaded with a
// window under the profile's target — the reported bug: an agent that works
// for ordinary turns and fails a document-sized request. 25037 mirrors the
// value check_test.go's initCtxShortfall fixture already relies on being
// below profileCtxTarget() in the test environment.
func (g *gateTransport) ctxShortfall() *gateTransport {
	g.initBody = map[string]any{
		"ready": true,
		"lemonade": map[string]any{
			"reachable": true, "base_url": "http://localhost:8000/api/v1",
			"version": "8.2.0", "min_version": "8.1.0", "compatible": true,
		},
		"model": map[string]any{
			"id": "Gemma-4-E4B-it-GGUF", "present": true, "ctx_size": 25037,
		},
	}
	return g
}

// --- a root driver ----------------------------------------------------------

type rootDriver struct {
	t   *testing.T
	m   root.RootModel
	cat *catalog.Catalog
	tr  preflight.Transport
}

// transport is the fake the gate is pointed at, for asserting what the gate did
// or did not ask the daemon to do.
func (d *rootDriver) transport() *gateTransport {
	g, ok := d.tr.(*gateTransport)
	if !ok {
		d.t.Fatalf("the driver's transport is %T, not a gateTransport", d.tr)
	}
	return g
}

// newRootDriver builds a root model whose gate is pointed at g, with the ready
// hold squeezed to keep the tests fast.
func newRootDriver(t *testing.T, g preflight.Transport, w, h int) *rootDriver {
	t.Helper()
	return newRootDriverOpts(t, g, w, h, preflight.Options{ReadyHold: time.Millisecond})
}

// newRootDriverOpts is newRootDriver with the gate's options spelled out —
// ManualProceed keeps an all-green gate on screen so it can be looked at.
func newRootDriverOpts(t *testing.T, g preflight.Transport, w, h int, opts preflight.Options) *rootDriver {
	t.Helper()
	cat := catalog.NewCatalog()
	cat.MarkInstalled("email", "0.5.0")
	m := root.NewRootModelWithHub(cat, nil, false).WithPreflight(g, opts)
	d := &rootDriver{t: t, m: m, cat: cat, tr: g}
	d.send(windowSize(w, h))
	return d
}

func (d *rootDriver) send(msg tea.Msg) {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(root.RootModel)
	d.pump(cmd)
}

// sendNoPump delivers one message and hands back the command it produced WITHOUT
// running it — for the tests where the point is work that is still in flight.
func (d *rootDriver) sendNoPump(msg tea.Msg) tea.Cmd {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(root.RootModel)
	return cmd
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
	// "Did not launch" is about the agent, not just the view: nothing may have
	// asked the daemon to spawn a sidecar behind that failure.
	if g := d.transport(); g.ensures != 0 || g.starts != 0 {
		t.Errorf("a blocked gate started things anyway: %d ensures, %d daemon starts", g.ensures, g.starts)
	}
	if got := d.cat.Get("email").Status; got == catalog.StatusActive {
		t.Error("a blocked gate marked the agent active")
	}
}

// The mailbox bug: the connector list said connected + can_send, the gate showed
// 5 of 5 ready, and the first triage came back with a credential error. A row
// that is only ever as true as a stored record must not green-light a launch.
func TestAMailboxWhoseCredentialsAreRejectedNeverGreenLightsALaunch(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().mailboxCredentialsRejected(), 80, 24)
	d.launchEmail()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view with a dead mailbox credential = %q, want preflight\n%s", got, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"Mailbox",
		"sign-in no longer works",
		"no forwarded 'google' credential is available",
		"run: gaia connectors connect google --grant-agent installed:email",
		"f connect a mailbox",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the gate does not show %q:\n%s", want, d.screen())
		}
	}
	// The old wording, which is what made it look fine.
	if strings.Contains(screen, "· can read and send") || strings.Contains(screen, "· can send") {
		t.Errorf("the mailbox row still claims a working mailbox:\n%s", d.screen())
	}
	if g := d.transport(); g.searches == 0 {
		t.Error("the gate passed the mailbox row without reading the mailbox")
	}
	// Never automatic — but the user may choose it. The agent repairs this state
	// inside the conversation, so refusing the choice would hide the fix behind
	// the gate that found the problem.
	if !strings.Contains(screen, "continue") {
		t.Errorf("the gate hid the launch that reaches the in-conversation fix:\n%s", d.screen())
	}
	d.send(keyEnter())
	if got := d.view(); got != "chat" {
		t.Fatalf("enter over a repairable mailbox = %q, want chat\n%s", got, d.screen())
	}
}

// An agent that is "running" and answers nothing must be named as such. The gate
// used to show `[ok] Email agent 0.5.0 · running` and then blame the mailbox for
// the timeout that followed — sending the user to the one part that was fine.
func TestAnAgentThatAnswersNothingIsNamedOnItsOwnRow(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().sidecarWedged(), 80, 24)
	d.launchEmail()

	if got := d.view(); got != "preflight" {
		t.Fatalf("a wedged agent landed on %q, want preflight\n%s", got, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"Email agent", "not answering",
		"run: gaia daemon stop-agent email && gaia daemon start-agent email",
		"~/.gaia/agents/email/logs/",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the gate does not show %q:\n%s", want, d.screen())
		}
	}
	// The rows below it were never asked, so none of them may be blamed.
	if strings.Contains(screen, "cannot be checked") {
		t.Errorf("a row below the wedged agent reported a failure of its own:\n%s", d.screen())
	}
	if g := d.transport(); g.searches != 0 {
		t.Errorf("the gate read the mailbox through an agent that answers nothing: %d reads", g.searches)
	}
	// And it never claims the agent is fine.
	if strings.Contains(screen, "0.5.0 · running (pid") {
		t.Errorf("the agent row still reads as healthy:\n%s", d.screen())
	}
}

// The whole point of the probe is that it is the LAST thing tried: nothing pays
// for a live mailbox read while an earlier row is already broken.
func TestTheMailboxIsNotReadWhenAnEarlierRowFailed(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)
	d.launchEmail()

	if g := d.transport(); g.searches != 0 {
		t.Errorf("the gate read the mailbox behind a missing model: %d reads", g.searches)
	}
}

// `f` on a stopped sidecar is the gate's most-used fix. Root has to carry the
// fix's result back to it, or the screen spins forever on work that finished.
func TestFixingAStoppedSidecarFromTheGateReachesChat(t *testing.T) {
	g := readyGateTransport()
	g.agentState = "stopped"
	d := newRootDriver(t, g, 80, 24)
	d.launchEmail()

	screen := d.flat()
	if !strings.Contains(screen, "installed, not started") {
		t.Fatalf("the gate does not report the stopped sidecar:\n%s", d.screen())
	}
	if !strings.Contains(screen, "f start the agent") {
		t.Errorf("the stopped-sidecar row offers no fix:\n%s", d.screen())
	}

	d.send(key("f"))
	if g.ensures != 1 {
		t.Fatalf("f asked the daemon to start the agent %d times, want 1", g.ensures)
	}
	// The fix re-checks and everything else was already green, so it hands off.
	if got := d.view(); got != "chat" {
		t.Fatalf("after the fix the gate landed on %q, want chat\n%s", got, d.screen())
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

// analystAgent is a second daemon-backed agent, for the two-gates race below.
func analystAgent() catalog.Agent {
	return catalog.Agent{
		ID: "analyst", Name: "Analyst", Status: catalog.StatusInstalled,
		Transport: catalog.TransportDaemon,
	}
}

// The nastiest race in this seam: a probe started for one agent, answered after
// the user backed out and launched another. Its report must not drive the second
// agent's gate — an all-green report for A would otherwise green-light B and open
// a chat for an agent nothing ever probed.
func TestAnAbandonedGatesReportCannotGreenLightAnotherAgent(t *testing.T) {
	g := readyGateTransport()
	// Email would pass; Analyst is blocked on its model. So if Email's stale
	// report reaches Analyst's gate, it turns a blocked screen into a launch.
	g.initByAgent = map[string]initAnswer{
		"analyst": {status: http.StatusServiceUnavailable, body: readyGateTransport().modelMissing().initBody},
	}
	d := newRootDriver(t, g, 80, 24)

	// Gate 1 for email, its probe captured and NOT run yet — this is the in-flight
	// probe the user walks away from.
	emailProbe := d.sendNoPump(hub.LaunchAgentMsg{Agent: *d.cat.Get("email")})
	d.send(keyEsc())

	// Gate 2 for a different agent, blocked and waiting for the user.
	d.send(hub.LaunchAgentMsg{Agent: analystAgent()})
	if got := d.view(); got != "preflight" {
		t.Fatalf("the second launch landed on %q, want preflight\n%s", got, d.screen())
	}

	// Email's probe finally answers, into Analyst's gate.
	d.pump(emailProbe)

	if got := d.view(); got != "preflight" {
		t.Fatalf("a stale report launched %q\n%s", got, d.screen())
	}
	screen := d.flat()
	if !strings.Contains(screen, "Getting Analyst ready") {
		t.Errorf("the gate is no longer the one that was on screen:\n%s", d.screen())
	}
	// The Mailbox row exists only in email's report: seeing it here means the
	// stale report was adopted.
	if strings.Contains(screen, "Mailbox") {
		t.Errorf("email's rows are being shown on the Analyst gate:\n%s", d.screen())
	}
	if !strings.Contains(screen, "not downloaded") {
		t.Errorf("Analyst's own blocked row was replaced:\n%s", d.screen())
	}
	if got := d.cat.Get("email").Status; got == catalog.StatusActive {
		t.Error("the abandoned gate launched email anyway")
	}
}

// A subprocess agent has no daemon relay, so the gate has nothing to probe and
// must not invent four failures over a launch that works.
func TestASubprocessAgentIsNotGated(t *testing.T) {
	// The test binary itself is the one executable path guaranteed to resolve
	// on every OS — /bin/echo does not exist on Windows, and the client never
	// spawns it here anyway.
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}
	d := newRootDriver(t, readyGateTransport(), 80, 24)
	d.send(hub.LaunchAgentMsg{Agent: catalog.Agent{
		ID: "bash", Name: "Bash", Status: catalog.StatusInstalled,
		Transport: catalog.TransportSubprocess, BinaryPath: self,
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

// On a terminal too short for the whole hand-off, the command is the last thing
// that may be dropped — a screen that keeps the explanation and loses the fix
// names a problem and takes away the answer.
func TestAShortTerminalKeepsTheHandoffCommand(t *testing.T) {
	for _, rows := range []int{24, 16, 12, 10} {
		d := newRootDriver(t, readyGateTransport().mailboxMissing(), 80, 24)
		d.launchEmail()
		d.send(key("f"))
		d.send(windowSize(80, rows))

		screen := d.screen()
		if got := len(strings.Split(screen, "\n")); got > rows {
			t.Errorf("at %d rows the hand-off renders %d lines:\n%s", rows, got, screen)
		}
		flat := strings.Join(strings.Fields(screen), " ")
		// The whole command, scopes included — a half-command is worse than none.
		for _, want := range []string{
			"gaia connectors connect google --grant-agent installed:email",
			"gmail.send",
			"esc back to the checks",
		} {
			if !strings.Contains(flat, want) {
				t.Errorf("at %d rows the hand-off lost %q:\n%s", rows, want, screen)
			}
		}
	}
}

// If the check ever produces a command for a different provider than the mailbox
// it is describing, the screen must not let its own title vouch for it.
func TestAMismatchedProviderCommandIsFlagged(t *testing.T) {
	g := readyGateTransport()
	// A provider the connect-command builder has no scope list for: it falls back
	// to the Google command, which is not what this mailbox needs.
	g.connectors = map[string]any{
		"agent_id": "email",
		"providers": []map[string]any{
			{"provider": "yahoo", "connected": true, "account_email": "user@yahoo.com",
				"can_send": false, "scopes": []string{}},
		},
	}
	d := newRootDriver(t, g, 80, 24)
	d.launchEmail()
	d.send(key("f"))

	flat := d.flat()
	if !strings.Contains(flat, "Heads up") {
		t.Errorf("a Gmail command under a yahoo mailbox is presented as correct:\n%s", d.screen())
	}
	if !strings.Contains(flat, "report it") {
		t.Errorf("the mismatch is not reportable:\n%s", d.screen())
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
//
// Row count alone is a tautology here — both views clamp themselves to the height
// they are given, so they can always "fit" by dropping the very thing the user
// needs. Each case therefore names what has to still be on screen at 80x24.
func TestEveryGateStateFitsEightyByTwentyFour(t *testing.T) {
	cases := []struct {
		name  string
		build func() *gateTransport
		keys  []tea.KeyMsg
		// hold keeps an all-green gate on screen; without it the case would
		// measure the chat view the gate hands off to.
		hold bool
		// wants is what must survive the fit at the minimum size.
		wants []string
	}{
		{name: "all ready", build: readyGateTransport, hold: true,
			wants: []string{"Getting Email ready", "ready", "Mailbox", "esc back"}},
		{name: "model missing", build: func() *gateTransport { return readyGateTransport().modelMissing() },
			wants: []string{"AI model", "not downloaded", "run: gaia init", "esc back"}},
		{name: "model missing, details", build: func() *gateTransport { return readyGateTransport().modelMissing() },
			keys:  []tea.KeyMsg{key("d")},
			wants: []string{"AI model — failed", "esc back"}},
		{name: "mailbox missing", build: func() *gateTransport { return readyGateTransport().mailboxMissing() },
			wants: []string{"Mailbox", "not connected", "gaia connectors connect google", "esc back"}},
		{name: "mailbox hand-off", build: func() *gateTransport { return readyGateTransport().mailboxMissing() },
			keys:  []tea.KeyMsg{key("f")},
			wants: []string{"Connect a mailbox for Email", "gmail.send", "esc back to the checks"}},
		{name: "daemon down", build: func() *gateTransport {
			g := readyGateTransport()
			g.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			return g
		}, wants: []string{"Background service", "not running", "f start it for me", "esc back"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := preflight.Options{ReadyHold: time.Millisecond, ManualProceed: tc.hold}
			d := newRootDriverOpts(t, tc.build(), 80, 24, opts)
			d.launchEmail()
			for _, k := range tc.keys {
				d.send(k)
			}
			if got := d.view(); got != "preflight" {
				t.Fatalf("this case is not measuring the gate — view = %q\n%s", got, d.screen())
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
			flat := strings.Join(strings.Fields(d.screen()), " ")
			for _, want := range tc.wants {
				if !strings.Contains(flat, want) {
					t.Errorf("at 80x24 the screen lost %q:\n%s", want, d.screen())
				}
			}
		})
	}
}

// --- the halt flag ------------------------------------------------------------
//
// A DispositionHalt row makes the readiness screen hold itself and explain
// why — that is entirely increment 3's doing, already covered in
// internal/ui/preflight. What lives here is the flag RootModel derives from
// it: a state signal for automation (ControlSnapshot's Overlay), nothing
// drawn, nothing intercepted. TestACtxShortfallHaltsTheRealGate drives the
// real Check() pipeline for the row this issue exists to fix; the rest
// inject a synthetic status.Outcome directly.

// The reported bug, through the real gate end to end: a ctx-shortfall report
// halts (Overlay reports it without blinding the view, and the screen names
// the window that will fail), and the SAME single enter that the screen has
// always offered is what both proceeds and clears the flag — there is no
// separate dismiss to press first.
func TestACtxShortfallHaltsTheRealGate(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchEmail()

	if !d.m.Halted() {
		t.Fatalf("a ctx-shortfall report did not halt:\n%s", d.screen())
	}
	snap := d.m.ControlSnapshot()
	if snap.Overlay != "halt" {
		t.Errorf("Overlay = %q, want %q", snap.Overlay, "halt")
	}
	if snap.View != "preflight" {
		t.Errorf("View = %q, want %q — a halt must not blind the automation's view", snap.View, "preflight")
	}
	if !strings.Contains(d.flat(), "25037") {
		t.Errorf("the screen does not show the window that will fail:\n%s", d.screen())
	}

	// One enter — the screen's own "continue" choice — both proceeds AND
	// clears the flag. There is no separate prompt in front of it.
	d.send(keyEnter())
	if got := d.view(); got != "chat" {
		t.Fatalf("enter on the holding gate = %q, want chat", got)
	}
	if d.m.Halted() {
		t.Error("Halted() stayed true after the screen proceeded")
	}
	if d.m.ControlSnapshot().Overlay == "halt" {
		t.Error("Overlay still reports halt after proceeding")
	}
}

// ctrl+c quits on the FIRST press while halted — RootModel does not
// intercept it, so this is really proving the preflight screen's own
// ctrl+c handling (unrelated to this issue) still works while HasHalt()
// holds it open.
func TestCtrlCQuitsOnFirstPressWhileHalted(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchEmail()
	if !d.m.Halted() {
		t.Fatal("test setup: want the gate halted")
	}

	cmd := d.sendNoPump(tea.KeyMsg{Type: tea.KeyCtrlC})
	if cmd == nil {
		t.Fatal("ctrl+c while halted produced no command")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatalf("ctrl+c while halted produced %T, want tea.QuitMsg", cmd())
	}
}

// Re-checking a still-holding gate (r) never needed a fix here: the screen
// was never blocked from receiving r in the first place, so hitting it
// leaves the flag exactly as it was — still Halted(), still the same row.
func TestReCheckOnAHoldingGateStaysHalted(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchEmail()
	if !d.m.Halted() {
		t.Fatal("test setup: want the gate halted")
	}

	d.send(key("r"))
	if !d.m.Halted() {
		t.Error("re-checking a row that is still genuinely bad cleared the halt")
	}
	if got := d.view(); got != "preflight" {
		t.Fatalf("view after re-check = %q, want preflight", got)
	}
}

// Proceeding past a halt suppresses that StepID for the rest of the
// session: the screen still pauses on every launch (that pause is the
// feature, not something suppression touches), but automation's "a NEW,
// unhandled halt" signal does not fire twice for a row the user already
// chose to proceed past once — without this, a permanently-unknown row
// (this is exactly one) would report a fresh halt on every single relaunch,
// reintroducing the confirm-fatigue the whole Notify/Halt split exists to
// avoid.
func TestProceedingPastAHaltSuppressesTheFlagForTheRestOfTheSession(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchEmail()
	if !d.m.Halted() {
		t.Fatal("test setup: want the gate halted")
	}

	d.send(keyEnter())
	if got := d.view(); got != "chat" {
		t.Fatalf("enter on the holding gate = %q, want chat", got)
	}
	if d.m.Halted() {
		t.Error("Halted() stayed true after the screen proceeded")
	}

	d.send(chat.ReturnToHubMsg{AgentID: "email"})
	d.launchEmail()

	// The screen itself still pauses every launch on the same row —
	// unrelated to suppression, and not something this issue changes.
	if got := d.view(); got != "preflight" {
		t.Fatalf("relaunch view = %q, want preflight — the screen still pauses on the same row", got)
	}
	// But the flag does not fire again for a row already accepted this
	// session.
	if d.m.Halted() {
		t.Error("the same row set Halted() again after the user already proceeded past it this session")
	}
}

// A resize (or any non-key message) must still reach the gate while
// Halted() — RootModel never gates message routing on the flag, only
// ControlSnapshot reads it. Proven by the rendered width actually
// narrowing, which only happens if the message reached the preflight
// model's own Update.
func TestNonKeyMessagesPassThroughWhileHalted(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchEmail()
	if !d.m.Halted() {
		t.Fatal("test setup: want the gate halted")
	}

	d.send(tea.WindowSizeMsg{Width: 40, Height: 20})
	for i, line := range strings.Split(d.screen(), "\n") {
		if w := ansi.StringWidth(line); w > 40 {
			t.Fatalf("line %d is %d columns wide after a 40-column resize while halted — "+
				"the resize did not reach the screen:\n%s", i, w, d.screen())
		}
	}
}

// A spinner.TickMsg specifically must reach a gate that is still Busy() —
// the acceptance criterion's literal case. sendNoPump keeps the gate's
// Init() from running so it is still phaseChecking when the halt lands,
// which only a synthetic Outcome can arrange (the real pipeline cannot halt
// before its own report exists).
func TestASpinnerTickReachesABusyGateWhileHalted(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 100, 30)
	agent := d.cat.Get("email")
	if agent == nil {
		t.Fatal("test setup: the email agent is missing from the catalog")
	}
	d.sendNoPump(hub.LaunchAgentMsg{Agent: *agent})
	if d.view() != "preflight" {
		t.Fatalf("test setup: want preflight, got %q", d.view())
	}

	d.send(status.Outcome{
		StepID: "synthetic", Label: "Synthetic",
		Level: status.LevelUnknown, Disposition: status.DispositionHalt, Summary: "test",
	})
	if !d.m.Halted() {
		t.Fatal("test setup: the synthetic Outcome did not halt")
	}

	if cmd := d.sendNoPump(spinner.TickMsg{}); cmd == nil {
		t.Fatal("a spinner tick produced no command while halted — it did not reach the checking gate")
	}
}

// The flag sets the same way from either screen it can be raised from — it
// is not wired to one view.
func TestCrossScreenHaltingWorksFromHubAndFromPreflight(t *testing.T) {
	t.Run("hub", func(t *testing.T) {
		d := newRootDriver(t, readyGateTransport(), 100, 30)
		if d.view() != "hub" {
			t.Fatalf("test setup: want hub, got %q", d.view())
		}
		d.send(status.Outcome{StepID: "hub-synthetic", Label: "Synthetic",
			Level: status.LevelFailed, Disposition: status.DispositionHalt, Summary: "test"})
		if !d.m.Halted() {
			t.Error("a synthetic halting Outcome while in the hub did not halt")
		}
	})
	t.Run("preflight", func(t *testing.T) {
		// ManualProceed keeps an all-green gate parked on the preflight
		// screen instead of auto-proceeding to chat during the synchronous
		// pump — this sub-test is about the SCREEN, not the report.
		opts := preflight.Options{ReadyHold: time.Millisecond, ManualProceed: true}
		d := newRootDriverOpts(t, readyGateTransport(), 100, 30, opts)
		d.launchEmail()
		if d.view() != "preflight" {
			t.Fatalf("test setup: want preflight, got %q", d.view())
		}
		d.send(status.Outcome{StepID: "preflight-synthetic", Label: "Synthetic",
			Level: status.LevelFailed, Disposition: status.DispositionHalt, Summary: "test"})
		if !d.m.Halted() {
			t.Error("a synthetic halting Outcome while in preflight did not halt")
		}
	})
}
