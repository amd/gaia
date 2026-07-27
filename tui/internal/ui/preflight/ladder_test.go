package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// The ladder's whole value is picking the RIGHT cause. These cases are the ones
// where a plausible-looking wrong answer sends the user to the wrong process.
func TestLadderPicksTheRightSubject(t *testing.T) {
	l := Ladder{AgentID: "email"}

	cases := []struct {
		name     string
		diagnose func() Diagnosis
		wantCmd  string
		wantIn   string
		wantNot  string
	}{
		{
			// A Go transport error can only come from the daemon: this process
			// never opens a socket to Lemonade.
			name: "refused connection to the daemon",
			diagnose: func() Diagnosis {
				return l.Error("reach the agent", errors.New("dial tcp 127.0.0.1:51230: connect: connection refused"))
			},
			wantCmd: "gaia daemon status",
			wantIn:  "background service",
			wantNot: "Lemonade",
		},
		{
			// The same words in a RESPONSE BODY are the sidecar telling us about
			// Lemonade, and mean the opposite.
			name: "sidecar says Lemonade is not reachable",
			diagnose: func() Diagnosis {
				return l.Text("check the local AI", "Local Lemonade Server is not reachable at http://localhost:8000")
			},
			// Resolved against this machine, not hardcoded: `lemonade-server` does
			// not exist on a modern install. From the REMEDY, which always names
			// something — resolveLemonade().Start is "" where nothing is installed,
			// and an empty wantCmd is skipped, so this would assert nothing in CI.
			wantCmd: lemonadeStartRemedy().Command,
			wantIn:  "local model server",
		},
		{
			name: "version too old beats the model rung",
			diagnose: func() Diagnosis {
				return l.Text("check the local AI", "Lemonade 8.0.1 is older than the required 8.1.0 — upgrade it")
			},
			wantCmd: "gaia init",
			wantIn:  "older than",
			wantNot: "not downloaded",
		},
		{
			name: "the relay giving up on a long pull is not a missing model",
			diagnose: func() Diagnosis {
				return l.Text("download the model", "sidecar for agent 'email' dropped the connection mid-response on /v1/email/init (ReadTimeout)")
			},
			wantCmd: "gaia init",
			wantIn:  "gave up relaying",
		},
		{
			name: "a wrong-contract daemon is never fixed by starting another",
			diagnose: func() Diagnosis {
				return l.Error("attach", &daemon.VersionError{Have: "1.0", Reason: "predates the relay"})
			},
			wantCmd: "gaia daemon restart",
			wantIn:  "v1.0",
		},
		{
			name:     "401 is a rotated token, not a permissions problem",
			diagnose: func() Diagnosis { return l.Status("call the agent", 401, "") },
			wantCmd:  "gaia daemon restart",
			wantIn:   "token",
		},
		{
			name:     "404 means the agent is not registered",
			diagnose: func() Diagnosis { return l.Status("call the agent", 404, `{"detail":"unknown agent"}`) },
			wantCmd:  "gaia hub install email",
			wantIn:   "email",
		},
		{
			name:     "502 from a dead sidecar points at the sidecar",
			diagnose: func() Diagnosis { return l.Status("call the agent", 502, `{"detail":"sidecar did not answer"}`) },
			wantCmd:  "gaia daemon start-agent email",
			wantIn:   "email agent",
		},
		{
			// Same status, different subject: the relay gave up on a long
			// buffered call. Restarting the sidecar would fix nothing.
			name: "502 from the relay giving up is not a dead sidecar",
			diagnose: func() Diagnosis {
				return l.Status("download the model", 502,
					`{"detail":"sidecar for agent 'email' dropped the connection mid-response (ReadTimeout)"}`)
			},
			wantCmd: "gaia init",
			wantIn:  "gave up relaying",
			wantNot: "start-agent",
		},
		{
			name:     "an unrecognised body still names a next step",
			diagnose: func() Diagnosis { return l.Text("check the local AI", "<html>gateway</html>") },
			wantCmd:  "gaia daemon start-agent email",
			wantIn:   "sidecar",
		},
		{
			name:     "a cancelled context is not an error to fix",
			diagnose: func() Diagnosis { return l.Error("check the local AI", context.Canceled) },
			wantIn:   "cancelled",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := tc.diagnose()
			all := d.String()
			if tc.wantCmd != "" && d.Command != tc.wantCmd {
				t.Errorf("command = %q, want %q (%s)", d.Command, tc.wantCmd, all)
			}
			if tc.wantIn != "" && !strings.Contains(all, tc.wantIn) {
				t.Errorf("diagnosis does not mention %q: %s", tc.wantIn, all)
			}
			if tc.wantNot != "" && strings.Contains(all, tc.wantNot) {
				t.Errorf("diagnosis wrongly mentions %q: %s", tc.wantNot, all)
			}
			if d.Cause == "" {
				t.Error("a diagnosis with no cause tells the user nothing")
			}
		})
	}
}

// Every diagnosis has to name what to do and where to look, or it is just an
// error message with extra steps.
func TestEveryDiagnosisIsActionable(t *testing.T) {
	l := Ladder{AgentID: "email"}
	for _, status := range []int{400, 401, 403, 404, 500, 502, 503} {
		d := l.Status("call the agent", status, "")
		if d.Cause == "" || d.Remedy == "" || d.Where == "" {
			t.Errorf("HTTP %d produced an incomplete diagnosis: %+v", status, d)
		}
		if strings.Contains(d.Cause, "HTTP") {
			t.Errorf("HTTP %d leaked a status code to the user: %q", status, d.Cause)
		}
	}
}

// A remedy a user cannot copy verbatim is not a remedy, so no diagnosis may
// contain a placeholder — including one built without an agent id.
func TestNoDiagnosisEverPrintsAPlaceholder(t *testing.T) {
	for _, l := range []Ladder{{}, {AgentID: "email"}} {
		for _, status := range []int{400, 401, 404, 500, 502, 503} {
			d := l.Status("call the agent", status, "")
			if strings.ContainsAny(d.Command, "<>") {
				t.Errorf("HTTP %d (agent %q) produced an uncopyable command: %q", status, l.AgentID, d.Command)
			}
			if d.Remedy == "" {
				t.Errorf("HTTP %d (agent %q) says nothing to do", status, l.AgentID)
			}
		}
	}
}
