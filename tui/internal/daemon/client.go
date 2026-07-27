package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// Timeouts mirror src/gaia/daemon/{instance,client}.py.
const (
	// A live daemon answers loopback in milliseconds; a longer wait only delays
	// reclaiming a dead one.
	defaultProbeTimeout = 1500 * time.Millisecond
	// Default for a general Do() call. Deliberately NOT the probe timeout: a
	// relay call does real work (e.g. GET /v1/<agent>/init checks Lemonade, the
	// model, and connectors) and 1.5s times it out. Callers with a known-slow or
	// streaming call still pass their own client.
	defaultRequestTimeout = 60 * time.Second
	// Daemon start: spawn + bind + import.
	defaultStartTimeout = 30 * time.Second
	// Ensure: a first-run ensure may lazily fetch the sidecar binary before answering.
	defaultEnsureTimeout = 900 * time.Second
	// Cap on captured launcher output quoted back in an error.
	maxLauncherOutput = 4096
)

// Options configures a Client. The zero value is valid and uses the defaults
// above with the real `gaia daemon start` launcher.
type Options struct {
	ProbeTimeout  time.Duration
	StartTimeout  time.Duration
	EnsureTimeout time.Duration

	// StartCommand builds the command that starts the daemon. Defaults to
	// `gaia daemon start`, which is preferred over invoking the module directly
	// because it validates the required extras and produces better errors.
	// Overridden by tests.
	StartCommand func(ctx context.Context) (*exec.Cmd, error)

	// PIDAlive overrides the process-liveness check. Defaults to PIDAlive.
	PIDAlive func(pid int) bool

	// Logf receives progress and best-effort-failure notes. It MUST never be
	// given a raw token; pass Instance values (whose String redacts it).
	Logf func(format string, args ...any)
}

// Client is a thin client for one machine's GAIA daemon.
//
// It is safe for concurrent use: it holds no mutable instance state. Callers pass
// the *Instance they are working with, and Do hands back the (possibly
// refreshed) instance whose token authorized the call — the token rotates on
// every daemon restart, so it is never cached for the process lifetime.
type Client struct {
	opts Options
	// control is used ONLY for the status probe, whose timeout is deliberately
	// tiny — a live daemon answers loopback in milliseconds.
	control *http.Client
	// request is the default for Do(): everything that is not a probe, not an
	// ensure, and not a stream.
	request *http.Client
	// ensure is used for the possibly-very-slow ensure call.
	ensure *http.Client
}

// New builds a Client, filling unset options with their defaults.
func New(opts Options) *Client {
	if opts.ProbeTimeout <= 0 {
		opts.ProbeTimeout = defaultProbeTimeout
	}
	if opts.StartTimeout <= 0 {
		opts.StartTimeout = defaultStartTimeout
	}
	if opts.EnsureTimeout <= 0 {
		opts.EnsureTimeout = defaultEnsureTimeout
	}
	if opts.PIDAlive == nil {
		opts.PIDAlive = PIDAlive
	}
	if opts.StartCommand == nil {
		opts.StartCommand = gaiaDaemonStart
	}
	if opts.Logf == nil {
		opts.Logf = func(string, ...any) {}
	}
	return &Client{
		opts:    opts,
		control: &http.Client{Timeout: opts.ProbeTimeout},
		request: &http.Client{Timeout: defaultRequestTimeout},
		ensure:  &http.Client{Timeout: opts.EnsureTimeout},
	}
}

// Attach returns the running daemon if one is live and contract-compatible.
//
// It returns *NotRunningError / *StaleError when there is nothing to attach to,
// and *VersionError when a live daemon speaks the wrong contract — a version
// skew is never fixed by starting another daemon, so callers must surface it.
func (c *Client) Attach(ctx context.Context) (*Instance, error) {
	inst, err := ReadInstance()
	if err != nil {
		return nil, err
	}
	if err := c.verify(ctx, inst); err != nil {
		return nil, err
	}
	if err := inst.CheckVersion(); err != nil {
		return nil, err
	}
	return inst, nil
}

// verify applies the two-check trust rule: the recorded pid must be alive AND a
// token-authed /daemon/v1/status probe must answer with our service id and the
// recorded pid. After a hard crash the file points at a dead pid or a freed port
// some unrelated process now owns — either check fails.
func (c *Client) verify(ctx context.Context, inst *Instance) error {
	path, _ := InstancePath()
	if !c.opts.PIDAlive(inst.PID) {
		return &StaleError{
			Kind:   StalePIDDead,
			Path:   path,
			Reason: fmt.Sprintf("its pid %d is not running", inst.PID),
		}
	}
	if kind, err := c.probe(ctx, inst); err != nil {
		return &StaleError{Kind: kind, Path: path, Reason: err.Error()}
	}
	return nil
}

type statusBody struct {
	Service string `json:"service"`
	PID     int    `json:"pid"`
}

// probe checks the recorded port with the recorded token. It returns a nil error
// only if the server answers 200 with our service id and the pid from the
// registry. The returned StaleKind says whether a failure looks like OUR daemon
// misbehaving or an unrelated process holding a recycled port.
func (c *Client) probe(ctx context.Context, inst *Instance) (StaleKind, error) {
	ctx, cancel := context.WithTimeout(ctx, c.opts.ProbeTimeout)
	defer cancel()

	req, err := c.newRequest(ctx, inst, http.MethodGet, APIPrefix+"/status", nil, nil)
	if err != nil {
		return StaleUnresponsive, err
	}
	resp, err := c.control.Do(req)
	if err != nil {
		// Refused / timed out: nothing is serving, so this is our own daemon
		// wedged or dying rather than a foreign process.
		return StaleUnresponsive, fmt.Errorf("port %d did not answer a status probe (%v)", inst.Port, err)
	}
	defer drainAndClose(resp)

	if resp.StatusCode != http.StatusOK {
		// 401 and 5xx are shapes OUR daemon produces; any other 4xx is most
		// likely a foreign HTTP server that does not know this route.
		kind := StaleForeign
		if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode >= 500 {
			kind = StaleUnresponsive
		}
		return kind, fmt.Errorf("port %d answered the status probe with HTTP %d", inst.Port, resp.StatusCode)
	}
	var body statusBody
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&body); err != nil {
		return StaleForeign, fmt.Errorf("port %d answered the status probe with an unreadable body (%v)", inst.Port, err)
	}
	if body.Service != ServiceID {
		return StaleForeign, fmt.Errorf("port %d is served by %q, not %q — an unrelated process took the freed port",
			inst.Port, body.Service, ServiceID)
	}
	if body.PID != inst.PID {
		return StaleForeign, fmt.Errorf("port %d is served by pid %d but the registry records pid %d",
			inst.Port, body.PID, inst.PID)
	}
	return StaleCorrupt, nil
}

// StartOrAttach returns the running daemon, starting one only if needed.
// Single-instance is guaranteed by the exclusive start lock.
func (c *Client) StartOrAttach(ctx context.Context) (*Instance, error) {
	inst, err := c.Attach(ctx)
	if err == nil {
		return inst, nil
	}
	// A contract skew is the user's to resolve — starting a second daemon cannot
	// help, and attaching anyway would 404 or misbehave silently.
	if isVersionError(err) {
		return nil, err
	}
	c.opts.Logf("daemon: no attachable instance (%v); starting one", err)

	if _, derr := ensureHostDir(); derr != nil {
		return nil, derr
	}
	lockPath, err := LockPath()
	if err != nil {
		return nil, err
	}
	lock, err := acquireLock(lockPath, c.opts.StartTimeout)
	if err != nil {
		return nil, err
	}
	defer lock.release()

	// Re-check under the lock: a concurrent caller may have just started it.
	inst, err = c.Attach(ctx)
	if err == nil {
		return inst, nil
	}
	if isVersionError(err) {
		return nil, err
	}

	// A recorded pid that is alive AND whose port looks like our own wedged
	// daemon must not be silently replaced. The TUI deliberately does NOT kill it
	// (unlike the Python client, which can verify the cmdline via psutil first):
	// killing a possibly-recycled pid from a UI is worse than telling the user
	// exactly what to run.
	//
	// A FOREIGN answer on that port is different: the probe already proved the
	// record is garbage (recycled pid, port taken by something else), so blocking
	// on it would leave the TUI permanently unable to start a daemon.
	var stale *StaleError
	if errors.As(err, &stale) && stale.Kind == StaleUnresponsive {
		if rec, rerr := ReadInstance(); rerr == nil && c.opts.PIDAlive(rec.PID) {
			return nil, &StartError{Reason: fmt.Sprintf(
				"the recorded daemon pid %d is running but does not answer a status probe on port %d. "+
					"Run `gaia daemon restart` to reclaim it", rec.PID, rec.Port)}
		}
	}

	return c.spawnAndWait(ctx)
}

// gaiaDaemonStart builds the default launcher command.
func gaiaDaemonStart(ctx context.Context) (*exec.Cmd, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return nil, &StartError{Reason: "the `gaia` CLI is not on PATH, so the daemon cannot be launched. " +
			"Install GAIA (`pip install -e .` in the repo, or the released wheel) and retry"}
	}
	return exec.CommandContext(ctx, bin, "daemon", "start"), nil
}

// spawnAndWait launches the daemon and polls until a live instance registers.
//
// Unlike the Python client, the pid of the process we launch is NOT matched
// against instance.json: `gaia daemon start` is a launcher that itself spawns the
// detached daemon, so the registered pid belongs to its grandchild. Trust comes
// from the two-check verify plus the version gate instead.
func (c *Client) spawnAndWait(ctx context.Context) (*Instance, error) {
	startCtx, cancel := context.WithTimeout(ctx, c.opts.StartTimeout)
	defer cancel()

	cmd, err := c.opts.StartCommand(startCtx)
	if err != nil {
		return nil, err
	}
	if cmd == nil {
		return nil, &StartError{
			Reason: "the configured daemon start command returned no command to run",
		}
	}
	var out bytes.Buffer
	if cmd.Stdout == nil {
		cmd.Stdout = &out
	}
	if cmd.Stderr == nil {
		cmd.Stderr = &out
	}
	if err := cmd.Start(); err != nil {
		return nil, &StartError{Reason: fmt.Sprintf("failed to launch `%s`: %v", strings.Join(cmd.Args, " "), err)}
	}

	launcherDone := make(chan error, 1)
	go func() { launcherDone <- cmd.Wait() }()

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	cmdLine := strings.Join(cmd.Args, " ")
	var launcherExit error
	launcherExited := false

	for {
		inst, aerr := c.Attach(ctx)
		if aerr == nil {
			c.opts.Logf("daemon: started %s", inst)
			return inst, nil
		}
		if isVersionError(aerr) {
			return nil, aerr
		}

		// The launcher exits as soon as the daemon has registered, so a clean
		// exit gets one more Attach above before we call it a failure. Reading
		// `out` is only safe once Wait has returned.
		if launcherExited {
			reason := "exited cleanly but no live daemon registered"
			if launcherExit != nil {
				reason = fmt.Sprintf("exited with %v before a daemon registered", launcherExit)
			}
			return nil, &StartError{Reason: fmt.Sprintf("`%s` %s. Last attach error: %v.%s",
				cmdLine, reason, aerr, quoteOutput(out.String()))}
		}

		select {
		case launcherExit = <-launcherDone:
			launcherExited = true
		case <-ticker.C:
		case <-startCtx.Done():
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			<-launcherDone
			return nil, &StartError{Reason: fmt.Sprintf(
				"no daemon became healthy within %s — `%s` may be stuck binding a port or importing.%s",
				c.opts.StartTimeout, cmdLine, quoteOutput(out.String()))}
		}
	}
}

// isVersionError reports whether err is (or wraps) a contract-version failure.
func isVersionError(err error) bool {
	var verr *VersionError
	return errors.As(err, &verr)
}

func quoteOutput(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if len(s) > maxLauncherOutput {
		s = s[:maxLauncherOutput] + "…"
	}
	return " Launcher output:\n" + s
}

// Request is one authenticated call against the daemon.
type Request struct {
	Method string
	// Path is daemon-root-relative, e.g. "/daemon/v1/agents/email/ensure" or
	// "/v1/email/query".
	Path string
	// Body is the raw request body, nil for none. It is retained so the call can
	// be replayed after a token refresh.
	Body []byte
	// Header carries extra request headers; Authorization is always set by Do.
	Header http.Header
	// HTTPClient overrides the client used for this call. Required for a
	// streaming call, and for anything that can outlast defaultRequestTimeout.
	HTTPClient *http.Client
	// Op names the operation for error messages, e.g. "stream the 'email' query".
	Op string
}

// Do sends an authenticated request to the daemon.
//
// The client token rotates on every daemon restart, so a 401 is not a fatal
// auth failure: instance.json is re-read, the fresh instance re-verified, and
// the request replayed exactly once. The instance whose token authorized the
// response is returned so the caller can reuse it (e.g. to cancel a run).
//
// The caller owns resp.Body and must close it.
func (c *Client) Do(ctx context.Context, inst *Instance, r Request) (*http.Response, *Instance, error) {
	resp, err := c.send(ctx, inst, r)
	if err != nil {
		return nil, inst, err
	}
	if resp.StatusCode != http.StatusUnauthorized {
		return resp, inst, nil
	}
	drainAndClose(resp)

	fresh, err := c.refresh(ctx, inst, r.Path)
	if err != nil {
		return nil, inst, err
	}
	c.opts.Logf("daemon: client token rotated; retrying %s %s", r.Method, r.Path)

	resp, err = c.send(ctx, fresh, r)
	if err != nil {
		return nil, fresh, err
	}
	if resp.StatusCode == http.StatusUnauthorized {
		drainAndClose(resp)
		return nil, fresh, &RequestError{
			Op: c.opDescription(r),
			Detail: "the daemon rejected the refreshed client token (HTTP 401). " +
				"Run `gaia daemon restart` to mint a new one",
		}
	}
	return resp, fresh, nil
}

// refresh re-reads instance.json after a 401 and re-verifies it. An unchanged
// token means the 401 was not a rotation, so retrying would just 401 again —
// that surfaces as a loud error instead.
func (c *Client) refresh(ctx context.Context, old *Instance, path string) (*Instance, error) {
	fresh, err := ReadInstance()
	if err != nil {
		return nil, err
	}
	if fresh.Token == old.Token {
		// A 401 on a RELAYED path may be the sidecar refusing its own bearer,
		// which a daemon restart does not fix — pointing the user at
		// `gaia daemon restart` would send them down the wrong path.
		if !strings.HasPrefix(path, APIPrefix) {
			return nil, &RequestError{
				Op: fmt.Sprintf("authenticate the relayed call to %s", path),
				Detail: "the daemon client token is current, so the 401 came from the agent sidecar " +
					"rather than the daemon. Restart the sidecar with " +
					"`gaia daemon stop-agent <id>` and retry",
			}
		}
		return nil, &RequestError{
			Op: "authenticate against the daemon",
			Detail: "it rejected the client token (HTTP 401) but instance.json still records the same token. " +
				"Run `gaia daemon restart` to mint a new one",
		}
	}
	if err := c.verify(ctx, fresh); err != nil {
		return nil, err
	}
	if err := fresh.CheckVersion(); err != nil {
		return nil, err
	}
	return fresh, nil
}

func (c *Client) send(ctx context.Context, inst *Instance, r Request) (*http.Response, error) {
	req, err := c.newRequest(ctx, inst, r.Method, r.Path, r.Body, r.Header)
	if err != nil {
		return nil, err
	}
	httpClient := r.HTTPClient
	if httpClient == nil {
		httpClient = c.request
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, &RequestError{Op: c.opDescription(r), Detail: err.Error()}
	}
	return resp, nil
}

func (c *Client) opDescription(r Request) string {
	if r.Op != "" {
		return r.Op
	}
	return fmt.Sprintf("call %s %s on the daemon", r.Method, r.Path)
}

func (c *Client) newRequest(
	ctx context.Context,
	inst *Instance,
	method, path string,
	body []byte,
	header http.Header,
) (*http.Request, error) {
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, inst.BaseURL()+path, rdr)
	if err != nil {
		return nil, &RequestError{
			Op:     fmt.Sprintf("build a %s request for %s", method, path),
			Detail: err.Error(),
		}
	}
	for k, vs := range header {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	req.Header.Set("Authorization", AuthScheme+" "+inst.Token)
	return req, nil
}

// EnsureAgent starts-or-attaches the daemon and ensures agentID's sidecar is
// running, returning the daemon instance a thin client drives it through.
//
// The ensure response body is deliberately NOT read: it carries the sidecar's
// bearer token, which a thin client must never learn or hold. The TUI presents
// only the daemon client token and the daemon swaps it server-side.
//
// Blocking — daemon start, sidecar spawn, and a possible first-run binary fetch
// all happen here. Call it off the UI event loop.
func (c *Client) EnsureAgent(ctx context.Context, agentID string) (*Instance, error) {
	if agentID == "" {
		return nil, &RequestError{Op: "ensure an agent sidecar", Detail: "no agent id was given"}
	}
	inst, err := c.StartOrAttach(ctx)
	if err != nil {
		return nil, err
	}
	if err := inst.CheckAgentsFloor(); err != nil {
		return nil, err
	}

	resp, inst, err := c.Do(ctx, inst, Request{
		Method:     http.MethodPost,
		Path:       APIPrefix + "/agents/" + agentID + "/ensure",
		Body:       []byte("{}"),
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		HTTPClient: c.ensure,
		Op:         fmt.Sprintf("ensure the '%s' sidecar via the daemon", agentID),
	})
	if err != nil {
		return nil, err
	}
	defer drainAndClose(resp)

	if resp.StatusCode != http.StatusOK {
		return nil, &RequestError{
			Op:     fmt.Sprintf("ensure the '%s' sidecar via the daemon", agentID),
			Detail: ErrorDetail(resp),
		}
	}
	return inst, nil
}

// Logf emits a diagnostic through the client's configured logger. Exported so
// callers that own an agent-contract call (e.g. the SSE transport's cancel) log
// through the same channel. Never pass a raw token.
func (c *Client) Logf(format string, args ...any) {
	c.opts.Logf(format, args...)
}

// ErrorDetail extracts the actionable `detail` from a daemon error response,
// falling back to the bare status line when the body carries none.
func ErrorDetail(resp *http.Response) string {
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if err != nil || len(raw) == 0 {
		return fmt.Sprintf("HTTP %d", resp.StatusCode)
	}
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	if err := json.Unmarshal(raw, &body); err == nil && len(body.Detail) > 0 {
		var s string
		if err := json.Unmarshal(body.Detail, &s); err == nil && s != "" {
			return fmt.Sprintf("HTTP %d: %s", resp.StatusCode, s)
		}
		return fmt.Sprintf("HTTP %d: %s", resp.StatusCode, string(body.Detail))
	}
	return fmt.Sprintf("HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(raw)))
}

// StreamHTTPClient builds an HTTP client for a long-lived SSE response: connect
// fast (a dead daemon should fail quickly), then hold the stream open — a single
// upstream chunk can span a whole agent-loop step, so read pacing is enforced by
// the caller's own idle watchdog rather than a client-wide timeout.
//
// headerTimeout bounds the wait for the response STATUS LINE only, which no
// client-wide timeout can express without also capping the stream. Without it a
// daemon that completes the handshake and never answers hangs the caller
// forever — the reference implementation gets this for free because requests'
// read timeout also covers the header wait.
func StreamHTTPClient(connectTimeout, headerTimeout time.Duration) *http.Client {
	return &http.Client{
		Transport: &http.Transport{
			DialContext:           (&net.Dialer{Timeout: connectTimeout}).DialContext,
			TLSHandshakeTimeout:   connectTimeout,
			ResponseHeaderTimeout: headerTimeout,
			// Loopback only, one stream at a time — no pooling benefit, and a
			// pooled connection would outlive the run.
			DisableKeepAlives: true,
		},
	}
}

func drainAndClose(resp *http.Response) {
	if resp == nil || resp.Body == nil {
		return
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
	resp.Body.Close()
}
