package daemon

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strconv"
	"strings"
)

const (
	// ServiceID is the identity a probed port must answer with before we trust it.
	ServiceID = "gaia-daemon"

	// APIPrefix is the daemon's versioned client-API surface.
	APIPrefix = "/daemon/v1"

	// AuthScheme is the client-token auth scheme; the header is
	// `Authorization: Bearer <token>` on EVERY request.
	AuthScheme = "Bearer"

	// DefaultHost is the loopback address the daemon binds.
	DefaultHost = "127.0.0.1"

	// ReservedPort is reserved repo-wide and must never be used by GAIA.
	// A registry claiming it is treated as untrustworthy.
	ReservedPort = 4001

	// RequiredAPIMajor is the daemon contract MAJOR this client speaks.
	RequiredAPIMajor = 1

	// RequiredAgentsMinor is the MINOR that introduced the /daemon/v1/agents
	// control plane and the /v1/<agent>/* relay. Below it every agents route
	// 404s, so a pre-#2142 daemon must fail loudly rather than be attached to.
	RequiredAgentsMinor = 1
)

// RequiredAPIVersion is the lowest host API this client can use, named in every
// skew message so the user can tell whether their core is too old or too new.
func RequiredAPIVersion() string {
	return fmt.Sprintf("%d.%d", RequiredAPIMajor, RequiredAgentsMinor)
}

// Instance is the daemon's single-instance registry record (~/.gaia/host/instance.json,
// mode 0600).
type Instance struct {
	PID        int     `json:"pid"`
	Port       int     `json:"port"`
	Token      string  `json:"token"`
	Host       string  `json:"host"`
	APIVersion string  `json:"api_version"`
	Service    string  `json:"service"`
	StartedAt  float64 `json:"started_at"`
}

// BaseURL is the daemon's loopback root, e.g. "http://127.0.0.1:51234".
func (i *Instance) BaseURL() string {
	return fmt.Sprintf("http://%s:%d", i.Host, i.Port)
}

// String redacts the client token so an Instance is safe to log or embed in an
// error. Never format an Instance with %#v.
func (i *Instance) String() string {
	return fmt.Sprintf("daemon{pid:%d port:%d api:%s service:%s token:<redacted>}",
		i.PID, i.Port, i.APIVersion, i.Service)
}

// ReadInstance loads and structurally validates instance.json.
//
// It returns *NotRunningError when the file is absent and *StaleError when it is
// present but untrustworthy. A successful read means the record is well-formed —
// NOT that a daemon is alive; that needs Client.verify (pid + status probe).
func ReadInstance() (*Instance, error) {
	path, err := InstancePath()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, &NotRunningError{Path: path}
	}
	if err != nil {
		return nil, &StaleError{Kind: StaleCorrupt, Path: path, Reason: fmt.Sprintf("it cannot be read (%v)", err)}
	}

	var inst Instance
	if err := json.Unmarshal(raw, &inst); err != nil {
		return nil, &StaleError{
			Kind:   StaleCorrupt,
			Path:   path,
			Reason: fmt.Sprintf("it is not valid JSON (%v) — a crash mid-write leaves a truncated file", err),
		}
	}
	if inst.PID <= 0 {
		return nil, &StaleError{Kind: StaleCorrupt, Path: path, Reason: "it records no usable pid"}
	}
	if inst.Port <= 0 || inst.Port > 65535 {
		return nil, &StaleError{Kind: StaleCorrupt, Path: path, Reason: fmt.Sprintf("it records an invalid port (%d)", inst.Port)}
	}
	if inst.Port == ReservedPort {
		return nil, &StaleError{
			Kind:   StaleCorrupt,
			Path:   path,
			Reason: fmt.Sprintf("it records port %d, which is reserved and never used by GAIA", ReservedPort),
		}
	}
	if inst.Token == "" {
		return nil, &StaleError{Kind: StaleCorrupt, Path: path, Reason: "it records no client token, so no request could be authenticated"}
	}
	if inst.Service != "" && inst.Service != ServiceID {
		return nil, &StaleError{
			Kind:   StaleCorrupt,
			Path:   path,
			Reason: fmt.Sprintf("it was written by service %q, not %q", inst.Service, ServiceID),
		}
	}
	if inst.APIVersion == "" {
		return nil, &StaleError{
			Kind:   StaleCorrupt,
			Path:   path,
			Reason: "it records no api_version, so the daemon contract version cannot be verified",
		}
	}
	if inst.Host == "" {
		inst.Host = DefaultHost
	}
	return &inst, nil
}

// parseAPIVersion splits a "MAJOR.MINOR" contract version. A missing MINOR reads
// as 0 (mirrors the Python floor check); a non-numeric MAJOR is a hard error.
func parseAPIVersion(v string) (major, minor int, err error) {
	parts := strings.Split(strings.TrimSpace(v), ".")
	major, err = strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, fmt.Errorf("cannot parse a MAJOR daemon API version from %q", v)
	}
	if len(parts) > 1 {
		if m, cerr := strconv.Atoi(parts[1]); cerr == nil {
			minor = m
		}
	}
	return major, minor, nil
}

// CheckVersion fails loudly on a daemon↔client contract MAJOR skew. An app
// update replaces the client while an old daemon keeps running, so this is the
// expected path after an upgrade, not an edge case.
func (i *Instance) CheckVersion() error {
	major, _, err := parseAPIVersion(i.APIVersion)
	if err != nil {
		return &VersionError{Have: i.APIVersion, Want: RequiredAPIVersion(), Reason: err.Error()}
	}
	if major != RequiredAPIMajor {
		return &VersionError{
			Have: i.APIVersion,
			Want: RequiredAPIVersion(),
			Reason: fmt.Sprintf("this client speaks MAJOR %d and cannot use MAJOR %d",
				RequiredAPIMajor, major),
		}
	}
	return nil
}

// CheckAgentsFloor fails loudly if the running daemon predates the sidecar
// control plane and the /v1/<agent>/* relay (needs v1.1+).
func (i *Instance) CheckAgentsFloor() error {
	if err := i.CheckVersion(); err != nil {
		return err
	}
	_, minor, err := parseAPIVersion(i.APIVersion)
	if err != nil {
		return &VersionError{Have: i.APIVersion, Want: RequiredAPIVersion(), Reason: err.Error()}
	}
	if minor < RequiredAgentsMinor {
		return &VersionError{
			Have:   i.APIVersion,
			Want:   RequiredAPIVersion(),
			Reason: "it predates the sidecar control plane + agent relay, so every agents route would 404",
		}
	}
	return nil
}
