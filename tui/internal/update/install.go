// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
)

// Kind is how this gaia-tui was installed, which decides how it is replaced.
type Kind string

const (
	// KindScript is the one-line installer (install.ps1 / install.sh), which
	// owns ~/.gaia/bin and takes GAIA_RELEASE_TAG to install an exact release.
	KindScript Kind = "script"
	// KindWindowsSetup is gaia-<v>-win-x64-setup.exe.
	KindWindowsSetup Kind = "windows-setup"
	// KindMacPkg is gaia-<v>-darwin-<arch>.pkg, which owns /usr/local/bin.
	KindMacPkg Kind = "macos-pkg"
	// KindDeb and KindRpm are the Linux packages, which own /usr/bin.
	KindDeb Kind = "deb"
	KindRpm Kind = "rpm"
)

// Script installers. amd-gaia.ai serves them at the site root.
const (
	installPS1URL = "https://amd-gaia.ai/install.ps1"
	installSHURL  = "https://amd-gaia.ai/install.sh"
	installerSums = "gaia-installers-SHA256SUMS.txt"
)

// Swapped by tests.
var (
	executable = os.Executable
	goos       = runtime.GOOS
	goarch     = runtime.GOARCH
	lookPath   = exec.LookPath
	ownsFile   = packageOwns
	// windowsSetupDir is where the GAIA Windows setup installed the terminal
	// (HKCU\Software\GAIA\TerminalHub InstallDir), or "".
	windowsSetupDir = setupInstallDir
)

// DetectKind works out how the running binary was installed. A binary no
// GAIA installer put there (a `go build`, a copied file) cannot be updated in
// place, and says so instead of guessing.
func DetectKind() (Kind, error) {
	exe, err := executable()
	if err != nil {
		return "", fmt.Errorf("cannot find the running gaia-tui: %w", err)
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	if home, err := homeDir(); err == nil && sameDir(filepath.Dir(exe), filepath.Join(home, ".gaia", "bin")) {
		return KindScript, nil
	}
	switch goos {
	case "windows":
		// Only the folder the GAIA setup recorded it installed to; a copied or
		// extracted exe elsewhere is not one the setup can replace.
		if dir := windowsSetupDir(); dir != "" && sameDir(filepath.Dir(exe), dir) {
			return KindWindowsSetup, nil
		}
	case "darwin":
		if filepath.ToSlash(filepath.Dir(exe)) == "/usr/local/bin" {
			return KindMacPkg, nil
		}
	case "linux":
		if filepath.ToSlash(filepath.Dir(exe)) == "/usr/bin" {
			if ownsFile("dpkg-query", "-S", exe) {
				return KindDeb, nil
			}
			if ownsFile("rpm", "-qf", exe) {
				return KindRpm, nil
			}
		}
	}
	return "", fmt.Errorf("this gaia-tui (%s) was not put there by a GAIA installer, so it cannot update itself — "+
		"install a release from %s, or run the installer from https://amd-gaia.ai", exe, ReleasesPage)
}

func sameDir(a, b string) bool {
	if goos == "windows" {
		return strings.EqualFold(filepath.Clean(a), filepath.Clean(b))
	}
	return filepath.Clean(a) == filepath.Clean(b)
}

func packageOwns(tool string, args ...string) bool {
	if _, err := lookPath(tool); err != nil {
		return false
	}
	return exec.Command(tool, args...).Run() == nil
}

// Plan is one install the user asked for, run after the TUI exits.
type Plan struct {
	Tag     string
	Kind    Kind
	Release Release
	Pin     bool // keep this version: an exact-version install pins it
}

// assetPattern is the installer file for kind on this machine.
func assetPattern(kind Kind) (*regexp.Regexp, error) {
	switch kind {
	case KindWindowsSetup:
		return regexp.MustCompile(`^gaia-[0-9][^/]*-win-x64-setup\.exe$`), nil
	case KindMacPkg:
		arch := "x64"
		if goarch == "arm64" {
			arch = "arm64"
		}
		return regexp.MustCompile(`^gaia-[0-9][^/]*-darwin-` + arch + `\.pkg$`), nil
	case KindDeb:
		return regexp.MustCompile(`^gaia_[0-9][^/]*_amd64\.deb$`), nil
	case KindRpm:
		return regexp.MustCompile(`^gaia-[0-9][^/]*\.x86_64\.rpm$`), nil
	}
	return nil, fmt.Errorf("no installer file for %s installs", kind)
}

// installerAsset finds the installer file for kind in r.
func installerAsset(r Release, kind Kind) (Asset, error) {
	re, err := assetPattern(kind)
	if err != nil {
		return Asset{}, err
	}
	for _, a := range r.Assets {
		if re.MatchString(a.Name) {
			return a, nil
		}
	}
	return Asset{}, fmt.Errorf("GAIA %s has no %s installer for this machine — pick another release at %s", r.Tag, kind, ReleasesPage)
}

// NewPlan checks that tag can be installed here, before the TUI exits for it.
func NewPlan(r Release, pin bool) (Plan, error) {
	kind, err := DetectKind()
	if err != nil {
		return Plan{}, err
	}
	if kind != KindScript {
		if _, err := installerAsset(r, kind); err != nil {
			return Plan{}, err
		}
		if _, ok := r.Asset(installerSums); !ok {
			return Plan{}, fmt.Errorf("GAIA %s publishes no %s, so its installer cannot be verified — pick another release at %s", r.Tag, installerSums, ReleasesPage)
		}
	}
	return Plan{Tag: r.Tag, Kind: kind, Release: r, Pin: pin}, nil
}

var (
	pendingMu sync.Mutex
	pending   *Plan
)

// Schedule queues p to run once the TUI has exited.
func Schedule(p Plan) {
	pendingMu.Lock()
	defer pendingMu.Unlock()
	pending = &p
}

// Pending returns the queued install, if any.
func Pending() (Plan, bool) {
	pendingMu.Lock()
	defer pendingMu.Unlock()
	if pending == nil {
		return Plan{}, false
	}
	return *pending, true
}

// Cancel drops the queued install.
func Cancel() {
	pendingMu.Lock()
	defer pendingMu.Unlock()
	pending = nil
}

// RunPending runs the queued install, if any, in the terminal the TUI just
// gave back. Called after the TUI has closed, so the installer can replace it.
func RunPending(ctx context.Context, stdout, stderr io.Writer) error {
	p, ok := Pending()
	if !ok {
		return nil
	}
	Cancel()
	fmt.Fprintf(stdout, "Installing GAIA %s…\n", p.Tag)
	var err error
	if p.Kind == KindScript {
		err = runScript(ctx, p.Tag, stdout, stderr)
	} else {
		err = runPackage(ctx, p, stdout, stderr)
	}
	if err != nil {
		return fmt.Errorf("GAIA %s was not installed: %w", p.Tag, err)
	}
	if p.Kind == KindWindowsSetup {
		// The setup is still open, so whether it finishes is unknown here;
		// pinning now would pause updates for a version that may never land.
		if p.Pin {
			fmt.Fprintln(stdout, "Not pinned: if you keep GAIA "+p.Tag+", run /update auto off so it stays put.")
		}
		return nil
	}
	pin := ""
	if p.Pin {
		pin = p.Tag
	}
	// An unpinned install (/update now) also drops an older pin, or later
	// checks would say updates are paused for a version no longer running.
	if err := SetPin(pin); err != nil {
		return fmt.Errorf("GAIA %s installed, but the pin setting was not saved: %w", p.Tag, err)
	}
	return nil
}

// runScript re-runs the one-line installer pinned to tag.
func runScript(ctx context.Context, tag string, stdout, stderr io.Writer) error {
	dir, err := os.MkdirTemp("", "gaia-update-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)

	var cmd *exec.Cmd
	if goos == "windows" {
		script := filepath.Join(dir, "install.ps1")
		if err := download(ctx, installPS1URL, script); err != nil {
			return err
		}
		cmd = exec.CommandContext(ctx, "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script)
	} else {
		script := filepath.Join(dir, "install.sh")
		if err := download(ctx, installSHURL, script); err != nil {
			return err
		}
		cmd = exec.CommandContext(ctx, "sh", script)
	}
	cmd.Env = append(os.Environ(), "GAIA_RELEASE_TAG="+tag)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, stdout, stderr

	// Windows will not overwrite a running .exe but will rename one, so move
	// this binary aside for the installer and put it back if the install fails.
	restore := func() error { return nil }
	if goos == "windows" {
		if restore, err = moveSelfAside(); err != nil {
			return err
		}
	}
	if err := cmd.Run(); err != nil {
		if rerr := restore(); rerr != nil {
			return fmt.Errorf("the installer stopped (%v), and %w", err, rerr)
		}
		return fmt.Errorf("the installer stopped (%v) — its output above says why", err)
	}
	// An installer that succeeded without writing this binary must not leave
	// the user with none.
	if err := restore(); err != nil {
		return err
	}
	return nil
}

func moveSelfAside() (func() error, error) {
	exe, err := executable()
	if err != nil {
		return nil, err
	}
	old := exe + ".old"
	_ = os.Remove(old) // a leftover from an earlier update; absent is fine
	if err := os.Rename(exe, old); err != nil {
		return nil, fmt.Errorf("cannot move %s aside for the installer: %w", exe, err)
	}
	// Puts the old binary back only when nothing replaced it.
	return func() error {
		if _, err := os.Stat(exe); !errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err := os.Rename(old, exe); err != nil {
			return fmt.Errorf("gaia-tui is missing: putting it back from %s failed (%v) — rename that file to %s by hand", old, err, exe)
		}
		return nil
	}, nil
}

// RemoveLeftover deletes the binary an earlier Windows update moved aside.
func RemoveLeftover() error {
	exe, err := executable()
	if err != nil {
		return fmt.Errorf("cannot find the running gaia-tui to tidy after an update: %w", err)
	}
	err = os.Remove(exe + ".old")
	if err == nil || errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return fmt.Errorf("cannot remove %s.old left by the last update: %w", exe, err)
}

// runPackage downloads the platform installer, verifies it against the
// release's checksums, and hands it to the system's installer.
func runPackage(ctx context.Context, p Plan, stdout, stderr io.Writer) error {
	asset, err := installerAsset(p.Release, p.Kind)
	if err != nil {
		return err
	}
	sums, ok := p.Release.Asset(installerSums)
	if !ok {
		return fmt.Errorf("GAIA %s publishes no %s, so its installer cannot be verified", p.Tag, installerSums)
	}
	dir, err := os.MkdirTemp("", "gaia-update-")
	if err != nil {
		return err
	}
	sumsPath := filepath.Join(dir, installerSums)
	if err := download(ctx, sums.URL, sumsPath); err != nil {
		return err
	}
	want, err := checksumFor(sumsPath, asset.Name)
	if err != nil {
		return err
	}
	file := filepath.Join(dir, asset.Name)
	fmt.Fprintf(stdout, "Downloading %s…\n", asset.Name)
	if err := download(ctx, asset.URL, file); err != nil {
		return err
	}
	if got, err := sha256File(file); err != nil {
		return err
	} else if got != want {
		os.Remove(file)
		return fmt.Errorf("%s does not match its published checksum (expected %s, got %s) — refusing to install it; retry, and report it at https://github.com/amd/gaia/issues if it persists", asset.Name, want, got)
	}

	var cmd *exec.Cmd
	switch p.Kind {
	case KindWindowsSetup:
		// Started, not waited on: setup replaces this binary, so it must
		// outlive it. Its own window takes it from here, which is also why
		// its temp folder is left for the OS to clear.
		fmt.Fprintf(stdout, "Opening the GAIA %s setup. Finish it there.\n", p.Tag)
		return exec.Command(file).Start()
	case KindMacPkg:
		fmt.Fprintf(stdout, "Opening the GAIA %s installer. Finish it there.\n", p.Tag)
		cmd = exec.CommandContext(ctx, "open", "-W", file)
	case KindDeb:
		cmd = exec.CommandContext(ctx, "sudo", "dpkg", "-i", file)
	case KindRpm:
		cmd = exec.CommandContext(ctx, "sudo", "rpm", "-U", "--oldpackage", file)
	default:
		return fmt.Errorf("no installer for %s installs", p.Kind)
	}
	defer os.RemoveAll(dir)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, stdout, stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s stopped (%v) — its output above says why; the verified installer was %s", cmd.Path, err, file)
	}
	return nil
}

func download(ctx context.Context, url, dest string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	// No overall timeout: an installer is large. The dial and headers still
	// fail fast through the transport's defaults.
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("cannot download %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("cannot download %s: %s", url, resp.Status)
	}
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return fmt.Errorf("download of %s stopped: %w", url, err)
	}
	return f.Close()
}

// checksumFor reads name's digest out of a sha256sum-format file.
func checksumFor(sumsPath, name string) (string, error) {
	f, err := os.Open(sumsPath)
	if err != nil {
		return "", err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) == 2 && strings.TrimPrefix(fields[1], "*") == name {
			return strings.ToLower(fields[0]), nil
		}
	}
	if err := sc.Err(); err != nil {
		return "", err
	}
	return "", fmt.Errorf("%s has no checksum for %s — refusing to install an unverified file", installerSums, name)
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
