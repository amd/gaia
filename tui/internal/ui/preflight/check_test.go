package preflight

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/amd/gaia/tui/internal/gaiainit"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// --- fixtures ---------------------------------------------------------------
//
// Every body below is the REAL shape the corresponding endpoint serializes:
// the sidecar's InitResponse / InitLemonadeStatus / InitModelStatus, and
// SidecarRegistry.list_agents from src/gaia/daemon/sidecars/registry.py.

const (
	// "user" is what a real daemon reports here — AgentSidecarManager.mode
	// validates against exactly "user" and "dev", and the listing carries
	// resolved_mode verbatim.
	agentsRunning = `{"agents":[{"agent_id":"gaia","state":"running","mode":"user",` +
		`"pid":41999,"port":51234,"base_url":"http://127.0.0.1:51234","api_version":"2.3",` +
		`"agent_version":"0.5.0","started_at":1750000000.0,"dev_src_dir":null}]}`

	// The same listing for a sidecar someone started from a checkout. "dev" and
	// "user" are the only modes SidecarRegistry records (AgentSidecarManager.mode
	// validates against exactly those two).
	agentsRunningDev = `{"agents":[{"agent_id":"gaia","state":"running","mode":"dev",` +
		`"pid":41999,"port":51234,"base_url":"http://127.0.0.1:51234","api_version":"2.3",` +
		`"agent_version":"0.5.0","started_at":1750000000.0,` +
		`"dev_src_dir":"/home/u/gaia/hub/agents/gaia/python"}]}`

	agentsStopped = `{"agents":[{"agent_id":"gaia","state":"stopped","mode":null,` +
		`"pid":null,"port":null,"base_url":null,"api_version":null,"agent_version":null,` +
		`"started_at":null,"dev_src_dir":null}]}`

	agentsEmpty = `{"agents":[]}`

	// GET /v1/gaia/health — HealthResponse. It touches
	// no account, no model and no network, so only a sidecar that is SERVING can
	// produce it.
	healthOK = `{"status":"ok"}`

	// The relay's 503 for a sidecar that passed its startup handshake and has
	// since stopped serving: whatever parks the event loop — a blocking call, a
	// hung dependency — takes every route with it while pid and port stay
	// intact, so the daemon's own listing keeps saying "running".
	// VERBATIM from sidecars/manager.check_responsive; the phrase the row
	// matches on is pinned Python-side too (test_sidecar_alive_but_not_serving).
	healthWedged = `{"detail":"gaia sidecar (pid 41999) is alive but did not answer ` +
		`http://127.0.0.1:51234/health within 2.0s (ReadTimeout: ). It passed its startup ` +
		"health check, so it has stopped serving since — typically a blocked event loop or " +
		"a hung dependency. Restart it with `gaia daemon start-agent gaia` and check the " +
		`sidecar log at ~/.gaia/agents/gaia/logs/gaia-1.log."}`

	initReady = `{"ready":true,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:8000/api/v1","version":"8.1.10","min_version":"8.1.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":true,"loadable":null,` +
		`"ctx_size":65536},"hint":null}`

	initUnreachable = `{"ready":false,"lemonade":{"reachable":false,` +
		`"base_url":"http://localhost:8000/api/v1","version":null,"min_version":"8.1.0",` +
		`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null,` +
		`"ctx_size":null},"hint":"Local Lemonade Server is not reachable at ` +
		"http://localhost:8000/api/v1. GAIA starts it automatically — run `gaia daemon start` " +
		"if the background service is not running.\"}"

	initTooOld = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:8000/api/v1","version":"8.0.1","min_version":"8.1.0",` +
		`"compatible":false},"model":{"id":"Gemma-4-E4B-it-GGUF","present":true,"loadable":null,` +
		`"ctx_size":null},"hint":"Lemonade 8.0.1 is older than the required 8.1.0 — upgrade it ` +
		"(see https://lemonade-server.ai or run `gaia init`), then retry.\"}"

	// The server does not advertise a version, so `compatible` is null. The
	// sidecar still reports ready=true (it mirrors gaia init's policy of not
	// failing on an unparseable version) — the gate must NOT read that as a pass.
	initUnknownVersion = `{"ready":true,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:8000/api/v1","version":null,"min_version":"8.1.0",` +
		`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":true,"loadable":null,` +
		`"ctx_size":65536},"hint":null}`

	// Captured from a live sidecar: when ctx_size is null the serializer OMITS
	// the key entirely, so the parser must not depend on it being present.
	initModelMissing = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":"10.2.1","min_version":"10.2.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		"\"hint\":\"Model `Gemma-4-E4B-it-GGUF` not downloaded — run `gaia init` " +
		`(or pull it via Lemonade), then retry."}`

	// CAPTURED LIVE: the model is downloaded and loaded, the server is healthy,
	// short turns work — and it came up with a window well under the 65536 this
	// machine's profile pins, so a document-sized request is refused. Measured at
	// 25037 with the server started bare (32527 when the window was requested);
	// llama.cpp clamps to the memory free at load time, so the figure moves.
	initCtxShortfall = `{"ready":true,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":"10.10.0","min_version":"10.2.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":true,"loadable":null,` +
		`"ctx_size":25037},"hint":null}`

	// Lemonade answered /health but its /models read failed. `present:false` here
	// means "could not tell" — the hint is the ONLY thing that says so.
	initModelListUnreadable = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":"10.2.1","min_version":"10.2.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		`"hint":"Lemonade is reachable but its model list at http://localhost:13305/api/v1/models ` +
		`could not be read (ConnectionError: connection aborted). Make sure the server is healthy, then retry."}`

	// Lemonade answers but advertises no version AND the model is missing — the
	// live shape that put an indeterminate row above a real failure.
	initUnknownVersionModelMissing = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":null,"min_version":"10.2.0",` +
		`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		"\"hint\":\"Model `Gemma-4-E4B-it-GGUF` not downloaded — run `gaia init` " +
		`(or pull it via Lemonade), then retry."}`

	// FastAPI's validation shape: `detail` is an ARRAY, not a string. Only the
	// `msg` fields are readable language; the array must never reach a row.
	validationError = `{"detail":[{"type":"missing","loc":["body","max_results"],` +
		`"msg":"Field required","input":{}}]}`
)

// testConfig is the daemon-sidecar gate these tests drive.
func testConfig() Config { return ConfigFor("gaia", "GAIA") }

// --- fake transport ---------------------------------------------------------

type call struct {
	method string
	path   string
}

type fakeTransport struct {
	attachErr error
	startErr  error
	ensureErr error
	info      DaemonInfo

	// bodies is keyed "GET /daemon/v1/agents".
	bodies map[string]Response
	errs   map[string]error

	streamStatus int
	streamBody   string
	streamErr    error

	// ensureFn overrides EnsureAgent so a test can hold the call open and prove
	// the screen's context really reaches it.
	ensureFn func(context.Context) error

	// How the daemon answers "start the local model server".
	startLemonadeErr error
	startLemonadeFn  func(context.Context) error

	calls []call
}

func newFake() *fakeTransport {
	return &fakeTransport{
		info: DaemonInfo{PID: 41822, Port: 51230, APIVersion: "1.1"},
		bodies: map[string]Response{
			"GET /daemon/v1/agents": {Status: 200, Body: []byte(agentsRunning)},
			"GET /v1/gaia/health":   {Status: 200, Body: []byte(healthOK)},
			"GET /v1/gaia/init":     {Status: 200, Body: []byte(initReady)},
		},
		errs: map[string]error{},
	}
}

func (f *fakeTransport) with(key string, status int, body string) *fakeTransport {
	f.bodies[key] = Response{Status: status, Body: []byte(body)}
	return f
}

func (f *fakeTransport) Attach(context.Context) (DaemonInfo, error) {
	if f.attachErr != nil {
		return DaemonInfo{}, f.attachErr
	}
	return f.info, nil
}

func (f *fakeTransport) Start(context.Context) (DaemonInfo, error) {
	if f.startErr != nil {
		return DaemonInfo{}, f.startErr
	}
	f.attachErr = nil
	return f.info, nil
}

func (f *fakeTransport) EnsureAgent(ctx context.Context, _ string) error {
	if f.ensureFn != nil {
		return f.ensureFn(ctx)
	}
	return f.ensureErr
}

// StartLemonade defaults to succeeding WITHOUT changing the /init answer, which
// is the realistic shape of "the daemon started a server and the agent still
// cannot use it" — and keeps every pre-existing expectation about the down-row's
// wording intact. A test that wants the repaired path sets startLemonadeFn to
// rewrite the body the way a real start would.
func (f *fakeTransport) StartLemonade(ctx context.Context) error {
	f.calls = append(f.calls, call{http.MethodPost, daemon.APIPrefix + "/lemonade/start"})
	if f.startLemonadeFn != nil {
		return f.startLemonadeFn(ctx)
	}
	return f.startLemonadeErr
}

func (f *fakeTransport) Do(_ context.Context, method, path string, _ []byte) (Response, error) {
	key := method + " " + path
	f.calls = append(f.calls, call{method, path})
	if err, ok := f.errs[key]; ok {
		return Response{}, err
	}
	resp, ok := f.bodies[key]
	if !ok {
		return Response{Status: 404, Body: []byte(`{"detail":"not found"}`)}, nil
	}
	return resp, nil
}

func (f *fakeTransport) Stream(_ context.Context, method, path string, _ []byte) (Stream, error) {
	f.calls = append(f.calls, call{method, path})
	if f.streamErr != nil {
		return Stream{}, f.streamErr
	}
	status := f.streamStatus
	if status == 0 {
		status = 200
	}
	return Stream{Status: status, Body: io.NopCloser(strings.NewReader(f.streamBody))}, nil
}

func (f *fakeTransport) called(method, path string) bool {
	for _, c := range f.calls {
		if c.method == method && c.path == path {
			return true
		}
	}
	return false
}

// --- the table --------------------------------------------------------------

// realCommands are the command prefixes that actually exist. A remedy that names
// something outside this set sends the user somewhere that does not work, which
// is worse than no remedy at all.
//
// The `gaia` family is fixed. The local-model-server command is NOT: it depends
// on what is installed on the machine the remedy will be typed into, so it is
// taken from the same resolver the rows use. `lemonade-server serve` is
// deliberately absent from the fixed list — it was hardcoded here, which is how a
// command that exists on no modern install stayed asserted-as-real for so long.
func realCommands() []string {
	fixed := []string{
		"gaia daemon status",
		"gaia daemon start",
		"gaia daemon restart",
		"gaia daemon start-agent ",
		"gaia daemon stop-agent ",
		"gaia daemon agents",
		"gaia hub install ",
		"gaia init",
		// Reached only when the machine cannot name Lemonade's launcher
		// (lemonadeRestartRemedy, !l.Found) — so it fires on a runner with no
		// Lemonade installed and never on a developer box that has one.
		"gaia kill",
		// The published one-line installers, which install GAIA's binaries
		// (no Python): the remedy for a core older than this app.
		gaiainit.InstallCommand,
		gaiainit.InstallPowerShellCommand,
	}
	l := resolveLemonade()
	for _, cmd := range []string{l.Start, l.Restart} {
		if cmd != "" {
			fixed = append(fixed, cmd)
		}
	}
	return fixed
}

// The remedy for a host that cannot name Lemonade's launcher only fires where
// nothing is installed — a CI runner, never a developer box with Lemonade on
// it. So the rows that reach it pass locally and fail only in CI, which is how
// "gaia kill" stayed missing from realCommands(). Resolving against a simulated
// bare Linux host puts that answer in reach of a local `go test`.
func TestTheBareHostRemedyIsARealCommand(t *testing.T) {
	orig := realHostProbe
	realHostProbe = func() hostProbe { return fakeHostFor("linux", nil, nil, map[string]string{}) }
	defer func() { realHostProbe = orig }()

	if l := resolveLemonade(); l.Found {
		t.Fatalf("a host with nothing installed resolved a launcher: %+v", l)
	}
	assertRealCommand(t, Row{
		Key:    KeyLemonade,
		Remedy: lemonadeRestartRemedy(),
	})
}

func assertRealCommand(t *testing.T, row Row) {
	t.Helper()
	cmd := row.Remedy.Command
	if cmd == "" {
		t.Fatalf("row %q failed with no command to run: %+v", row.Key, row.Remedy)
	}
	for _, prefix := range realCommands() {
		if strings.HasPrefix(cmd, prefix) {
			if strings.Contains(cmd, "<") {
				t.Fatalf("row %q remedy still has a placeholder: %q", row.Key, cmd)
			}
			return
		}
	}
	t.Fatalf("row %q remedy names a command that does not exist on this machine: %q",
		row.Key, cmd)
}

// assertRunnable is the stronger check the Lemonade remedies need: not "it is on
// a list we maintain" but "its program is on THIS host". A list can go stale
// silently; a resolved binary cannot.
func assertRunnable(t *testing.T, cmd string) {
	t.Helper()
	if cmd == "" {
		t.Fatal("no command to run")
	}
	program := firstWord(cmd)
	if program == "gaia" {
		// The gaia CLI is this repo's own entry point and is not required to be
		// installed to run these tests.
		return
	}
	if strings.HasPrefix(program, "/") || strings.Contains(program, string(os.PathSeparator)) {
		if _, err := os.Stat(program); err != nil {
			t.Errorf("remedy names %q, which is not on this machine: %v", program, err)
		}
		return
	}
	if _, err := exec.LookPath(program); err != nil {
		t.Errorf("remedy names %q, which is not on PATH: %v", program, err)
	}
}

// firstWord is the program a command line invokes, honouring the quoting
// quoteCommand applies to a path with a space in it.
//
// It steps over the context-window prefix first. Without that it would check
// that `env` exists — which it always does — and stop checking the binary that
// actually has to be there, turning the guard off exactly where it matters.
func firstWord(cmd string) string {
	cmd = strings.TrimSpace(stripEnvPrefix(cmd))
	if strings.HasPrefix(cmd, `"`) {
		if end := strings.Index(cmd[1:], `"`); end >= 0 {
			return cmd[1 : end+1]
		}
	}
	if i := strings.IndexAny(cmd, " \t"); i >= 0 {
		return cmd[:i]
	}
	return cmd
}

// stripEnvPrefix removes `env VAR=V ` (POSIX) or `set VAR=V && ` (cmd.exe) from
// the front of a command, leaving the program it actually runs.
func stripEnvPrefix(cmd string) string {
	cmd = strings.TrimSpace(cmd)
	for strings.HasPrefix(cmd, `set "`) {
		_, after, found := strings.Cut(cmd, "&&")
		if !found {
			return cmd
		}
		cmd = strings.TrimSpace(after)
	}
	for {
		rest, ok := strings.CutPrefix(cmd, "env ")
		if !ok {
			return cmd
		}
		rest = strings.TrimSpace(rest)
		// Only an assignment is part of the prefix; the next token is the program.
		word, after, _ := strings.Cut(rest, " ")
		if !strings.Contains(word, "=") {
			return rest
		}
		cmd = "env " + strings.TrimSpace(after)
		if strings.TrimSpace(after) == "" {
			return ""
		}
	}
}

func TestCheck(t *testing.T) {
	tests := []struct {
		name string
		// build returns the transport for this scenario.
		build func() *fakeTransport
		// wantStates is keyed by row key. Rows omitted are not asserted.
		wantStates map[string]State
		wantReady  bool
		// wantBlocker is the key of the row that must REFUSE the launch, "" for
		// none. An optional row never refuses one, however it failed.
		wantBlocker string
		// wantAttention is the key of the row the user must act on. It is the
		// blocker whenever there is one, so it defaults to wantBlocker; a failed
		// optional row is the case where the two differ.
		wantAttention string
		// wantIn asserts substrings on the acted-on row's rendered remedy.
		wantIn []string
		// wantNotIn asserts what the remedy must NOT say.
		wantNotIn []string
		// wantFix is the fix the acted-on row offers.
		wantFix FixKind
		// mustNotCall names paths the walk must not reach (stop at first failure).
		mustNotCall []call
	}{
		{
			name:  "all ready",
			build: newFake,
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateOK,
				KeyModel: StateOK,
			},
			wantReady: true,
		},
		{
			name: "daemon down",
			build: func() *fakeTransport {
				f := newFake()
				f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
				return f
			},
			wantStates: map[string]State{
				KeyDaemon: StateFailed, KeySidecar: StatePending, KeyLemonade: StatePending,
				KeyModel: StatePending,
			},
			wantBlocker: KeyDaemon,
			wantIn:      []string{"gaia daemon start"},
			wantFix:     FixStartDaemon,
			mustNotCall: []call{{"GET", "/daemon/v1/agents"}, {"GET", "/v1/gaia/init"}},
		},
		{
			name: "daemon speaks the wrong contract",
			build: func() *fakeTransport {
				f := newFake()
				f.attachErr = &daemon.VersionError{Have: "1.0", Want: "1.1", Reason: "it predates the agent relay"}
				return f
			},
			wantStates:  map[string]State{KeyDaemon: StateFailed},
			wantBlocker: KeyDaemon,
			// Both halves of the skew, and a remedy that can actually clear it:
			// the version comes from the installed core, so a restart loops.
			wantIn: []string{"host API v1.0", "v1.1", "https://amd-gaia.ai/#install"},
			// Restarting a machine-wide daemon other clients share is not ours to do.
			wantFix: FixNone,
		},
		{
			name: "sidecar not running",
			build: func() *fakeTransport {
				return newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateFailed, KeyLemonade: StatePending,
			},
			wantBlocker: KeySidecar,
			wantIn:      []string{"gaia daemon start-agent gaia"},
			wantFix:     FixStartSidecar,
			mustNotCall: []call{{"GET", "/v1/gaia/init"}},
		},
		{
			// THE WEDGE. The daemon's listing says "running" because the PROCESS is
			// there; the agent itself takes the request and never answers.
			name: "the agent is registered and running but never answers",
			build: func() *fakeTransport {
				f := newFake()
				f.errs["GET /v1/gaia/health"] = &daemon.RequestError{
					Op: "call GET /v1/gaia/health through the background service",
					Detail: "context deadline exceeded (Client.Timeout or context " +
						"cancellation while reading body)",
				}
				return f
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateFailed, KeyLemonade: StatePending,
				KeyModel: StatePending,
			},
			wantBlocker: KeySidecar,
			wantIn: []string{
				"running, not answering", "never replied",
				"gaia daemon stop-agent gaia && gaia daemon start-agent gaia",
				"~/.gaia/agents/gaia/logs/",
			},
			// start-agent ATTACHES to a registered sidecar, so a one-key start
			// would report success and change nothing.
			wantFix: FixNone,
			// And nothing downstream is probed over an agent that cannot answer.
			mustNotCall: []call{
				{"GET", "/v1/gaia/init"},
			},
		},
		{
			// Same wedge, seen by the daemon first: its pre-relay probe finds the
			// sidecar alive but no longer serving and 503s with that verdict.
			name: "the background service cannot get an answer out of the agent",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/health", 503, healthWedged)
			},
			wantStates: map[string]State{
				KeySidecar: StateFailed, KeyLemonade: StatePending,
			},
			wantBlocker: KeySidecar,
			wantIn: []string{
				"running, not answering", "could not get an answer out of it",
				"gaia daemon stop-agent gaia",
			},
			wantFix:     FixNone,
			mustNotCall: []call{{"GET", "/v1/gaia/init"}},
		},
		{
			// An agent with no health route still ANSWERED, and an answer off the
			// event loop is the whole question. Reading a 404 as a dead agent would
			// fail every sidecar older than this check.
			name: "an agent without a health route still counts as answering",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/health", 404, `{"detail":"Not Found"}`)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateOK,
				KeyModel: StateOK,
			},
			wantReady: true,
		},
		{
			name: "agent not installed",
			build: func() *fakeTransport {
				return newFake().with("GET /daemon/v1/agents", 200, agentsEmpty)
			},
			wantStates:  map[string]State{KeySidecar: StateFailed},
			wantBlocker: KeySidecar,
			wantIn:      []string{"gaia init"},
			wantFix:     FixNone,
		},
		{
			name: "lemonade unreachable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 503, initUnreachable)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateFailed,
				KeyModel: StatePending,
			},
			wantBlocker: KeyLemonade,
			// The command is whatever THIS machine can actually run; only the
			// diagnosis half is a fixed string.
			wantIn: []string{"not running"},
			// The sidecar cannot install or launch Lemonade; pretending otherwise
			// would be a key that does nothing.
			wantFix: FixNone,
		},
		{
			name: "lemonade too old",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 503, initTooOld)
			},
			wantStates: map[string]State{
				KeyLemonade: StateFailed, KeyModel: StatePending,
			},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"8.0.1", "8.1.0", "gaia init"},
		},
		{
			name: "lemonade version unknown is not a pass",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 200, initUnknownVersion)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateUnknown,
				KeyModel: StateOK,
			},
			// Unknown is not ready — but it does not block either, matching the
			// sidecar's own policy of not failing on an unparseable version.
			wantReady:   false,
			wantBlocker: "",
		},
		{
			name: "model missing",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 503, initModelMissing)
			},
			wantStates: map[string]State{
				KeyLemonade: StateOK, KeyModel: StateFailed,
			},
			wantBlocker: KeyModel,
			wantIn:      []string{"gaia init", "Gemma-4-E4B-it-GGUF"},
			wantFix:     FixPullModel,
		},
		{
			// present:false must NOT be reported as "download the model" when the
			// sidecar could not read the list to begin with.
			name: "lemonade reachable but its model list is unreadable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 503, initModelListUnreadable)
			},
			wantStates: map[string]State{
				KeyLemonade: StateFailed, KeyModel: StatePending,
			},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"model list"},
			wantFix:     FixNone,
		},
		{
			name: "sidecar relay is down",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 502,
					`{"detail":"the 'gaia' sidecar is registered but not running (POST /daemon/v1/agents/gaia/ensure) and retry."}`)
			},
			wantStates:  map[string]State{KeyLemonade: StateFailed},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"gaia daemon start-agent gaia"},
		},
		{
			name: "init answers with something unreadable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/gaia/init", 200, `<html>not json</html>`)
			},
			wantStates:  map[string]State{KeyLemonade: StateFailed},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"gaia daemon start-agent gaia"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// The gate reads the caller's mode preference, so a developer with
			// one exported would otherwise change what these cases assert.
			t.Setenv("GAIA_GAIA_AGENT_MODE", "")
			f := tt.build()
			rep := Check(context.Background(), f, testConfig())

			if got := rep.Ready(); got != tt.wantReady {
				t.Errorf("Ready() = %v, want %v\n%s", got, tt.wantReady, rep)
			}
			for key, want := range tt.wantStates {
				row, ok := rep.Find(key)
				if !ok {
					t.Fatalf("no row %q in report:\n%s", key, rep)
				}
				if row.State != want {
					t.Errorf("row %q state = %s, want %s\n%s", key, row.State.Word(), want.Word(), rep)
				}
			}

			blocker, hasBlocker := rep.Blocker()
			if tt.wantBlocker == "" {
				if hasBlocker {
					t.Fatalf("expected no blocking row, got %q\n%s", blocker.Key, rep)
				}
			} else {
				if !hasBlocker {
					t.Fatalf("expected %q to block, nothing did\n%s", tt.wantBlocker, rep)
				}
				if blocker.Key != tt.wantBlocker {
					t.Fatalf("blocker = %q, want %q\n%s", blocker.Key, tt.wantBlocker, rep)
				}
			}

			attention := tt.wantAttention
			if attention == "" {
				attention = tt.wantBlocker
			}
			if attention != "" {
				idx := rep.FirstAttention()
				if idx < 0 || rep.Rows[idx].Key != attention {
					t.Fatalf("the user is pointed at %d, want the %q row\n%s", idx, attention, rep)
				}
				row := rep.Rows[idx]
				assertRealCommand(t, row)
				if row.Fix != tt.wantFix {
					t.Errorf("%q fix = %v, want %v", row.Key, row.Fix, tt.wantFix)
				}
				text := fmt.Sprintf("%s %s %s %s %s",
					row.Line, row.Detail, row.Remedy.Action,
					row.Remedy.Command, row.Remedy.Where)
				for _, want := range tt.wantIn {
					if !strings.Contains(text, want) {
						t.Errorf("the %q row does not mention %q; it says: %s", row.Key, want, text)
					}
				}
				for _, unwanted := range tt.wantNotIn {
					if strings.Contains(text, unwanted) {
						t.Errorf("the %q row must not mention %q; it says: %s", row.Key, unwanted, text)
					}
				}
			}

			for _, c := range tt.mustNotCall {
				if f.called(c.method, c.path) {
					t.Errorf("walk should have stopped before %s %s", c.method, c.path)
				}
			}
		})
	}
}

// Every non-OK row — not just the blocking one — has to be actionable, because
// the user can move the cursor onto any of them.
func TestEveryFailedRowIsActionable(t *testing.T) {
	scenarios := map[string]*fakeTransport{
		"unreachable": newFake().with("GET /v1/gaia/init", 503, initUnreachable),
		"too old":     newFake().with("GET /v1/gaia/init", 503, initTooOld),
		"model":       newFake().with("GET /v1/gaia/init", 503, initModelMissing),
		"sidecar":     newFake().with("GET /daemon/v1/agents", 200, agentsStopped),
		"wedged":      newFake().with("GET /v1/gaia/health", 503, healthWedged),
	}
	for name, f := range scenarios {
		t.Run(name, func(t *testing.T) {
			rep := Check(context.Background(), f, testConfig())
			for _, row := range rep.Rows {
				switch row.State {
				case StateFailed:
					assertRealCommand(t, row)
					if row.Detail == "" {
						t.Errorf("failed row %q says nothing about what went wrong", row.Key)
					}
					if row.Remedy.Where == "" {
						t.Errorf("failed row %q does not say where to look next", row.Key)
					}
				case StatePending:
					if row.Detail == "" {
						t.Errorf("pending row %q does not say what it is waiting on", row.Key)
					}
				}
			}
		})
	}
}

// Every non-OK row (Failed or Unknown) must declare a real Disposition —
// DispositionUnset on such a row is a bug, not a pass, the same way an
// unset State would be. This is the "go vet-style exhaustiveness test":
// every branch in check.go that resolves a row to Failed or Unknown is
// exercised here, and a new branch that forgets to set Disposition fails
// this test rather than silently defaulting to "proceed".
//
// wantDisposition also locks the BLOCKER-1 guard (the lemonade-version row
// must be Notify, never Halt, or a blanket rule would halt on 44-100% of
// launches) and the one row this issue exists to fix (the
// ctx shortfall must be Halt).
func TestEveryNonOKRowDeclaresADisposition(t *testing.T) {
	tests := []struct {
		name            string
		build           func() *fakeTransport
		wantKey         string
		wantDisposition status.Disposition
	}{
		{"daemon not running", func() *fakeTransport {
			f := newFake()
			f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			return f
		}, KeyDaemon, status.DispositionHalt},
		{"daemon version skew", func() *fakeTransport {
			f := newFake()
			f.attachErr = &daemon.VersionError{Have: "1.0", Reason: "it predates the agent relay"}
			return f
		}, KeyDaemon, status.DispositionHalt},
		{"sidecar list: transport error", func() *fakeTransport {
			f := newFake()
			f.errs["GET /daemon/v1/agents"] = &daemon.RequestError{Op: "list agents", Detail: "boom"}
			return f
		}, KeySidecar, status.DispositionHalt},
		{"sidecar list: bad status", func() *fakeTransport {
			return newFake().with("GET /daemon/v1/agents", 500, `{"detail":"boom"}`)
		}, KeySidecar, status.DispositionHalt},
		{"sidecar list: unreadable json", func() *fakeTransport {
			return newFake().with("GET /daemon/v1/agents", 200, `not json`)
		}, KeySidecar, status.DispositionHalt},
		{"sidecar: not started", func() *fakeTransport {
			return newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
		}, KeySidecar, status.DispositionHalt},
		{"sidecar: not installed", func() *fakeTransport {
			return newFake().with("GET /daemon/v1/agents", 200, agentsEmpty)
		}, KeySidecar, status.DispositionHalt},
		{"lemonade: transport error", func() *fakeTransport {
			f := newFake()
			f.errs["GET /v1/gaia/init"] = &daemon.RequestError{Op: "check readiness", Detail: "boom"}
			return f
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: bad status", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 500, `{"detail":"boom"}`)
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: unparseable body", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 200, `<html>not json</html>`)
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: unreachable", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 503, initUnreachable)
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: model list unreadable", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 503, initModelListUnreadable)
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: too old", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 503, initTooOld)
		}, KeyLemonade, status.DispositionHalt},
		{"lemonade: version unknown — guards BLOCKER-1", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 200, initUnknownVersion)
		}, KeyLemonade, status.DispositionNotify},
		{"model: missing", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 503, initModelMissing)
		}, KeyModel, status.DispositionHalt},
		{"model: ctx shortfall — the reported bug", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 200, initCtxShortfall)
		}, KeyModel, status.DispositionHalt},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rep := Check(context.Background(), tt.build(), testConfig())
			row, ok := rep.Find(tt.wantKey)
			if !ok {
				t.Fatalf("no row %q in report:\n%s", tt.wantKey, rep)
			}
			if row.State != StateFailed && row.State != StateUnknown {
				t.Fatalf("row %q state = %s, want Failed or Unknown\n%s", tt.wantKey, row.State.Word(), rep)
			}
			if row.Disposition == status.DispositionUnset {
				t.Fatalf("row %q (%s) has no Disposition — a non-OK row must always declare one\n%s",
					tt.wantKey, row.State.Word(), rep)
			}
			if row.Disposition != tt.wantDisposition {
				t.Errorf("row %q disposition = %v, want %v", tt.wantKey, row.Disposition, tt.wantDisposition)
			}
		})
	}
}

// HasHalt is what preflight/model.go's reportMsg handler will gate the
// screen-does-not-lie behaviour on. It must be true precisely when a Halt
// row is present and false for a report holding only Notify rows — even
// when those Notify rows are themselves the BLOCKER-1 guard cases.
func TestReportHasHalt(t *testing.T) {
	cases := []struct {
		name  string
		build func() *fakeTransport
		want  bool
	}{
		{"all ready", newFake, false},
		{"lemonade version unknown — Notify only", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 200, initUnknownVersion)
		}, false},
		{"ctx shortfall — Halt, the reported bug", func() *fakeTransport {
			return newFake().with("GET /v1/gaia/init", 200, initCtxShortfall)
		}, true},
		{"daemon down — Halt (already blocks today)", func() *fakeTransport {
			f := newFake()
			f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			return f
		}, true},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			rep := Check(context.Background(), tt.build(), testConfig())
			if got := rep.HasHalt(); got != tt.want {
				t.Errorf("HasHalt() = %v, want %v\n%s", got, tt.want, rep)
			}
		})
	}
}

// A row nobody assigned a Disposition to must halt, not silently proceed —
// the exact failure shape (something not okay, nobody decided, launch
// happens anyway) that this whole issue exists to fix, just relocated to a
// single forgotten field. Notify is the value a row opts INTO to skip
// holding the screen; DispositionUnset is not that, so it must hold too.
// Built directly rather than through Check(), because check.go now declares
// a Disposition at every branch it has today — this proves what HasHalt does
// on a row a FUTURE branch forgets to declare one for.
func TestHasHaltDefaultsToHaltingOnAnUndeclaredDisposition(t *testing.T) {
	rep := Report{Rows: []Row{
		{Key: "mystery", State: StateFailed, Disposition: status.DispositionUnset},
	}}
	if !rep.HasHalt() {
		t.Fatal("a non-OK row with no declared Disposition must halt, not silently proceed")
	}
}

// The security review found two paths that put upstream server text into a
// row unsanitized: checkInit assigns body.hint() to Detail verbatim (the
// modelListUnreadable branch, check.go:427), and Ladder's fallback builds a
// Cause with %s — not %q — over upstream/transport text (ladder.go's
// transport() and Text() fallbacks). Fixing those existing render paths is
// out of scope for this issue; what matters here is the NEW surface this
// issue adds — Outcome — which must not carry the payload through, even
// though the row it was built from still does.
func TestAHostileHintCannotEscapeIntoAnOutcome(t *testing.T) {
	const payload = "\x1b[2J\x1b]0;pwned\x07"
	hint := "Lemonade is reachable but its model list could not be read " + payload

	// Built via json.Marshal, not a hand-quoted string literal: %q escapes a
	// control byte Go's way (`\x1b`), which is not a legal JSON escape and
	// would make json.Unmarshal fail the whole body rather than exercise the
	// modelListUnreadable branch this test targets. Marshal escapes it the
	// JSON way (``), which decodes back to the real byte — proven with
	// a throwaway script before writing this fixture this way.
	type initFixture struct {
		Ready    bool `json:"ready"`
		Lemonade struct {
			Reachable  bool   `json:"reachable"`
			BaseURL    string `json:"base_url"`
			Version    string `json:"version"`
			MinVersion string `json:"min_version"`
			Compatible bool   `json:"compatible"`
		} `json:"lemonade"`
		Model struct {
			ID      string `json:"id"`
			Present bool   `json:"present"`
		} `json:"model"`
		Hint string `json:"hint"`
	}
	fixture := initFixture{Ready: false, Hint: hint}
	fixture.Lemonade.Reachable = true
	fixture.Lemonade.BaseURL = "http://localhost:13305/api/v1"
	fixture.Lemonade.Version = "10.2.1"
	fixture.Lemonade.MinVersion = "10.2.0"
	fixture.Lemonade.Compatible = true
	fixture.Model.ID = "Gemma-4-E4B-it-GGUF"
	fixture.Model.Present = false
	body, err := json.Marshal(fixture)
	if err != nil {
		t.Fatalf("test setup: could not marshal the fixture: %v", err)
	}
	f := newFake().with("GET /v1/gaia/init", 503, string(body))

	rep := Check(context.Background(), f, testConfig())
	row, ok := rep.Find(KeyLemonade)
	if !ok {
		t.Fatal("no lemonade row")
	}
	if row.State != StateFailed {
		t.Fatalf("row state = %s, want Failed\n%s", row.State.Word(), rep)
	}
	if !strings.Contains(row.Detail, "\x1b") {
		t.Fatalf("test setup: row.Detail should still carry the raw escape — that is what "+
			"proves the sanitizing boundary is Outcome(), not the row itself: %q", row.Detail)
	}

	o := row.Outcome()
	if strings.ContainsAny(o.Summary, "\x1b\x07") {
		t.Fatalf("Outcome().Summary carries raw control bytes: %q", o.Summary)
	}
	if o.Disposition == status.DispositionUnset {
		t.Fatal("Outcome() dropped the row's Disposition")
	}
}

// Same boundary via the OTHER unsanitized path: a transport-error Detail
// reaching a Cause through Ladder's %s fallback.
func TestAHostileTransportDetailCannotEscapeIntoAnOutcome(t *testing.T) {
	const payload = "\x1b[2J\x1b]0;pwned\x07"
	f := newFake()
	f.errs["GET /daemon/v1/agents"] = &daemon.RequestError{
		Op: "list the agents the background service supervises", Detail: payload,
	}

	rep := Check(context.Background(), f, testConfig())
	row, ok := rep.Find(KeySidecar)
	if !ok {
		t.Fatal("no sidecar row")
	}
	if row.State != StateFailed {
		t.Fatalf("row state = %s, want Failed\n%s", row.State.Word(), rep)
	}
	if !strings.Contains(row.Detail, "\x1b") {
		t.Fatalf("test setup: row.Detail should still carry the raw escape: %q", row.Detail)
	}

	o := row.Outcome()
	if strings.ContainsAny(o.Summary, "\x1b\x07") {
		t.Fatalf("Outcome().Summary carries raw control bytes: %q", o.Summary)
	}
}

// The daemon row must distinguish "start one" from "this one is unusable": a
// version skew is never fixed by starting a second daemon.
func TestDaemonRowFixDependsOnWhyItFailed(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want FixKind
	}{
		{"never started", &daemon.NotRunningError{Path: "/tmp/i.json"}, FixStartDaemon},
		{"dead pid", &daemon.StaleError{Kind: daemon.StalePIDDead, Path: "/tmp/i.json", Reason: "its pid 1 is not running"}, FixStartDaemon},
		{"port stolen", &daemon.StaleError{Kind: daemon.StaleForeign, Path: "/tmp/i.json", Reason: "served by something else"}, FixStartDaemon},
		{"wedged", &daemon.StaleError{Kind: daemon.StaleUnresponsive, Path: "/tmp/i.json", Reason: "no answer"}, FixNone},
		{"version skew", &daemon.VersionError{Have: "1.0", Want: "1.1", Reason: "too old"}, FixNone},
		{"would not start", &daemon.StartError{Reason: "port bind failed"}, FixNone},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFake()
			f.attachErr = tc.err
			rep := Check(context.Background(), f, testConfig())
			row, _ := rep.Find(KeyDaemon)
			if row.Fix != tc.want {
				t.Errorf("fix = %v, want %v (%s)", row.Fix, tc.want, row.Detail)
			}
			assertRealCommand(t, row)
		})
	}
}

func TestProvisionOutcomeComesFromTheFinalLine(t *testing.T) {
	cases := []struct {
		name    string
		status  int
		body    string
		wantOK  bool
		wantIn  string
		wantCmd string
	}{
		{
			name:   "success",
			status: 200,
			body: "→ Chat model: Gemma-4-E4B-it-GGUF\n" +
				"→ Pulling Gemma-4-E4B-it-GGUF via Lemonade…\n" +
				"✓ Gemma-4-E4B-it-GGUF downloaded.\n" +
				"✓ Provisioning complete. Re-run GET /v1/gaia/init to confirm readiness.\n",
			wantOK: true,
		},
		{
			// A committed 200 cannot change status mid-stream, so a failure
			// arrives as HTTP 200 with a ✗ final line.
			name:   "failed inside a committed 200",
			status: 200,
			body: "→ Chat model: Gemma-4-E4B-it-GGUF\n" +
				"✓ Lemonade reachable\n" +
				"✗ Provisioning failed: ConnectionError: connection refused\n" +
				"✗ The model was not downloaded. Check the Lemonade Server logs, then retry.\n",
			wantOK:  false,
			wantIn:  "not downloaded",
			wantCmd: "gaia init",
		},
		{
			name:   "refused before streaming",
			status: 503,
			body:   "✗ Local Lemonade Server is not reachable at http://localhost:8000/api/v1.\n",
			wantOK: false,
			wantIn: "not reachable",
			// Resolved per machine, and taken from the REMEDY rather than the raw
			// launcher: the launcher is empty where nothing is installed, and an
			// empty wantCmd is skipped by the table — a silently-disabled assertion.
			wantCmd: lemonadeStartRemedy().Command,
		},
		{
			name:    "ends saying nothing",
			status:  200,
			body:    "",
			wantOK:  false,
			wantCmd: "gaia init",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFake()
			f.streamStatus, f.streamBody = tc.status, tc.body

			var seen []string
			res := Provision(context.Background(), f, testConfig(), func(l string) { seen = append(seen, l) })

			if res.OK != tc.wantOK {
				t.Fatalf("OK = %v, want %v (final: %q)", res.OK, tc.wantOK, res.Final)
			}
			if !f.called("POST", "/v1/gaia/init") {
				t.Error("provisioning did not POST /v1/gaia/init")
			}
			if len(seen) != len(res.Lines) {
				t.Errorf("streamed %d lines to the caller but recorded %d", len(seen), len(res.Lines))
			}
			if tc.wantIn != "" && !strings.Contains(res.Final+res.Diagnosis.String(), tc.wantIn) {
				t.Errorf("outcome does not mention %q: final=%q diag=%q", tc.wantIn, res.Final, res.Diagnosis)
			}
			if tc.wantCmd != "" && res.Diagnosis.Command != tc.wantCmd {
				t.Errorf("remedy command = %q, want %q", res.Diagnosis.Command, tc.wantCmd)
			}
		})
	}
}

func TestProvisionSurfacesATransportFailure(t *testing.T) {
	f := newFake()
	f.streamErr = &daemon.RequestError{Op: "stream the download", Detail: "connection refused"}

	res := Provision(context.Background(), f, testConfig(), nil)
	if res.OK {
		t.Fatal("a transport failure reported success")
	}
	// The TUI only ever dials the daemon, so a refused connection here is the
	// daemon being unreachable — telling the user to start Lemonade would send
	// them to the one process that is not involved.
	if res.Diagnosis.Command != "gaia daemon status" {
		t.Errorf("remedy = %q, want the daemon command", res.Diagnosis.Command)
	}
	if strings.Contains(res.Diagnosis.Cause, "Lemonade") {
		t.Errorf("a daemon transport failure was blamed on Lemonade: %q", res.Diagnosis.Cause)
	}
}

func TestCheckSurfacesATransportError(t *testing.T) {
	f := newFake()
	f.errs["GET /v1/gaia/init"] = &daemon.RequestError{
		Op: "call the agent", Detail: "context deadline exceeded"}

	rep := Check(context.Background(), f, testConfig())
	row, _ := rep.Find(KeyLemonade)
	if row.State != StateFailed {
		t.Fatalf("state = %s, want failed", row.State.Word())
	}
	assertRealCommand(t, row)
	if !strings.Contains(row.Detail, "did not finish in time") {
		t.Errorf("a timeout should be diagnosed as one, got %q", row.Detail)
	}
}

func TestReportSummary(t *testing.T) {
	rep := Check(context.Background(), newFake().with("GET /v1/gaia/init", 503, initUnreachable), testConfig())
	if got := rep.Summary(); got != "2 of 4 ready" {
		t.Errorf("summary = %q, want %q", got, "2 of 4 ready")
	}
	ready := Check(context.Background(), newFake(), testConfig())
	if got := ready.Summary(); got != "ready" {
		t.Errorf("summary = %q, want %q", got, "ready")
	}
}

// Sanity: the fake never hands out something the real transport would not.
var _ Transport = (*fakeTransport)(nil)

func TestErrorsAsWiringForDaemonTypes(t *testing.T) {
	// Guards the ladder's errors.As chain against a future refactor that wraps
	// daemon errors — a wrapped NotRunningError must still map to `daemon start`.
	wrapped := fmt.Errorf("attach failed: %w", &daemon.NotRunningError{Path: "/tmp/i.json"})
	d := Ladder{AgentID: "gaia"}.Error("reach the background service", wrapped)
	if d.Command != "gaia daemon start" {
		t.Fatalf("wrapped NotRunningError diagnosed as %q", d.Command)
	}
}

func TestUnknownStateNeverRendersAsReady(t *testing.T) {
	rep := Check(context.Background(), newFake().with("GET /v1/gaia/init", 200, initUnknownVersion), testConfig())
	if rep.Ready() {
		t.Fatal("a report with an indeterminate row reported itself ready")
	}
	if rep.Blocked() {
		t.Fatal("an indeterminate row must not block the launch")
	}
	row, _ := rep.Find(KeyLemonade)
	if row.State.Marker() == StateOK.Marker() {
		t.Fatal("unknown must not render with the ok marker")
	}
	if !strings.Contains(row.Detail, "no version") {
		t.Errorf("unknown row does not explain itself: %q", row.Detail)
	}
}

// Live-captured shape: an indeterminate Local AI row sitting ABOVE a genuinely
// missing model. The cursor must land on the failure, not on the row with
// nothing to do.
func TestFocusPrefersTheFailureOverAnIndeterminateRow(t *testing.T) {
	f := newFake().with("GET /v1/gaia/init", 503, initUnknownVersionModelMissing)
	rep := Check(context.Background(), f, testConfig())

	lemonade, _ := rep.Find(KeyLemonade)
	if lemonade.State != StateUnknown {
		t.Fatalf("lemonade row = %s, want unknown", lemonade.State.Word())
	}
	idx := rep.FirstAttention()
	if idx < 0 || rep.Rows[idx].Key != KeyModel {
		t.Fatalf("attention is on %q, want the model row\n%s", rep.Rows[idx].Key, rep)
	}
}

func TestConfigForDerivesTheDisplayName(t *testing.T) {
	if got := ConfigFor("analyst", ""); got.AgentName != "Analyst" {
		t.Errorf("missing display name = %q, want it derived from the id", got.AgentName)
	}
}

// The relay answers 503 with `{"detail": ...}` and NO readiness object when the
// sidecar dies between the agents listing and this probe. Decoded into value
// structs that read as "Lemonade unreachable" — the wrong subject AND the wrong
// remedy, pointing away from the process that actually died.
func TestARelayErrorIsNotReportedAsLemonadeBeingDown(t *testing.T) {
	f := newFake().with("GET /v1/gaia/init", 503,
		`{"detail":"agent 'gaia' is registered but its sidecar is not running (POST /daemon/v1/agents/gaia/ensure), then retry."}`)

	rep := Check(context.Background(), f, testConfig())
	row, _ := rep.Find(KeyLemonade)

	if row.State != StateFailed {
		t.Fatalf("state = %s, want failed", row.State.Word())
	}
	if strings.Contains(row.Remedy.Command, "lemonade-server") {
		t.Errorf("a dead sidecar was blamed on Lemonade: %q", row.Remedy.Command)
	}
	if !strings.Contains(row.Remedy.Command, "start-agent") {
		t.Errorf("remedy = %q, want the sidecar restart", row.Remedy.Command)
	}
	if strings.Contains(row.Line, "not running at ") && !strings.Contains(row.Line, "http") {
		t.Errorf("the row shows an empty base URL: %q", row.Line)
	}
	assertRealCommand(t, row)
}

// `d` on the model row has to show the body the probe actually saw, even when
// an earlier row is what failed.
func TestTheModelRowKeepsItsRawAnswerWhenLemonadeFails(t *testing.T) {
	f := newFake().with("GET /v1/gaia/init", 503, initUnreachable)
	rep := Check(context.Background(), f, testConfig())

	row, _ := rep.Find(KeyModel)
	if !strings.Contains(row.Raw, "lemonade") {
		t.Errorf("the model row lost the probe body it was answered with: %q", row.Raw)
	}
	if row.Detail == "" {
		t.Error("the model row does not say what it is waiting on")
	}
}

func TestNonASCIIAgentIDDoesNotCorruptTheDisplayName(t *testing.T) {
	if got := ConfigFor("émail", "").AgentName; got != "Émail" {
		t.Errorf("display name = %q, want %q", got, "Émail")
	}
}

// The three questions an indeterminate row answers differently. Pinned as a
// test because "unknown does not block" is one refactor away from becoming
// "unknown passes", which is the exact dishonesty this state exists to prevent.
func TestIndeterminateIsNeitherReadyNorBlocking(t *testing.T) {
	unknown := Check(context.Background(),
		newFake().with("GET /v1/gaia/init", 200, initUnknownVersion), testConfig())
	broken := Check(context.Background(),
		newFake().with("GET /v1/gaia/init", 503, initUnreachable), testConfig())
	ready := Check(context.Background(), newFake(), testConfig())

	for _, tc := range []struct {
		name                  string
		rep                   Report
		wantReady, wantBlocks bool
	}{
		{"proven ready", ready, true, false},
		{"indeterminate", unknown, false, false},
		{"proven broken", broken, false, true},
	} {
		if got := tc.rep.Ready(); got != tc.wantReady {
			t.Errorf("%s: Ready() = %v, want %v", tc.name, got, tc.wantReady)
		}
		if got := tc.rep.Blocked(); got != tc.wantBlocks {
			t.Errorf("%s: Blocked() = %v, want %v", tc.name, got, tc.wantBlocks)
		}
	}

	// And the unknown row is visibly distinct from an ok one, without colour.
	row, _ := unknown.Find(KeyLemonade)
	if row.State.Marker() == StateOK.Marker() || row.State.Word() == StateOK.Word() {
		t.Error("an indeterminate row is indistinguishable from a passing one")
	}
}

// --- the agent that is running but not answering ------------------------------

// The wedge, end to end: an agent whose event loop is parked keeps its pid and
// its port, so the daemon's listing says "running" forever. The row has to name
// the agent rather than let every row below it time out.
func TestAnAgentThatNeverAnswersIsNamed(t *testing.T) {
	f := newFake().with("GET /v1/gaia/health", 503, healthWedged)
	rep := Check(context.Background(), f, testConfig())

	sidecar, _ := rep.Find(KeySidecar)
	if sidecar.State != StateFailed {
		t.Fatalf("a sidecar that answers nothing = %s, want failed\n%s", sidecar.State.Word(), rep)
	}
	if !strings.Contains(sidecar.Line, "not answering") {
		t.Errorf("the row does not say what is wrong: %q", sidecar.Line)
	}
	assertRealCommand(t, sidecar)
	if !strings.Contains(sidecar.Remedy.Command, "stop-agent") {
		t.Errorf("the remedy attaches to the stuck process instead of replacing it: %q",
			sidecar.Remedy.Command)
	}
	if sidecar.Remedy.Where == "" {
		t.Error("the row does not say where to look next")
	}

	// And the blocker the user is sent to is the agent.
	blocker, _ := rep.Blocker()
	if blocker.Key != KeySidecar {
		t.Errorf("blocker = %q, want the agent row\n%s", blocker.Key, rep)
	}
}

// The probe is one bounded call and it has to be visible in `d` — including what
// it cost, which is the only way anyone can tell whether the gate got slower.
func TestTheLivenessProbeIsOneCallAndIsRecorded(t *testing.T) {
	f := newFake()
	rep := Check(context.Background(), f, testConfig())

	row, _ := rep.Find(KeySidecar)
	if row.State != StateOK {
		t.Fatalf("a healthy agent = %s, want ok\n%s", row.State.Word(), rep)
	}
	for _, want := range []string{"sidecar probe: GET /v1/gaia/health", "HTTP 200", "ms"} {
		if !strings.Contains(row.Raw, want) {
			t.Errorf("the probe trace does not record %q:\n%s", want, row.Raw)
		}
	}
	// The agents listing it started from is still there — `d` must show both.
	if !strings.Contains(row.Raw, "agent_id") {
		t.Errorf("the probe trace replaced the agents listing:\n%s", row.Raw)
	}

	var probes int
	for _, c := range f.calls {
		if c.method == "GET" && c.path == "/v1/gaia/health" {
			probes++
		}
	}
	if probes != 1 {
		t.Errorf("the gate probed the agent %d times; one launch is one probe", probes)
	}
}

// A probe that never reached the daemon is not an agent that went quiet. The
// row's line and its cause have to name the same subject, or the user restarts
// the wrong thing.
func TestADaemonThatDropsTheProbeIsNotReportedAsASilentAgent(t *testing.T) {
	f := newFake()
	f.errs["GET /v1/gaia/health"] = &daemon.RequestError{
		Op: "call GET /v1/gaia/health through the background service", Detail: "connection refused"}

	rep := Check(context.Background(), f, testConfig())
	row, _ := rep.Find(KeySidecar)

	if row.State != StateFailed {
		t.Fatalf("state = %s, want failed\n%s", row.State.Word(), rep)
	}
	if strings.Contains(row.Line, "not answering") {
		t.Errorf("a dead background service was reported as a silent agent: %q", row.Line)
	}
	if !strings.Contains(row.Detail, "background service") {
		t.Errorf("the row does not name what actually failed: %q", row.Detail)
	}
	if strings.Contains(row.Remedy.Command, "stop-agent") {
		t.Errorf("the remedy restarts an agent over a background service that is gone: %q",
			row.Remedy.Command)
	}
	assertRealCommand(t, row)
}

// A sidecar the daemon has not started is not one that stopped answering: the
// row must keep the remedy that STARTS it, and must not pay for a probe of a
// process that is not there.
func TestAStoppedSidecarIsNotProbedOrDiagnosedAsWedged(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
	rep := Check(context.Background(), f, testConfig())

	row, _ := rep.Find(KeySidecar)
	if row.Fix != FixStartSidecar {
		t.Errorf("fix = %v, want the one-key start", row.Fix)
	}
	if strings.Contains(row.Remedy.Command, "stop-agent") {
		t.Errorf("a sidecar that was never started is told to stop first: %q", row.Remedy.Command)
	}
	if f.called("GET", "/v1/gaia/health") {
		t.Error("the gate probed a sidecar the daemon says is not running")
	}
}

// FastAPI's validation errors put an ARRAY in `detail`. Only the `msg` fields are
// language a person can read; the array itself must never reach a row.
func TestAValidationErrorNeverPutsRawJSONOnTheScreen(t *testing.T) {
	got := jsonDetail([]byte(validationError))
	for _, unwanted := range []string{`{"type"`, `"loc"`, "[{", `"input"`} {
		if strings.Contains(got, unwanted) {
			t.Errorf("raw validation JSON reached the row (%q): %q", unwanted, got)
		}
	}
	if !strings.Contains(got, "Field required") {
		t.Errorf("the readable half of the error was dropped: %q", got)
	}
}

// Every upstream message here carries `→` and em dashes. A byte-wise truncation
// cuts through one and renders mojibake that no trimming repairs.
func TestTruncationNeverSplitsAMultibyteCharacter(t *testing.T) {
	// A message whose only content is multibyte, longer than either cap.
	long := strings.Repeat("Settings → Connections — reconnect. ", 40)
	for name, got := range map[string]string{
		"firstSentence": firstSentence(strings.ReplaceAll(long, ". ", " ")),
		"detailSuffix":  detailSuffix(strings.ReplaceAll(long, ". ", " ")),
		"clip":          clip(long, 7),
	} {
		if !utf8.ValidString(got) {
			t.Errorf("%s produced invalid UTF-8: %q", name, got)
		}
		if strings.Contains(got, "�") {
			t.Errorf("%s produced a replacement character: %q", name, got)
		}
	}
	// And it still truncates: a cap that never fires is not a cap.
	if got := clip(long, 7); len([]rune(got)) > 8 {
		t.Errorf("clip(…, 7) returned %d runes: %q", len([]rune(got)), got)
	}
}

// --- the context-window shortfall -------------------------------------------

// A green row over a broken state. The server is healthy, the model is
// downloaded, short turns work — and it came up with a window far under the one
// this machine's profile pins, so a document-sized request comes back as a
// context-length error. The row used to render that as a plain green
// "· 25037 context", which reads as a fact about a working system rather than a
// warning about one that fails on large input.
func TestAModelLoadedBelowTheProfileWindowIsNotReportedAsReady(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("GET /v1/gaia/init", 200, initCtxShortfall), testConfig())
	row, ok := rep.Find(KeyModel)
	if !ok {
		t.Fatalf("no model row:\n%s", rep)
	}

	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown\n%s", row.State.Word(), rep)
	}
	if row.State.Marker() == StateOK.Marker() {
		t.Error("a short window renders with the ok marker")
	}
	if rep.Ready() {
		t.Error("a report with a short window called itself ready")
	}

	// Not a hard fail: ordinary turns work, so blocking the launch would be worse
	// than the shortfall it is warning about.
	if rep.Blocked() {
		t.Errorf("a short window blocked the launch\n%s", rep)
	}
	if _, blocked := rep.Blocker(); blocked {
		t.Error("a short window was reported as a blocker")
	}
}

// The row has to show BOTH numbers. A colour or a marker says something is off;
// only "25037 of 64K" tells the user which inputs will fail.
func TestTheShortfallRowShowsTheActualWindowAndTheExpectedOne(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("GET /v1/gaia/init", 200, initCtxShortfall), testConfig())
	row, _ := rep.Find(KeyModel)

	for _, want := range []string{"25037", humanCtx(profileCtxTarget())} {
		if !strings.Contains(row.Line, want) {
			t.Errorf("line = %q, want it to show %q", row.Line, want)
		}
	}
	// And the detail says what actually breaks, in terms of what a user does.
	for _, want := range []string{"long document", "context-length error"} {
		if !strings.Contains(row.Detail, want) {
			t.Errorf("detail = %q, want it to mention %q", row.Detail, want)
		}
	}
	if row.Remedy.Command == "" {
		t.Error("the shortfall row has nothing to run")
	}
	assertRealCommand(t, row)
	assertRunnable(t, row.Remedy.Command)
	// Modern Lemonade reads no context-size variable (#3320), so naming one
	// would promise a fix the restart cannot deliver.
	if strings.Contains(row.Remedy.Command, "LEMONADE_CTX_SIZE") {
		t.Errorf("the remedy names a variable Lemonade never reads: %q", row.Remedy.Command)
	}
	// It must not promise a cure it cannot deliver — the window is set at load
	// time, so a restart is a good bet and not a guarantee.
	assertHedged(t, row.Remedy.Action)
	// And it must not blame the hardware. Enumerating every llama-server alive on
	// the measured machine found 5 loads at the full 65536 across the same twelve
	// hours the 36807 one happened in, so "this machine cannot" was false — and it
	// is the one wording that leaves the user nothing to do.
	assertNoCapacityClaim(t, row.Remedy.Action)
	// And it must be runnable FROM THIS STATE. The row only exists once a model
	// is loaded, so a server already holds the port; lemond has no stop or
	// restart verb, and a second instance exits with "already in use". A restart
	// remedy that omits the stop is a command the caller cannot run.
	assertSaysToStopFirst(t, row.Remedy)
	// Nothing here is safe to do from the TUI.
	if row.Fix != FixNone {
		t.Errorf("the shortfall row offers a one-key fix: %v", row.Fix)
	}
}

// A window AT or ABOVE the profile is simply fine, and a model that is not
// loaded yet has no window to judge — reporting either as a shortfall would make
// the warning fire constantly and mean nothing.
func TestAnAdequateOrUnknownWindowIsNotWarnedAbout(t *testing.T) {
	target := profileCtxTarget()
	cases := map[string]struct {
		body      string
		wantState State
		wantLine  string
	}{
		"exactly the profile window": {
			body: fmt.Sprintf(`{"ready":true,"lemonade":{"reachable":true,`+
				`"base_url":"http://x","version":"10.10.0","min_version":"10.2.0","compatible":true},`+
				`"model":{"id":"M","present":true,"ctx_size":%d},"hint":null}`, target),
			wantState: StateOK,
		},
		"above the profile window": {
			body: fmt.Sprintf(`{"ready":true,"lemonade":{"reachable":true,`+
				`"base_url":"http://x","version":"10.10.0","min_version":"10.2.0","compatible":true},`+
				`"model":{"id":"M","present":true,"ctx_size":%d},"hint":null}`, target*2),
			wantState: StateOK,
		},
		"downloaded but not loaded": {
			body: `{"ready":true,"lemonade":{"reachable":true,` +
				`"base_url":"http://x","version":"10.10.0","min_version":"10.2.0","compatible":true},` +
				`"model":{"id":"M","present":true},"hint":null}`,
			wantState: StateOK,
			wantLine:  "not loaded yet",
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			rep := Check(context.Background(),
				newFake().with("GET /v1/gaia/init", 200, tc.body), testConfig())
			row, _ := rep.Find(KeyModel)
			if row.State != tc.wantState {
				t.Errorf("state = %s, want %s (line %q)", row.State.Word(), tc.wantState.Word(), row.Line)
			}
			if tc.wantLine != "" && !strings.Contains(row.Line, tc.wantLine) {
				t.Errorf("line = %q, want it to mention %q", row.Line, tc.wantLine)
			}
		})
	}
}

// The shortfall row is now DispositionHalt — the reported bug is exactly that
// it used to name itself and pass by in the 2500ms hold an indeterminate
// screen got, launching over an input that will fail. It now holds the
// screen for a person instead: no tick, no claim of starting, and the number
// the user needs (25037) still on screen — the naming was never the problem,
// the silent proceeding was.
func TestTheShortfallHoldsTheScreenAndStillNamesTheWindow(t *testing.T) {
	f := newFake().with("GET /v1/gaia/init", 200, initCtxShortfall)
	m := New(f, testConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	updated, cmd := updated.(Model).Update(reportMsg{rep: Check(context.Background(), f, testConfig())})
	m = updated.(Model)

	// cmd is no longer nil — it carries the halt Outcome the host listens
	// for — but it must not be the tick toward an automatic ProceedMsg.
	if cmd != nil {
		if _, ok := cmd().(proceedTickMsg); ok {
			t.Fatal("a Halt row scheduled the auto-proceed tick — the launch must wait for a person, not a timer")
		}
	}
	screen := ansi.Strip(m.View())
	if strings.Contains(screen, "Starting anyway") {
		t.Errorf("the screen still claims to be starting anyway over a Halt row:\n%s", screen)
	}
	if strings.Contains(screen, "Starting "+testConfig().AgentName+"…") {
		t.Errorf("the screen claims to be starting while a Halt row is unresolved:\n%s", screen)
	}
	if !strings.Contains(screen, "25037") {
		t.Errorf("the hand-off never shows the window that will fail:\n%s", screen)
	}
	assertFits(t, splitLines(screen), 80, 24)
}

// assertSaysToStopFirst checks a remedy aimed at a RUNNING server either names a
// command that restarts in place (systemd, launchd) or tells the user to stop it
// first. Anything else is a command that exits with "address already in use" the
// moment it is followed.
func assertSaysToStopFirst(t *testing.T, r Remedy) {
	t.Helper()
	restartsInPlace := strings.Contains(r.Command, "restart") ||
		strings.Contains(r.Command, "kickstart")
	if restartsInPlace {
		return
	}
	for _, phrase := range []string{"Stop the running server", "Stop it", "Quit it"} {
		if strings.Contains(r.Action, phrase) {
			return
		}
	}
	t.Errorf("this remedy starts a server while one is already running, and never says "+
		"to stop it: action=%q command=%q", r.Action, r.Command)
}

// Every row that describes a server which is UP has to survive that check: they
// all fire while something holds the port, so none of them may print a bare
// start command.
func TestEveryRemedyAimedAtARunningServerSaysToStopItFirst(t *testing.T) {
	cases := map[string]string{
		// Reachable, but its model list could not be read.
		"model list unreadable": initModelListUnreadable,
		// Loaded, healthy, and below the profile window.
		"context shortfall": initCtxShortfall,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			status := 503
			if name == "context shortfall" {
				status = 200
			}
			rep := Check(context.Background(),
				newFake().with("GET /v1/gaia/init", status, body), testConfig())
			for _, key := range []string{KeyLemonade, KeyModel} {
				row, _ := rep.Find(key)
				if row.State == StateOK || row.State == StatePending {
					continue
				}
				assertSaysToStopFirst(t, row.Remedy)
			}
		})
	}

	// The ladder's timeout rung too: a server that answered and then stalled is
	// still holding the port.
	d := Ladder{AgentID: "gaia"}.Text("check the local AI", "the request timed out")
	assertSaysToStopFirst(t, d.AsRemedy())
}

// A launcher that CAN restart in place must not be told to stop first — that
// would be busywork, and systemctl/launchctl already do both halves.
func TestALauncherThatRestartsInPlaceIsNotToldToStopFirst(t *testing.T) {
	systemd := withProbe(
		fakeHostFor("linux", []string{"systemctl"},
			[]string{"/usr/bin/lemond", "/usr/lib/systemd/user/lemond.service"}, nil),
		lemonadeRestartRemedy)

	if !strings.Contains(systemd.Command, "restart") {
		t.Fatalf("expected an in-place restart command, got %q", systemd.Command)
	}
	if strings.Contains(systemd.Action, "Stop the running server") {
		t.Errorf("systemd restarts in place; telling the user to stop first is busywork: %q",
			systemd.Action)
	}
	assertSaysToStopFirst(t, systemd)
}

// assertHedged checks a remedy for a load-time-variable outcome promises a
// likelihood, not a fix.
func assertHedged(t *testing.T, action string) {
	t.Helper()
	for _, hedge := range []string{"usually", "often", "may", "might"} {
		if strings.Contains(action, hedge) {
			return
		}
	}
	t.Errorf("the remedy reads as a guaranteed fix for something set at load time: %q", action)
}

// assertNoCapacityClaim rejects wording that attributes a short window to the
// machine's capacity.
//
// The claim is false — the same box reached the full window five times the same
// day — and it is uniquely bad wording: every other remedy in this package ends
// with something the user can do, and "your machine cannot" ends the interaction
// instead. Guarded rather than merely fixed, because it reads plausibly and the
// data that disproves it is not in front of whoever edits this next.
func assertNoCapacityClaim(t *testing.T, action string) {
	t.Helper()
	for _, claim := range []string{
		"did not have the memory",
		"does not have the memory",
		"not enough memory",
		"machine cannot",
		"hardware",
	} {
		if strings.Contains(strings.ToLower(action), strings.ToLower(claim)) {
			t.Errorf("the remedy blames the machine's capacity (%q), which the measurements "+
				"contradict and which leaves the user nowhere to go: %q", claim, action)
		}
	}
}

// The shortfall remedy has to leave the user somewhere to go. It names WHEN the
// window is decided, which is the actionable part, and it does not name specific
// processes to close — the only candidates on the measured machine were ~35 MB of
// orphaned llama-servers that the memory evidence does not implicate, and this
// package cannot see them anyway: it dials the daemon and nothing else.
func TestTheShortfallRemedyPointsAtTheLoadMomentNotTheHardware(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("GET /v1/gaia/init", 200, initCtxShortfall), testConfig())
	row, _ := rep.Find(KeyModel)

	assertNoCapacityClaim(t, row.Remedy.Action)
	assertHedged(t, row.Remedy.Action)

	// It says when the window is decided, which is what makes "try again" sensible
	// rather than superstitious.
	if !strings.Contains(row.Remedy.Action, "when the model loads") {
		t.Errorf("the remedy never says when the window is chosen: %q", row.Remedy.Action)
	}
	// A vague "check for other processes" would be worse than nothing, so there is
	// no such hint — and no invented one.
	for _, vague := range []string{"other processes", "close other", "llama-server"} {
		if strings.Contains(strings.ToLower(row.Remedy.Action), vague) {
			t.Errorf("the remedy points at processes it cannot see and did not measure: %q",
				row.Remedy.Action)
		}
	}
}

// --- the sidecar's running mode ---------------------------------------------
//
// #4077: the launch used to send mode=user on everyone's behalf, so the daemon
// 409'd against a running dev sidecar AFTER this gate had passed and the chat
// header had rendered. The gate now answers the question the launch will ask.

func TestCheckAttachesToASidecarRunningInAnotherModeWithoutAPreference(t *testing.T) {
	t.Setenv("GAIA_GAIA_AGENT_MODE", "")
	f := newFake().with("GET /daemon/v1/agents", 200, agentsRunningDev)

	rep := Check(context.Background(), f, testConfig())

	row, ok := rep.Find(KeySidecar)
	if !ok {
		t.Fatalf("no sidecar row:\n%s", rep)
	}
	if row.State != StateOK {
		t.Fatalf("a launch with no mode preference must attach to the running "+
			"sidecar, got %s (%s)\n%s", row.State.Word(), row.Line, rep)
	}
}

func TestCheckRefusesASidecarRunningInTheOtherRequestedMode(t *testing.T) {
	t.Setenv("GAIA_GAIA_AGENT_MODE", "user")
	f := newFake().with("GET /daemon/v1/agents", 200, agentsRunningDev)

	rep := Check(context.Background(), f, testConfig())

	row, ok := rep.Find(KeySidecar)
	if !ok {
		t.Fatalf("no sidecar row:\n%s", rep)
	}
	if row.State != StateFailed {
		t.Fatalf("an EXPLICIT mode that differs must still conflict, got %s\n%s",
			row.State.Word(), rep)
	}
	if row.Line != "running in dev mode, user requested" {
		t.Errorf("row line = %q, want %q", row.Line, "running in dev mode, user requested")
	}
	if row.Fix != FixRestartSidecar {
		t.Errorf("row fix = %v, want FixRestartSidecar", row.Fix)
	}
	blocker, has := rep.Blocker()
	if !has || blocker.Key != KeySidecar {
		t.Fatalf("the sidecar row must block the launch:\n%s", rep)
	}
	// The conflict is decided from the listing, so nothing below it is probed —
	// and the chat composer is never reached with a guaranteed 409 behind it.
	if f.called("GET", "/v1/gaia/init") {
		t.Error("a mode conflict must stop the walk, not fall through to init")
	}
	said := row.Remedy.Action + " " + row.Remedy.Command + " " + row.Detail
	for _, want := range []string{"gaia daemon stop-agent gaia", "GAIA_GAIA_AGENT_MODE"} {
		if !strings.Contains(said, want) {
			t.Errorf("the remedy must name %q; got %q", want, said)
		}
	}
}

func TestCheckAcceptsASidecarRunningInTheRequestedMode(t *testing.T) {
	t.Setenv("GAIA_GAIA_AGENT_MODE", "dev")
	f := newFake().with("GET /daemon/v1/agents", 200, agentsRunningDev)

	rep := Check(context.Background(), f, testConfig())

	row, _ := rep.Find(KeySidecar)
	if row.State != StateOK {
		t.Fatalf("a matching explicit mode must pass, got %s (%s)\n%s",
			row.State.Word(), row.Line, rep)
	}
}
