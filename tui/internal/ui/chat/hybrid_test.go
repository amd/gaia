// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

func useHybridConfig(t *testing.T, doc string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(doc), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
}

func TestSwitchingToHybridChecksItsConcreteModels(t *testing.T) {
	base := fakeLemonade(t)
	useHybridConfig(t, `{"hybrid_cheap_model":"`+localModel+`","hybrid_strong_model":"`+gatewayMdl+`"}`)
	id, err := checkRelayedSwitch(context.Background(), &relayClient{}, base, "hybrid", "hybrid")
	if err != nil || id != lemonade.HybridModelID {
		t.Fatalf("hybrid over served models: id=%q err=%v", id, err)
	}

	useHybridConfig(t, `{"hybrid_strong_model":"amd.Not-Served"}`)
	_, err = checkRelayedSwitch(context.Background(), &relayClient{}, base, "hybrid", "hybrid")
	if err == nil || !strings.Contains(err.Error(), "amd.Not-Served") ||
		!strings.Contains(err.Error(), "gaia config set hybrid_strong_model") {
		t.Fatalf("err = %v, want one naming the missing strong model and its key", err)
	}
}

func TestHybridIsLabelledAsCloud(t *testing.T) {
	if got := choiceLabel("hybrid", "hybrid"); !strings.Contains(got, "cloud") {
		t.Fatalf("choiceLabel = %q, want a cloud label", got)
	}
}

const secondLocal = "Qwen3.6-35B-A3B-GGUF"

// fakeLemonadeWith serves ids as downloaded local models.
func fakeLemonadeWith(t *testing.T, ids ...string) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != lemonadeAPI+"/models" {
			http.NotFound(w, r)
			return
		}
		rows := make([]string, len(ids))
		for i, id := range ids {
			rows[i] = fmt.Sprintf(`{"id":%q,"recipe":"llamacpp","downloaded":true}`, id)
		}
		fmt.Fprintf(w, `{"data":[%s]}`, strings.Join(rows, ","))
	}))
	t.Cleanup(srv.Close)
	return srv.URL + lemonadeAPI
}

func TestAnAllLocalHybridIsLabelledLocal(t *testing.T) {
	useHybridConfig(t, `{"hybrid_cheap_model":"`+localModel+`","hybrid_strong_model":"`+secondLocal+`"}`)
	if got := choiceLabel("hybrid", "hybrid"); !strings.HasSuffix(got, "(local)") {
		t.Fatalf("choiceLabel = %q, want a local label", got)
	}
}

func TestAHybridWithACloudMemberIsLabelledCloud(t *testing.T) {
	useHybridConfig(t, `{"hybrid_cheap_model":"`+localModel+`","hybrid_strong_model":"`+gatewayMdl+`"}`)
	if got := choiceLabel("hybrid", "hybrid"); !strings.HasSuffix(got, "(cloud)") {
		t.Fatalf("choiceLabel = %q, want a cloud label", got)
	}
}

func TestABrokenHybridConfigClaimsNoPlace(t *testing.T) {
	useHybridConfig(t, `{"hybrid_cheap_model":"amd.X","hybrid_strong_model":"amd.X"}`)
	if got := choiceLabel("hybrid", "hybrid"); strings.Contains(got, "(") {
		t.Fatalf("choiceLabel = %q, want no local/cloud claim for a broken config", got)
	}
}
