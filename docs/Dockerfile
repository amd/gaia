# GAIA SDK Documentation - Auth Proxy
#
# Simple Node.js proxy server for access-code protected docs.
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci --omit=dev

# Copy server
COPY server.js ./

# Set environment
ENV NODE_ENV=production

EXPOSE 3000

CMD ["node", "server.js"]
