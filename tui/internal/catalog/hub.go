package catalog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Security tiers published in the hub index. Anything that is not
// TierVerified runs unaudited third-party code, so installing it needs the
// user's explicit opt-in (the daemon answers 403 until it gets one).
const (
	TierVerified     = "verified"
	TierCommunity    = "community"
	TierExperimental = "experimental"
)

// Install status values reported by GET /daemon/v1/agents/{id}/install-status.
const (
	InstallQueued    = "queued"
	InstallRunning   = "running"
	InstallCompleted = "completed"
	InstallFailed    = "failed"
)

// HubEntry is one agent row of GET /daemon/v1/catalog: the hub index entry
// (schema workers/agent-hub/schemas/index.schema.json) merged with the local
// install state the daemon reads from the ~/.gaia/agents/*/.installed
// sentinels.
type HubEntry struct {
	ID                string   `json:"id"`
	Name              string   `json:"name"`
	Description       string   `json:"description"`
	Category          string   `json:"category"`
	Icon              string   `json:"icon"`
	Tags              []string `json:"tags"`
	Author            string   `json:"author"`
	SecurityTier      string   `json:"security_tier"`
	Permissions       []string `json:"permissions"`
	DownloadSizeBytes int64    `json:"download_size_bytes"`
	LatestVersion     string   `json:"latest_version"`
	Deprecated        bool     `json:"deprecated"`

	// Merged by the daemon (gaia.daemon.sidecars.install._merge_entry).
	Installed        bool   `json:"installed"`
	InstalledVersion string `json:"installed_version"`
	UpdateAvailable  bool   `json:"update_available"`
	Supervised       bool   `json:"supervised"`
}

// RequiresTrust reports whether installing this agent needs the user's explicit
// opt-in. It mirrors gaia.hub.catalog._requires_trust — the daemon's catalog
// route does not compute the flag, so the client derives it from the same rule
// rather than assuming "no flag" means "safe".
func (e HubEntry) RequiresTrust() bool { return e.SecurityTier != TierVerified }

// HubCatalog is the body of GET /daemon/v1/catalog.
type HubCatalog struct {
	Agents      []HubEntry `json:"agents"`
	Offline     bool       `json:"offline"`
	Source      string     `json:"source"`
	GeneratedAt string     `json:"generated_at"`
	HubURL      string     `json:"hub_url"`
	// UnsupervisedFiltered lists ids the daemon hid because it has no sidecar
	// spec for them: it could install them but never start them. Reported, not
	// dropped, so the UI can say WHY an expected agent is missing.
	UnsupervisedFiltered []string `json:"unsupervised_filtered"`
}

// InstallProgress is the body of GET /daemon/v1/agents/{id}/install-status.
type InstallProgress struct {
	AgentID string  `json:"agent_id"`
	Status  string  `json:"status"`
	Phase   string  `json:"phase"`
	Percent float64 `json:"percent"`
	Version string  `json:"version"`
	Error   string  `json:"error"`
}

// Terminal reports whether polling can stop.
func (p InstallProgress) Terminal() bool {
	return p.Status == InstallCompleted || p.Status == InstallFailed
}

// AgentRuntime is one entry of GET /daemon/v1/agents — a registered sidecar and
// whether it is currently running. Never carries a token.
type AgentRuntime struct {
	AgentID      string `json:"agent_id"`
	State        string `json:"state"`
	Mode         string `json:"mode"`
	PID          int    `json:"pid"`
	Port         int    `json:"port"`
	AgentVersion string `json:"agent_version"`
}

// TrustRequiredError is the daemon's 403 refusal to install a non-verified
// agent without an explicit opt-in.
//
// It is a distinct type on purpose: the ONLY correct response is to show the
// user what they would be trusting and retry after they say yes. A caller that
// re-sends with trusted=true on its own has defeated the gate, so this error
// never carries the retry itself.
type TrustRequiredError struct {
	AgentID string
	Detail  string
}

func (e *TrustRequiredError) Error() string {
	return fmt.Sprintf("installing '%s' needs an explicit trust opt-in: %s", e.AgentID, e.Detail)
}

// HubError is any other actionable failure from a hub route.
type HubError struct {
	Op     string
	Status int
	Detail string
}

func (e *HubError) Error() string {
	if e.Status == 0 {
		return fmt.Sprintf("could not %s: %s", e.Op, e.Detail)
	}
	// The background service is always the responder, even when it is relaying
	// the Agent Hub. Naming it keeps a local failure from reading as a remote
	// one — the status code is exactly where that distinction matters.
	return fmt.Sprintf("could not %s: the GAIA background service answered HTTP %d: %s",
		e.Op, e.Status, e.Detail)
}

// HubClient drives the daemon's Agent Hub control plane.
//
// It holds the daemon Instance whose token last authorized a call, because that
// token rotates on every daemon restart. Safe for concurrent use — the hub
// screen polls install-status from a Bubble Tea command while the user keeps
// typing.
type HubClient struct {
	dc *daemon.Client

	// connectMu serializes discovery so exactly one caller can start a daemon.
	connectMu sync.Mutex

	mu   sync.Mutex
	inst *daemon.Instance
}

// NewHubClient builds a client over the real daemon discovery path. logf may be
// nil; it must never be handed a raw token.
func NewHubClient(logf func(string, ...any)) *HubClient {
	return &HubClient{dc: daemon.New(daemon.Options{Logf: logf})}
}

// NewHubClientWith wraps an already-configured daemon client (tests).
func NewHubClientWith(dc *daemon.Client) *HubClient { return &HubClient{dc: dc} }

// connect resolves a daemon instance. start=false only attaches to a daemon
// that is already running (the hub's background catalog load must not spawn one
// behind the user's back); start=true starts one, which is what an explicit
// install/uninstall/list asks for.
func (h *HubClient) connect(ctx context.Context, start bool) (*daemon.Instance, error) {
	// Held across the whole discovery, not just the cache read: a check-then-act
	// would let two concurrent callers (the install poll and a refresh) each
	// decide no daemon was running and each start one.
	h.connectMu.Lock()
	defer h.connectMu.Unlock()

	h.mu.Lock()
	inst := h.inst
	h.mu.Unlock()
	if inst != nil {
		return inst, nil
	}
	var err error
	if start {
		inst, err = h.dc.StartOrAttach(ctx)
	} else {
		inst, err = h.dc.Attach(ctx)
	}
	if err != nil {
		return nil, err
	}
	if err := inst.CheckAgentsFloor(); err != nil {
		return nil, err
	}
	h.remember(inst)
	return inst, nil
}

func (h *HubClient) remember(inst *daemon.Instance) {
	h.mu.Lock()
	h.inst = inst
	h.mu.Unlock()
}

// forget drops the cached instance so the next call re-discovers the daemon.
// Used when a call fails at the transport level — the daemon may have restarted
// on a new port.
func (h *HubClient) forget() {
	h.mu.Lock()
	h.inst = nil
	h.mu.Unlock()
}

// call performs one authenticated request. out (when non-nil) receives the
// decoded 2xx body. A non-2xx status is returned with the daemon's actionable
// `detail` so the caller can branch on the code (403 → trust gate) without
// re-reading the body.
func (h *HubClient) call(
	ctx context.Context,
	start bool,
	req daemon.Request,
	out any,
) (int, string, error) {
	inst, err := h.connect(ctx, start)
	if err != nil {
		return 0, "", err
	}
	resp, fresh, err := h.dc.Do(ctx, inst, req)
	if err != nil {
		h.forget()
		return 0, "", err
	}
	h.remember(fresh)
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		resp.Body.Close()
	}()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		// Callers wrap the detail in their own message that already names the
		// status, so keep ErrorDetail's "HTTP 503: " prefix out of it.
		detail := stripStatusPrefix(daemon.ErrorDetail(resp))
		// Diagnosed once, here, so no call site has to tell version skew from a
		// refusal — and so none of them can get the attribution wrong.
		if daemon.IsRouteMissing(req.Path, resp.StatusCode, detail) {
			return resp.StatusCode, detail, &daemon.RouteMissingError{Op: req.Op, Path: req.Path}
		}
		return resp.StatusCode, detail, nil
	}
	if out != nil {
		if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<22)).Decode(out); err != nil {
			return resp.StatusCode, "", &HubError{
				Op:     req.Op,
				Detail: fmt.Sprintf("the daemon answered %s with a body this client cannot read: %v", req.Path, err),
			}
		}
	}
	return resp.StatusCode, "", nil
}

// Catalog fetches the hub catalog merged with local install state.
//
// start=false attaches to a running daemon only. installedOnly answers from the
// local sentinels without any network call, so it works offline.
func (h *HubClient) Catalog(ctx context.Context, start, installedOnly, refresh bool) (*HubCatalog, error) {
	path := daemon.APIPrefix + "/catalog"
	sep := "?"
	if installedOnly {
		path += sep + "installed_only=true"
		sep = "&"
	}
	if refresh {
		path += sep + "refresh=true"
	}

	var out HubCatalog
	code, detail, err := h.call(ctx, start, daemon.Request{
		Method: http.MethodGet,
		Path:   path,
		Op:     "read the Agent Hub catalog",
	}, &out)
	if err != nil {
		// This one call has a way through that needs no daemon at all.
		var missing *daemon.RouteMissingError
		if errors.As(err, &missing) {
			missing.Alternative = "`gaia tui status` still works meanwhile — " +
				"it reads what is installed straight from disk"
		}
		return nil, err
	}
	if code != http.StatusOK {
		return nil, &HubError{Op: "read the Agent Hub catalog", Status: code, Detail: detail}
	}
	return &out, nil
}

// Install queues an install of agentID.
//
// trusted is the user's explicit opt-in to run a non-verified agent's code. It
// MUST come from a real confirmation the user answered — never from a retry
// this client decided to make. A refusal comes back as *TrustRequiredError.
func (h *HubClient) Install(ctx context.Context, agentID, version string, trusted bool) error {
	payload := map[string]any{"trusted": trusted}
	if version != "" {
		payload["version"] = version
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return &HubError{Op: fmt.Sprintf("build the install request for '%s'", agentID), Detail: err.Error()}
	}

	code, detail, err := h.call(ctx, true, daemon.Request{
		Method: http.MethodPost,
		Path:   daemon.APIPrefix + "/agents/" + agentID + "/install",
		Body:   body,
		Header: http.Header{"Content-Type": []string{"application/json"}},
		Op:     fmt.Sprintf("install '%s' via the daemon", agentID),
	}, nil)
	if err != nil {
		return err
	}
	switch code {
	case http.StatusAccepted, http.StatusOK:
		return nil
	case http.StatusForbidden:
		return &TrustRequiredError{AgentID: agentID, Detail: detail}
	default:
		return &HubError{Op: fmt.Sprintf("install '%s'", agentID), Status: code, Detail: detail}
	}
}

// InstallStatus polls the progress of an install.
func (h *HubClient) InstallStatus(ctx context.Context, agentID string) (*InstallProgress, error) {
	var out InstallProgress
	code, detail, err := h.call(ctx, false, daemon.Request{
		Method: http.MethodGet,
		Path:   daemon.APIPrefix + "/agents/" + agentID + "/install-status",
		Op:     fmt.Sprintf("read the install status of '%s'", agentID),
	}, &out)
	if err != nil {
		return nil, err
	}
	if code != http.StatusOK {
		return nil, &HubError{
			Op:     fmt.Sprintf("read the install status of '%s'", agentID),
			Status: code,
			Detail: detail,
		}
	}
	if out.AgentID == "" {
		out.AgentID = agentID
	}
	return &out, nil
}

// Uninstall stops the sidecar and removes its install directory.
func (h *HubClient) Uninstall(ctx context.Context, agentID string) error {
	code, detail, err := h.call(ctx, true, daemon.Request{
		Method: http.MethodDelete,
		Path:   daemon.APIPrefix + "/agents/" + agentID,
		Op:     fmt.Sprintf("uninstall '%s' via the daemon", agentID),
	}, nil)
	if err != nil {
		return err
	}
	if code != http.StatusOK {
		return &HubError{Op: fmt.Sprintf("uninstall '%s'", agentID), Status: code, Detail: detail}
	}
	return nil
}

// Agents lists the sidecars the daemon knows about and whether they run.
func (h *HubClient) Agents(ctx context.Context, start bool) ([]AgentRuntime, error) {
	var out struct {
		Agents []AgentRuntime `json:"agents"`
	}
	code, detail, err := h.call(ctx, start, daemon.Request{
		Method: http.MethodGet,
		Path:   daemon.APIPrefix + "/agents",
		Op:     "list the daemon's agent sidecars",
	}, &out)
	if err != nil {
		return nil, err
	}
	if code != http.StatusOK {
		return nil, &HubError{Op: "list the daemon's agent sidecars", Status: code, Detail: detail}
	}
	return out.Agents, nil
}

// Instance returns the daemon instance this client last authenticated against,
// or nil if it has not connected yet. The returned value redacts its token when
// formatted.
func (h *HubClient) Instance() *daemon.Instance {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.inst
}

// stripStatusPrefix drops the "HTTP 403: " that ErrorDetail prepends. The trust
// gate quotes the daemon's sentence to the user; a status line in the middle of
// a security prompt is noise, and the code is already implied by the prompt.
func stripStatusPrefix(detail string) string {
	if !strings.HasPrefix(detail, "HTTP ") {
		return detail
	}
	if i := strings.Index(detail, ": "); i > 0 {
		return detail[i+2:]
	}
	return detail
}

// WithoutCLIHint drops the daemon's trailing "From the CLI: `…`." sentence.
//
// The daemon appends it for callers that have no prompt of their own. A client
// that DOES show its own next step must not print both — two different
// commands four lines apart is worse than one.
func WithoutCLIHint(detail string) string {
	if i := strings.Index(detail, "From the CLI:"); i >= 0 {
		return strings.TrimSpace(detail[:i])
	}
	return detail
}

// FormatSize renders a download size the way a person reads it. 0 means the
// catalog entry carried no size, which is reported as "unknown" rather than
// "0 B" — a confident wrong number is worse than an honest gap.
func FormatSize(bytes int64) string {
	switch {
	case bytes <= 0:
		return "unknown size"
	case bytes < 1024:
		return fmt.Sprintf("%d B", bytes)
	case bytes < 1024*1024:
		return fmt.Sprintf("%.0f KB", float64(bytes)/1024)
	case bytes < 1024*1024*1024:
		return fmt.Sprintf("%.1f MB", float64(bytes)/(1024*1024))
	default:
		return fmt.Sprintf("%.1f GB", float64(bytes)/(1024*1024*1024))
	}
}
