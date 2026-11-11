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
// token rotates on every daemon restart. Safe for concurrent use.
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
// behind the user's back); start=true starts one.
func (h *HubClient) connect(ctx context.Context, start bool) (*daemon.Instance, error) {
	// Held across the whole discovery, not just the cache read: a check-then-act
	// would let two concurrent callers each decide no daemon was running and
	// each start one.
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
// `detail` so the caller can branch on the code without re-reading the body.
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
		Op:     "read the agent catalog",
	}, &out)
	if err != nil {
		// This one call has a way through that needs no daemon at all.
		var missing *daemon.RouteMissingError
		if errors.As(err, &missing) {
			missing.Alternative = "`gaia-tui status` still works meanwhile — " +
				"it reads what is installed straight from disk"
		}
		return nil, err
	}
	if code != http.StatusOK {
		return nil, &HubError{Op: "read the agent catalog", Status: code, Detail: detail}
	}
	return &out, nil
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

// stripStatusPrefix drops the "HTTP 503: " that ErrorDetail prepends; callers
// wrap the detail in a message that already names the status.
func stripStatusPrefix(detail string) string {
	if !strings.HasPrefix(detail, "HTTP ") {
		return detail
	}
	if i := strings.Index(detail, ": "); i > 0 {
		return detail[i+2:]
	}
	return detail
}
