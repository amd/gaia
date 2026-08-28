package preflight

import (
	"fmt"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"
)

// How this package answers "the local model server is not running".
//
// `lemonade-server serve` was the answer for years and is now wrong on most
// machines: modern Lemonade (10.7+) dropped that CLI. Windows ships
// LemonadeServer.exe plus a tray app, Linux ships /usr/bin/lemond under a
// systemd user unit, macOS ships an app bundle — and on none of them does
// `lemonade-server` exist. A row that correctly detects the outage and then
// names a command that errors is worse than one that says nothing: the user has
// no path forward and no reason to doubt the instruction.
//
// So the remedy is RESOLVED against this machine rather than hardcoded. That is
// deliberately stronger than switching on runtime.GOOS: `gaia.llm
// .lemonade_launcher.resolve_lemonade` — the Python that owns this decision —
// only probes the Windows and Linux canonical paths before falling back to the
// legacy CLI names, so on macOS it reports "nothing installed" even where
// /usr/local/bin/lemond is the process serving the port. A GOOS table copied
// from it would inherit that hole. Probing what is on disk cannot.
//
// The two lists it agrees with exactly: the legacy CLI names and their order,
// and the modern per-platform launchers. `lemonade-server` also remains the
// correct PACKAGE name (the MSI and .deb are `lemonade-server-*`); it is only
// the run command that is stale, so it stays in the legacy probe below.

// legacyBinaries are the pre-10.7 CLI names, in probe order. Mirrors
// lemonade_launcher._LEGACY_BINARIES — `-dev` is the pip/CI variant.
var legacyBinaries = []string{"lemonade-server", "lemonade-server-dev"}

// serverPathEnv is the explicit override. Mirrors lemonade_launcher: an
// override is run VERBATIM, never rerouted to systemctl, because a user who
// named a binary meant that binary.
const serverPathEnv = "LEMONADE_SERVER_PATH"

// lemonadeRowLabel names the local model server on the checklist.
//
// "Local AI" said what it does and not what it IS, so every remedy under it —
// and every doc, log line and support answer — talked about Lemonade while the
// row the user was looking at never used the word. Naming it is what makes the
// row searchable.
const lemonadeRowLabel = "Lemonade"

// lemonadeDocs is where the run instructions for every platform live.
const lemonadeDocs = "https://lemonade-server.ai/docs/guide/"

// launcher is how THIS machine starts its local model server.
type launcher struct {
	// Start is the command that starts it. Empty only when nothing is installed.
	Start string
	// Restart is the command for a server that is up but wedged. Equal to Start
	// where the launcher has no distinct restart form.
	Restart string
	// AppHint names the way a human is expected to do it when that is not a
	// command at all — the macOS Applications folder, the Windows tray icon.
	// Empty on platforms with no such path.
	AppHint string
	// Foreground is true when Start occupies the terminal it is run in. The
	// remedy then must not say "and press r" as though the shell came back.
	Foreground bool
	// ServiceManaged is true when a unit file, launchd plist, app bundle, or the
	// command's own flags own the server's environment. The context window then
	// comes from THAT definition, and prefixing the command with an env
	// assignment would change nothing while looking like it did.
	ServiceManaged bool
	// CtxSize is the context window the server must come up with, for the
	// remedy's prose. Always resolved, even when the command cannot carry it.
	CtxSize int
	// BadOverride is a LEMONADE_SERVER_PATH that names something absent. It is
	// its own state: the fix is to correct the variable, not to install anything.
	BadOverride string
	// Found is false when no Lemonade tooling could be located, in which case the
	// honest remedy is to install it, not to start it.
	Found bool
}

// hostProbe is the host lookup resolveLemonade performs. It is a struct of
// functions so a test can resolve against a machine it does not have — proving
// the Windows and Linux answers on a macOS box, and the not-installed answer on
// a developer box that has Lemonade.
type hostProbe struct {
	goos     string
	lookPath func(string) (string, error)
	exists   func(string) bool
	getenv   func(string) string
	homeDir  func() (string, error)
	readFile func(string) ([]byte, error)
}

// realHostProbe is a var so a test can resolve against a machine it does not
// have. Nothing outside tests reassigns it.
var realHostProbe = func() hostProbe {
	return hostProbe{
		goos:     runtime.GOOS,
		lookPath: exec.LookPath,
		exists: func(path string) bool {
			_, err := os.Stat(path)
			return err == nil
		},
		getenv:   os.Getenv,
		homeDir:  os.UserHomeDir,
		readFile: osReadFile,
	}
}

// macAppBundles are the bundle names to look for on macOS. `lemonade-app.app`
// is what the current installer actually lays down (verified on a 10.10.0 box);
// the others are kept as tolerated variants. A command is only ever emitted for
// a bundle found to EXIST — naming a guessed bundle would reintroduce the
// phantom-command bug in a new place.
var macAppBundles = []string{
	"lemonade-app.app", "Lemonade.app", "Lemonade Server.app", "LemonadeServer.app",
}

// macDaemonLabel is the launchd job that owns the server on macOS, and
// macDaemonPlist is the file whose presence proves it is installed. The daemon is
// a SYSTEM job (RunAtLoad, no KeepAlive), so it does not come back on its own
// after it exits, and only launchctl can restart it.
//
// The label tracks the pinned Lemonade: upstream renamed it from
// com.lemonade.server after 11.5.0, so the old one matches nothing on 11.8.1.
const (
	macDaemonLabel = "ai.lemonadeserver.server"
	macDaemonPlist = "/Library/LaunchDaemons/ai.lemonadeserver.server.plist"
)

// macDaemonBinaries are where the installer puts lemond. Probed by PATH-free
// existence because a TUI launched from a GUI session inherits a PATH of
// /usr/bin:/bin:/usr/sbin:/sbin — so a PATH-only probe reports a fully installed
// machine as having nothing, and the row then advises `gaia init` over a server
// that is merely stopped.
var macDaemonBinaries = []string{"/usr/local/bin/lemond", "/opt/homebrew/bin/lemond"}

// resolveLemonade reports how to start the local model server on this machine.
//
// It is called fresh every time rather than resolved once: the gate re-runs on
// every launch and caches nothing on purpose, and a user who installs Lemonade
// and then presses r must be told the command they now have. The cost is a
// handful of stat and PATH lookups, no network.
func resolveLemonade() launcher { return resolveLemonadeWith(realHostProbe()) }

// resolveLemonadeWith resolves the launcher AND the context window it has to come
// up with. Those are one answer, not two: a start command without the window
// produces a server that answers /health and 502s every agent query, which would
// take this row green and fail on first use.
func resolveLemonadeWith(p hostProbe) launcher {
	l := resolveLauncherWith(p)
	if l.Found {
		l.CtxSize = profileCtxSize(p)
		if !l.ServiceManaged {
			prefix := ctxPrefix(p.goos, l.CtxSize)
			l.Start, l.Restart = prefix+l.Start, prefix+l.Restart
		}
	}
	return l
}

func resolveLauncherWith(p hostProbe) launcher {
	// 1. An explicit override wins — but only if it is THERE. The Python runs it
	// verbatim and lets the exec fail loudly; this advises a human, and a `run:`
	// line for a file that does not exist is the bug this file exists to remove.
	// A stale override also has to stop suppressing the install advice, so it
	// reports itself rather than silently falling through to a different answer.
	if override := strings.TrimSpace(p.getenv(serverPathEnv)); override != "" {
		if !p.exists(override) {
			return launcher{BadOverride: override}
		}
		cmd := quoteCommand(override)
		return launcher{Start: cmd, Restart: cmd, Found: true}
	}

	switch p.goos {
	case "windows":
		if l, ok := resolveWindows(p); ok {
			return l
		}
	case "linux":
		if l, ok := resolveLinux(p); ok {
			return l
		}
	case "darwin":
		if l, ok := resolveDarwin(p); ok {
			return l
		}
	}

	// 2. A modern daemon on PATH, wherever this is. macOS installs put lemond on
	// PATH at /usr/local/bin; a hand-built or relocated install can land it
	// anywhere. It is the process that serves the port, so it is the most
	// precise thing to name.
	if path, err := p.lookPath("lemond"); err == nil {
		cmd := quoteCommand(path)
		return launcher{Start: cmd, Restart: cmd, AppHint: appHintFor(p.goos), Found: true}
	}

	// 3. Legacy CLI, if this machine really does still have it.
	for _, name := range legacyBinaries {
		if _, err := p.lookPath(name); err == nil {
			// The legacy CLI takes the window as a FLAG, not an environment
			// variable (build_start_command's legacy branch), so it appends its
			// own and opts out of the env prefix.
			cmd := name + " serve" + legacyCtxFlag(profileCtxSize(p))
			return launcher{Start: cmd, Restart: cmd, ServiceManaged: true, Found: true}
		}
	}

	// 4. Nothing is installed. "Start it" is not a remedy for something that is
	// not there — the only honest next step is the installer.
	return launcher{}
}

// windowsStart renders the server invocation.
//
// SHELL NOTE: this is cmd.exe syntax. A leading quoted string is an expression in
// PowerShell, which needs the `&` call operator — and `&` is a command separator
// in cmd.exe, so no single string is right in both. The quoting only appears when
// the install path contains a space (a user profile like "Jane Doe"), and the
// AppHint carries a shell-free alternative for exactly that reason: the tray app
// is what the docs tell a Windows user to use, so it leads the remedy's prose and
// this line is the terminal fallback.
func windowsStart(exe string) string { return quoteCommand(exe) + " --silent" }

const windowsTrayHint = "the 🍋 tray icon → Open Lemonade App"

func resolveWindows(p hostProbe) (launcher, bool) {
	// Canonical modern location, per lemonade_launcher.resolve_lemonade. The
	// non-empty guard matters: LOCALAPPDATA unset would otherwise build a
	// RELATIVE path that could accidentally exist.
	if local := p.getenv("LOCALAPPDATA"); local != "" {
		exe := filepath.Join(local, "lemonade_server", "bin", "LemonadeServer.exe")
		if p.exists(exe) {
			cmd := windowsStart(exe)
			return launcher{Start: cmd, Restart: cmd, AppHint: windowsTrayHint, Found: true}, true
		}
	}
	if path, err := p.lookPath("LemonadeServer.exe"); err == nil {
		cmd := windowsStart(path)
		return launcher{Start: cmd, Restart: cmd, AppHint: windowsTrayHint, Found: true}, true
	}
	return launcher{}, false
}

// linuxUnitPaths are where the lemond user unit lands. Its presence — plus a
// systemctl to drive it — is what makes the systemd answer real.
var linuxUnitPaths = []string{
	"/usr/lib/systemd/user/lemond.service",
	"/lib/systemd/user/lemond.service",
	"/etc/systemd/user/lemond.service",
}

func resolveLinux(p hostProbe) (launcher, bool) {
	daemon := ""
	for _, path := range []string{"/usr/bin/lemond", "/usr/local/bin/lemond"} {
		if p.exists(path) {
			daemon = path
			break
		}
	}
	if daemon == "" && !p.exists("/usr/bin/lemonade") {
		return launcher{}, false
	}

	// The modern Linux package runs the daemon under a systemd USER unit, so
	// start and restart are genuinely different commands — a wedged daemon needs
	// the second one. But `/usr/bin/lemond` existing does NOT imply a systemd
	// session: a container, WSL1, a non-systemd distro, or a tarball install with
	// no unit all answer `systemctl` with an error. Require both halves, or name
	// the binary instead.
	if _, err := p.lookPath("systemctl"); err == nil && p.exists(unitPath(p)) {
		// systemd: the unit file owns the service environment, so the context
		// window belongs there and an inline `env` prefix would be inert.
		return launcher{
			Start:          "systemctl --user start lemond",
			Restart:        "systemctl --user restart lemond",
			ServiceManaged: true,
			Found:          true,
		}, true
	}
	if daemon != "" {
		return launcher{Start: daemon, Restart: daemon, Foreground: true, Found: true}, true
	}
	return launcher{}, false
}

// unitPath returns the first lemond unit file that exists, or the first candidate
// so the caller's exists() check simply fails.
func unitPath(p hostProbe) string {
	for _, path := range linuxUnitPaths {
		if p.exists(path) {
			return path
		}
	}
	return linuxUnitPaths[0]
}

// resolveDarwin picks the macOS launcher.
//
// The order here is EVIDENCE-led, and the evidence is worth recording because two
// more obvious orders are both wrong on a real 10.10.0 machine:
//
//   - The app bundle is what the docs tell a user to run, but opening
//     /Applications/lemonade-app.app on that machine produced only
//     `/usr/local/bin/lemonade-tray` — no server. It brings up the tray UI; it is
//     not, on its own, a verified way to get the port listening.
//   - `launchctl kickstart system/<macDaemonLabel>` looks canonical (the
//     plist is installed, ProgramArguments is [/usr/local/bin/lemond]) but on
//     that machine the job reports `state = not running` while a lemond outside
//     launchd serves the port — and driving a system-domain job needs sudo, so
//     the command could not be verified at all. Naming an unverified sudo
//     command is the same sin as naming a command that does not exist.
//
// What WAS verified end to end: running the binary directly brings the port up in
// about two seconds, and the gate's own re-check then goes green. So that is what
// the row names, with the tray app offered as the no-terminal alternative and
// launchd kept only for a machine that has the job but not the binary.
func resolveDarwin(p hostProbe) (launcher, bool) {
	// 1. The daemon binary, by ABSOLUTE PATH rather than PATH lookup: a TUI
	// launched from a GUI session inherits /usr/bin:/bin:/usr/sbin:/sbin, and a
	// PATH-only probe would report a fully installed machine as having nothing —
	// then advise `gaia init` over a server that is merely stopped.
	for _, path := range macDaemonBinaries {
		if p.exists(path) {
			return launcher{
				Start: path, Restart: path,
				AppHint:    appHintFor("darwin"),
				Foreground: true,
				Found:      true,
			}, true
		}
	}

	// 2. The app bundle, for a machine where the binary is inside it.
	for _, dir := range macAppDirs(p) {
		for _, bundle := range macAppBundles {
			// path.Join, not filepath.Join: these are macOS paths and are always
			// POSIX. filepath would emit "\\Applications\\..." when this resolver
			// runs on a Windows host — which the darwin tests do.
			full := path.Join(dir, bundle)
			if p.exists(full) {
				cmd := "open " + quoteCommand(full)
				return launcher{
					Start: cmd, Restart: cmd,
					AppHint:        appHintFor("darwin"),
					ServiceManaged: true,
					Found:          true,
				}, true
			}
		}
	}

	// 3. The launchd job, when it is the only thing here. Last because of the
	// sudo it needs and because it could not be verified; still better than
	// telling the user nothing is installed when the job is plainly there.
	if p.exists(macDaemonPlist) {
		if _, err := p.lookPath("launchctl"); err == nil {
			target := "system/" + macDaemonLabel
			return launcher{
				Start:          "sudo launchctl kickstart " + target,
				Restart:        "sudo launchctl kickstart -k " + target,
				AppHint:        appHintFor("darwin"),
				ServiceManaged: true,
				Found:          true,
			}, true
		}
	}
	return launcher{}, false
}

func macAppDirs(p hostProbe) []string {
	dirs := []string{"/Applications"}
	if home, err := p.homeDir(); err == nil && home != "" {
		dirs = append(dirs, path.Join(home, "Applications"))
	}
	return dirs
}

func appHintFor(goos string) string {
	switch goos {
	case "darwin":
		return "the Lemonade app in your Applications folder"
	case "windows":
		return "the 🍋 tray icon → Open Lemonade App"
	default:
		return ""
	}
}

// quoteCommand wraps a path in double quotes when it contains a space, so a
// Windows install under "C:\Users\Jane Doe\..." stays copy-pasteable.
func quoteCommand(path string) string {
	if strings.ContainsAny(path, " \t") {
		return `"` + path + `"`
	}
	return path
}

// --- the two remedies -------------------------------------------------------

// lemonadeStartRemedy is what to do about a local model server that is not
// running.
func lemonadeStartRemedy() Remedy {
	l := resolveLemonade()
	switch {
	case l.BadOverride != "":
		return badOverrideRemedy(l)
	case !l.Found:
		return Remedy{
			Action: "Install the local model server first — it is not on this machine. " +
				"`gaia init` sets it up, then press r to re-check.",
			Command: "gaia init",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
	}
	return Remedy{
		Action:  "Start it" + orTheApp(l) + comeBack(l) + ctxNote(l),
		Command: l.Start,
		Where:   lemonadeDocs,
	}
}

// lemonadeRestartRemedy is what to do about one that is up but not answering
// properly.
func lemonadeRestartRemedy() Remedy {
	l := resolveLemonade()
	switch {
	case l.BadOverride != "":
		return badOverrideRemedy(l)
	case !l.Found:
		// Something IS answering the port, so something is installed — this machine
		// just cannot name its launcher. `gaia init` will not restart a server it
		// finds reachable, so the honest command is the one that clears it first.
		return Remedy{
			Action: "GAIA could not work out how the local model server is launched on this " +
				"machine. Stop it the way you started it — or clear it with the command " +
				"below — then start it again and press r.",
			Command: "gaia kill",
			Where:   lemonadeDocs,
		}
	}
	if l.Restart != l.Start {
		return Remedy{
			Action:  "Restart it, then press r to re-check." + ctxNote(l),
			Command: l.Restart,
			Where:   lemonadeDocs,
		}
	}
	// No distinct restart form, so the stop is the user's to do — and saying WHY
	// is what makes the instruction followable. Measured on lemond 10.10.0: it has
	// no stop, restart or daemonize verb, and a second instance against a held
	// port prints "Port 13305 on 127.0.0.1 is already in use. … This instance will
	// now exit." So a restart remedy that omits the stop is a command that cannot
	// run from the only state the row appears in.
	return Remedy{
		Action: "Stop the running server first — it holds the port, and a second one just " +
			"exits — then start it again" + orTheApp(l) + comeBack(l) + ctxNote(l),
		Command: l.Restart,
		Where:   lemonadeDocs,
	}
}

// badOverrideRemedy names the variable, because nothing else on the machine is
// wrong and installing something would not help.
func badOverrideRemedy(l launcher) Remedy {
	return Remedy{
		Action: fmt.Sprintf(
			"%s points at %q, which is not on this machine. Unset it (or correct it) so "+
				"GAIA can find the installed server, then press r.", serverPathEnv, l.BadOverride),
		Command: "unset " + serverPathEnv,
		Where:   lemonadeDocs,
	}
}

func orTheApp(l launcher) string {
	if l.AppHint == "" {
		return ""
	}
	return fmt.Sprintf(" (or open %s)", l.AppHint)
}

// comeBack closes the sentence. A foreground server never gives the shell back,
// so "then press r" would be advice the user cannot follow in that terminal.
func comeBack(l launcher) string {
	if l.Foreground {
		return " — it keeps that terminal, so press r back here once it is up."
	}
	return ", then press r to re-check."
}

// ctxNote is added ONLY where the command cannot carry the context window
// itself — a systemd unit, a launchd plist, an app bundle. Those own their own
// environment, so the window has to be set there, and a server started without
// it answers /health while 502-ing every agent query. Saying nothing would leave
// the user reading that as an agent bug.
func ctxNote(l launcher) string {
	if !l.ServiceManaged || l.CtxSize <= 0 || strings.Contains(l.Start, "--ctx-size") {
		return ""
	}
	return fmt.Sprintf(" It has to come up with %s=%d — that is set in the service's own "+
		"configuration, and without it queries fail even though the server looks healthy.",
		ctxSizeEnv, l.CtxSize)
}
