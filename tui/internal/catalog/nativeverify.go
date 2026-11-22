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

// These two names are shared with the Agent UI's backend, which downloads
// gaiacpp (src/gaia/daemon/sidecars/fetch.py). A test pins them to that file.
const (
	// NativePathEnv names one particular gaiacpp file to run as the agent.
	NativePathEnv = "GAIA_GAIACPP_PATH"
	// NativeInstallRecord is the file the Agent UI's download writes beside
	// gaiacpp: its version, tag and SHA-256.
	NativeInstallRecord = "gaiacpp.install.json"
)

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

// nativeExecutableName is the exact file name the Agent UI downloads to.
func nativeExecutableName() string {
	if runtime.GOOS == "windows" {
		return NativeAgentBinary + ".exe"
	}
	return NativeAgentBinary
}

// VerifyNative checks the gaiacpp at binary against the download record beside
// it, as verified_native_flagship does on the Python side. No record means no
// check: a build of your own or an installer's copy has none. A record that is
// there must be readable, carry a checksum, and match the file.
func VerifyNative(binary string) error {
	name := filepath.Base(binary)
	want := nativeExecutableName()
	if name != want && !(runtime.GOOS == "windows" && strings.EqualFold(name, want)) {
		return nil
	}
	record := filepath.Join(filepath.Dir(binary), NativeInstallRecord)
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
			"it does not match the gaiacpp that was downloaded (expected SHA-256 %s, actual %s)", expected, actual))
	}
	return nil
}
