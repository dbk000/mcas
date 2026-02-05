# Multi-stage build for MCAS UI
# Produces a minimal Caddy-based image with built assets

# Stage 1: Build
FROM node:20-alpine AS build
WORKDIR /app

# Install dependencies first (better caching)
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Stage 2: Production image with Caddy
FROM caddy:2-alpine

# Copy built assets
COPY --from=build /app/dist /srv/mcas-ui

# Copy Caddyfile (simplified for container use)
COPY <<EOF /etc/caddy/Caddyfile
:80 {
    root * /srv/mcas-ui
    
    # SPA routing
    try_files {path} {path}/ /index.html
    
    file_server
    
    # Compress responses
    encode gzip
    
    # Security headers
    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
EOF

EXPOSE 80

# Caddy runs as entrypoint by default
