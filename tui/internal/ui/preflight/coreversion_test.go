package preflight

import (
	"context"
	"errors"
	"testing"
)

func TestCheckRecordsTheVerifiedDaemonsGaiaVersion(t *testing.T) {
	f := newFake()
	f.info.GaiaVersion = "0.25.2"
	rep := Check(context.Background(), f, EmailConfig())
	if rep.CoreVersion != "0.25.2" {
		t.Fatalf("CoreVersion = %q, want the attached daemon's 0.25.2", rep.CoreVersion)
	}
}

func TestCheckLeavesCoreVersionEmptyWhenTheDaemonIsUnreachable(t *testing.T) {
	f := newFake()
	f.info.GaiaVersion = "9.9.9"
	f.attachErr = errors.New("connection refused")
	f.startErr = errors.New("cannot start")
	rep := Check(context.Background(), f, EmailConfig())
	if rep.CoreVersion != "" {
		t.Fatalf("CoreVersion = %q from a daemon that never verified", rep.CoreVersion)
	}
}
