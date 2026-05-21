.PHONY: build build-release test lint clean cross-compile mock-agent dev

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

lint:
	go vet ./...

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
