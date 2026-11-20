package catalog

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"
)

// The Agent UI downloads gaiacpp into the bin folder of GAIA's home, and both
// apps share one saved choice, so that folder has to be searched here too for
// gaiacpp asked for by name, which is what a saved flagship_agent=gaiacpp becomes.
func TestGaiacppOnlyInGaiaHomeBinIsFound(t *testing.T) {
	homes := map[string]func(*testing.T, nativeFixture) string{
		"the default ~/.gaia": func(t *testing.T, f nativeFixture) string {
			return filepath.Join(f.home, ".gaia")
		},
		"GAIA_HOME": func(t *testing.T, f nativeFixture) string {
			dir := t.TempDir()
			t.Setenv("GAIA_HOME", dir)
			return dir
		},
		"GAIA_CONFIG_DIR": func(t *testing.T, f nativeFixture) string {
			dir := t.TempDir()
			t.Setenv("GAIA_CONFIG_DIR", dir)
			return dir
		},
	}
	for name, home := range homes {
		for _, choice := range []string{NativeAgentBinary} {
			t.Run(name+"/choice="+choice, func(t *testing.T) {
				f := isolateNative(t)
				want := writeProgram(t, filepath.Join(home(t, f), "bin"), NativeAgentBinary)

				c := NewCatalog()
				if err := selectAgent(c, choice); err != nil {
					t.Fatalf("gaiacpp in GAIA home's bin was not found: %v", err)
				}
				got := c.Get(FlagshipID)
				if !got.Native || !samePath(t, got.BinaryPath, want) {
					t.Fatalf("picked native=%v path=%q, want %q", got.Native, got.BinaryPath, want)
				}
			})
		}
	}
}

// GAIA_HOME moves the whole tree: the default home's bin is then not searched.
func TestGaiaHomeOverrideReplacesTheDefaultBin(t *testing.T) {
	f := isolateNative(t)
	writeProgram(t, filepath.Join(f.home, ".gaia", "bin"), NativeAgentBinary)
	moved := t.TempDir()
	t.Setenv("GAIA_HOME", moved)

	l := FindNative(true)
	if l.Found() {
		t.Fatalf("gaiacpp under the default home was run although GAIA_HOME points elsewhere: %s", l.Path)
	}
	if !strings.Contains(strings.Join(l.Looked, "|"), filepath.Join(moved, "bin")) {
		t.Errorf("the search does not report GAIA_HOME's bin: %v", l.Looked)
	}
}

// A missing gaiacpp names GAIA home's bin among the places it looked.
func TestMissingGaiacppErrorNamesGaiaHomeBin(t *testing.T) {
	f := isolateNative(t)
	err := selectAgent(NewCatalog(), NativeAgentBinary)
	var missing *MissingProgramError
	if !errors.As(err, &missing) {
		t.Fatalf("got %v, want a missing-program error", err)
	}
	want := filepath.Join(f.home, ".gaia", "bin")
	if !strings.Contains(err.Error(), want) {
		t.Errorf("the error does not list %s:\n%v", want, err)
	}
}
