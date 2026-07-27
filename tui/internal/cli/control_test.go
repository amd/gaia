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
