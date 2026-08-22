package cli

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	version = "dev"
	commit  = "unknown"
	date    = "unknown"
)

// versionString is the one line both `version` and `--version` print, so a
// published artifact reports the same thing either way a user asks.
func versionString() string {
	return fmt.Sprintf("gaia %s (commit: %s, built: %s)\n", version, commit, date)
}

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Print version information",
	Run: func(cmd *cobra.Command, args []string) {
		// cmd.OutOrStdout(), not a bare fmt.Print: cobra's own --version path
		// writes through the command's configured writer, and a raw stdout
		// write here would silently diverge from it under any test or caller
		// that redirects output (as TestVersionSubcommandMatchesFlag does).
		fmt.Fprint(cmd.OutOrStdout(), versionString())
	},
}

func init() {
	rootCmd.AddCommand(versionCmd)
	// rootCmd.Version left empty means cobra never registers --version at all
	// (root_test.go pins this) -- a published binary with no way to answer
	// "what version is this" is exactly what the install script has nothing to
	// assert against.
	rootCmd.Version = version
	rootCmd.SetVersionTemplate(versionString())
}
