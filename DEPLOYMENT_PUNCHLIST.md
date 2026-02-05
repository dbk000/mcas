# MCAS UI Deployment Punchlist

Deploy the MCAS frontend to **mcasui.com** via Cloudflare Pages.

---

## Prerequisites

- [ ] **Cloudflare account** with mcasui.com domain configured
- [ ] **Backend API** deployed and accessible from the internet (for the UI to connect)
- [ ] **API Key** generated (64-char hex) for authentication

---

## Phase 1: Prepare the Codebase

### 1.1 Add Cloudflare Pages SPA Routing

Create `Frontend/public/_redirects` file for SPA routing:

```
/*    /index.html   200
```

**Why**: Cloudflare Pages needs this to handle client-side routing (React Router). Without it, direct navigation to `/opportunities` or page refresh will 404.

- [ ] Create `Frontend/public/_redirects` with the rule above

### 1.2 (Optional) Add Security Headers

Create `Frontend/public/_headers` file:

```
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
```

- [ ] Create `Frontend/public/_headers` (optional but recommended)

### 1.3 Verify Build Works Locally

```bash
cd Frontend
npm install
npm run build
npm run preview  # Test at http://localhost:4173
```

- [ ] Confirm `npm run build` succeeds
- [ ] Confirm `dist/` folder contains `index.html` and assets

---

## Phase 2: Cloudflare Pages Setup

### 2.1 Connect Repository (Git-based Deploy)

**Option A: GitHub/GitLab Integration**

1. Go to Cloudflare Dashboard → Pages → Create a project
2. Connect your GitHub/GitLab account
3. Select the MCAS repository
4. Configure build settings:
   - **Framework preset**: None (or Vite)
   - **Build command**: `cd Frontend && npm install && npm run build`
   - **Build output directory**: `Frontend/dist`
   - **Root directory**: `/` (leave default)

- [ ] Connect repo to Cloudflare Pages
- [ ] Set build command: `cd Frontend && npm install && npm run build`
- [ ] Set output directory: `Frontend/dist`

**Option B: Direct Upload (Manual)**

```bash
cd Frontend
npm run build
# Upload dist/ folder via Cloudflare Pages dashboard
```

- [ ] Upload `dist/` to Cloudflare Pages (if not using Git integration)

### 2.2 Configure Custom Domain

1. Cloudflare Pages → Your project → Custom domains
2. Add `mcasui.com` (and optionally `www.mcasui.com`)
3. Cloudflare will auto-configure DNS if domain is on Cloudflare

- [ ] Add `mcasui.com` as custom domain
- [ ] Verify DNS propagation (may take a few minutes)
- [ ] Confirm HTTPS certificate is active

---

## Phase 3: Backend API Setup

The frontend connects to the backend via runtime configuration (Settings page). The backend must be:

### 3.1 Deploy Backend API

Options:
- **VPS/Cloud VM**: Docker Compose on DigitalOcean, Linode, AWS EC2, etc.
- **Cloudflare Tunnel**: Expose local/private backend via Cloudflare
- **Managed services**: Railway, Fly.io, Render, etc.

**Minimum requirements**:
- PostgreSQL 16
- Redis 7
- Python 3.12 (FastAPI)
- Public HTTPS endpoint

- [ ] Backend deployed with public HTTPS URL (e.g., `https://api.mcasui.com` or `https://mcas-api.yourserver.com`)

### 3.2 Configure CORS (if API on different domain)

If the API is on a different domain (e.g., `api.mcasui.com`), add CORS headers to the backend.

**In `Backend/api/app/main.py`**, add after app creation:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://mcasui.com", "https://www.mcasui.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] Add CORS middleware to FastAPI (if API on separate domain)
- [ ] Rebuild and redeploy backend

### 3.3 Generate Production API Key

```bash
# Generate a secure 64-character hex key
openssl rand -hex 32
```

Update backend `.env`:
```
API_KEY=<your-64-char-hex-key>
```

- [ ] Generate production API key
- [ ] Update backend `.env` with new key
- [ ] Restart backend services

---

## Phase 4: Post-Deployment Configuration

### 4.1 Configure UI to Connect to Backend

1. Open `https://mcasui.com` in browser
2. You'll be redirected to Settings page (no config yet)
3. Enter:
   - **Base URL**: `https://api.mcasui.com` (your backend URL)
   - **API Key**: Your 64-char hex key
4. Click "Test Connection"
5. If successful, click "Save Configuration"

- [ ] Open mcasui.com and navigate to Settings
- [ ] Enter backend URL and API key
- [ ] Test and save connection

### 4.2 Verify Core Functionality

- [ ] Dashboard loads with market data
- [ ] Opportunities page shows signals
- [ ] Control Plane can start/stop scans
- [ ] Paper Trading page shows positions

---

## Phase 5: Production Hardening (Optional)

### 5.1 Environment Variables in Cloudflare Pages

If you want to set a default API URL at build time (optional):

1. Cloudflare Pages → Settings → Environment variables
2. Add: `VITE_API_BASE_URL` = `https://api.mcasui.com`
3. Modify `ApiContext.jsx` to use `import.meta.env.VITE_API_BASE_URL` as default

- [ ] (Optional) Set `VITE_API_BASE_URL` in Cloudflare Pages env vars
- [ ] (Optional) Update `ApiContext.jsx` to use env var as default

### 5.2 Cloudflare Web Analytics

1. Cloudflare Pages → Your project → Analytics
2. Enable Web Analytics (free, privacy-focused)

- [ ] (Optional) Enable Cloudflare Web Analytics

### 5.3 Cloudflare Access (Auth Protection)

If you want to restrict access to the UI:

1. Cloudflare Zero Trust → Access → Applications
2. Add `mcasui.com` with email/SSO authentication

- [ ] (Optional) Configure Cloudflare Access for auth

---

## Quick Reference

| Item | Value |
|------|-------|
| UI Domain | `https://mcasui.com` |
| Build Command | `cd Frontend && npm install && npm run build` |
| Output Dir | `Frontend/dist` |
| SPA Routing | `_redirects` file with `/* /index.html 200` |
| API Config | Runtime via Settings page (localStorage) |

---

## Troubleshooting

**404 on page refresh**: Missing `_redirects` file or incorrect SPA routing config.

**CORS errors**: Backend needs `CORSMiddleware` with `allow_origins` including your UI domain.

**Connection failed**: Check backend URL has `https://`, no trailing slash, and API key matches backend `.env`.

**Build fails**: Run `npm install && npm run build` locally first to debug.

---

## Rollback Plan

Cloudflare Pages keeps deployment history. To rollback:
1. Cloudflare Pages → Deployments
2. Find previous working deployment
3. Click "..." → "Rollback to this deployment"
