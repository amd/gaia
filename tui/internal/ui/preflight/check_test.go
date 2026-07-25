package preflight

import (
	"context"
	"fmt"
	"io"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// --- fixtures ---------------------------------------------------------------
//
// Every body below is the REAL shape the corresponding endpoint serializes:
// InitResponse / InitLemonadeStatus / InitModelStatus from
// gaia_agent_email/api_routes.py, the connectors body from connector_routes.py,
// and SidecarRegistry.list_agents from src/gaia/daemon/sidecars/registry.py.

const (
	agentsRunning = `{"agents":[{"agent_id":"email","state":"running","mode":"frozen",` +
		`"pid":41999,"port":51234,"base_url":"http://127.0.0.1:51234","api_version":"2.3",` +
		`"agent_version":"0.5.0","started_at":1750000000.0,"dev_src_dir":null}]}`

	agentsStopped = `{"agents":[{"agent_id":"email","state":"stopped","mode":null,` +
		`"pid":null,"port":null,"base_url":null,"api_version":null,"agent_version":null,` +
		`"started_at":null,"dev_src_dir":null}]}`

	agentsEmpty = `{"agents":[]}`

	initReady = `{"ready":true,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:8000/api/v1","version":"8.1.10","min_version":"8.1.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":true,"loadable":null,` +
		`"ctx_size":16384},"hint":null}`

	initUnreachable = `{"ready":false,"lemonade":{"reachable":false,` +
		`"base_url":"http://localhost:8000/api/v1","version":null,"min_version":"8.1.0",` +
		`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null,` +
		`"ctx_size":null},"hint":"Local Lemonade Server is not reachable at ` +
		"http://localhost:8000/api/v1 — start it with `lemonade-server serve` (or run `gaia init`), then retry.\"}"

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
		`"ctx_size":16384},"hint":null}`

	// Captured from a live sidecar: when ctx_size is null the serializer OMITS
	// the key entirely, so the parser must not depend on it being present.
	initModelMissing = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":"10.2.1","min_version":"10.2.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		"\"hint\":\"Model `Gemma-4-E4B-it-GGUF` not downloaded — run `gaia init` " +
		`(or pull it via Lemonade), then retry."}`

	// Lemonade answered /health but its /models read failed. `present:false` here
	// means "could not tell" — the hint is the ONLY thing that says so.
	initModelListUnreadable = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":"10.2.1","min_version":"10.2.0",` +
		`"compatible":true},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		`"hint":"Lemonade is reachable but its model list at http://localhost:13305/api/v1/models ` +
		`could not be read (ConnectionError: connection aborted). Make sure the server is healthy, then retry."}`

	connectorsReady = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":"you@gmail.com",` +
		`"scopes":["https://www.googleapis.com/auth/gmail.readonly","https://www.googleapis.com/auth/gmail.send"],` +
		`"can_send":true},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`

	connectorsNone = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":false,"account_email":null,"scopes":[],"can_send":false},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`

	// Captured live: a connected mailbox whose account_email is null (the store's
	// DEFAULT_ACCOUNT sentinel is mapped away rather than leaked to the UI).
	connectorsNoEmail = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":null,"scopes":[],"can_send":true},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`

	// Lemonade answers but advertises no version AND the model is missing — the
	// live shape that put an indeterminate row above a real failure.
	initUnknownVersionModelMissing = `{"ready":false,"lemonade":{"reachable":true,` +
		`"base_url":"http://localhost:13305/api/v1","version":null,"min_version":"10.2.0",` +
		`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null},` +
		"\"hint\":\"Model `Gemma-4-E4B-it-GGUF` not downloaded — run `gaia init` " +
		`(or pull it via Lemonade), then retry."}`

	connectorsNoSend = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":"you@gmail.com",` +
		`"scopes":["https://www.googleapis.com/auth/gmail.readonly"],"can_send":false},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`
)

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

	calls []call
}

func newFake() *fakeTransport {
	return &fakeTransport{
		info: DaemonInfo{PID: 41822, Port: 51230, APIVersion: "1.1"},
		bodies: map[string]Response{
			"GET /daemon/v1/agents":    {Status: 200, Body: []byte(agentsRunning)},
			"GET /v1/email/init":       {Status: 200, Body: []byte(initReady)},
			"GET /v1/email/connectors": {Status: 200, Body: []byte(connectorsReady)},
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

func (f *fakeTransport) EnsureAgent(context.Context, string) error { return f.ensureErr }

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
var realCommands = []string{
	"gaia daemon start",
	"gaia daemon restart",
	"gaia daemon start-agent ",
	"gaia daemon agents",
	"gaia hub install ",
	"gaia init",
	"lemonade-server serve",
	"gaia connectors connect ",
	"gaia connectors grants grant ",
}

func assertRealCommand(t *testing.T, row Row) {
	t.Helper()
	cmd := row.Remedy.Command
	if cmd == "" {
		t.Fatalf("row %q failed with no command to run: %+v", row.Key, row.Remedy)
	}
	for _, prefix := range realCommands {
		if strings.HasPrefix(cmd, prefix) {
			if strings.Contains(cmd, "<") {
				t.Fatalf("row %q remedy still has a placeholder: %q", row.Key, cmd)
			}
			return
		}
	}
	t.Fatalf("row %q remedy names a command that does not exist: %q", row.Key, cmd)
}

func TestCheck(t *testing.T) {
	tests := []struct {
		name string
		// build returns the transport for this scenario.
		build func() *fakeTransport
		// wantStates is keyed by row key. Rows omitted are not asserted.
		wantStates map[string]State
		wantReady  bool
		// wantBlocker is the key of the row that must be the first failure, "" for none.
		wantBlocker string
		// wantIn asserts substrings on the blocking row's rendered remedy.
		wantIn []string
		// wantFix is the fix the blocking row offers.
		wantFix FixKind
		// mustNotCall names paths the walk must not reach (stop at first failure).
		mustNotCall []call
	}{
		{
			name:  "all ready",
			build: newFake,
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateOK,
				KeyModel: StateOK, KeyMailbox: StateOK,
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
				KeyModel: StatePending, KeyMailbox: StatePending,
			},
			wantBlocker: KeyDaemon,
			wantIn:      []string{"gaia daemon start"},
			wantFix:     FixStartDaemon,
			mustNotCall: []call{{"GET", "/daemon/v1/agents"}, {"GET", "/v1/email/init"}},
		},
		{
			name: "daemon speaks the wrong contract",
			build: func() *fakeTransport {
				f := newFake()
				f.attachErr = &daemon.VersionError{Have: "1.0", Reason: "it predates the agent relay"}
				return f
			},
			wantStates:  map[string]State{KeyDaemon: StateFailed},
			wantBlocker: KeyDaemon,
			wantIn:      []string{"gaia daemon restart", "host API v1.0"},
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
			wantIn:      []string{"gaia daemon start-agent email"},
			wantFix:     FixStartSidecar,
			mustNotCall: []call{{"GET", "/v1/email/init"}},
		},
		{
			name: "agent not installed",
			build: func() *fakeTransport {
				return newFake().with("GET /daemon/v1/agents", 200, agentsEmpty)
			},
			wantStates:  map[string]State{KeySidecar: StateFailed},
			wantBlocker: KeySidecar,
			wantIn:      []string{"gaia hub install email"},
			wantFix:     FixNone,
		},
		{
			name: "lemonade unreachable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 503, initUnreachable)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateFailed,
				KeyModel: StatePending, KeyMailbox: StatePending,
			},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"lemonade-server serve", "not running"},
			// The sidecar cannot install or launch Lemonade; pretending otherwise
			// would be a key that does nothing.
			wantFix:     FixNone,
			mustNotCall: []call{{"GET", "/v1/email/connectors"}},
		},
		{
			name: "lemonade too old",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 503, initTooOld)
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
				return newFake().with("GET /v1/email/init", 200, initUnknownVersion)
			},
			wantStates: map[string]State{
				KeyDaemon: StateOK, KeySidecar: StateOK, KeyLemonade: StateUnknown,
				KeyModel: StateOK, KeyMailbox: StateOK,
			},
			// Unknown is not ready — but it does not block either, matching the
			// sidecar's own policy of not failing on an unparseable version.
			wantReady:   false,
			wantBlocker: "",
		},
		{
			name: "model missing",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 503, initModelMissing)
			},
			wantStates: map[string]State{
				KeyLemonade: StateOK, KeyModel: StateFailed, KeyMailbox: StatePending,
			},
			wantBlocker: KeyModel,
			wantIn:      []string{"gaia init", "Gemma-4-E4B-it-GGUF"},
			wantFix:     FixPullModel,
		},
		{
			name: "no mailbox connected",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/connectors", 200, connectorsNone)
			},
			wantStates: map[string]State{
				KeyModel: StateOK, KeyMailbox: StateFailed,
			},
			wantBlocker: KeyMailbox,
			wantIn:      []string{"gaia connectors connect google", "installed:email"},
			wantFix:     FixConnectMailbox,
		},
		{
			name: "mailbox connected but send not granted",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/connectors", 200, connectorsNoSend)
			},
			wantStates:  map[string]State{KeyMailbox: StateFailed},
			wantBlocker: KeyMailbox,
			wantIn: []string{
				"you@gmail.com", "send not allowed",
				"gaia connectors grants grant google installed:email",
				"https://www.googleapis.com/auth/gmail.send",
			},
			wantFix: FixConnectMailbox,
		},
		{
			// present:false must NOT be reported as "download the model" when the
			// sidecar could not read the list to begin with.
			name: "lemonade reachable but its model list is unreadable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 503, initModelListUnreadable)
			},
			wantStates: map[string]State{
				KeyLemonade: StateFailed, KeyModel: StatePending,
			},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"model list", "lemonade-server serve"},
			wantFix:     FixNone,
		},
		{
			name: "sidecar relay is down",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 502,
					`{"detail":"the 'email' sidecar is registered but not running (POST /daemon/v1/agents/email/ensure) and retry."}`)
			},
			wantStates:  map[string]State{KeyLemonade: StateFailed},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"gaia daemon start-agent email"},
		},
		{
			name: "init answers with something unreadable",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/init", 200, `<html>not json</html>`)
			},
			wantStates:  map[string]State{KeyLemonade: StateFailed},
			wantBlocker: KeyLemonade,
			wantIn:      []string{"gaia daemon start-agent email"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := tt.build()
			rep := Check(context.Background(), f, EmailConfig())

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
				assertRealCommand(t, blocker)
				if blocker.Fix != tt.wantFix {
					t.Errorf("blocker fix = %v, want %v", blocker.Fix, tt.wantFix)
				}
				text := fmt.Sprintf("%s %s %s %s %s",
					blocker.Line, blocker.Detail, blocker.Remedy.Action,
					blocker.Remedy.Command, blocker.Remedy.Where)
				for _, want := range tt.wantIn {
					if !strings.Contains(text, want) {
						t.Errorf("blocking row does not mention %q; it says: %s", want, text)
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
		"unreachable": newFake().with("GET /v1/email/init", 503, initUnreachable),
		"too old":     newFake().with("GET /v1/email/init", 503, initTooOld),
		"model":       newFake().with("GET /v1/email/init", 503, initModelMissing),
		"mailbox":     newFake().with("GET /v1/email/connectors", 200, connectorsNone),
		"no send":     newFake().with("GET /v1/email/connectors", 200, connectorsNoSend),
		"sidecar":     newFake().with("GET /daemon/v1/agents", 200, agentsStopped),
	}
	for name, f := range scenarios {
		t.Run(name, func(t *testing.T) {
			rep := Check(context.Background(), f, EmailConfig())
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
		{"version skew", &daemon.VersionError{Have: "1.0", Reason: "too old"}, FixNone},
		{"would not start", &daemon.StartError{Reason: "port bind failed"}, FixNone},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFake()
			f.attachErr = tc.err
			rep := Check(context.Background(), f, EmailConfig())
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
			body: "→ Email triage model: Gemma-4-E4B-it-GGUF\n" +
				"→ Pulling Gemma-4-E4B-it-GGUF via Lemonade…\n" +
				"✓ Gemma-4-E4B-it-GGUF downloaded.\n" +
				"✓ Provisioning complete. Re-run GET /v1/email/init to confirm readiness.\n",
			wantOK: true,
		},
		{
			// A committed 200 cannot change status mid-stream, so a failure
			// arrives as HTTP 200 with a ✗ final line.
			name:   "failed inside a committed 200",
			status: 200,
			body: "→ Email triage model: Gemma-4-E4B-it-GGUF\n" +
				"✓ Lemonade reachable\n" +
				"✗ Provisioning failed: ConnectionError: connection refused\n" +
				"✗ The model was not downloaded. Check the Lemonade Server logs, then retry.\n",
			wantOK:  false,
			wantIn:  "not downloaded",
			wantCmd: "gaia init",
		},
		{
			name:    "refused before streaming",
			status:  503,
			body:    "✗ Local Lemonade Server is not reachable at http://localhost:8000/api/v1.\n",
			wantOK:  false,
			wantIn:  "not reachable",
			wantCmd: "lemonade-server serve",
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
			res := Provision(context.Background(), f, EmailConfig(), func(l string) { seen = append(seen, l) })

			if res.OK != tc.wantOK {
				t.Fatalf("OK = %v, want %v (final: %q)", res.OK, tc.wantOK, res.Final)
			}
			if !f.called("POST", "/v1/email/init") {
				t.Error("provisioning did not POST /v1/email/init")
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

	res := Provision(context.Background(), f, EmailConfig(), nil)
	if res.OK {
		t.Fatal("a transport failure reported success")
	}
	if res.Diagnosis.Command != "lemonade-server serve" {
		t.Errorf("remedy = %q, want the lemonade command", res.Diagnosis.Command)
	}
}

// A generic agent with no extras gets the four generic rows and nothing
// email-specific — the mailbox check belongs behind the extras hook.
func TestGenericAgentHasNoMailboxRow(t *testing.T) {
	f := newFake()
	f.bodies["GET /daemon/v1/agents"] = Response{Status: 200,
		Body: []byte(strings.ReplaceAll(agentsRunning, `"email"`, `"analyst"`))}
	f.bodies["GET /v1/analyst/init"] = Response{Status: 200, Body: []byte(initReady)}

	rep := Check(context.Background(), f, Config{AgentID: "analyst", AgentName: "Analyst"})
	if len(rep.Rows) != 4 {
		t.Fatalf("generic agent got %d rows, want 4: %s", len(rep.Rows), rep)
	}
	if _, ok := rep.Find(KeyMailbox); ok {
		t.Error("generic agent should not get the email mailbox row")
	}
	if !rep.Ready() {
		t.Errorf("expected ready:\n%s", rep)
	}
	if f.called("GET", "/v1/analyst/connectors") {
		t.Error("generic agent must not probe the email connectors route")
	}
}

func TestCheckSurfacesATransportError(t *testing.T) {
	f := newFake()
	f.errs["GET /v1/email/init"] = &daemon.RequestError{
		Op: "call the agent", Detail: "context deadline exceeded"}

	rep := Check(context.Background(), f, EmailConfig())
	row, _ := rep.Find(KeyLemonade)
	if row.State != StateFailed {
		t.Fatalf("state = %s, want failed", row.State.Word())
	}
	assertRealCommand(t, row)
	if !strings.Contains(row.Detail, "did not respond") {
		t.Errorf("a timeout should be diagnosed as one, got %q", row.Detail)
	}
}

func TestReportSummary(t *testing.T) {
	rep := Check(context.Background(), newFake().with("GET /v1/email/init", 503, initUnreachable), EmailConfig())
	if got := rep.Summary(); got != "2 of 5 ready" {
		t.Errorf("summary = %q, want %q", got, "2 of 5 ready")
	}
	ready := Check(context.Background(), newFake(), EmailConfig())
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
	d := Ladder{AgentID: "email"}.Error("reach the background service", wrapped)
	if d.Command != "gaia daemon start" {
		t.Fatalf("wrapped NotRunningError diagnosed as %q", d.Command)
	}
}

func TestUnknownStateNeverRendersAsReady(t *testing.T) {
	rep := Check(context.Background(), newFake().with("GET /v1/email/init", 200, initUnknownVersion), EmailConfig())
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
	f := newFake().with("GET /v1/email/init", 503, initUnknownVersionModelMissing)
	rep := Check(context.Background(), f, EmailConfig())

	lemonade, _ := rep.Find(KeyLemonade)
	if lemonade.State != StateUnknown {
		t.Fatalf("lemonade row = %s, want unknown", lemonade.State.Word())
	}
	idx := rep.FirstAttention()
	if idx < 0 || rep.Rows[idx].Key != KeyModel {
		t.Fatalf("attention is on %q, want the model row\n%s", rep.Rows[idx].Key, rep)
	}
}

// A mailbox with no account email must still read as connected, not as the
// store's internal sentinel and not as an empty string.
func TestConnectedMailboxWithoutAnAccountEmail(t *testing.T) {
	f := newFake().with("GET /v1/email/connectors", 200, connectorsNoEmail)
	rep := Check(context.Background(), f, EmailConfig())

	row, _ := rep.Find(KeyMailbox)
	if row.State != StateOK {
		t.Fatalf("mailbox row = %s, want ok\n%s", row.State.Word(), rep)
	}
	if !strings.Contains(row.Line, "Gmail") || !strings.Contains(row.Line, "can send") {
		t.Errorf("mailbox line = %q", row.Line)
	}
	if strings.Contains(row.Line, "default") {
		t.Errorf("the store sentinel leaked into the UI: %q", row.Line)
	}
}

func TestConfigForAddsTheEmailExtrasOnlyForEmail(t *testing.T) {
	if got := ConfigFor("email", "Email"); len(got.Extras) != 1 || got.Extras[0].Key != KeyMailbox {
		t.Errorf("email config has extras %+v, want the mailbox check", got.Extras)
	}
	if got := ConfigFor("analyst", "Analyst"); len(got.Extras) != 0 {
		t.Errorf("a generic agent got email extras: %+v", got.Extras)
	}
	if got := ConfigFor("analyst", ""); got.AgentName != "Analyst" {
		t.Errorf("missing display name = %q, want it derived from the id", got.AgentName)
	}
}
