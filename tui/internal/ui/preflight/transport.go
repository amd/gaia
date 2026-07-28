package preflight

import (
	"context"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// DaemonInfo is what the readiness screen needs to know about a live daemon.
// It never carries the client token.
type DaemonInfo struct {
	PID        int
	Port       int
	APIVersion string
}

// Response is a buffered answer from the daemon or a relayed sidecar route.
type Response struct {
	Status int
	Body   []byte
}

// Stream is an answer whose body is read incrementally — the provisioning
// progress of POST /v1/<agent>/init. The caller owns Body and must close it.
type Stream struct {
	Status int
	Body   io.ReadCloser
}

// Transport is every call the readiness gate makes. It exists so Check and the
// screen are testable against the real response shapes without a daemon, and so
// this package never learns the daemon's auth rules — those already live in
// internal/daemon and are reused verbatim.
type Transport interface {
	// Attach returns the live daemon if one is running and speaks a contract
	// this build can use. It never starts one.
	Attach(ctx context.Context) (DaemonInfo, error)
	// Start starts-or-attaches the daemon. This is the `f` fix on a daemon row.
	Start(ctx context.Context) (DaemonInfo, error)
	// EnsureAgent asks the daemon to spawn-or-attach the agent's sidecar.
	EnsureAgent(ctx context.Context, agentID string) error
	// Do issues an authenticated request against the daemon's control plane
	// ("/daemon/v1/...") or its agent relay ("/v1/<agent>/...").
	Do(ctx context.Context, method, path string, body []byte) (Response, error)
	// Stream issues a request whose body is consumed as it arrives.
	Stream(ctx context.Context, method, path string, body []byte) (Stream, error)
}

// Connect+header timeouts for the provisioning stream.
//
// The header budget is deliberately huge because the daemon relay BUFFERS every
// non-SSE response (relay.py reads the whole upstream body before answering),
// so NO response header arrives until the model pull has finished. A handshake
// timeout here would abort every real download and blame the wrong thing. The
// caller's context is what actually bounds the pull.
const (
	streamConnectTimeout = 10 * time.Second
	streamHeaderTimeout  = 60 * time.Minute
)

// daemonTransport adapts internal/daemon's client to Transport.
//
// It caches the verified instance between calls because the daemon client is
// stateless by design and hands back the (possibly refreshed) instance whose
// token authorized each call — the token rotates on every daemon restart, so the
// refreshed one must replace the old one rather than be discarded.
type daemonTransport struct {
	c *daemon.Client

	mu   sync.Mutex
	inst *daemon.Instance
}

// NewDaemonTransport wraps a daemon client for the readiness gate.
func NewDaemonTransport(c *daemon.Client) Transport {
	return &daemonTransport{c: c}
}

func (t *daemonTransport) set(inst *daemon.Instance) {
	t.mu.Lock()
	t.inst = inst
	t.mu.Unlock()
}

func (t *daemonTransport) get() *daemon.Instance {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.inst
}

// verify applies BOTH version gates: the MAJOR contract check and the MINOR
// floor that introduced the agent relay. Without the floor check every relayed
// call 404s, which would surface as "the agent is not installed" — a wrong
// answer with a wrong remedy.
func verify(inst *daemon.Instance) error {
	return inst.CheckAgentsFloor()
}

func info(inst *daemon.Instance) DaemonInfo {
	return DaemonInfo{PID: inst.PID, Port: inst.Port, APIVersion: inst.APIVersion}
}

func (t *daemonTransport) Attach(ctx context.Context) (DaemonInfo, error) {
	inst, err := t.c.Attach(ctx)
	if err != nil {
		// Drop the cached instance: a failed attach means the record we were
		// using is no longer trustworthy, and reusing it would send the next
		// call to a port the daemon may have left.
		t.set(nil)
		return DaemonInfo{}, err
	}
	if err := verify(inst); err != nil {
		return DaemonInfo{}, err
	}
	t.set(inst)
	return info(inst), nil
}

func (t *daemonTransport) Start(ctx context.Context) (DaemonInfo, error) {
	inst, err := t.c.StartOrAttach(ctx)
	if err != nil {
		return DaemonInfo{}, err
	}
	if err := verify(inst); err != nil {
		return DaemonInfo{}, err
	}
	t.set(inst)
	return info(inst), nil
}

func (t *daemonTransport) EnsureAgent(ctx context.Context, agentID string) error {
	inst, err := t.c.EnsureAgent(ctx, agentID)
	if err != nil {
		return err
	}
	t.set(inst)
	return nil
}

// instance returns the verified instance, attaching first when this is the
// first call. It never starts a daemon: starting one is a user-visible action
// that belongs to the daemon row's `f` key, not to an incidental probe.
func (t *daemonTransport) instance(ctx context.Context) (*daemon.Instance, error) {
	if inst := t.get(); inst != nil {
		return inst, nil
	}
	if _, err := t.Attach(ctx); err != nil {
		return nil, err
	}
	return t.get(), nil
}

func (t *daemonTransport) do(ctx context.Context, method, path, accept string, body []byte, httpClient *http.Client) (int, io.ReadCloser, error) {
	inst, err := t.instance(ctx)
	if err != nil {
		return 0, nil, err
	}
	header := http.Header{"Accept": []string{accept}}
	if body != nil {
		header.Set("Content-Type", "application/json")
	}
	resp, fresh, err := t.c.Do(ctx, inst, daemon.Request{
		Method:     method,
		Path:       path,
		Body:       body,
		Header:     header,
		HTTPClient: httpClient,
		Op:         "call " + method + " " + path + " through the background service",
	})
	if fresh != nil {
		t.set(fresh)
	}
	if err != nil {
		// Force a fresh attach next time rather than retrying against an
		// instance that just failed to answer.
		t.set(nil)
		return 0, nil, err
	}
	return resp.StatusCode, resp.Body, nil
}

func (t *daemonTransport) Do(ctx context.Context, method, path string, body []byte) (Response, error) {
	status, rc, err := t.do(ctx, method, path, "application/json", body, nil)
	if err != nil {
		return Response{}, err
	}
	defer rc.Close()
	raw, rerr := io.ReadAll(io.LimitReader(rc, 1<<20))
	if rerr != nil {
		return Response{}, &daemon.RequestError{
			Op:     "read the answer to " + method + " " + path,
			Detail: rerr.Error(),
		}
	}
	return Response{Status: status, Body: raw}, nil
}

func (t *daemonTransport) Stream(ctx context.Context, method, path string, body []byte) (Stream, error) {
	// The provisioning verb answers text/plain, not JSON.
	status, rc, err := t.do(ctx, method, path, "text/plain", body,
		daemon.StreamHTTPClient(streamConnectTimeout, streamHeaderTimeout))
	if err != nil {
		return Stream{}, err
	}
	return Stream{Status: status, Body: rc}, nil
}
