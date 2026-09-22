package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui"
)

// dev is developer mode: rich in-TUI output (per-turn timings, step and turn
// boundaries, tool arguments and truncated tool output, raw harness statuses)
// and DEBUG-level file logging in the agent it spawns.
//
// One flag, not two. `--debug` already meant exactly this in the TUI, and the
// agent half of the same feature shipped as `--dev`; keeping both names as
// separate switches would give one idea two spellings that could disagree. So
// `--dev` is the name and `--debug` is a hidden alias onto this same variable
// (see init) — old scripts and docs keep working, help lists one flag.
var dev bool

// bypassPermissions starts agents with confirmation prompts off: every gated
// tool — shell commands, file writes — runs without asking.
//
// Off unless passed, and only for this launch. Nothing persists it, so there
// is no way to land in this mode without having typed it, and the TUI carries
// an unmissable banner for as long as it is on.
var bypassPermissions bool

// useClaude routes the spawned agent's inference to Anthropic's Claude API
// instead of the local Lemonade backend. A real privacy change from GAIA's
// local-by-default posture, so the chat header carries a "claude" chip for as
// long as the session runs.
var useClaude bool

// claudeModel picks which Claude model --use-claude uses. Defaults to Claude
// Sonnet 5; an explicit empty value lets the agent pick its own default.
var claudeModel string

// defaultClaudeModel is the model --use-claude runs on when --claude-model is
// not given.
const defaultClaudeModel = "claude-sonnet-5"

// claudeModelArg is the model to forward to the child. Without --use-claude
// there is nothing to forward — the default would otherwise trip the factory's
// model-without-mode refusal on every local launch.
func claudeModelArg() string {
	if !useClaude {
		return ""
	}
	return claudeModel
}

const defaultBinaryName = "gaia-tui"

// binaryName derives the command name from argv[0]. The installer ships this as
// `gaia-tui` because the Python CLI owns `gaia`, so a hardcoded name would print
// usage lines for a command the user does not have.
func binaryName(argv0 string) string {
	name := filepath.Base(strings.TrimSpace(argv0))
	if ext := filepath.Ext(name); strings.EqualFold(ext, ".exe") {
		name = strings.TrimSuffix(name, ext)
	}
	switch name {
	case "", ".", "..", "/", `\`:
		return defaultBinaryName
	}
	// Cobra takes the command name from the first word of Use, so a name with
	// whitespace in it would be silently truncated.
	if strings.ContainsAny(name, " \t") {
		return defaultBinaryName
	}
	return name
}

// mockAgent overrides the agent binary with a stand-in, for tests that need a
// deterministic child. Declared here rather than beside the flag so every entry
// point that honours it reads the same variable.
var mockAgent string

// tracePath is --trace's raw value: "" (off), traceAutoPath (bare --trace), or
// the path the user gave. Resolved by openTrace.
var tracePath string

// traceAutoPath is what bare --trace parses to: cobra needs a NoOptDefVal for
// the flag to be legal without a value, and "" already means "off".
//
// A WORD, not an unprintable sentinel — pflag prints NoOptDefVal verbatim in
// --help, so a control character lands in the flag listing. As a side effect
// `--trace=auto` is the same as bare `--trace`, which is what it reads like.
// A file genuinely named "auto" is still reachable as `--trace=./auto`.
const traceAutoPath = "auto"

// traceArgAdvice explains a stray positional that is really a spaced --trace
// path, and returns nil when --trace does not explain it.
//
// want is how many positionals the command legitimately takes. pflag refuses to
// attach a spaced value to a flag that is legal without one, so
// `… --trace out.jsonl` leaves out.jsonl as an argument and records to the
// DEFAULT path — the exact "recording somewhere you did not ask for" this flag
// exists to remove. Every command that accepts --trace has to say so, not just
// the root one.
func traceArgAdvice(args []string, want int) error {
	if tracePath != traceAutoPath || len(args) <= want {
		return nil
	}
	stray := args[want]
	return fmt.Errorf(
		"--trace takes its path attached, not spaced: write --trace=%s "+
			"(as written, %q was read as an argument, and the trace would have gone "+
			"to the default path instead)", stray, stray)
}

// openTrace turns --trace into a writer, or nil when the flag was not passed.
// agentID names the run in the default filename, so a trace can be told apart
// from another agent's without opening it.
//
// It returns an error rather than warning and continuing: a trace that is not
// being written is indistinguishable from an agent that did nothing, which is
// the exact confusion the flag exists to remove.
func openTrace(agentID string) (*event.TraceWriter, error) {
	if tracePath == "" {
		return nil, nil
	}
	path := tracePath
	if path == traceAutoPath {
		auto, err := event.DefaultTracePath(agentID, time.Now())
		if err != nil {
			return nil, err
		}
		path = auto
	}
	w, err := event.NewTraceWriter(path)
	if err != nil {
		return nil, err
	}
	// A recorder the user cannot find is a recorder that does not exist.
	fmt.Fprintf(os.Stderr, "trace → %s\n", w.Path())
	return w, nil
}

var rootCmd = &cobra.Command{
	Use:   defaultBinaryName,
	Short: "GAIA in your terminal",
	Long: "Chat with GAIA — documents, data, web research, memory and skills — " +
		"running on this machine.",
	// A one-line refusal followed by 20 lines of command listing pushes the
	// actual error off a short terminal. Usage is what --help is for.
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		trace, err := openTrace(catalog.FlagshipID)
		if err != nil {
			return err
		}
		defer closeTrace(trace)
		return ui.RunFlagship(dev, mockAgent, ctrl, bypassPermissions, useClaude, claudeModelArg(), trace)
	},
}

// closeTrace flushes and closes the trace, reporting a recording that stopped
// early. Silence here would let a truncated trace read as a complete one.
func closeTrace(w *event.TraceWriter) {
	if err := w.Close(); err != nil {
		fmt.Fprintf(os.Stderr, "trace: %v\n", err)
	}
}

func init() {
	// Empty is cobra's documented opt-out for the Windows Explorer guard. Left
	// at its default, cobra refuses to run and prints "This is a command line
	// tool" for ANY launch it did not trace to a console — which includes the
	// Start Menu and desktop shortcuts the installer creates. This binary opens
	// its own full-screen TUI, so an Explorer launch is a supported entry point,
	// not a mistake. root_test.go pins this.
	cobra.MousetrapHelpText = ""

	rootCmd.PersistentFlags().BoolVar(&dev, "dev", false,
		"developer mode: show per-turn timings, steps, and tool arguments and output "+
			"(agents the TUI spawns itself also log at DEBUG to ~/.gaia/logs/). "+
			"This is what is on SCREEN; --trace writes the same events to a file")
	// Same variable as --dev, hidden: the previous name for this mode. Kept so
	// existing scripts and docs do not break, out of --help so the two spellings
	// never read as two features.
	rootCmd.PersistentFlags().BoolVar(&dev, "debug", false, "deprecated alias for --dev")
	if err := rootCmd.PersistentFlags().MarkHidden("debug"); err != nil {
		panic(err) // only fails on a flag name that was never registered
	}
	rootCmd.PersistentFlags().BoolVar(&bypassPermissions, "bypass-permissions", false,
		"subprocess agents only: run every tool without asking for confirmation — the agent acts fully "+
			"autonomously. Off by default; the TUI shows a persistent warning "+
			"while it is on, and /bypass off turns it off mid-session")
	rootCmd.PersistentFlags().BoolVar(&useClaude, "use-claude", false,
		"run the agent against Anthropic's Claude API instead of the local Lemonade "+
			"backend — your conversation is sent to Anthropic, not processed on this "+
			"machine. Requires ANTHROPIC_API_KEY. The local server is NOT started and "+
			"first-run setup is skipped; the chat header names the model in use "+
			"(e.g. \"claude · haiku-4.5\") while this is on")
	rootCmd.PersistentFlags().StringVar(&claudeModel, "claude-model", defaultClaudeModel,
		"Claude model id to use with --use-claude: "+
			strings.Join(client.ClaudeModelIDs(), ", ")+
			" (pass \"\" to let the agent pick)")
	// Both --claude-model refusals happen here, before any UI opens: a flag
	// that will not do what it says must fail as a command-line error, not as
	// something the user has to notice inside a running TUI.
	rootCmd.PersistentPreRunE = func(cmd *cobra.Command, args []string) error {
		if rootCmd.PersistentFlags().Changed("claude-model") && !useClaude {
			return fmt.Errorf(
				"--claude-model only applies with --use-claude: the local Lemonade " +
					"backend does not run Claude models. Add --use-claude, or drop --claude-model")
		}
		// An id nothing accepts reaches Anthropic verbatim and comes back a
		// 404 mid-turn — see client.ValidateClaudeModel for why nothing
		// downstream catches it.
		if err := client.ValidateClaudeModel(claudeModel); err != nil {
			return fmt.Errorf("--claude-model: %w", err)
		}
		return nil
	}
	rootCmd.PersistentFlags().BoolVar(&controlEnabled, "control", false,
		"expose the loopback control API so an assistant can drive this session (auto-assigned port)")
	rootCmd.PersistentFlags().IntVar(&controlPort, "control-port", 0,
		"control API port (implies --control; 0 auto-assigns)")
	// Persistent: `run` launches an agent too, and a --mock that only worked on
	// the bare launch let a test spawn the real agent while believing it had
	// substituted a stand-in.
	rootCmd.PersistentFlags().StringVar(&mockAgent, "mock", "",
		"path to a stand-in agent binary, for tests (overrides the agent being launched)")
	rootCmd.PersistentFlags().StringVar(&tracePath, "trace", "",
		"record every agent event — tool calls WITH their arguments, results, errors "+
			"and timings — to a JSONL file, one event per line, for later inspection. "+
			"Bare --trace writes ~/.gaia/traces/<timestamp>-<agent>.jsonl; --trace=<path> "+
			"picks the file (the path must be attached with =, not spaced). Independent "+
			"of --dev, which shows the same events on screen instead. The file holds "+
			"whatever the agent read — file contents, shell output, email — so review "+
			"it before sharing. Does NOT capture prompt size or token accounting — "+
			"those live only in the agent's own recorder (GAIA_TURN_LOG)")
	// Without this, bare --trace is a parse error ("flag needs an argument").
	rootCmd.PersistentFlags().Lookup("trace").NoOptDefVal = traceAutoPath
	// pflag will not attach a spaced value to a NoOptDefVal flag, so
	// `--trace out.jsonl` leaves out.jsonl as a positional and would otherwise
	// be reported as an unknown command — with the real fix nowhere in sight.
	//
	// Cobra's own unknown-command path defaults this lazily; the exported
	// SuggestionsFor does not, and a zero distance matches nothing.
	rootCmd.SuggestionsMinimumDistance = 2
	rootCmd.Args = func(cmd *cobra.Command, args []string) error {
		if len(args) == 0 {
			return nil
		}
		// Cobra's own legacyArgs message, suggestions included — this hook
		// replaced it, so it owes the same help for an ordinary typo.
		near := cmd.SuggestionsFor(args[0])
		// A near-miss is a misspelled COMMAND, not a misplaced path: with
		// --trace on, `gaia-tui --trace chatt` still has to suggest `chat`.
		if len(near) == 0 {
			if err := traceArgAdvice(args, 0); err != nil {
				return err
			}
		}
		msg := fmt.Sprintf("unknown command %q for %q", args[0], cmd.CommandPath())
		if len(near) > 0 {
			msg += "\n\nDid you mean this?\n\t" + strings.Join(near, "\n\t")
		}
		return fmt.Errorf("%s", msg)
	}
}

// Execute runs the CLI.
//
// A leading `tui` word is accepted and dropped. This binary is addressed as
// `gaia tui …` everywhere it is documented, but its own root command is the
// binary's own name, so without this `gaia tui run email` — the exact spelling
// the docs use — would fail with "unknown command". Both spellings work; only
// the first argument is considered, so an agent named "tui" is unaffected.
func Execute() error {
	rootCmd.Use = binaryName(os.Args[0])
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "tui" {
		rootCmd.SetArgs(args[1:])
	}
	return rootCmd.Execute()
}

func debugLog(format string, args ...interface{}) {
	if dev {
		fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
	}
}
