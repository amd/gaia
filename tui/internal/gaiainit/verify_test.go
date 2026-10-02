package gaiainit

import (
	"bufio"
	"slices"
	"strings"
	"testing"
)

func TestVerifyArgsAskForTheLoadCheck(t *testing.T) {
	got := VerifyArgs(false, "Qwen3-4B-Instruct-GGUF")
	want := []string{"init", "--check", "--profile", Profile, "--load", "--json",
		"--chat-model", "Qwen3-4B-Instruct-GGUF"}
	if !slices.Equal(got, want) {
		t.Errorf("args = %v, want %v", got, want)
	}
	// A Claude- or cloud-backed session loads no local chat model at all.
	if got := VerifyArgs(true, "Qwen3-4B-Instruct-GGUF"); slices.Contains(got, "--chat-model") {
		t.Errorf("claude mode still names a chat model: %v", got)
	}
}

func TestParseStatusSkipsLogLinesBeforeTheResult(t *testing.T) {
	out := "[2026-09-29 00:04:56] | ERROR | gaia.llm.lemonade_client.embeddings | x\n" +
		`{"ready": false, "stage": "load", "reasons": ["r"], "models": [` +
		`{"id": "e", "role": "embedding", "size_gb": 0.311, "loaded": false, "error": "boom"}]}` + "\n"
	st, err := parseStatus(out)
	if err != nil {
		t.Fatal(err)
	}
	e, ok := st.Embedder()
	if st.Stage != StageLoad || !ok || e.Loaded || e.Error != "boom" || *e.SizeGB != 0.311 {
		t.Errorf("status = %+v", st)
	}
}

func TestParseStatusTreatsAnUnnamedNotReadyAsSetup(t *testing.T) {
	st, err := parseStatus(`{"ready": false, "stage": null, "reasons": ["not installed"], "models": []}`)
	if err != nil || st.Stage != StageSetup {
		t.Errorf("status = %+v err = %v, want the setup stage", st, err)
	}
}

func TestParseStatusRefusesOutputWithNoResult(t *testing.T) {
	if _, err := parseStatus("usage: gaia init [-h]\ngaia: error: unrecognized arguments: --load\n"); err == nil {
		t.Error("output with no JSON result was accepted")
	}
}

func TestDescribeKeepsLogRecordsOffTheScreen(t *testing.T) {
	for _, line := range []string{
		"[2026-09-28 23:28:11] | INFO | gaia.llm.lemonade_embedded._download | lemonade_embedded.py:486 | Downloading",
		"[2026-09-28 23:28:40] | WARNING | gaia.llm.lemonade_client._post_load_with_transient_retry | x",
		"┌──────────────┐",
		"   Ensuring 1 model(s) are downloaded:",
		"",
	} {
		if p, ok := Describe(line); ok {
			t.Errorf("Describe(%q) = %+v; that line means nothing to a person", line, p)
		}
	}
}

func TestDescribeNamesEachPhase(t *testing.T) {
	cases := []struct {
		line  string
		phase Phase
		text  string
	}{
		{"Step 1/5: Starting Lemonade Server...", PhaseServer, "Starting the local model server"},
		{"   Downloading Lemonade Server v2026.40.0...", PhaseServer, "Downloading the local model server"},
		{"Step 2/5: Downloading models for 'gaia' profile...", PhaseModels, "Downloading the models"},
		{"   Downloading: user.embeddinggemma-300m-GGUF", PhaseModels, "Downloading user.embeddinggemma-300m-GGUF"},
		{"Step 5/5: Verifying setup...", PhaseFinish, "Checking that the models load"},
	}
	for _, c := range cases {
		p, ok := Describe(c.line)
		if !ok || p.Phase != c.phase || p.Text != c.text {
			t.Errorf("Describe(%q) = %+v, %v; want phase %v %q", c.line, p, ok, c.phase, c.text)
		}
	}
	if p, ok := Describe("   [========------------] 42% (2.1 MB/5.1 MB)"); !ok || p.Percent != 42 {
		t.Errorf("progress bar = %+v, %v; want 42%%", p, ok)
	}
	if p, ok := Describe("   ❌ user.embeddinggemma-300m-GGUF - Request failed"); !ok || !p.Failed {
		t.Errorf("error line = %+v, %v; want Failed", p, ok)
	}
}

// A progress bar redraws with \r and no newline; split on \n alone, the whole
// download arrives as one line when it is already over.
func TestTheScannerDeliversEachProgressRedraw(t *testing.T) {
	in := "\r   [==--] 10% (1 MB/5 MB)\r   [====] 100% (5 MB/5 MB)\n   ✓ Installed\n"
	sc := bufio.NewScanner(strings.NewReader(in))
	sc.Split(scanLinesOrReturns)
	var got []string
	for sc.Scan() {
		if l := strings.TrimSpace(sc.Text()); l != "" {
			got = append(got, l)
		}
	}
	want := []string{"[==--] 10% (1 MB/5 MB)", "[====] 100% (5 MB/5 MB)", "✓ Installed"}
	if !slices.Equal(got, want) {
		t.Errorf("lines = %q, want %q", got, want)
	}
}
