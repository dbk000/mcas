# MCAS Web Dashboard (v1)

A React-based web interface for the Market Cognition at Scale (MCAS) trading system.

## v1 Changes

**Critical Fixes Applied:**
1. **Auto-connect on startup** - App automatically tests connection when config exists (no more manual "Test Connection" required)
2. **Unauthorized redirect** - Automatically redirects to Settings with toast message on 401
3. **baseUrl normalization** - Strips trailing slashes and whitespace to prevent routing issues
4. **ProtectedRoute requires apiKey** - Both baseUrl and apiKey must be set to access protected pages
5. **Loading state optimization** - Polling hooks only show loading spinner on first fetch, not subsequent polls

**UX Improvements:**
- Re-test connection button in sidebar
- QuickScanButton shows toast feedback and navigates to new run
- Better connection status display (shows "Connecting..." state)

**Deployment:**
- Added Caddyfile with SPA routing (`try_files` fallback to index.html)
- Added Dockerfile for containerized deployment

## Features

- **Dashboard**: System status, top signals, latest scan results, paper trading snapshot
- **Opportunities**: Browse and filter trading opportunities with trade plan copy
- **Markets**: Explore markets with live candlestick charts and squeeze indicators
- **Runs**: View scan, backtest, and paper trading run history
- **Control Plane**: Create new scans, backtests, and manage paper trading sessions
- **Paper Trading**: Monitor positions, trades, and performance analytics
- **Settings**: Configure API connection with health check

## Tech Stack

- React 18 + Vite
- Tailwind CSS with custom terminal theme
- React Router for navigation
- lightweight-charts for candlestick visualization
- lucide-react icons
- date-fns for formatting

## Design

Industrial terminal aesthetic with:
- Dark theme (terminal grays)
- Amber accent color
- JetBrains Mono for data/numbers
- High-density information display
- Polling-based real-time updates

## Quick Start

```bash
# Install dependencies
npm install

# Development server (connects to localhost:8000 by default)
npm run dev

# Production build
npm run build

# Preview production build
npm run preview
```

## Configuration

The app stores configuration in localStorage:
- `mcas_api_url`: Backend API base URL (e.g., `http://localhost:8000`)
- `mcas_api_key`: API key for authentication

Configure these in the Settings page after launching.

## API Requirements

The dashboard requires the MCAS backend API running with the following endpoints:

### Core Endpoints
- `GET /v1/health` - Health check
- `GET /v1/auth/ping` - Auth verification
- `GET /v1/markets` - Available markets
- `GET /v1/timeframes` - Available timeframes
- `GET /v1/ohlcv` - OHLCV candlestick data
- `GET /v1/snapshot/minute` - Live price snapshot
- `GET /v1/squeeze/state` - Squeeze indicator state
- `GET /v1/btc/squeeze-status` - BTC correlation status

### Opportunities
- `GET /v1/opportunities` - List opportunities
- `GET /v1/opportunities/:id` - Single opportunity
- `GET /v1/signals/top` - Top scoring signals

### Runs
- `GET /v1/runs` - List runs
- `GET /v1/runs/:id` - Run details
- `GET /v1/runs/:id/trades` - Run trades
- `GET /v1/runs/:id/events` - Run events
- `GET /v1/runs/latest` - Latest run by type

### Control Plane
- `POST /v1/control/runs/scan` - Create scan
- `POST /v1/control/runs/backtest` - Create backtest
- `POST /v1/control/runs/paper/start` - Start paper trading
- `POST /v1/control/runs/paper/stop` - Stop paper trading
- `POST /v1/control/runs/:id/cancel` - Cancel run
- `GET /v1/control/queue` - Queue status
- `GET /v1/control/paper/status` - Paper status

### Trading
- `GET /v1/trading/account` - Account status
- `GET /v1/trading/positions` - Positions
- `GET /v1/trading/trades` - Trade history
- `GET /v1/trading/performance` - Performance analytics
- `POST /v1/trading/positions/:id/close` - Close position

## Project Structure

```
mcas-ui/
├── index.html
├── package.json
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
├── public/
│   └── favicon.svg
└── src/
    ├── main.jsx          # Entry point
    ├── App.jsx           # Routes
    ├── index.css         # Global styles
    ├── context/
    │   ├── ApiContext.jsx    # API client & connection state
    │   └── ToastContext.jsx  # Notifications
    ├── hooks/
    │   └── useData.js    # Data fetching hooks
    ├── utils/
    │   └── formatters.js # Number/date formatting
    ├── components/
    │   ├── Layout.jsx    # Sidebar & header
    │   └── ui.jsx        # Reusable components
    └── pages/
        ├── Dashboard.jsx
        ├── Opportunities.jsx
        ├── OpportunityDetail.jsx
        ├── Markets.jsx
        ├── MarketDetail.jsx
        ├── Runs.jsx
        ├── RunDetail.jsx
        ├── ControlPlane.jsx
        ├── PaperTrading.jsx
        └── Settings.jsx
```

## Deployment

### Docker (Recommended)

Build and run with the included Dockerfile:

```bash
# Build image
docker build -t mcas-ui .

# Run on port 3000
docker run -p 3000:80 mcas-ui
```

The Dockerfile uses Caddy with proper SPA routing built-in.

### Caddy (Manual)

If deploying alongside existing Caddy setup, use the provided `Caddyfile`:

```bash
# Copy built assets
npm run build
cp -r dist/* /srv/mcas-ui/

# Use the Caddyfile template, editing the domain
caddy run --config Caddyfile
```

**Important:** SPA requires `try_files` to serve index.html for all routes:
```
try_files {path} {path}/ /index.html
```

Without this, direct navigation to `/markets/BTCUSDT` will 404.

### nginx Alternative

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    # Optional: proxy API
    location /v1 {
        proxy_pass http://api:8000;
    }
}
```

### Static Hosting

Build and serve the `dist` folder:
```bash
npm run build
npx serve dist
```

## Development

### Proxy Configuration

During development, API requests are proxied via Vite. Edit `vite.config.js`:

```javascript
export default defineConfig({
  server: {
    proxy: {
      '/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
```

### Adding New Pages

1. Create page component in `src/pages/`
2. Add route in `src/App.jsx`
3. Add navigation link in `src/components/Layout.jsx`

### Custom Hooks

Create data fetching hooks in `src/hooks/useData.js`:

```javascript
export function useMyData(params = {}) {
  return useFetch('/v1/my-endpoint', params)
}

// With polling
export function useMyLiveData(enabled = true) {
  return usePolling('/v1/my-endpoint', {}, 5000, { enabled })
}
```

## License

Proprietary - MCAS Trading System
