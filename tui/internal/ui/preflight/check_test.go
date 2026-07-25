package preflight

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"unicode/utf8"

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

	// can_send is false and the sign-in ITSELF never carried the send scope: no
	// grant can add it, only a new sign-in.
	connectorsNoSend = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":"you@gmail.com",` +
		`"scopes":["https://www.googleapis.com/auth/gmail.readonly"],"can_send":false},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`

	// can_send is false but the sign-in DOES carry the send scope — the agent was
	// never granted it. A different half is missing, so a different sentence.
	connectorsNoGrant = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":"you@gmail.com",` +
		`"scopes":["https://www.googleapis.com/auth/gmail.modify","https://www.googleapis.com/auth/gmail.send"],` +
		`"can_send":false},` +
		`{"provider":"microsoft","connected":false,"account_email":null,"scopes":[],"can_send":false}]}`

	// Both mailboxes linked and granted. The read route refuses to guess between
	// them, so nothing about either can be proven.
	connectorsBoth = `{"agent_id":"installed:email","providers":[` +
		`{"provider":"google","connected":true,"account_email":"you@gmail.com","scopes":[],"can_send":true},` +
		`{"provider":"microsoft","connected":true,"account_email":"you@outlook.com","scopes":[],"can_send":true}]}`

	// --- POST /v1/email/search: the credential probe ------------------------
	//
	// EmailSearchResponse (contract.py) for a one-message page. `count` is a
	// REQUIRED field there, so a fixture without it is not the real shape.
	searchOK = `{"schema_version":"2.5","query":null,"count":1,"messages":[{"id":"18f0",` +
		`"thread_id":"18f0","subject":"Welcome","from":"a@b.com","to":"you@gmail.com",` +
		`"date":"Tue, 1 Jul 2026 09:00:00 -0700","snippet":"hello","label_ids":["INBOX"]}],` +
		`"next_page_token":null}`

	// An inbox with nothing in it still proves the credential works.
	searchEmptyInbox = `{"schema_version":"2.5","query":null,"count":0,"messages":[],` +
		`"next_page_token":null}`

	// CAPTURED LIVE from this machine's broken Google connector — the exact body
	// that made the old row go green. The daemon owns the refresh token and
	// forwards short-lived access tokens to the sidecar; the connector list still
	// says connected:true, can_send:true while no token ever arrives.
	searchNoForwardedCredential = `{"detail":"no forwarded 'google' credential is available to the ` +
		`email sidecar. The connection may not be granted to this agent, or it was ` +
		"revoked/withdrawn. Connect and grant it in one command — no Agent UI required: " +
		"`gaia connectors connect google --scopes <scopes> --grant-agent installed:email`, " +
		`or use Settings -> Connections in the Agent UI. The daemon forwards a token on the next use."}`

	// ConnectionRevokedError -> 403 (api_routes.search_inbox's except-ladder).
	searchRevoked = `{"detail":"the stored 'google' connection was revoked upstream. ` +
		`Reconnect it, then retry."}`

	// The forwarded token lapsed between the daemon's re-forwards. The sidecar
	// says so itself ("Retry in a moment") — this one clears on its own, so it
	// must not cost the user a browser sign-in.
	// VERBATIM from forwarded_credentials._require_forwarded.
	searchTokenLapsed = `{"detail":"the forwarded 'google' access token has expired and the ` +
		"daemon has not re-forwarded a fresh one yet. Retry in a moment; if it persists, " +
		"reconnect with `gaia connectors connect google --scopes <scopes>` (or Settings -> " +
		`Connections in the Agent UI)."}`

	// The RELAY's own 502: the sidecar died between the agents listing and this
	// call. Says nothing about the mailbox, so it must not fail the row.
	// VERBATIM from relay.py's httpx.HTTPError handler — the "sidecar for agent"
	// marker relayGaveUp keys on is not pinned on the Python side, so a reword
	// there has to break a test here.
	searchRelayDown = `{"detail":"sidecar for agent 'email' at http://127.0.0.1:57193 did not ` +
		`answer (ConnectError: All connection attempts failed). It may have died after ` +
		"registration — re-ensure it (POST /daemon/v1/agents/email/ensure) and retry.\"}"

	// The relay's own 503, when the sidecar is gone by the time the probe lands.
	// VERBATIM from sidecars/registry.connection. Reported as a broken mailbox it
	// would send the user through OAuth for a sidecar they need to restart.
	searchRelay503 = `{"detail":"agent 'email' has no running sidecar to relay to. Start it ` +
		"first (`gaia daemon start-agent email` or POST /daemon/v1/agents/email/ensure), " +
		`then retry."}`

	// get_search_backend refuses to pick between two connected mailboxes.
	searchAmbiguous = `{"detail":"Multiple mailboxes connected (google, microsoft); the search ` +
		`API can't choose which inbox to search. Search from the agent/UI, or disconnect all ` +
		`but one mailbox."}`

	// FastAPI's validation shape: `detail` is an ARRAY, not a string. Only the
	// `msg` fields are readable language; the array must never reach a row.
	searchUnprocessable = `{"detail":[{"type":"missing","loc":["body","max_results"],` +
		`"msg":"Field required","input":{}}]}`
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

	// ensureFn overrides EnsureAgent so a test can hold the call open and prove
	// the screen's context really reaches it.
	ensureFn func(context.Context) error

	calls []call
}

func newFake() *fakeTransport {
	return &fakeTransport{
		info: DaemonInfo{PID: 41822, Port: 51230, APIVersion: "1.1"},
		bodies: map[string]Response{
			"GET /daemon/v1/agents":    {Status: 200, Body: []byte(agentsRunning)},
			"GET /v1/email/init":       {Status: 200, Body: []byte(initReady)},
			"GET /v1/email/connectors": {Status: 200, Body: []byte(connectorsReady)},
			"POST /v1/email/search":    {Status: 200, Body: []byte(searchOK)},
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
		"gaia daemon agents",
		"gaia hub install ",
		"gaia init",
		"gaia connectors connect ",
		"gaia connectors grants grant ",
		"gaia connectors list",
	}
	l := resolveLemonade()
	for _, cmd := range []string{l.Start, l.Restart} {
		if cmd != "" {
			fixed = append(fixed, cmd)
		}
	}
	return fixed
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
func firstWord(cmd string) string {
	cmd = strings.TrimSpace(cmd)
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
		// wantNotIn asserts what the remedy must NOT say.
		wantNotIn []string
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
			// The command is whatever THIS machine can actually run; only the
			// diagnosis half is a fixed string.
			wantIn: []string{"not running"},
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
			wantIn: []string{
				"gaia connectors connect google", "installed:email",
				// The union, not just the send scope: --scopes REPLACES the
				// provider defaults, so a short list authorizes a mailbox the
				// agent can no longer read.
				"openid", "https://www.googleapis.com/auth/gmail.modify",
			},
			wantFix: FixConnectMailbox,
		},
		{
			name: "mailbox signed in without the send scope",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/connectors", 200, connectorsNoSend)
			},
			wantStates:  map[string]State{KeyMailbox: StateFailed},
			wantBlocker: KeyMailbox,
			wantIn: []string{
				"you@gmail.com", "sign-in has no send access",
				"signed in without the send scope",
				"gaia connectors connect google --grant-agent installed:email",
				"https://www.googleapis.com/auth/gmail.modify",
				"https://www.googleapis.com/auth/gmail.send",
			},
			wantNotIn: []string{"grants grant"},
			wantFix:   FixConnectMailbox,
			// A mailbox metadata already proves unusable is never probed.
			mustNotCall: []call{{"POST", "/v1/email/search"}},
		},
		{
			// Same can_send:false, different missing half, different sentence: the
			// sign-in HAS send, the agent was never handed it. One remedy for both
			// would send this user to re-authorize something already authorized.
			name: "mailbox signed in with send but the agent was never granted it",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/connectors", 200, connectorsNoGrant)
			},
			wantStates:  map[string]State{KeyMailbox: StateFailed},
			wantBlocker: KeyMailbox,
			wantIn: []string{
				"you@gmail.com", "send access not granted",
				"does include send permission",
				"gaia connectors connect google --grant-agent installed:email",
			},
			// `grants grant` OVERWRITES the ledger entry with whatever scopes it is
			// given, so naming it here would trade a calendar grant for a mail one.
			wantNotIn:   []string{"grants grant"},
			wantFix:     FixConnectMailbox,
			mustNotCall: []call{{"POST", "/v1/email/search"}},
		},
		{
			// THE BUG. Linked, granted, `connected: true`, `can_send: true` — and
			// the first read fails. The row used to say "can send".
			name: "mailbox connected and granted but its credentials are rejected",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 502, searchNoForwardedCredential)
			},
			wantStates: map[string]State{
				KeyModel: StateOK, KeyMailbox: StateFailed,
			},
			wantBlocker: KeyMailbox,
			wantIn: []string{
				"sign-in no longer works",
				"no forwarded 'google' credential is available",
				"gaia connectors connect google --grant-agent installed:email",
			},
			// The sidecar's own detail carries a `<scopes>` placeholder; showing it
			// hands the user a command they cannot copy.
			wantNotIn: []string{"<scopes>"},
			wantFix:   FixConnectMailbox,
		},
		{
			name: "mailbox connection was revoked upstream",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 403, searchRevoked)
			},
			wantStates:  map[string]State{KeyMailbox: StateFailed},
			wantBlocker: KeyMailbox,
			wantIn:      []string{"revoked upstream", "gaia connectors connect google"},
			wantFix:     FixConnectMailbox,
		},
		{
			// The RELAY gave up, not the mailbox. Blaming the mailbox would hand the
			// user a browser sign-in for a dead sidecar.
			name: "the relay drops the probe on the way to the sidecar",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 502, searchRelayDown)
			},
			wantStates: map[string]State{KeyMailbox: StateUnknown},
			// Unknown never blocks — nothing here proved the mailbox broken.
			wantReady:   false,
			wantBlocker: "",
		},
		{
			// The relay's OTHER self-authored answer: the sidecar is gone by the
			// time the probe lands, so it 503s. Reported as a broken mailbox it
			// would send the user through OAuth for a sidecar to restart.
			name: "the sidecar is gone by the time the probe lands",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 503, searchRelay503)
			},
			wantStates:  map[string]State{KeyMailbox: StateUnknown},
			wantReady:   false,
			wantBlocker: "",
		},
		{
			// The forwarded token lapsed between re-forwards and the sidecar said
			// so. Pressing r clears it; a browser sign-in would not.
			name: "a forwarded token that lapsed between re-forwards is not a dead sign-in",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 502, searchTokenLapsed)
			},
			wantStates:  map[string]State{KeyMailbox: StateUnknown},
			wantReady:   false,
			wantBlocker: "",
		},
		{
			// Two mailboxes: the read route takes no provider and 400s on 2+, so
			// the answer is known without asking. Nothing is proven either way.
			name: "two mailboxes connected leaves the probe unable to answer",
			build: func() *fakeTransport {
				return newFake().with("GET /v1/email/connectors", 200, connectorsBoth)
			},
			wantStates:  map[string]State{KeyMailbox: StateUnknown},
			wantReady:   false,
			wantBlocker: "",
			// And it does NOT pay for a read whose answer it already knows.
			mustNotCall: []call{{"POST", "/v1/email/search"}},
		},
		{
			// An older sidecar with no read route says nothing about credentials.
			name: "a sidecar without the read route leaves the mailbox unverified",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 404, `{"detail":"Not Found"}`)
			},
			wantStates:  map[string]State{KeyMailbox: StateUnknown},
			wantReady:   false,
			wantBlocker: "",
		},
		{
			// An empty inbox is a working mailbox, not an unverified one.
			name: "an empty inbox still proves the credentials work",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 200, searchEmptyInbox)
			},
			wantStates: map[string]State{KeyMailbox: StateOK},
			wantReady:  true,
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
			wantIn:      []string{"model list"},
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
				for _, unwanted := range tt.wantNotIn {
					if strings.Contains(text, unwanted) {
						t.Errorf("blocking row must not mention %q; it says: %s", unwanted, text)
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
		"no grant":    newFake().with("GET /v1/email/connectors", 200, connectorsNoGrant),
		"creds dead":  newFake().with("POST /v1/email/search", 502, searchNoForwardedCredential),
		"revoked":     newFake().with("POST /v1/email/search", 403, searchRevoked),
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
	if !strings.Contains(row.Detail, "did not finish in time") {
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
	if !strings.Contains(row.Line, "Gmail") || !strings.Contains(row.Line, "can read and send") {
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

// The relay answers 503 with `{"detail": ...}` and NO readiness object when the
// sidecar dies between the agents listing and this probe. Decoded into value
// structs that read as "Lemonade unreachable" — the wrong subject AND the wrong
// remedy, pointing away from the process that actually died.
func TestARelayErrorIsNotReportedAsLemonadeBeingDown(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503,
		`{"detail":"agent 'email' is registered but its sidecar is not running (POST /daemon/v1/agents/email/ensure), then retry."}`)

	rep := Check(context.Background(), f, EmailConfig())
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
	f := newFake().with("GET /v1/email/init", 503, initUnreachable)
	rep := Check(context.Background(), f, EmailConfig())

	row, _ := rep.Find(KeyModel)
	if !strings.Contains(row.Raw, "lemonade") {
		t.Errorf("the model row lost the probe body it was answered with: %q", row.Raw)
	}
	if row.Detail == "" {
		t.Error("the model row does not say what it is waiting on")
	}
}

// The connect remedy must request the SAME scope union the sidecar's own
// /configure builds — --scopes replaces the provider defaults, so a short list
// authorizes a mailbox the agent cannot read.
func TestConnectCommandRequestsTheFullScopeUnion(t *testing.T) {
	for provider, want := range map[string][]string{
		"google": {
			"openid", "email", "profile",
			"https://www.googleapis.com/auth/gmail.modify",
			"https://www.googleapis.com/auth/gmail.send",
		},
		"microsoft": {
			"openid", "offline_access", "https://graph.microsoft.com/User.Read",
			"https://graph.microsoft.com/Mail.ReadWrite",
			"https://graph.microsoft.com/Mail.Send",
		},
	} {
		cmd := connectCommand(provider)
		for _, scope := range want {
			if !strings.Contains(cmd, scope) {
				t.Errorf("%s connect command is missing %q: %s", provider, scope, cmd)
			}
		}
		if !strings.Contains(cmd, "--grant-agent "+emailAgentGrantID) {
			t.Errorf("%s connect command does not grant the agent: %s", provider, cmd)
		}
		if strings.Contains(cmd, "grants grant") {
			t.Errorf("%s uses the overwrite-scopes path: %s", provider, cmd)
		}
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
		newFake().with("GET /v1/email/init", 200, initUnknownVersion), EmailConfig())
	broken := Check(context.Background(),
		newFake().with("GET /v1/email/init", 503, initUnreachable), EmailConfig())
	ready := Check(context.Background(), newFake(), EmailConfig())

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

// --- the mailbox: four states, four remedies ---------------------------------

// The four states the connector list plus one read can tell apart. Each needs
// its own sentence: one "reconnect your mailbox" for all of them is what makes
// people reconnect the wrong thing — or re-authorize something that was already
// fine while the actual gap goes untouched.
func TestTheFourMailboxStatesReadDifferently(t *testing.T) {
	cases := []struct {
		name  string
		build func() *fakeTransport
		state State
		// line is a substring unique to this state's row.
		line string
		// detail is a substring of the sentence that explains it.
		detail string
	}{
		{
			name:   "not connected",
			build:  func() *fakeTransport { return newFake().with("GET /v1/email/connectors", 200, connectorsNone) },
			state:  StateFailed,
			line:   "not connected",
			detail: "cannot do anything until it can read a mailbox",
		},
		{
			name:   "connected but the agent has no send grant",
			build:  func() *fakeTransport { return newFake().with("GET /v1/email/connectors", 200, connectorsNoGrant) },
			state:  StateFailed,
			line:   "send access not granted",
			detail: "does include send permission",
		},
		{
			name:   "connected but the sign-in is missing the send scope",
			build:  func() *fakeTransport { return newFake().with("GET /v1/email/connectors", 200, connectorsNoSend) },
			state:  StateFailed,
			line:   "sign-in has no send access",
			detail: "signed in without the send scope",
		},
		{
			name: "connected but the credentials are rejected",
			build: func() *fakeTransport {
				return newFake().with("POST /v1/email/search", 502, searchNoForwardedCredential)
			},
			state:  StateFailed,
			line:   "sign-in no longer works",
			detail: "no forwarded 'google' credential",
		},
	}

	seenLines := map[string]string{}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rep := Check(context.Background(), tc.build(), EmailConfig())
			row, ok := rep.Find(KeyMailbox)
			if !ok {
				t.Fatalf("no mailbox row:\n%s", rep)
			}
			if row.State != tc.state {
				t.Fatalf("state = %s, want %s\n%s", row.State.Word(), tc.state.Word(), rep)
			}
			if !strings.Contains(row.Line, tc.line) {
				t.Errorf("line = %q, want it to mention %q", row.Line, tc.line)
			}
			if !strings.Contains(row.Detail, tc.detail) {
				t.Errorf("detail = %q, want it to mention %q", row.Detail, tc.detail)
			}
			// Whatever the state, the command must never narrow what the account
			// can already do: `grants grant` overwrites the ledger entry and
			// `connect --scopes` replaces the provider defaults, so the only safe
			// remedy is the full union through the connect flow.
			assertRealCommand(t, row)
			if strings.Contains(row.Remedy.Command, "grants grant") {
				t.Errorf("%s names the overwrite-scopes path: %q", tc.name, row.Remedy.Command)
			}
			if strings.Contains(row.Remedy.Command, "connectors connect") {
				for _, scope := range connectScopes["google"] {
					if !strings.Contains(row.Remedy.Command, scope) {
						t.Errorf("%s reconnects without %q, narrowing the account: %s",
							tc.name, scope, row.Remedy.Command)
					}
				}
			}
			if prev, dup := seenLines[row.Line]; dup {
				t.Errorf("%s reads identically to %s: %q", tc.name, prev, row.Line)
			}
			seenLines[row.Line] = tc.name
		})
	}
}

// A mailbox the metadata already proves unusable is never read: the probe is the
// only call on this screen that leaves the machine, and paying for it to confirm
// a failure already in hand is the cost users would resent.
func TestTheProbeOnlyRunsWhenTheMetadataSaysItShouldWork(t *testing.T) {
	for name, body := range map[string]string{
		"nothing connected": connectorsNone,
		"no send scope":     connectorsNoSend,
		"no agent grant":    connectorsNoGrant,
	} {
		t.Run(name, func(t *testing.T) {
			f := newFake().with("GET /v1/email/connectors", 200, body)
			Check(context.Background(), f, EmailConfig())
			if f.called("POST", "/v1/email/search") {
				t.Error("the gate paid for a live mailbox read it did not need")
			}
		})
	}

	// And it DOES run once the metadata stops being able to answer.
	f := newFake()
	Check(context.Background(), f, EmailConfig())
	if !f.called("POST", "/v1/email/search") {
		t.Error("a connected+granted mailbox was passed without being read")
	}
}

// The probe is one bounded read, and `d` has to show it — including what it cost,
// which is the only way anyone can tell whether the gate got slower.
func TestTheProbeRecordsWhatItDidAndWhatItCost(t *testing.T) {
	f := newFake()
	rep := Check(context.Background(), f, EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	for _, want := range []string{
		"mailbox probe: POST /v1/email/search",
		`{"max_results":1}`, // bounded: one message, one hydration
		"HTTP 200",
		"ms",
	} {
		if !strings.Contains(row.Raw, want) {
			t.Errorf("the probe trace does not record %q:\n%s", want, row.Raw)
		}
	}
	// The connector body it started from is still there — `d` must show both.
	if !strings.Contains(row.Raw, "can_send") {
		t.Errorf("the probe trace replaced the connector body:\n%s", row.Raw)
	}

	var searches int
	for _, c := range f.calls {
		if c.method == "POST" && c.path == "/v1/email/search" {
			searches++
		}
	}
	if searches != 1 {
		t.Errorf("the gate read the mailbox %d times; one launch is one read", searches)
	}
}

// A probe that never answers must not hold the gate for its whole 90s check
// budget, and must not be reported as a broken mailbox either.
func TestAProbeThatHangsIsReportedAsUnverifiedNotBroken(t *testing.T) {
	f := newFake()
	f.errs["POST /v1/email/search"] = &daemon.RequestError{
		Op: "read the mailbox", Detail: "context deadline exceeded"}

	rep := Check(context.Background(), f, EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown — a timeout proves nothing about the mailbox\n%s",
			row.State.Word(), rep)
	}
	if rep.Blocked() {
		t.Error("an unanswered probe blocked the launch")
	}
	if rep.Ready() {
		t.Error("an unanswered probe reported the report ready")
	}
	if row.Remedy.Empty() {
		t.Error("an unverified mailbox row has nothing to tell the user")
	}
	if strings.Contains(row.Line, "can read") {
		t.Errorf("an unverified row claims a capability: %q", row.Line)
	}
}

// A relay failure and a mailbox failure both arrive as 502. Telling them apart is
// the difference between "sign in again" (a browser round trip that fixes
// nothing) and "your sidecar died".
func TestARelay502IsNotBlamedOnTheMailbox(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("POST /v1/email/search", 502, searchRelayDown), EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	// != StateFailed would also pass on StateOK, which is the WORSE regression: a
	// dead relay read as a healthy mailbox.
	if row.State != StateUnknown {
		t.Fatalf("a dead relay was reported as %s, want unknown\n%s", row.State.Word(), rep)
	}
	if strings.Contains(row.Remedy.Command, "connectors connect") {
		t.Errorf("a dead relay is answered with an OAuth sign-in: %q", row.Remedy.Command)
	}
	if row.Fix == FixConnectMailbox {
		t.Error("a dead relay offers a one-key mailbox reconnect")
	}
}

// A sidecar too old to have the read route is not a broken mailbox, and the
// answer must not be "install the agent you are already running".
func TestAMissingReadRouteDoesNotReadAsAMissingAgent(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("POST /v1/email/search", 404, `{"detail":"Not Found"}`), EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown\n%s", row.State.Word(), rep)
	}
	if !strings.Contains(row.Detail, "does not answer the read") {
		t.Errorf("detail = %q, want it to name the missing route", row.Detail)
	}
	if strings.Contains(row.Detail, "does not know the agent") {
		t.Errorf("a running agent was reported as unknown to the daemon: %q", row.Detail)
	}
}

// The sidecar's own credential error ends with a command carrying a `<scopes>`
// placeholder. Quoting the whole thing hands the user something they cannot run.
func TestTheRefusalQuotesTheDiagnosisNotTheSidecarsOwnCommand(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("POST /v1/email/search", 502, searchNoForwardedCredential), EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	if !strings.Contains(row.Detail, "no forwarded 'google' credential is available") {
		t.Errorf("the row drops the diagnosis: %q", row.Detail)
	}
	for _, unwanted := range []string{"<scopes>", "--scopes <", "Settings -> Connections"} {
		if strings.Contains(row.Detail, unwanted) {
			t.Errorf("the row quotes %q from the sidecar's own remedy: %q", unwanted, row.Detail)
		}
	}
	assertRealCommand(t, row)
}

// A relay answer and a mailbox answer arrive on the SAME status codes. Telling
// them apart is the difference between "sign in again" — a browser round trip
// that fixes nothing — and "restart your sidecar".
func TestRelayAuthoredAnswersAreNeverBlamedOnTheMailbox(t *testing.T) {
	for name, tc := range map[string]struct {
		status int
		body   string
	}{
		"502 the sidecar did not answer": {502, searchRelayDown},
		"503 the sidecar is gone":        {503, searchRelay503},
	} {
		t.Run(name, func(t *testing.T) {
			rep := Check(context.Background(),
				newFake().with("POST /v1/email/search", tc.status, tc.body), EmailConfig())
			row, _ := rep.Find(KeyMailbox)

			if row.State != StateUnknown {
				t.Fatalf("state = %s, want unknown — the relay said nothing about the mailbox\n%s",
					row.State.Word(), rep)
			}
			if strings.Contains(row.Remedy.Command, "connectors connect") {
				t.Errorf("a dead relay hop is answered with an OAuth sign-in: %q", row.Remedy.Command)
			}
			if row.Fix == FixConnectMailbox {
				t.Error("a dead relay hop offers a one-key mailbox reconnect")
			}
			if rep.Blocked() {
				t.Errorf("a dead relay hop blocked the launch on the mailbox row\n%s", rep)
			}
		})
	}

	// And the sidecar's OWN 503 — it can no longer resolve a mailbox at all —
	// still fails the row, or this fix would have made 503 unusable.
	rep := Check(context.Background(), newFake().with("POST /v1/email/search", 503,
		`{"detail":"No mailbox connected — connect Google or Microsoft in Settings -> Connectors before searching the inbox."}`),
		EmailConfig())
	row, _ := rep.Find(KeyMailbox)
	if row.State != StateFailed {
		t.Errorf("the sidecar's own 503 = %s, want failed\n%s", row.State.Word(), rep)
	}
}

// The one credential failure that clears itself. The sidecar says "Retry in a
// moment"; blocking the launch would charge the user a browser sign-in for it.
func TestALapsedForwardedTokenIsNotReportedAsADeadSignIn(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("POST /v1/email/search", 502, searchTokenLapsed), EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown\n%s", row.State.Word(), rep)
	}
	if rep.Blocked() {
		t.Error("a self-clearing credential gap blocked the launch")
	}
	if row.Fix == FixConnectMailbox || strings.Contains(row.Remedy.Command, "connectors connect") {
		t.Errorf("a self-clearing gap is answered with a sign-in: %q", row.Remedy.Command)
	}
	if !strings.Contains(row.Remedy.Action, "r") {
		t.Errorf("the remedy does not tell the user to re-check: %q", row.Remedy.Action)
	}
}

// FastAPI's validation errors put an ARRAY in `detail`. Only the `msg` fields are
// language a person can read; the array itself must never reach a row.
func TestAValidationErrorNeverPutsRawJSONOnTheScreen(t *testing.T) {
	rep := Check(context.Background(),
		newFake().with("POST /v1/email/search", 422, searchUnprocessable), EmailConfig())
	row, _ := rep.Find(KeyMailbox)

	if row.State != StateUnknown {
		t.Fatalf("state = %s, want unknown\n%s", row.State.Word(), rep)
	}
	for _, unwanted := range []string{`{"type"`, `"loc"`, "[{", `"input"`} {
		if strings.Contains(row.Detail, unwanted) {
			t.Errorf("raw validation JSON reached the row (%q): %q", unwanted, row.Detail)
		}
	}
	if !strings.Contains(row.Detail, "Field required") {
		t.Errorf("the readable half of the error was dropped: %q", row.Detail)
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
