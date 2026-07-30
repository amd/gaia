package client

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"
)

func TestFetchAttentionReturnsResultObject(t *testing.T) {
	f := newFakeRelay(t)
	f.attentionBody = `{"schema_version":"2.8","result":{"kind":"email_attention","items":[{"kind":"meeting_request","sender":"a@example.com","subject":"Sync","why":"proposes a meeting"}],"coverage":{"scanned":5},"generated_at":"x","cache_age_seconds":0.0,"stale":false}}`
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	raw, err := c.FetchAttention(ctx)
	if err != nil {
		t.Fatalf("FetchAttention: %v", err)
	}

	var decoded struct {
		Kind  string `json:"kind"`
		Items []struct {
			Sender string `json:"sender"`
		} `json:"items"`
	}
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("FetchAttention returned invalid JSON: %v (%s)", err, raw)
	}
	if decoded.Kind != "email_attention" {
		t.Errorf("kind = %q, want email_attention", decoded.Kind)
	}
	if len(decoded.Items) != 1 || decoded.Items[0].Sender != "a@example.com" {
		t.Errorf("items = %+v, want one item from a@example.com", decoded.Items)
	}
}

func TestFetchAttentionSurfacesNoMailboxConnected(t *testing.T) {
	f := newFakeRelay(t)
	f.attentionStatus = http.StatusServiceUnavailable
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchAttention(ctx)
	if err == nil {
		t.Fatal("expected an error when no mailbox is connected, got nil")
	}
}

func TestFetchAttentionNeverPresentsTheSidecarBearer(t *testing.T) {
	f := newFakeRelay(t)
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchAttention(ctx); err != nil {
		t.Fatalf("FetchAttention: %v", err)
	}

	for _, auth := range f.auths {
		if auth == "Bearer "+sidecarBearer {
			t.Fatalf("the TUI presented the sidecar bearer token on a relayed request")
		}
	}
}
