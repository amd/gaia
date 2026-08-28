package test

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// A daemon that predates a route this build needs answers a bare 404, and that
// was reported as "could not read the Agent Hub catalog (HTTP 404): Not Found".
// The hub was fine; the local daemon was old. Ten minutes went into checking a
// remote service over a `gaia daemon restart`.
func TestAMissingDaemonRouteIsNotBlamedOnTheAgentHub(t *testing.T) {
	fake := newFakeDaemon(t)
	fake.omitRoute(daemon.APIPrefix + "/catalog")

	_, err := fake.client().Catalog(context.Background(), false, false, false)
	if err == nil {
		t.Fatal("a daemon with no catalog route returned a catalog")
	}
	text := err.Error()

	// The diagnosis: it is the background service, and it is out of date.
	if !strings.Contains(text, "background service") {
		t.Errorf("the error does not name the daemon as the cause:\n%s", text)
	}
	if !strings.Contains(text, "older than this GAIA") {
		t.Errorf("the error does not diagnose version skew:\n%s", text)
	}
	// The remedy has to be able to fix the cause. The missing route comes from
	// the installed core, so a restart relaunches the same one — a core this old
	// may not even have a `daemon` subcommand to restart with.
	if !strings.Contains(text, "pip install --upgrade amd-gaia") {
		t.Errorf("the error gives no way to fix it:\n%s", text)
	}
	if !strings.Contains(text, "brings the same one back") {
		t.Errorf("the error does not say a restart cannot fix this:\n%s", text)
	}
	// Where to look next.
	if !strings.Contains(text, "daemon log") {
		t.Errorf("the error does not say where to look next:\n%s", text)
	}
	// The way through that needs no daemon at all.
	if !strings.Contains(text, "--installed") {
		t.Errorf("the error does not mention the offline way through:\n%s", text)
	}
	// And it must NOT send the user to the Agent Hub.
	if strings.Contains(text, "could not read the Agent Hub catalog (HTTP 404)") {
		t.Errorf("the failure is still attributed to the Agent Hub:\n%s", text)
	}
}

// It is typed, so a caller can branch on it rather than matching on words.
func TestAMissingDaemonRouteIsATypedError(t *testing.T) {
	fake := newFakeDaemon(t)
	fake.omitRoute(daemon.APIPrefix + "/catalog")

	_, err := fake.client().Catalog(context.Background(), false, false, false)
	var missing *daemon.RouteMissingError
	if !errors.As(err, &missing) {
		t.Fatalf("error is %T, want *daemon.RouteMissingError: %v", err, err)
	}
	if missing.Path != daemon.APIPrefix+"/catalog" {
		t.Errorf("Path = %q, want the route that is absent", missing.Path)
	}
}

// The same skew on any other daemon route gets the same diagnosis, without that
// call site having to write it. This is the rollout case: a TUI that updates
// before the daemon restarts hits every route at once.
func TestEveryDaemonRouteReportsSkewTheSameWay(t *testing.T) {
	cases := []struct {
		name  string
		route string
		call  func(*fakeDaemon) error
	}{
		{"agents", daemon.APIPrefix + "/agents", func(f *fakeDaemon) error {
			_, err := f.client().Agents(context.Background(), false)
			return err
		}},
		{"uninstall", daemon.APIPrefix + "/agents/email", func(f *fakeDaemon) error {
			return f.client().Uninstall(context.Background(), "email")
		}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fake := newFakeDaemon(t)
			fake.omitRoute(tc.route)

			err := tc.call(fake)
			if err == nil {
				t.Fatal("a missing route succeeded")
			}
			var missing *daemon.RouteMissingError
			if !errors.As(err, &missing) {
				t.Fatalf("error is %T, want *daemon.RouteMissingError: %v", err, err)
			}
			if !strings.Contains(err.Error(), "gaia daemon restart") {
				t.Errorf("no remedy for the %s route:\n%v", tc.name, err)
			}
		})
	}
}

// A 404 the route ITSELF sends is a refusal about the thing asked for, not
// version skew, and must keep its own explanation. Installing an unknown agent
// is the live example: the daemon names the agent and what is missing.
func TestARouteThatAnswersKeepsItsOwnExplanation(t *testing.T) {
	const detail = "'nope' is not installed (no .installed at ~/.gaia/agents/nope)"
	fake := newFakeDaemon(t)
	fake.refuseRoute(daemon.APIPrefix+"/agents/nope", http.StatusNotFound, detail)

	err := fake.client().Uninstall(context.Background(), "nope")
	if err == nil {
		t.Fatal("a refused uninstall reported success")
	}

	var missing *daemon.RouteMissingError
	if errors.As(err, &missing) {
		t.Fatalf("a route's own refusal was diagnosed as version skew: %v", err)
	}
	if !strings.Contains(err.Error(), detail) {
		t.Errorf("the route's own explanation was replaced:\n%v", err)
	}
	// It still has to say who answered — that is the attribution half of this fix.
	if !strings.Contains(err.Error(), "background service") {
		t.Errorf("the error does not name who answered:\n%v", err)
	}
}

// The healthy path must be untouched.
func TestAPresentRouteStillWorks(t *testing.T) {
	fake := newFakeDaemon(t)

	cat, err := fake.client().Catalog(context.Background(), false, false, false)
	if err != nil {
		t.Fatalf("Catalog against a healthy daemon: %v", err)
	}
	if len(cat.Agents) == 0 {
		t.Error("the catalog came back empty")
	}
}
