package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/control"
)

var (
	controlEnabled bool
	controlPort    int
)

// controlOptionsFor reads this command's flags. Split from controlOptions so the
// decision logic stays a pure function (and so the package has no init cycle
// between rootCmd and the flag lookup).
func controlOptionsFor(cmd *cobra.Command) (*control.Options, error) {
	return controlOptions(controlEnabled, controlPort, cmd.Flags().Changed("control-port"))
}

// controlOptions turns the --control / --control-port flags into control
// options, or nil when the control API is off (the default).
//
// --control-port implies --control: asking for a specific port and then not
// getting a server would be a silent no-op.
func controlOptions(enabled bool, port int, explicitPort bool) (*control.Options, error) {
	// explicitPort, not "port != 0": `--control-port 0` means "auto-assign", and
	// inferring "off" from the zero value would start the TUI with no control
	// API while the user believes they enabled it.
	if !enabled && !explicitPort {
		return nil, nil
	}
	if port == control.ReservedPort {
		return nil, fmt.Errorf(
			"--control-port %d is reserved and must never be bound; pick another port or drop the flag to auto-assign",
			control.ReservedPort)
	}
	if port < 0 || (port > 0 && port < 1024) || port > 65535 {
		return nil, fmt.Errorf("--control-port %d is not a usable port; pass 1024-65535, or omit it to auto-assign", port)
	}
	return &control.Options{
		Port:    port,
		Debug:   debug,
		Version: version,
	}, nil
}
