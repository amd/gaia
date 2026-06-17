# Demo/staging container for the GAIA Agent Hub Worker on Railway.
# Runs `wrangler dev` (Miniflare) with simulated R2 persisted to a volume at
# /data — NOT a production Cloudflare deployment (see README.md "Deploying").
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN chmod +x docker-entrypoint.sh

# wrangler is non-interactive in a container; disable telemetry prompt/upload.
ENV WRANGLER_SEND_METRICS=false \
    CI=true

ENTRYPOINT ["./docker-entrypoint.sh"]
