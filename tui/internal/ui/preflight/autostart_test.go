package preflight

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// What this file protects.
//
// A local model server that is not running used to end the launch: the gate
// named a command and stopped. GAIA ships that server and manages it, so the
// gate now asks the DAEMON to start it and answers the same question again.
//
// The three things that can go wrong, and each has a test below:
//
//   - it never fires, and the user is back to typing a command;
//   - it fires when it should not — on a server that is already up (startup
//     latency for everyone) or on one that lives on another machine (starting
//     a server here that nothing will talk to);
//   - it fires, fails, and the row says something useless. The daemon's own
//     refusal text is the most specific thing in the system about WHICH
//     failure it was, so it has to survive to the screen.

// initUnreachableLocal is the down answer with a LOOPBACK base URL — the one
// state GAIA repairs itself. The shared initUnreachable fixture is loopback
// too; this restates it locally so a change to that fixture cannot silently
// turn these tests into a different scenario.
const initUnreachableLocal = `{"ready":false,"lemonade":{"reachable":false,` +
	`"base_url":"http://localhost:13305/api/v1","version":null,"min_version":"8.1.0",` +
	`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null,` +
	`"ctx_size":null},"hint":"Local Lemonade Server is not reachable."}`

// initUnreachableRemote points the agent at another machine.
const initUnreachableRemote = `{"ready":false,"lemonade":{"reachable":false,` +
	`"base_url":"http://192.168.1.50:13305/api/v1","version":null,"min_version":"8.1.0",` +
	`"compatible":null},"model":{"id":"Gemma-4-E4B-it-GGUF","present":false,"loadable":null,` +
	`"ctx_size":null},"hint":"Lemonade Server is not reachable."}`

func lemonadeRow(t *testing.T, rep Report) Row {
	t.Helper()
	for _, row := range rep.Rows {
		if row.Key == KeyLemonade {
			return row
		}
	}
	t.Fatalf("no Local AI row in the report")
	return Row{}
}

// --- it fires, and a successful start takes the row green ---------------------

func TestADownLocalServerIsStartedWithoutTheUserTypingAnything(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initUnreachableLocal)
	// A real start makes the next /init answer differently. Without this the
	// test would prove only that a POST happened, not that it repaired anything.
	f.startLemonadeFn = func(context.Context) error {
		f.with("GET /v1/email/init", 200, initReady)
		return nil
	}

	rep := Check(context.Background(), f, EmailConfig())

	if !f.called(http.MethodPost, daemon.APIPrefix+"/lemonade/start") {
		t.Fatalf("the gate never asked the daemon to start the local model server")
	}
	if got := lemonadeRow(t, rep).State; got != StateOK {
		t.Errorf("Local AI = %v after a successful start, want StateOK", got)
	}
	if !rep.Ready() {
		t.Errorf("report not ready after the server was started:\n%+v", rep.Rows)
	}
}

// --- it does not fire when there is nothing to fix ---------------------------

func TestAReachableServerIsNeverStarted(t *testing.T) {
	// The latency guarantee: the common case must cost the probes it already
	// cost, and no more.
	f := newFake()

	Check(context.Background(), f, EmailConfig())

	if f.called(http.MethodPost, daemon.APIPrefix+"/lemonade/start") {
		t.Errorf("started a model server that was already running")
	}
}

func TestARemoteServerIsNeverStartedHere(t *testing.T) {
	// Starting a server on THIS machine would not be used by an agent pointed
	// at another one — so the honest answer stays "start it over there".
	f := newFake().with("GET /v1/email/init", 503, initUnreachableRemote)

	rep := Check(context.Background(), f, EmailConfig())

	if f.called(http.MethodPost, daemon.APIPrefix+"/lemonade/start") {
		t.Fatalf("tried to start a local server for a remote base URL")
	}
	row := lemonadeRow(t, rep)
	if row.State != StateFailed || !strings.Contains(row.Detail, "another machine") {
		t.Errorf("remote row did not say the server lives elsewhere: %+v", row)
	}
}

// --- it fires at most once ---------------------------------------------------

func TestTheStartIsAttemptedOnlyOnce(t *testing.T) {
	// A second attempt re-waits the full start budget to reach the same answer.
	// Every failure the starter reports is one a retry cannot change.
	f := newFake().with("GET /v1/email/init", 503, initUnreachableLocal)

	Check(context.Background(), f, EmailConfig())

	starts := 0
	for _, c := range f.calls {
		if c.method == http.MethodPost && c.path == daemon.APIPrefix+"/lemonade/start" {
			starts++
		}
	}
	if starts != 1 {
		t.Errorf("asked the daemon to start the server %d times, want exactly 1", starts)
	}
}

// --- when it fails, the reason has to reach the screen ------------------------

func TestARefusedStartShowsTheDaemonsOwnReason(t *testing.T) {
	// The daemon knows whether the port was held by a stranger, whether the
	// server died on launch, or whether there was nothing to launch. "Start it"
	// is the wrong remedy for two of those three, so its text is what shows.
	const detail = "Port 13305 on localhost is in use by a process that does not answer " +
		"Lemonade's health endpoint, so GAIA will not start a server there."
	f := newFake().with("GET /v1/email/init", 503, initUnreachableLocal)
	f.startLemonadeErr = &LemonadeStartRefused{Status: http.StatusServiceUnavailable, Detail: detail}

	rep := Check(context.Background(), f, EmailConfig())

	row := lemonadeRow(t, rep)
	if row.State != StateFailed {
		t.Fatalf("Local AI = %v after a refused start, want StateFailed", row.State)
	}
	if !strings.Contains(row.Remedy.Action, "in use by a process") {
		t.Errorf("the daemon's reason did not reach the row:\n  detail: %q\n  remedy: %q",
			row.Detail, row.Remedy.Action)
	}
	if row.Fix != FixNone {
		t.Errorf("offered a one-key fix for something that just failed: %v", row.Fix)
	}
}

func TestADaemonTooOldToStartLemonadeSaysSoRatherThanBlamingTheAgent(t *testing.T) {
	// A 404 on this route is a version skew. The generic 404 branch of the
	// ladder reads it as "the agent is not installed" — a wrong subject and a
	// wrong fix.
	f := newFake().with("GET /v1/email/init", 503, initUnreachableLocal)
	f.startLemonadeErr = &LemonadeStartRefused{Status: http.StatusNotFound}

	rep := Check(context.Background(), f, EmailConfig())

	row := lemonadeRow(t, rep)
	if !strings.Contains(row.Detail, "older than this app") {
		t.Errorf("a too-old core was not named as such: %q", row.Detail)
	}
	if row.Remedy.Action == "" {
		t.Errorf("no fallback remedy for a core that cannot start the server")
	}
}

func TestAnUnreachableDaemonIsDiagnosedAsTheDaemonNotAsLemonade(t *testing.T) {
	// The POST never reached the daemon, so the daemon is the subject. Saying
	// "start Lemonade" here sends the user at the wrong process.
	f := newFake().with("GET /v1/email/init", 503, initUnreachableLocal)
	f.startLemonadeErr = &daemon.RequestError{
		Op: "call POST /daemon/v1/lemonade/start", Detail: "connection refused",
	}

	rep := Check(context.Background(), f, EmailConfig())

	row := lemonadeRow(t, rep)
	if !strings.Contains(strings.ToLower(row.Detail), "background service") {
		t.Errorf("an unreachable daemon was blamed on Lemonade: %q", row.Detail)
	}
}

// --- the transport's own mapping ---------------------------------------------

func TestStartLemonadeMapsTheDaemonsAnswers(t *testing.T) {
	tests := []struct {
		name       string
		status     int
		body       string
		wantErr    bool
		wantDetail string
		wantTooOld bool
	}{
		{
			name:   "200 means a server is running afterwards",
			status: 200,
			body:   `{"status":"started","base_url":"http://localhost:13305/api/v1"}`,
		},
		{
			name:       "503 carries the actionable detail verbatim",
			status:     503,
			body:       `{"detail":"Lemonade Server is not installed. Run ` + "`gaia init`" + `."}`,
			wantErr:    true,
			wantDetail: "not installed",
		},
		{
			name:       "404 is a version skew, not a missing agent",
			status:     404,
			body:       `{"detail":"Not Found"}`,
			wantErr:    true,
			wantTooOld: true,
		},
		{
			name:       "a non-JSON body is still shown rather than swallowed",
			status:     500,
			body:       "upstream exploded",
			wantErr:    true,
			wantDetail: "upstream exploded",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := lemonadeStartResult(Response{Status: tt.status, Body: []byte(tt.body)})
			if (err != nil) != tt.wantErr {
				t.Fatalf("err = %v, wantErr = %v", err, tt.wantErr)
			}
			if !tt.wantErr {
				return
			}
			var refused *LemonadeStartRefused
			if !errors.As(err, &refused) {
				t.Fatalf("err = %T, want *LemonadeStartRefused", err)
			}
			if refused.TooOldToStartLemonade() != tt.wantTooOld {
				t.Errorf("TooOldToStartLemonade() = %v, want %v",
					refused.TooOldToStartLemonade(), tt.wantTooOld)
			}
			if tt.wantDetail != "" && !strings.Contains(err.Error(), tt.wantDetail) {
				t.Errorf("error = %q, want it to contain %q", err, tt.wantDetail)
			}
		})
	}
}

// A refusal with no body at all must still read as an error a human can act on,
// not as an empty line.
func TestARefusalWithNoBodyStillSaysSomething(t *testing.T) {
	err := lemonadeStartResult(Response{Status: 502})
	if err == nil || !strings.Contains(err.Error(), "502") {
		t.Errorf("bodiless refusal = %v, want it to name the status", err)
	}
}

// TestTheStartCallOutlastsTheDaemonsOwnStartBudget guards the bug that would
// silently undo this whole feature on the machines that need it most.
//
// The daemon's route BLOCKS until the model server answers health, for up to
// lemonade_supervisor.DEFAULT_START_TIMEOUT_S (120s). The TUI's default request
// timeout is 60s. Sending this call down the default path would therefore abort
// a cold start that was about to succeed, and the failure arrives as a
// transport error — so the row blames the background service, which is healthy,
// and the user is back to being told to start a server by hand.
//
// A cold start on the development machine measured ~6s, which is exactly why
// this has to be reasoned about rather than observed: a fast box never trips it.
func TestTheStartCallOutlastsTheDaemonsOwnStartBudget(t *testing.T) {
	const daemonStartBudget = 120 * time.Second
	if lemonadeStartHeaderTimeout <= daemonStartBudget {
		t.Errorf("the start call waits %s but the daemon may take %s — a slow "+
			"cold start would be aborted and then misdiagnosed",
			lemonadeStartHeaderTimeout, daemonStartBudget)
	}
	// The gate's overall deadline has to cover the call it makes, or the same
	// abort happens one level up.
	if checkTimeout <= lemonadeStartHeaderTimeout {
		t.Errorf("checkTimeout %s does not cover the start call's %s",
			checkTimeout, lemonadeStartHeaderTimeout)
	}
}
