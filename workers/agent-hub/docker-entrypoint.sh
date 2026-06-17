#!/bin/sh
# Entrypoint for the Railway demo container: materialize the PUBLISH_TOKENS
# secret into .dev.vars (how `wrangler dev` reads local secrets), then run
# wrangler dev bound to 0.0.0.0:$PORT with simulated R2 persisted to /data.
set -eu

if [ -z "${PUBLISH_TOKENS:-}" ]; then
  cat >&2 <<'EOF'
ERROR: PUBLISH_TOKENS environment variable is not set.

The Agent Hub worker requires a publisher token map and refuses to start
without one — there is no allow-all fallback.

Fix: set the PUBLISH_TOKENS service variable (Railway: service -> Variables)
to a JSON map of token -> publisher, e.g.:

  {"<token>":{"publisher":"AMD","authors":["AMD"]}}

See workers/agent-hub/README.md ("Deploying on Railway (demo)") for details.
EOF
  exit 1
fi

# .dev.vars is dotenv-style: KEY=<single-line value>. PUBLISH_TOKENS is a JSON
# map on one line, so it can be written verbatim.
printf 'PUBLISH_TOKENS=%s\n' "$PUBLISH_TOKENS" > .dev.vars

exec npx wrangler dev \
  --ip 0.0.0.0 \
  --port "${PORT:-8787}" \
  --persist-to /data/wrangler-state
