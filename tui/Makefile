.PHONY: build build-release test test-race lint vet cover clean cross-compile mock-agent dev

VERSION ?= dev
COMMIT  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE    ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
PKG      = github.com/amd/gaia/tui/internal/cli
LDFLAGS  = -ldflags="-s -w -X $(PKG).version=$(VERSION) -X $(PKG).commit=$(COMMIT) -X $(PKG).date=$(DATE)"

build:
	go build -o bin/gaia ./cmd/gaia

build-release:
	CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia ./cmd/gaia

test:
	go test ./... -v

# Mirrors the test job in .github/workflows/build_tui.yml, which inlines this
# command rather than calling make (windows-latest has no make). Keep the two in
# sync. The TUI is concurrent throughout (SSE streaming, the loopback control
# API, daemon polling), so -race is where its real bugs are.
test-race:
	go test ./... -v -count=1 -race -timeout=15m

# `lint` needs golangci-lint (see .golangci.yml); `vet` is the subset that needs
# no extra tool, so a contributor without it can still run the old check.
lint:
	golangci-lint run ./...

vet:
	go vet ./...

# Statement coverage, rolled up per package.
#
# A plain `go test -cover` reports internal/ui/root as 0% because its tests live
# in the test/ integration package. -coverpkg=./... attributes coverage back to
# the package that owns the code, so it reports its real number instead of a
# zero that invites deletion.
# `go tool cover` writes to a file rather than piping into tail: a pipeline
# reports the LAST command's status, so `go tool cover ... | tail` would read as
# success even when it failed. make checks the redirected command directly.
cover:
	go test ./... -count=1 -coverpkg=./... -coverprofile=coverage.out
	@go tool cover -func=coverage.out > coverage.txt
	@echo ""
	@tail -1 coverage.txt
	@echo ""
	@awk "$$COV_ROLLUP" coverage.out | sed 's|github.com/amd/gaia/tui/||' | sort -k2

# Per-package rollup. Exported so awk receives it verbatim; `$$` is a literal
# `$` after make expands the define.
define COV_ROLLUP
NR == 1 { next }
{
  if (!($$1 in seen)) seen[$$1] = $$2
  if ($$3 > 0) hit[$$1] = 1
}
END {
  for (blk in seen) {
    split(blk, a, ":")
    n = split(a[1], p, "/")
    pkg = ""
    for (i = 1; i < n; i++) pkg = pkg (i > 1 ? "/" : "") p[i]
    stmts[pkg] += seen[blk]
    if (blk in hit) covered[pkg] += seen[blk]
  }
  for (pkg in stmts) printf "%.1f%%\t%s\n", 100 * covered[pkg] / stmts[pkg], pkg
}
endef
export COV_ROLLUP

mock-agent:
	go build -o bin/mock-agent ./test/mockagent

dev: build mock-agent
	@echo "Built gaia + mock-agent. Run: ./bin/gaia"

clean:
	rm -rf bin/

# Cross-compile all 6 platform targets
cross-compile:
	@mkdir -p bin
	GOOS=linux   GOARCH=amd64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-linux-amd64       ./cmd/gaia
	GOOS=linux   GOARCH=arm64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-linux-arm64       ./cmd/gaia
	GOOS=darwin  GOARCH=amd64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-darwin-amd64      ./cmd/gaia
	GOOS=darwin  GOARCH=arm64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-darwin-arm64      ./cmd/gaia
	GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-windows-amd64.exe ./cmd/gaia
	GOOS=windows GOARCH=arm64 CGO_ENABLED=0 go build $(LDFLAGS) -o bin/gaia-windows-arm64.exe ./cmd/gaia
	@echo "Built 6 binaries:" && ls -lh bin/gaia-*
