package test

import (
	"context"
	"encoding/json"
	"errors"
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
	"github.com/amd/gaia/tui/internal/ui/modelsetup"
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
	// healthStatus and healthBody are the answer to the sidecar row's liveness
	// probe, GET /v1/<agent>/health. The daemon's listing only proves a PROCESS
	// exists, so the gate asks the agent itself before probing anything below it.
	healthStatus int
	healthBody   map[string]any

	starts  int
	ensures int
	healths int

	// startErr and ensureErr make the daemon or agent start fail, the way a
	// machine without `gaia` on PATH would.
	startErr  error
	ensureErr error

	// lemonadeStarts counts POST /daemon/v1/lemonade/start. lemonadeStartErr is
	// what that call answers; onLemonadeStart lets a test repair initBody the
	// way a real start would, so both "it fixed it" and "it did not" are
	// reachable from the same fake.
	lemonadeStarts   int
	lemonadeStartErr error
	onLemonadeStart  func(*gateTransport)
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
				// At the profile window: below it the language model row reports a shortfall,
				// which is a real answer but not the "everything ready" this fixture means.
				"id": "Gemma-4-E4B-it-GGUF", "present": true, "ctx_size": 65536,
			},
		},
		healthStatus: http.StatusOK,
		healthBody:   map[string]any{"status": "ok"},
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
	if g.startErr != nil {
		return preflight.DaemonInfo{}, g.startErr
	}
	g.attachErr = nil
	return g.Attach(ctx)
}

func (g *gateTransport) EnsureAgent(context.Context, string) error {
	g.ensures++
	if g.ensureErr != nil {
		return g.ensureErr
	}
	g.agentState = "running"
	return nil
}

// StartLemonade records that the gate asked the daemon to start the local model
// server, and reports success. lemonadeUp then decides what the next /init says,
// so a test can model both "the start fixed it" and "it did not".
func (g *gateTransport) StartLemonade(context.Context) error {
	g.lemonadeStarts++
	if g.lemonadeStartErr != nil {
		return g.lemonadeStartErr
	}
	if g.onLemonadeStart != nil {
		g.onLemonadeStart(g)
	}
	return nil
}

func (g *gateTransport) Do(_ context.Context, _, path string, _ []byte) (preflight.Response, error) {
	switch {
	case path == daemon.APIPrefix+"/agents":
		agents := []map[string]any{}
		if g.agentState != "" {
			pid := 5150
			for _, id := range []string{sidecarID, "analyst"} {
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

// sidecarWedged is the state the daemon's agent listing cannot see: the process
// is alive, registered and "running", and its event loop is parked, so it
// answers nothing. The daemon's pre-relay probe reports it on the next relayed
// call. VERBATIM from sidecars/manager.check_responsive.
func (g *gateTransport) sidecarWedged() *gateTransport {
	g.healthStatus = http.StatusServiceUnavailable
	g.healthBody = map[string]any{
		"detail": "notes sidecar (pid 41999) is alive but did not answer " +
			"http://127.0.0.1:51234/health within 2.0s (ReadTimeout: ). It passed its " +
			"startup health check, so it has stopped serving since — typically a blocked " +
			"event loop or a hung dependency.",
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
	m   root.FlagshipModel
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
	// Full motion: go test's output is not a terminal, which would otherwise
	// switch the gate's spinner off.
	return newRootDriverOpts(t, g, w, h, preflight.Options{ReadyHold: time.Millisecond, Motion: &modelsetup.Motion{}})
}

// newRootDriverOpts is newRootDriver with the gate's options spelled out —
// ManualProceed keeps an all-green gate on screen so it can be looked at.
func newRootDriverOpts(t *testing.T, g preflight.Transport, w, h int, opts preflight.Options) *rootDriver {
	t.Helper()
	return newRootDriverFor(t, sidecarAgent(), g, w, h, opts)
}

// newRootDriverFor builds the launch router around one agent. sidecarAgent is
// daemon-backed, so it is what exercises the daemon runner end to end; the
// local runner has its own driver (see flagship_test.go).
func newRootDriverFor(t *testing.T, agent catalog.Agent, g preflight.Transport, w, h int, opts preflight.Options) *rootDriver {
	t.Helper()
	cat := catalog.NewCatalog()
	m := root.NewFlagshipModel(agent, false).WithPreflight(g, opts)
	d := &rootDriver{t: t, m: m, cat: cat, tr: g}
	d.send(windowSize(w, h))
	return d
}

// sidecarID is a hub-installed daemon sidecar: GAIA ships only the flagship,
// which the TUI spawns itself, so the daemon gate is driven with this one.
const sidecarID = "notes"

// sidecarAgent is the daemon-transport entry the gate guards.
func sidecarAgent() catalog.Agent {
	return catalog.Agent{
		ID: sidecarID, Name: "Notes", Icon: "📦", Category: "general",
		Status: catalog.StatusInstalled, Transport: catalog.TransportDaemon,
	}
}

func (d *rootDriver) send(msg tea.Msg) {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(root.FlagshipModel)
	d.pump(cmd)
}

// sendNoPump delivers one message and hands back the command it produced WITHOUT
// running it — for the tests where the point is work that is still in flight.
func (d *rootDriver) sendNoPump(msg tea.Msg) tea.Cmd {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(root.FlagshipModel)
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
		d.m = updated.(root.FlagshipModel)
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

// beginNoPump starts the launch and stops at the FIRST frame of the gate,
// before any probe has answered — the state whose spinner a user actually
// watches. Deliberately unpumped: pumping would run the checks to completion.
func (d *rootDriver) beginNoPump() root.FlagshipModel {
	d.t.Helper()
	msg := d.m.Init()()
	updated, _ := d.m.Update(msg)
	d.m = updated.(root.FlagshipModel)
	return d.m
}

// launchSidecar starts the launch the router runs on Init: splash, then the gate.
func (d *rootDriver) launchSidecar() {
	d.t.Helper()
	d.pump(d.m.Init())
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
	d.m = d.beginNoPump()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view after launch = %q, want preflight", got)
	}
	screen := d.flat()
	if !strings.Contains(screen, "Checking Notes setup") {
		t.Errorf("the gate is not what got rendered:\n%s", d.screen())
	}
	if strings.Contains(screen, "Welcome to GAIA") {
		t.Error("chat opened without passing the gate")
	}
	if snap := d.m.ControlSnapshot(); snap.Agent != sidecarID {
		t.Errorf("snapshot agent = %q, want %s", snap.Agent, sidecarID)
	}
}

// An all-green gate hands off on its own. It must reach chat — through the same
// launch path the hub used to call directly.
func TestAnAllGreenGateReachesChat(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 80, 24)
	d.launchSidecar()

	if got := d.view(); got != "chat" {
		t.Fatalf("view after an all-green gate = %q, want chat\n%s", got, d.screen())
	}
	if snap := d.m.ControlSnapshot(); snap.Agent != sidecarID {
		t.Errorf("chat agent = %q, want %s", snap.Agent, sidecarID)
	}
	// The identity chip names the stable id, not the display name — that is
	// the string --agent takes back.
	if !strings.Contains(d.flat(), "agent "+sidecarID) {
		t.Errorf("the chat view does not name the agent:\n%s", d.screen())
	}
}

// A proved-broken precondition must hold the user on the gate with something to
// do about it — and must not start the agent behind that failure.
func TestAFailingPreconditionKeepsTheUserOnTheGate(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)
	d.launchSidecar()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view with a missing model = %q, want preflight\n%s", got, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"Language model",    // the row
		"not downloaded",    // what is wrong
		"or run: gaia init", // the remedy command, now the alternative
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
}

// An agent that is "running" and answers nothing must be named as such, not
// shown as `[ok] … agent 0.5.0 · running` over a timeout further down.
func TestAnAgentThatAnswersNothingIsNamedOnItsOwnRow(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().sidecarWedged(), 80, 24)
	d.launchSidecar()

	if got := d.view(); got != "preflight" {
		t.Fatalf("a wedged agent landed on %q, want preflight\n%s", got, d.screen())
	}
	screen := d.flat()
	for _, want := range []string{
		"Notes agent", "not answering",
		"run: gaia daemon stop-agent notes && gaia daemon start-agent notes",
		"~/.gaia/agents/notes/logs/",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the gate does not show %q:\n%s", want, d.screen())
		}
	}
	// The rows below it were never asked, so none of them may be blamed.
	if strings.Contains(screen, "cannot be checked") {
		t.Errorf("a row below the wedged agent reported a failure of its own:\n%s", d.screen())
	}
	// And it never claims the agent is fine.
	if strings.Contains(screen, "0.5.0 · running (pid") {
		t.Errorf("the agent row still reads as healthy:\n%s", d.screen())
	}
}

// `f` on a stopped sidecar is the gate's most-used fix. Root has to carry the
// fix's result back to it, or the screen spins forever on work that finished.
func TestAStoppedSidecarIsStartedWithoutAKeypress(t *testing.T) {
	g := readyGateTransport()
	g.agentState = "stopped"
	d := newRootDriver(t, g, 80, 24)
	d.launchSidecar()

	if g.ensures != 1 {
		t.Fatalf("the gate asked the daemon to start the agent %d times, want 1", g.ensures)
	}
	if got := d.view(); got != "chat" {
		t.Fatalf("a stopped agent the gate started itself landed on %q, want chat\n%s", got, d.screen())
	}
}

// After a reboot nothing is running: the gate starts the daemon, then the agent,
// and hands off to chat with no keypress in between.
func TestAfterARebootTheGateStartsTheDaemonAndTheAgent(t *testing.T) {
	g := readyGateTransport()
	g.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
	g.agentState = "stopped"
	d := newRootDriver(t, g, 80, 24)
	d.launchSidecar()

	if g.starts != 1 || g.ensures != 1 {
		t.Fatalf("daemon starts = %d, agent starts = %d, want 1 each", g.starts, g.ensures)
	}
	if got := d.view(); got != "chat" {
		t.Fatalf("the launch landed on %q, want chat\n%s", got, d.screen())
	}
}

// A start that fails keeps the row, its remedy and its `f` key — and pressing f
// once the cause is fixed still reaches chat.
func TestAFailedAgentStartLeavesTheFixKey(t *testing.T) {
	g := readyGateTransport()
	g.agentState = "stopped"
	g.ensureErr = errors.New("the sidecar binary is missing")
	d := newRootDriver(t, g, 80, 24)
	d.launchSidecar()

	screen := d.flat()
	for _, want := range []string{"installed, not started", "f start the agent", "Could not start the Notes agent automatically"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the screen does not show %q:\n%s", want, d.screen())
		}
	}

	g.ensureErr = nil
	d.send(key("f"))
	if g.ensures != 2 {
		t.Fatalf("f asked the daemon to start the agent %d times in total, want 2", g.ensures)
	}
	// A fix the user asked for holds for enter, so they can read what it did.
	if got := d.view(); got != "preflight" {
		t.Fatalf("the gate handed off to %q without letting the fix be read\n%s", got, d.screen())
	}
	d.send(keyEnter())
	if got := d.view(); got != "chat" {
		t.Fatalf("enter on the held gate landed on %q, want chat\n%s", got, d.screen())
	}
}

// A gate that has been left must not launch the agent afterwards: the hold tick
// it scheduled can still be in flight.
func TestALateProceedFromAnAbandonedGateLaunchesNothing(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().modelMissing(), 80, 24)
	d.launchSidecar()
	d.send(keyEsc())
	if got := d.view(); got == "chat" {
		t.Fatal("esc opened a chat instead of leaving")
	}

	d.send(preflight.ProceedMsg{AgentID: sidecarID})
	if got := d.view(); got == "chat" {
		t.Fatalf("a late ProceedMsg from an abandoned gate opened %q", got)
	}
}

// --- routing and layout ----------------------------------------------------

// The gate is a spinner-driven screen. If ticks and resizes do not reach it, it
// freezes mid-check and a resized terminal renders at the old size.
func TestTheGateGetsSpinnerAndResizeMessages(t *testing.T) {
	d := newRootDriver(t, readyGateTransport(), 80, 24)

	// Unpumped on purpose: this is the gate mid-check, which is the state whose
	// spinner the user actually watches.
	d.m = d.beginNoPump()
	if !strings.Contains(d.flat(), "checking") {
		t.Fatalf("the gate is not mid-check:\n%s", d.screen())
	}

	before := d.screen()
	spun := false
	for i := 0; i < 12 && !spun; i++ {
		updated, _ := d.m.Update(spinner.TickMsg{Time: time.Now()})
		d.m = updated.(root.FlagshipModel)
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
	d.launchSidecar()

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
			wants: []string{"Getting Notes ready", "ready", "Language model", "esc back"}},
		{name: "model missing", build: func() *gateTransport { return readyGateTransport().modelMissing() },
			wants: []string{"Language model", "not downloaded", "or run: gaia init", "esc back"}},
		{name: "model missing, details", build: func() *gateTransport { return readyGateTransport().modelMissing() },
			keys:  []tea.KeyMsg{key("d")},
			wants: []string{"Language model — failed", "esc back"}},
		{name: "daemon down", build: func() *gateTransport {
			g := readyGateTransport()
			g.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			g.startErr = &daemon.StartError{Reason: "the `gaia` CLI is not on PATH"}
			return g
		}, wants: []string{"Background service", "not running", "f start it for me", "esc back"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			opts := preflight.Options{ReadyHold: time.Millisecond, ManualProceed: tc.hold}
			d := newRootDriverOpts(t, tc.build(), 80, 24, opts)
			d.launchSidecar()
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
	d.launchSidecar()

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
	d.launchSidecar()
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
	d.launchSidecar()
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
	d.launchSidecar()
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

	d.launchSidecar()

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
// Halted() — FlagshipModel never gates message routing on the flag, only
// ControlSnapshot reads it. Proven by the rendered width actually
// narrowing, which only happens if the message reached the preflight
// model's own Update.
func TestNonKeyMessagesPassThroughWhileHalted(t *testing.T) {
	d := newRootDriver(t, readyGateTransport().ctxShortfall(), 100, 30)
	d.launchSidecar()
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
	d.beginNoPump()
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
func TestCrossScreenHaltingWorksFromSplashAndFromPreflight(t *testing.T) {
	t.Run("splash", func(t *testing.T) {
		d := newRootDriver(t, readyGateTransport(), 100, 30)
		if d.view() != "splash" {
			t.Fatalf("test setup: want splash, got %q", d.view())
		}
		d.send(status.Outcome{StepID: "splash-synthetic", Label: "Synthetic",
			Level: status.LevelFailed, Disposition: status.DispositionHalt, Summary: "test"})
		if !d.m.Halted() {
			t.Error("a synthetic halting Outcome while on the splash did not halt")
		}
	})
	t.Run("preflight", func(t *testing.T) {
		// ManualProceed keeps an all-green gate parked on the preflight
		// screen instead of auto-proceeding to chat during the synchronous
		// pump — this sub-test is about the SCREEN, not the report.
		opts := preflight.Options{ReadyHold: time.Millisecond, ManualProceed: true}
		d := newRootDriverOpts(t, readyGateTransport(), 100, 30, opts)
		d.launchSidecar()
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
