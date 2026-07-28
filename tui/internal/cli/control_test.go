package cli

import (
	"strconv"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/control"
)

func TestControlOffByDefault(t *testing.T) {
	opts, err := controlOptions(false, 0, false)
	if err != nil {
		t.Fatalf("controlOptions: %v", err)
	}
	if opts != nil {
		t.Error("the control API must be off unless asked for")
	}
}

// TestExplicitZeroPortStillEnablesControl pins a silent no-op: `--control-port 0`
// means "auto-assign", and inferring "off" from the zero value would start the
// TUI with no control API while the user believes they enabled it.
func TestExplicitZeroPortStillEnablesControl(t *testing.T) {
	opts, err := controlOptions(false, 0, true)
	if err != nil {
		t.Fatalf("controlOptions: %v", err)
	}
	if opts == nil {
		t.Fatal("--control-port 0 silently disabled the control API")
	}
	if opts.Port != 0 {
		t.Errorf("Port = %d, want 0 (auto-assign)", opts.Port)
	}
}

func TestControlFlagEnablesWithAutoPort(t *testing.T) {
	opts, err := controlOptions(true, 0, false)
	if err != nil {
		t.Fatalf("controlOptions: %v", err)
	}
	if opts == nil || opts.Port != 0 {
		t.Errorf("--control alone should enable with an auto-assigned port, got %+v", opts)
	}
}

func TestControlPortRejectsReservedAndUnusablePorts(t *testing.T) {
	for _, tc := range []struct {
		port int
		want string
	}{
		{control.ReservedPort, "reserved"},
		{80, "not a usable port"},
		{-1, "not a usable port"},
		{70000, "not a usable port"},
	} {
		_, err := controlOptions(true, tc.port, true)
		if err == nil {
			t.Errorf("port %d was accepted", tc.port)
			continue
		}
		if !strings.Contains(err.Error(), tc.want) {
			t.Errorf("port %d: error %q should mention %q", tc.port, err, tc.want)
		}
		if !strings.Contains(err.Error(), strconv.Itoa(tc.port)) {
			t.Errorf("port %d: error %q should quote the offending port", tc.port, err)
		}
	}
}

func TestControlPortAcceptsAUsablePort(t *testing.T) {
	opts, err := controlOptions(false, 8770, true)
	if err != nil {
		t.Fatalf("controlOptions: %v", err)
	}
	if opts == nil || opts.Port != 8770 {
		t.Errorf("got %+v, want port 8770", opts)
	}
}

// TestAgentControlOptionsThreadsThroughForInteractive pins #2512: `chat --agent`
// and `run <id>` used to accept --control-port and silently drop it — RunAgent
// had no parameter to receive it at all, so nothing bound and nothing was
// written to disk. This proves the decision function a real interactive launch
// consults actually returns a bound port, the same "silent no-op" class
// TestExplicitZeroPortStillEnablesControl pins for the bare root command.
func TestAgentControlOptionsThreadsThroughForInteractive(t *testing.T) {
	opts, err := agentControlOptions(false, 8815, true, false /* interactive: no --query */)
	if err != nil {
		t.Fatalf("agentControlOptions: %v", err)
	}
	if opts == nil {
		t.Fatal("--agent ... --control-port 8815 (interactive) silently disabled the control API")
	}
	if opts.Port != 8815 {
		t.Errorf("Port = %d, want 8815", opts.Port)
	}
}

// TestAgentControlOptionsOffByDefault mirrors TestControlOffByDefault for the
// --agent decision function: neither flag passed must never bind a port.
func TestAgentControlOptionsOffByDefault(t *testing.T) {
	opts, err := agentControlOptions(false, 0, false, false)
	if err != nil {
		t.Fatalf("agentControlOptions: %v", err)
	}
	if opts != nil {
		t.Error("the control API must be off unless asked for")
	}
}

// TestAgentControlOptionsRejectsOneShot: a --query run answers and exits before
// any session exists for an assistant to attach to, so accepting --control there
// would just move the silent no-op one call deeper instead of closing it. Loud
// refusal, never an accepted-and-ignored flag.
func TestAgentControlOptionsRejectsOneShot(t *testing.T) {
	_, err := agentControlOptions(true, 0, false, true /* --query set */)
	if err == nil {
		t.Fatal("--control combined with --query was silently accepted")
	}
	if !strings.Contains(err.Error(), "--query") {
		t.Errorf("error should name --query so the fix is obvious: %v", err)
	}
}

// TestAgentControlOptionsOneShotWithoutControlIsUnaffected: --query alone (the
// overwhelmingly common case) must not be penalized by a check that only exists
// for the --control combination.
func TestAgentControlOptionsOneShotWithoutControlIsUnaffected(t *testing.T) {
	opts, err := agentControlOptions(false, 0, false, true /* --query set */)
	if err != nil {
		t.Fatalf("a bare one-shot with no --control must not be refused: %v", err)
	}
	if opts != nil {
		t.Errorf("got %+v, want nil (control was never requested)", opts)
	}
}
