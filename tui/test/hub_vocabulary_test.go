package test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// A cheap net under a large deletion.
//
// Every non-test source file under internal/ is grepped for the hub's
// vocabulary. A partial deletion — a tab label left in a renderer, an "i
// install" hint in a footer, an error telling the user to run a subcommand that
// now exits 1 — compiles perfectly and only shows up when someone hits that
// path and reads it.
//
// Scoped to internal/ui at first, which is exactly why three `gaia tui list`
// remedies survived review: they live in catalog/, cli/ and client/, where a
// confused user meets them — an unreachable daemon, and the wrong transport for
// --use-claude.
//
// Deliberately source-level rather than screen-level: a rendered-frame check
// can only see the screens a test happens to visit.
func TestNoSourceStillSpeaksHub(t *testing.T) {
	// Banned EVERYWHERE under internal/: subcommands this binary no longer
	// has. Naming one in a remedy sends a confused user to an exit-1, and the
	// paths that do it are the ones a confused user reaches — an unreachable
	// daemon, the wrong transport for --use-claude. Scoping the first version
	// of this to internal/ui is exactly why three of them survived review.
	//
	// `gaia hub install` is deliberately NOT here: that is the Python CLI, it
	// still works, and it installs a non-flagship sidecar agent by id. Where it
	// may appear is pinned by TestNoStringSendsUsersToTheAgentHub.
	deadCommands := []string{"gaia tui list", "gaia tui install", "gaia tui uninstall"}

	// Banned in the SCREENS only: the browser's own vocabulary. "Agent Hub" is
	// checked separately, in string literals only: the download service is
	// real and HubClient still talks to it, but users no longer meet the name.
	browserWords := []string{"Coming Soon", "Available (", "Installed (", "vote", "Vote"}

	root := filepath.Join(repoTUIDir(t), "internal")
	uiRoot := filepath.Join(root, "ui")

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		text := string(raw)
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)

		banned := deadCommands
		if strings.HasPrefix(path, uiRoot) {
			banned = append(append([]string{}, deadCommands...), browserWords...)
		}
		for _, phrase := range banned {
			if strings.Contains(text, phrase) {
				t.Errorf("internal/%s still contains %q — the hub browser is gone, so this "+
					"is either dead text or a path that still sends the user at it", rel, phrase)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", root, err)
	}
}

// The dev build output is `gaia-tui`, never `gaia` — the Python CLI owns that
// name, the two have entirely different subcommands, and a build called `gaia`
// shadows the real CLI on PATH. That reads as the Python CLI having silently
// lost every command it has, which is exactly the confusion this rename fixes.
//
// The Makefile was corrected and tui/README.md was not, because nothing checked
// the docs. This does.
func TestNoDocTellsYouToBuildTheBinaryAsGaia(t *testing.T) {
	repo := filepath.Dir(repoTUIDir(t))
	docs := []string{
		filepath.Join(repo, "tui", "README.md"),
		filepath.Join(repo, "docs", "guides", "tui.mdx"),
		filepath.Join(repo, "docs", "reference", "cli.mdx"),
		filepath.Join(repo, "tui", "Makefile"),
	}
	// `bin/gaia` NOT followed by another name-forming character: bin/gaia-tui,
	// bin/gaia-agent and bin/gaia-linux-amd64 are all fine.
	bare := regexp.MustCompile(`bin/gaia(?:[^-\w]|$)`)
	// The desktop app's shim wraps the Python CLI, however the doc punctuates it.
	pythonShim := regexp.MustCompile(`\.local/bin/gaia(?:[^-\w]|$)`)

	for _, path := range docs {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("reading %s: %v", path, err)
		}
		for i, line := range strings.Split(string(raw), "\n") {
			// The Python CLI genuinely does live at <venv>/bin/gaia.
			if strings.Contains(line, "venv/bin/gaia") || pythonShim.MatchString(line) {
				continue
			}
			if bare.MatchString(line) {
				t.Errorf("%s:%d builds or names the TUI as `gaia`, which shadows the "+
					"Python CLI: %q", filepath.Base(path), i+1, strings.TrimSpace(line))
			}
		}
	}
}

// GAIA ships one agent, so "the Agent Hub" is no longer something a user is
// sent to (#5172). Only string literals are checked — comments may still name
// the download service the installer talks to.
//
// The flagship installs with `gaia init`, so `gaia hub install` may only be
// spelled inside catalog.InstallCommand, which maps every other agent id to it,
// and catalog.UpdateCommand, since `gaia init` never upgrades an installed agent.
// A literal elsewhere is how a remedy ends up telling a user to run
// `gaia hub install gaia`.
func TestNoStringSendsUsersToTheAgentHub(t *testing.T) {
	root := filepath.Join(repoTUIDir(t), "internal")
	fset := token.NewFileSet()

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		file, parseErr := parser.ParseFile(fset, path, nil, 0)
		if parseErr != nil {
			return parseErr
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)

		for _, decl := range file.Decls {
			fn, isFunc := decl.(*ast.FuncDecl)
			inInstallCommand := isFunc && fn.Recv == nil && rel == "catalog/catalog.go" &&
				(fn.Name.Name == "InstallCommand" || fn.Name.Name == "UpdateCommand")
			ast.Inspect(decl, func(n ast.Node) bool {
				lit, ok := n.(*ast.BasicLit)
				if !ok || lit.Kind != token.STRING {
					return true
				}
				text, unquoteErr := strconv.Unquote(lit.Value)
				if unquoteErr != nil {
					text = lit.Value
				}
				line := fset.Position(lit.Pos()).Line
				if strings.Contains(text, "Agent Hub") {
					t.Errorf("internal/%s:%d tells the user about the Agent Hub: %q", rel, line, text)
				}
				if strings.Contains(text, "gaia hub install") && !inInstallCommand {
					t.Errorf("internal/%s:%d spells `gaia hub install` itself; use "+
						"catalog.InstallCommand (or UpdateCommand) so the flagship gets `gaia init`: %q",
						rel, line, text)
				}
				return true
			})
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", root, err)
	}
}
