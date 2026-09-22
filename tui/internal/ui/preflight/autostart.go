package preflight

// Starting an installed-but-stopped Lemonade, instead of asking the user to.
//
// The readiness screen used to stop dead on "Lemonade not running" and wait for
// a keypress, on a machine where it had already resolved the exact command that
// would fix it. For the commonest case — installed, just not running — that is a
// prompt to do something the program can do itself, and it is the screen users
// hit most often.
//
// The line this does NOT cross is installing. Nothing is downloaded here: a
// first run still pulls gigabytes of server and weights, and that needs a human
// to agree. So an installed server is started automatically; an absent one keeps
// the `f` key it always had.
//
// It is also not a silent retry. A start that fails reports what was run and
// what came back, and the row goes red exactly as before — the user is never
// left looking at a screen that quietly tried and gave up.

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// autoStartWindow bounds the whole attempt: spawn, then wait for the server to
// answer. Lemonade loads no weights at startup, so this is process spawn plus an
// HTTP listener coming up; a machine that needs longer than this is not merely
// "slow to start" and the user deserves the row rather than a spinner.
const autoStartWindow = 45 * time.Second

// autoStartPoll is how often the server is asked whether it is up yet.
const autoStartPoll = 750 * time.Millisecond

// errNoAutoStart means this launcher must not be started for the user. It is a
// normal outcome, not a failure: nothing is installed, or the only way in is a
// tray icon or an app bundle a human opens.
var errNoAutoStart = fmt.Errorf("this machine has no launcher the TUI may start")

// canAutoStart reports whether l may be started without asking.
//
// Foreground launchers are INCLUDED deliberately. That flag describes what the
// command does to a human's terminal — it would sit there occupying the shell —
// which is a fact about pasting it, not about spawning it. Started as a detached
// child with no terminal attached, a foreground daemon is exactly what is wanted.
func canAutoStart(l launcher) bool {
	return l.Found && l.BadOverride == "" && len(l.Argv) > 0
}

// startLemonade spawns the resolved launcher and waits for the server to answer.
//
// The child is deliberately not waited on: Lemonade is a long-running server, so
// the process this starts outlives the readiness check and must outlive the TUI
// too. Its output goes nowhere — it writes its own log, and inheriting the TUI's
// stdout would paint the alt-screen with server chatter.
func startLemonade(ctx context.Context, l launcher, probe func(context.Context) bool) error {
	if !canAutoStart(l) {
		return errNoAutoStart
	}

	// exec.Command, NOT CommandContext: the context here bounds how long we WAIT
	// for the server, and tying the server's own lifetime to it would kill the
	// thing we just started the moment the check finishes.
	cmd := exec.Command(l.Argv[0], l.Argv[1:]...) // #nosec G204 -- argv is resolved from a fixed set of installed launchers, never user input
	cmd.Stdout, cmd.Stderr = nil, nil
	cmd.Env = startEnv(l)

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("could not run %s: %w", strings.Join(l.Argv, " "), err)
	}

	if waitForLemonade(ctx, probe) {
		return nil
	}
	return fmt.Errorf(
		"ran %s, but nothing was answering %ds later",
		strings.Join(l.Argv, " "), int(autoStartWindow.Seconds()))
}

// startEnv is the child's environment.
//
// The context window is passed here rather than in the command string because a
// service-managed launcher gets it from its unit file or plist — setting it on
// the `systemctl` client process would change nothing while looking like it did.
// Same reasoning as ctxPrefix, which is that decision's display half.
func startEnv(l launcher) []string {
	if l.ServiceManaged || l.CtxSize <= 0 {
		return os.Environ()
	}
	return append(os.Environ(), fmt.Sprintf("%s=%d", ctxSizeEnv, l.CtxSize))
}

// waitForLemonade polls until the server answers or the window closes.
func waitForLemonade(ctx context.Context, probe func(context.Context) bool) bool {
	deadline := time.Now().Add(autoStartWindow)
	ticker := time.NewTicker(autoStartPoll)
	defer ticker.Stop()

	for {
		if probe(ctx) {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-ticker.C:
		}
	}
}

// tryAutoStartLemonade starts an installed-but-stopped server and re-probes.
//
// Returns whether it came up, the base URL it answered on, and a trace for the
// details pane. An empty trace means no attempt was made — nothing installed,
// or a launcher only a human can drive — and the caller should say nothing
// about starting at all.
//
// A var so a test can exercise the row's three outcomes without a Lemonade on
// the box.
var tryAutoStartLemonade = func(ctx context.Context) (bool, string, string) {
	l := resolveLemonade()
	if !canAutoStart(l) {
		return false, "", ""
	}

	probe := func(c context.Context) bool {
		_, reachable, _ := probeLemonade(c)
		return reachable
	}
	if err := startLemonade(ctx, l, probe); err != nil {
		return false, "", "auto-start: " + err.Error()
	}

	base, reachable, trace := probeLemonade(ctx)
	if !reachable {
		// It answered the poll and then stopped answering. Saying "started" here
		// would hand the next row a server that is not there.
		return false, "", "auto-start: came up, then stopped answering\n" + trace
	}
	return true, base, "auto-start: ran " + strings.Join(l.Argv, " ")
}
