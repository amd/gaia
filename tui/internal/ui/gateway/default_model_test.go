package gateway

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// serveCatalog stands in for Lemonade's /models, which ListModels reads.
func serveCatalog(t *testing.T, ids ...string) *Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/models") {
			http.NotFound(w, r)
			return
		}
		var data []map[string]any
		for _, id := range ids {
			data = append(data, map[string]any{"id": id, "recipe": cloudRecipe})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"data": data})
	}))
	t.Cleanup(srv.Close)
	return NewClientAt(srv.URL)
}

func TestDeepSeekV41FlashIsTheDefaultGatewayModel(t *testing.T) {
	c := serveCatalog(t,
		Provider+".Claude-Opus-5",
		Provider+".Gemma-4-31B",
		Provider+".DeepSeek-V4-Flash",
		Provider+".DeepSeek-V4.1-Flash",
	)
	models, err := c.ListModels()
	if err != nil {
		t.Fatal(err)
	}
	if got := models[0].ID; got != Provider+".DeepSeek-V4.1-Flash" {
		t.Fatalf("first-ranked model = %q, want %s.DeepSeek-V4.1-Flash", got, Provider)
	}
}

func TestOlderDeepSeekFlashIsNotRecommended(t *testing.T) {
	if (Model{ID: Provider + ".DeepSeek-V4-Flash"}).Recommended() {
		t.Error("DeepSeek-V4-Flash must not match the V4.1 hint")
	}
}

func TestGemmaLeadsWhenDeepSeekIsAbsent(t *testing.T) {
	c := serveCatalog(t, Provider+".Claude-Opus-5", Provider+".Gemma-4-31B")
	models, err := c.ListModels()
	if err != nil {
		t.Fatal(err)
	}
	if got := models[0].ID; got != Provider+".Gemma-4-31B" {
		t.Fatalf("first-ranked model = %q, want %s.Gemma-4-31B", got, Provider)
	}
}
