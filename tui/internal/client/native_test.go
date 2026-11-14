package client

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

func nativeAgent(t *testing.T) catalog.Agent {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	// The flags come from the catalog's own entry, so a change to what the
	// catalog launches gaiacpp with is what the tests below check.
	seed := catalog.NewCatalog().Get(catalog.FlagshipID)
	return catalog.Agent{
		ID: catalog.FlagshipID, BinaryPath: self,
		BinaryArgs: []string{catalog.NativeStdioFlag}, DevArgs: seed.DevArgs,
		CanonicalEvents: true, Native: true,
	}
}

// gaiacppParser is where gaiacpp declares the flags it accepts.
const gaiacppParser = "cpp/agents/gaia/main.cpp"

var gaiacppFlagRe = regexp.MustCompile(`\ba == "(--?[A-Za-z][A-Za-z0-9-]*)"`)

// gaiacppFlags reads the flags gaiacpp accepts, and the ones it names only to
// refuse, out of its argument parser. Read from the source rather than listed
// here, so a flag renamed in C++ fails the tests that pass it.
func gaiacppFlags(t *testing.T) (accepted, refused map[string]bool) {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	var src []byte
	for {
		src, err = os.ReadFile(filepath.Join(dir, filepath.FromSlash(gaiacppParser)))
		if err == nil {
			break
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatalf("%s was not found above the test's folder, so the flags the terminal UI passes "+
				"to gaiacpp cannot be checked against the ones it accepts. Run the tests from the GAIA "+
				"checkout, or update gaiacppParser if the parser moved", gaiacppParser)
		}
		dir = parent
	}
	text := string(src)
	start := strings.Index(text, "Cli parseArgs(")
	end := strings.Index(text, `usageError("unknown argument`)
	if start < 0 || end < start {
		t.Fatalf("%s no longer has a parseArgs that ends in an unknown-argument error; "+
			"update this reader to match the parser", gaiacppParser)
	}
	accepted, refused = map[string]bool{}, map[string]bool{}
	lines := strings.Split(text[start:end], "\n")
	for i, line := range lines {
		into := accepted
		// A branch whose body is a usage error names the flag to reject it.
		if i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "usageError(") {
			into = refused
		}
		for _, m := range gaiacppFlagRe.FindAllStringSubmatch(line, -1) {
			into[m[1]] = true
		}
	}
	if len(accepted) < 5 {
		t.Fatalf("only %d flags were read out of %s; the reader no longer matches the parser", len(accepted), gaiacppParser)
	}
	return accepted, refused
}

// assertGaiacppAccepts fails for any argument in args that gaiacpp would exit on.
func assertGaiacppAccepts(t *testing.T, args []string) {
	t.Helper()
	accepted, _ := gaiacppFlags(t)
	for i, a := range args {
		if i > 0 && args[i-1] == "--model" {
			continue
		}
		if !accepted[a] {
			t.Errorf("argv %v carries %q, which %s does not accept", args, a, gaiacppParser)
		}
	}
}

// Every flag the terminal UI can put on gaiacpp's command line, at launch or on
// a respawn, has to be one its parser accepts: it exits on anything else.
func TestEveryFlagPassedToGaiacppIsOneItsParserAccepts(t *testing.T) {
	accepted, refused := gaiacppFlags(t)
	for _, flag := range append([]string{catalog.NativeStdioFlag, FullAccessFlag, AcceptEditsFlag, "--model"},
		nativeAgent(t).DevArgs...) {
		if !accepted[flag] {
			t.Errorf("the terminal UI passes %s, which %s does not accept", flag, gaiacppParser)
		}
	}
	// The reason Claude is refused before launch: gaiacpp names these to exit.
	for _, flag := range []string{UseClaudeFlag, ClaudeModelFlag} {
		if !refused[flag] || accepted[flag] {
			t.Errorf("%s is no longer refused by %s; if gaiacpp gained a Claude provider, "+
				"the terminal UI's refusal (NativeClaudeUnavailable) is out of date", flag, gaiacppParser)
		}
	}

	for _, opts := range []ForAgentOptions{
		{},
		{Dev: true},
		{FullAccess: true},
		{Model: "fireworks.deepseek-v4p1-flash"},
		{Dev: true, FullAccess: true, Model: "Gemma-4-E4B-it-GGUF"},
	} {
		c, err := ForAgent(nativeAgent(t), opts)
		if err != nil {
			t.Fatal(err)
		}
		sub := c.(*SubprocessClient)
		assertGaiacppAccepts(t, sub.args)
		for _, acceptEdits := range []bool{false, true} {
			sub.acceptEdits = acceptEdits
			for _, fullAccess := range []bool{false, true} {
				spawn := sub.spawnArgs(fullAccess)
				assertGaiacppAccepts(t, spawn)
				if len(spawn) == 0 || spawn[0] != catalog.NativeStdioFlag {
					t.Errorf("a respawn dropped %s: %v", catalog.NativeStdioFlag, spawn)
				}
			}
		}
		sub.RecordModelSwitch("amd.gpt-4.1", false)
		sub.RecordModelSwitch("claude-sonnet-5", true)
		assertGaiacppAccepts(t, sub.spawnArgs(true))
		c.Close()
	}
}

func TestTheNativeAgentIsLaunchedOnStdioWithTheFlagsItAccepts(t *testing.T) {
	c, err := ForAgent(nativeAgent(t), ForAgentOptions{
		Dev: true, FullAccess: true, Model: "fireworks.deepseek-v4p1-flash",
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	sub := c.(*SubprocessClient)
	if !sub.Native() || !sub.canonical {
		t.Fatalf("native=%v canonical=%v; gaiacpp speaks the canonical dialect", sub.Native(), sub.canonical)
	}
	want := []string{"--stdio", "--dev", "--full-access", "--model", "fireworks.deepseek-v4p1-flash"}
	if !reflect.DeepEqual(sub.args, want) {
		t.Errorf("argv = %v, want %v", sub.args, want)
	}
}

// The Python agent's launch is unchanged: no --stdio, and Claude still reaches it.
func TestThePythonAgentKeepsItsLaunchAndItsClaudeFlags(t *testing.T) {
	agent := nativeAgent(t)
	agent.Native, agent.BinaryArgs = false, nil
	c, err := ForAgent(agent, ForAgentOptions{UseClaude: true, ClaudeModel: "claude-sonnet-5"})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	sub := c.(*SubprocessClient)
	if sub.Native() {
		t.Error("the Python agent was built as the native one")
	}
	want := []string{UseClaudeFlag, ClaudeModelFlag, "claude-sonnet-5"}
	if !reflect.DeepEqual(sub.args, want) {
		t.Errorf("argv = %v, want %v", sub.args, want)
	}
}

// gaiacpp exits 2 on --use-claude. Asked for, it is refused in words before
// anything is spawned, and no client is handed back to crash on every message.
func TestClaudeIsRefusedForTheNativeAgentBeforeAnythingIsSpawned(t *testing.T) {
	for _, opts := range []ForAgentOptions{
		{UseClaude: true},
		{UseClaude: true, ClaudeModel: "claude-sonnet-5"},
	} {
		c, err := ForAgent(nativeAgent(t), opts)
		if err == nil {
			c.Close()
			t.Fatalf("Claude was accepted for the native agent: %+v", opts)
		}
		if err.Error() != NativeClaudeUnavailable {
			t.Errorf("the refusal is not the shared message: %q", err)
		}
	}
	for _, want := range []string{"Claude", "native", "gaiacpp", "/provider", "Lemonade"} {
		if !strings.Contains(NativeClaudeUnavailable, want) {
			t.Errorf("the message does not say %q: %s", want, NativeClaudeUnavailable)
		}
	}
	// The remedy is something to do in the app, never a command to type.
	for _, banned := range []string{"gaia init", "pip ", "`"} {
		if strings.Contains(NativeClaudeUnavailable, banned) {
			t.Errorf("the message tells the user to run a command (%q): %s", banned, NativeClaudeUnavailable)
		}
	}
}

// A Claude switch recorded on a native client would put --use-claude in the
// next child's argv, and every respawn would then exit at once.
func TestANativeClientNeverRecordsAClaudeSwitch(t *testing.T) {
	c := NewNativeSubprocessClient("unused", []string{"--stdio", "--model", "Gemma-4-E4B-it-GGUF"}, false)
	if c.RecordModelSwitch("claude-sonnet-5", true) {
		t.Fatal("a Claude switch was recorded for gaiacpp")
	}
	assertGaiacppAccepts(t, c.spawnArgs(false))
	if c.ModelAtLaunch() != "Gemma-4-E4B-it-GGUF" || c.ClaudeAtLaunch() {
		t.Errorf("a refused switch changed the launch: %v", c.args)
	}

	if !c.RecordModelSwitch("fireworks.deepseek-v4p1-flash", false) || c.ModelAtLaunch() != "fireworks.deepseek-v4p1-flash" {
		t.Errorf("a Lemonade switch was not recorded: %v", c.args)
	}
	if !c.SetModelBeforeStart("amd.gpt-4.1") {
		t.Error("a model picked before the first message was refused")
	}
	assertGaiacppAccepts(t, c.spawnArgs(true))
}

// gaiacpp reads only its environment, so the address and key GAIA resolved have
// to be handed to it; the Python agent finds both by itself. What it is handed
// is the terminal UI's decision and nothing else: an inherited address or key
// must never reach it beside, or in place of, the chosen one.
func TestTheNativeAgentGetsExactlyTheModelServerTheTUIChose(t *testing.T) {
	inherited := []string{
		"PATH=/bin",
		"LEMONADE_API_KEY=inherited-key",
		"lemonade_base_url=http://stale.example:1/api/v1",
		"GAIA_BASE_URL=http://other.example:2/v1",
		"GAIA_API_KEY=other-key",
		"GAIA_MODEL_ID=kept",
	}
	envOf := func(t *testing.T) []string {
		t.Helper()
		env, err := nativeEnv(context.Background(), inherited)
		if err != nil {
			t.Fatal(err)
		}
		return env
	}

	t.Run("an address and key the user set", func(t *testing.T) {
		isolateSubprocessConnection(t)
		t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9123")
		t.Setenv("LEMONADE_API_KEY", "user-key")
		want := []string{"PATH=/bin", "GAIA_MODEL_ID=kept",
			"LEMONADE_BASE_URL=http://127.0.0.1:9123/api/v1", "LEMONADE_API_KEY=user-key"}
		if got := envOf(t); !reflect.DeepEqual(got, want) {
			t.Errorf("env = %v\nwant  %v", got, want)
		}
	})

	t.Run("GAIA's own server, with its key", func(t *testing.T) {
		dir := isolateSubprocessConnection(t)
		writeEmbeddedState(t, dir, 49152, "embedded-key")
		want := []string{"PATH=/bin", "GAIA_MODEL_ID=kept",
			"LEMONADE_BASE_URL=http://localhost:49152/api/v1", "LEMONADE_API_KEY=embedded-key"}
		if got := envOf(t); !reflect.DeepEqual(got, want) {
			t.Errorf("env = %v\nwant  %v", got, want)
		}
	})

	// The user chose GAIA's own server over the one LEMONADE_BASE_URL names, so
	// the key exported for that other server is not GAIA's to send anywhere.
	t.Run("a key that belongs to a server GAIA no longer uses", func(t *testing.T) {
		dir := isolateSubprocessConnection(t)
		config := `{"lemonade_mode": "gaia", "lemonade_env_url_ignored": "http://elsewhere.example:8000/api/v1"}`
		if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte(config), 0o600); err != nil {
			t.Fatal(err)
		}
		t.Setenv("LEMONADE_BASE_URL", "http://elsewhere.example:8000")
		t.Setenv("LEMONADE_API_KEY", "elsewhere-key")
		want := []string{"PATH=/bin", "GAIA_MODEL_ID=kept", "LEMONADE_BASE_URL=http://localhost:13305/api/v1"}
		got := envOf(t)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("env = %v\nwant  %v", got, want)
		}
		for _, kv := range got {
			if strings.Contains(kv, "key") {
				t.Errorf("a key reached the child for a server it was not issued for: %s", kv)
			}
		}
	})

	t.Run("nothing chosen", func(t *testing.T) {
		isolateSubprocessConnection(t)
		want := []string{"PATH=/bin", "GAIA_MODEL_ID=kept", "LEMONADE_BASE_URL=http://localhost:13305/api/v1"}
		if got := envOf(t); !reflect.DeepEqual(got, want) {
			t.Errorf("env = %v\nwant  %v", got, want)
		}
	})
}

func writeEmbeddedState(t *testing.T, gaiaHome string, port int, key string) {
	t.Helper()
	state := filepath.Join(gaiaHome, "lemonade")
	if err := os.MkdirAll(state, 0o755); err != nil {
		t.Fatal(err)
	}
	body := fmt.Sprintf(`{"pid": %d, "port": %d, "api_key": %q}`, os.Getpid(), port, key)
	if err := os.WriteFile(filepath.Join(state, "state.json"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestLeftoverProcessMessagesNameTheProgramThatRan(t *testing.T) {
	c := NewNativeSubprocessClient(filepath.Join("some", "dir", "gaiacpp.exe"), nil, false)
	if c.processName() != "gaiacpp" {
		t.Errorf("process name = %q", c.processName())
	}
	notice := respawnNotice(false, fmt.Errorf("access denied"), c.processName())
	if !strings.Contains(notice, "leftover gaiacpp process") || strings.Contains(notice, "gaia-agent") {
		t.Errorf("the notice names the wrong program: %s", notice)
	}
}
