package catalog

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// These names are shared with the Agent UI's backend, which downloads
// gaiacpp (src/gaia/daemon/sidecars/fetch.py). A test pins them to that file.
const (
	// NativePathEnv names one particular gaiacpp file to run as the agent.
	NativePathEnv = "GAIA_GAIACPP_PATH"
	// NativeInstallRecord is the file the Agent UI's download writes beside
	// gaiacpp: its version, tag and SHA-256.
	NativeInstallRecord = "gaiacpp.install.json"
)

// NativePathEnvs names, for each native harness, the variable that points at one
// particular build of it (NATIVE_HARNESS_PATH_ENV in fetch.py).
var NativePathEnvs = map[string]string{
	NativeAgentBinary: NativePathEnv,
	"gaiacode":        "GAIA_GAIACODE_PATH",
}

// nativeInstallRecordFor is the download record a native harness's download writes beside it.
func nativeInstallRecordFor(name string) string { return name + ".install.json" }

// NativeIntegrityError is a gaiacpp that must not be run: it no longer matches
// its download record, or that record cannot be used to check it.
type NativeIntegrityError struct {
	Binary string
	Record string
	Reason string
}

func (e *NativeIntegrityError) Error() string {
	return fmt.Sprintf(
		"%s was not run: %s (download record: %s).\n"+
			"Fix:        download gaiacpp again in the Agent UI (Settings › Agent & model), "+
			"or switch to the Python agent with /agent %s (at launch: --agent-program %s)\n"+
			"look:       %s",
		e.Binary, e.Reason, e.Record, PythonAgentChoice, PythonAgentBinary, AgentChoiceDocsURL)
}

// nativeExecutableName is the exact file name the Agent UI downloads harness name to.
func nativeExecutableName(name string) string {
	if runtime.GOOS == "windows" {
		return name + ".exe"
	}
	return name
}

// VerifyNative checks the gaiacpp at binary against the download record beside
// it, as verified_native_flagship does on the Python side. No record means no
// check: a build of your own or an installer's copy has none. A record that is
// there must be readable, carry a checksum, and match the file.
func VerifyNative(binary string) error {
	harness := NativeHarnessOf(binary)
	if harness == "" {
		return nil
	}
	name := filepath.Base(binary)
	want := nativeExecutableName(harness)
	if name != want && !(runtime.GOOS == "windows" && strings.EqualFold(name, want)) {
		return nil
	}
	record := filepath.Join(filepath.Dir(binary), nativeInstallRecordFor(harness))
	raw, err := os.ReadFile(record)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	refuse := func(reason string) error {
		return &NativeIntegrityError{Binary: binary, Record: record, Reason: reason}
	}
	if err != nil {
		return refuse(fmt.Sprintf("its download record cannot be read (%v)", err))
	}
	var data struct {
		SHA256 *string `json:"sha256"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return refuse(fmt.Sprintf("its download record cannot be read (%v)", err))
	}
	if data.SHA256 == nil || strings.TrimSpace(*data.SHA256) == "" {
		return refuse("its download record has no checksum")
	}
	expected := strings.ToLower(strings.TrimSpace(*data.SHA256))

	f, err := os.Open(binary)
	if err != nil {
		return refuse(fmt.Sprintf("it cannot be read to check it (%v)", err))
	}
	defer f.Close()
	sum := sha256.New()
	if _, err := io.Copy(sum, f); err != nil {
		return refuse(fmt.Sprintf("it cannot be read to check it (%v)", err))
	}
	if actual := hex.EncodeToString(sum.Sum(nil)); actual != expected {
		return refuse(fmt.Sprintf(
			"it does not match the %s that was downloaded (expected SHA-256 %s, actual %s)", harness, expected, actual))
	}
	return nil
}
