package catalog

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The search order is the Agent UI backend's: the named file, GAIA home's bin,
// beside the terminal UI, PATH, then the agent install directory.
func TestGaiacppSearchOrder(t *testing.T) {
	f := isolateNative(t)
	custom := writeProgram(t, t.TempDir(), NativeAgentBinary)
	t.Setenv(NativePathEnv, custom)

	// Earliest first; each is removed in turn so the next one must win.
	places := []struct{ name, path string }{
		{NativePathEnv, custom},
		{"GAIA home's bin", writeProgram(t, filepath.Join(f.home, ".gaia", "bin"), NativeAgentBinary)},
		{"beside the terminal UI", writeProgram(t, f.beside, NativeAgentBinary)},
		{"PATH", writeProgram(t, f.path, NativeAgentBinary)},
		{"the agent install directory", writeProgram(t, filepath.Join(f.home, ".gaia", "agents", FlagshipID), NativeAgentBinary)},
	}
	for _, place := range places {
		for _, inRepo := range []bool{false, true} {
			l := FindNative(inRepo)
			if !l.Found() || !samePath(t, l.Path, place.path) {
				t.Fatalf("with %s the earliest copy left, FindNative(%v) picked %q, want %q",
					place.name, inRepo, l.Path, place.path)
			}
		}
		if err := os.Remove(place.path); err != nil {
			t.Fatal(err)
		}
	}
	if l := FindNative(true); l.Found() {
		t.Fatalf("with every copy removed FindNative still found %q", l.Path)
	}
}

// The "Looked in" list names the places in the order they are searched.
func TestGaiacppLookedInListFollowsTheSearchOrder(t *testing.T) {
	f := isolateNative(t)
	custom := filepath.Join(t.TempDir(), "my-gaiacpp")
	t.Setenv(NativePathEnv, custom)

	got := FindNative(true).Looked
	want := []string{
		custom + " (" + NativePathEnv + ")",
		filepath.Join(f.home, ".gaia", "bin"),
		f.beside,
		"your PATH",
		filepath.Join(f.home, ".gaia", "agents", FlagshipID),
		repoBuildDir,
	}
	if len(got) != len(want) {
		t.Fatalf("looked in %v, want %v", got, want)
	}
	for i := range want {
		if !strings.EqualFold(filepath.Clean(got[i]), filepath.Clean(want[i])) && got[i] != want[i] {
			t.Errorf("place %d is %q, want %q", i+1, got[i], want[i])
		}
	}
}

func fileSHA256(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func writeRecord(t *testing.T, binary, body string) string {
	t.Helper()
	record := filepath.Join(filepath.Dir(binary), NativeInstallRecord)
	if err := os.WriteFile(record, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return record
}

// A gaiacpp the Agent UI downloaded is checked against its record before it
// is run, for gaiacpp by name (what a saved choice becomes) and for its path.
func TestDownloadedGaiacppIsCheckedAgainstItsRecord(t *testing.T) {
	type recordCase struct {
		name string
		// body builds the record from the file's real checksum; "" writes none.
		body    func(sum string) string
		refused string // text the refusal must carry; "" means it runs
	}
	cases := []recordCase{
		{"no record", func(string) string { return "" }, ""},
		{"a matching record", func(sum string) string {
			return `{"version": "0.26.0", "sha256": "` + sum + `", "tag": "v0.26.0"}`
		}, ""},
		{"a matching record in upper case", func(sum string) string {
			return `{"sha256": "` + strings.ToUpper(sum) + `"}`
		}, ""},
		{"a record for another file", func(string) string {
			return `{"version": "0.26.0", "sha256": "` + strings.Repeat("0", 64) + `", "tag": "v0.26.0"}`
		}, "does not match the gaiacpp that was downloaded"},
		{"a record that is not JSON", func(string) string { return `{not json` }, "cannot be read"},
		{"a record that is not an object", func(string) string { return `["sha256"]` }, "cannot be read"},
		{"a record with no checksum", func(string) string { return `{"version": "0.26.0"}` }, "has no checksum"},
		{"a record with an empty checksum", func(string) string { return `{"sha256": ""}` }, "has no checksum"},
	}
	// How the program is asked for; the path form is filled in per case.
	choices := []string{NativeAgentBinary, "<path>"}

	for _, tc := range cases {
		for _, choice := range choices {
			t.Run(tc.name+"/choice="+choice, func(t *testing.T) {
				f := isolateNative(t)
				binary := writeProgram(t, filepath.Join(f.home, ".gaia", "bin"), NativeAgentBinary)
				// A second copy and the Python agent a refusal must not fall back to.
				writeProgram(t, f.path, NativeAgentBinary)
				writeProgram(t, f.path, PythonAgentBinary)
				record := ""
				if body := tc.body(fileSHA256(t, binary)); body != "" {
					record = writeRecord(t, binary, body)
				}
				asked := choice
				if asked == "<path>" {
					asked = binary
				}

				c := NewCatalog()
				before := *c.Get(FlagshipID)
				err := selectAgent(c, asked)
				got := c.Get(FlagshipID)

				if tc.refused == "" {
					if err != nil {
						t.Fatalf("gaiacpp was refused: %v", err)
					}
					if !got.Native || !samePath(t, got.BinaryPath, binary) {
						t.Fatalf("picked native=%v path=%q, want %q", got.Native, got.BinaryPath, binary)
					}
					return
				}

				var integrity *NativeIntegrityError
				if !errors.As(err, &integrity) {
					t.Fatalf("got %v, want an integrity refusal", err)
				}
				for _, want := range []string{tc.refused, binary, record, "Settings › Agent & model", "/agent gaiapy"} {
					if !strings.Contains(err.Error(), want) {
						t.Errorf("the refusal does not say %q:\n%v", want, err)
					}
				}
				if got.Native || got.BinaryPath != before.BinaryPath {
					t.Errorf("a refused gaiacpp still changed what runs: %+v", *got)
				}
			})
		}
	}
}

// The record belongs to the file the Agent UI downloads. A build under another
// name in the same folder is not that file and is not checked against it.
func TestRecordDoesNotApplyToADifferentlyNamedBuild(t *testing.T) {
	f := isolateNative(t)
	dir := filepath.Join(f.home, ".gaia", "bin")
	other := writeProgram(t, dir, NativeAgentBinary+"-nightly")
	writeRecord(t, other, `{"sha256": "`+strings.Repeat("0", 64)+`"}`)
	if err := VerifyNative(other); err != nil {
		t.Fatalf("a build that is not the downloaded file was checked against its record: %v", err)
	}
}

// The record's file name and the variable naming a build of your own are
// shared with the backend that writes and reads them.
func TestNativeNamesMatchTheBackend(t *testing.T) {
	path := filepath.Join("..", "..", "..", "src", "gaia", "daemon", "sidecars", "fetch.py")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("cannot read the backend's gaiacpp lookup at %s: %v", path, err)
	}
	pinned := []struct{ python, goValue string }{
		{"NATIVE_FLAGSHIP_PATH_ENV", NativePathEnv},
		{"NATIVE_INSTALL_RECORD_NAME", NativeInstallRecord},
		{"NATIVE_FLAGSHIP", NativeAgentBinary},
	}
	for _, p := range pinned {
		m := regexp.MustCompile(`(?m)^` + p.python + `\s*=\s*"([^"]+)"`).FindSubmatch(raw)
		if m == nil {
			t.Errorf("%s is no longer assigned a string in %s; update this test and the Go constant together", p.python, path)
			continue
		}
		if string(m[1]) != p.goValue {
			t.Errorf("%s is %q in %s but the terminal UI uses %q", p.python, m[1], path, p.goValue)
		}
	}
	// The record's checksum field is read by name on both sides.
	if !strings.Contains(string(raw), `data.get("sha256")`) {
		t.Errorf("%s no longer reads the record's \"sha256\" field the way VerifyNative does", path)
	}
}
