package test

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// requireNativeEnv makes a missing nativeAgentEnv a failure. CI sets it, so a
// lane that lost its gaiacpp fails where it used to pass on the mock agent.
const requireNativeEnv = "GAIA_TEST_REQUIRE_GAIACPP"

const ownServerModel = "Qwen3.5-4B-GGUF"

// realNativeAgent is the gaiacpp these tests run. Nothing stands in for it:
// they are about what the real program does.
func realNativeAgent(t *testing.T) string {
	t.Helper()
	path := os.Getenv(nativeAgentEnv)
	if path == "" {
		if os.Getenv(requireNativeEnv) == "1" {
			t.Fatalf("%s is not set, and %s=1 says this run must test a real gaiacpp. "+
				"Build one (cmake --build cpp/build --target gaiacpp) and name it in %s.",
				nativeAgentEnv, requireNativeEnv, nativeAgentEnv)
		}
		t.Skipf("needs a real gaiacpp: set %s to one", nativeAgentEnv)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("%s names %s: %v", nativeAgentEnv, path, err)
	}
	return path
}

// ownServer plays GAIA's own model server: on a port nothing names, behind a
// key, known only from the record GAIA keeps in <home>/.gaia/lemonade.
type ownServer struct {
	key     string
	version string
	srv     *httptest.Server

	mu       sync.Mutex
	models   []string
	refused  []string
	writeTo  string
	writeArg string
}

type chatMessage struct {
	Role    string `json:"role"`
	Content any    `json:"content"`
}

func (s *ownServer) port(t *testing.T) int {
	t.Helper()
	_, port, err := net.SplitHostPort(s.srv.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	n, err := strconv.Atoi(port)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func (s *ownServer) chatModels() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.models...)
}

func (s *ownServer) refusedPaths() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.refused...)
}

// writeOutside makes the model answer "write-outside" with a file_write to path.
func (s *ownServer) writeOutside(path string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.writeTo = path
}

func lemonadeVersion(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "src", "gaia", "version.py"))
	if err != nil {
		t.Fatalf("reading the Lemonade version GAIA targets: %v", err)
	}
	m := regexp.MustCompile(`LEMONADE_VERSION = "([^"]+)"`).FindSubmatch(raw)
	if m == nil {
		t.Fatal("src/gaia/version.py has no LEMONADE_VERSION")
	}
	return string(m[1])
}

func startOwnServer(t *testing.T) *ownServer {
	t.Helper()
	s := &ownServer{key: "own-server-key-" + strconv.FormatInt(time.Now().UnixNano(), 36), version: lemonadeVersion(t)}
	version := s.version
	entry := map[string]any{"id": ownServerModel, "object": "model", "downloaded": true, "suggested": true,
		"labels": []string{"chat", "tool-calling"}, "size": 3.58, "recipe": "llamacpp", "checkpoint": "fake/" + ownServerModel}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+s.key {
			s.mu.Lock()
			s.refused = append(s.refused, r.Method+" "+r.URL.Path)
			s.mu.Unlock()
			http.Error(w, `{"error":{"message":"Invalid or missing API key"}}`, http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		path := strings.TrimPrefix(regexp.MustCompile(`^/(api/)?v\d+`).ReplaceAllString(r.URL.Path, ""), "")
		switch {
		case path == "/health":
			json.NewEncoder(w).Encode(map[string]any{"status": "ok", "version": version, "model_loaded": ownServerModel,
				"all_models_loaded": []map[string]any{{"model_name": ownServerModel, "type": "llm", "device": "gpu",
					"recipe": "llamacpp", "recipe_options": map[string]any{"ctx_size": 65536},
					"checkpoint": "fake/" + ownServerModel, "backend_url": "http://127.0.0.1:0"}}})
		case path == "/models":
			json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": []any{entry}})
		case strings.HasPrefix(path, "/models/"):
			json.NewEncoder(w).Encode(entry)
		case path == "/load":
			io.WriteString(w, `{"status":"success"}`)
		case path == "/chat/completions":
			s.chat(w, r)
		default:
			http.Error(w, `{"error":{"message":"no such endpoint"}}`, http.StatusNotFound)
		}
	}))
	t.Cleanup(s.srv.Close)
	return s
}

func (s *ownServer) chat(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Model    string        `json:"model"`
		Stream   bool          `json:"stream"`
		Messages []chatMessage `json:"messages"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":{"message":"bad JSON"}}`, http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	s.models = append(s.models, body.Model)
	target := s.writeTo
	s.mu.Unlock()

	// The first message that asks for the write starts that turn; a tool result
	// after it means the write was already tried.
	asked, tried := -1, false
	for i, m := range body.Messages {
		text := fmt.Sprint(m.Content)
		if asked < 0 && m.Role == "user" && strings.Contains(text, "write-outside") {
			asked = i
		}
		if asked >= 0 && i > asked && m.Role == "tool" {
			tried = true
		}
	}
	message := map[string]any{"role": "assistant", "content": "Hello from GAIA's own server."}
	finish := "stop"
	if asked >= 0 && tried {
		message["content"] = "The write was tried."
	} else if asked >= 0 {
		args, _ := json.Marshal(map[string]string{"path": target, "content": "from gaiacpp"})
		call := map[string]any{"id": "call_1", "type": "function",
			"function": map[string]any{"name": "file_write", "arguments": string(args)}}
		if body.Stream {
			call["index"] = 0
		}
		message = map[string]any{"role": "assistant", "content": nil, "tool_calls": []any{call}}
		finish = "tool_calls"
	}
	usage := map[string]int{"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}
	if !body.Stream {
		json.NewEncoder(w).Encode(map[string]any{"id": "chatcmpl-own", "object": "chat.completion", "model": body.Model,
			"choices": []any{map[string]any{"index": 0, "finish_reason": finish, "message": message}}, "usage": usage})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Connection", "close")
	for _, choice := range []map[string]any{
		{"index": 0, "delta": message, "finish_reason": nil},
		{"index": 0, "delta": map[string]any{}, "finish_reason": finish},
	} {
		chunk, _ := json.Marshal(map[string]any{"id": "chatcmpl-own", "object": "chat.completion.chunk",
			"model": body.Model, "choices": []any{choice}, "usage": usage})
		fmt.Fprintf(w, "data: %s\n\n", chunk)
	}
	io.WriteString(w, "data: [DONE]\n\n")
}

// say types a chat message and sends it.
func (l *liveTUI) say(message string) {
	l.t.Helper()
	status, body := l.call(http.MethodPost, "/text", map[string]any{"text": message})
	if status != http.StatusOK {
		l.t.Fatalf("POST /text %q: status %d (%v)", message, status, body)
	}
	l.keys("enter")
}

// writeGaiaRecord writes what GAIA's embedded Lemonade and setup leave in the
// home: the running server's port and key, and the model that was chosen.
func writeGaiaRecord(t *testing.T, s *ownServer) {
	t.Helper()
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(home, ".gaia", "lemonade")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	// This process serves it, so the record's pid is alive for as long as it is.
	state, _ := json.Marshal(map[string]any{"pid": os.Getpid(), "port": s.port(t), "api_key": s.key, "version": s.version})
	if err := os.WriteFile(filepath.Join(dir, "state.json"), state, 0o600); err != nil {
		t.Fatal(err)
	}
	config, _ := json.Marshal(map[string]string{"default_model": ownServerModel})
	if err := os.WriteFile(filepath.Join(home, ".gaia", "config.json"), config, 0o600); err != nil {
		t.Fatal(err)
	}
}

// The real gaiacpp as the terminal UI's agent on an installed GAIA: no address
// or key in the environment, the model server known only from GAIA's own
// record. It answers from that server with its key, on the model setup chose,
// and a write outside the folder raises the folder prompt before anything is
// written (#8826, #8858).
func TestNativeAgentUsesGaiasOwnServerAndAsksForAFolder(t *testing.T) {
	native := realNativeAgent(t)
	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	programs := t.TempDir()
	copyProgram(t, native, filepath.Join(programs, catalog.NativeAgentBinary+suffix))

	// Lemonade's standard port, where a gaiacpp that never read GAIA's record looks.
	var strays []string
	var straysMu sync.Mutex
	if standard, err := net.Listen("tcp", "127.0.0.1:13305"); err == nil {
		go http.Serve(standard, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			straysMu.Lock()
			strays = append(strays, r.Method+" "+r.URL.Path+" from "+r.UserAgent())
			straysMu.Unlock()
			http.NotFound(w, r)
		}))
		t.Cleanup(func() { standard.Close() })
	} else {
		t.Logf("port 13305 is taken (%v): requests to it are not counted in this run", err)
	}

	server := startOwnServer(t)
	work := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside.txt")
	server.writeOutside(outside)

	tui := startLiveTUIWith(t, func(root.FlagshipModel) root.FlagshipModel {
		path := programs
		if runtime.GOOS == "windows" {
			path += string(os.PathListSeparator) + filepath.Join(os.Getenv("SystemRoot"), "System32")
		} else {
			path += string(os.PathListSeparator) + "/usr/bin" + string(os.PathListSeparator) + "/bin"
		}
		t.Setenv("PATH", path)
		t.Chdir(work)
		// Nothing names the server: not the harness's stub, not a key.
		for _, name := range []string{"LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_BASE_URL", "GAIA_API_KEY", "GAIA_HOME", "GAIA_CONFIG_FILE"} {
			t.Setenv(name, "")
			os.Unsetenv(name)
		}
		writeGaiaRecord(t, server)

		cat, _, err := catalog.ForFlagship(catalog.NativeAgentBinary)
		if err != nil {
			t.Fatalf("selecting gaiacpp: %v", err)
		}
		agent := cat.Get(catalog.FlagshipID)
		return root.NewFlagshipModel(*agent, false).WithCatalog(cat).
			WithLocalPreflight(preflight.LocalOptions{Binary: agent.BinaryPath}).
			WithPreflight(nil, preflight.Options{ReadyHold: time.Millisecond})
	})

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 46})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})
	tui.waitFor(map[string]any{"contains": "Ask anything"})

	// 1. The first message is answered by GAIA's own server.
	tui.say("Say hello.")
	tui.waitFor(map[string]any{"contains": "Hello from GAIA's own server."})
	models := server.chatModels()
	if len(models) == 0 {
		t.Fatalf("no chat request reached GAIA's own server:\n%s", tui.screen())
	}
	for _, model := range models {
		if model != ownServerModel {
			t.Errorf("gaiacpp asked for %q, not the saved default_model %q", model, ownServerModel)
		}
	}
	if refused := server.refusedPaths(); len(refused) != 0 {
		t.Errorf("requests reached GAIA's server without its key: %v", refused)
	}
	straysMu.Lock()
	for _, stray := range strays {
		// The terminal UI's own probe is Go's client; anything else is the agent.
		if strings.Contains(stray, "Go-http-client") {
			t.Logf("gaia-cli itself asked Lemonade's standard port: %s", stray)
			continue
		}
		t.Errorf("gaiacpp asked Lemonade's standard port, which is not GAIA's server: %s", stray)
	}
	straysMu.Unlock()

	// 2. A write outside the folder asks for the folder first; nothing is
	// written until both the folder and the write are allowed.
	// The turn's closing line, so the next message is typed into an idle chat.
	tui.waitFor(map[string]any{"contains": "tokens"})
	tui.say("write-outside please")
	tui.waitFor(map[string]any{"contains": "Allow access to this location?"})
	if _, err := os.Stat(outside); err == nil {
		t.Fatalf("%s was written before the folder prompt was answered", outside)
	}
	tui.keys("y")
	tui.waitFor(map[string]any{"contains": "Write this file?"})
	tui.keys("y")
	tui.waitFor(map[string]any{"contains": "The write was tried."})
	written, err := os.ReadFile(outside)
	if err != nil || string(written) != "from gaiacpp" {
		t.Fatalf("after both prompts were allowed, %s holds %q (%v)", outside, written, err)
	}
}
