# Worker 1 Security Update

## Summary

This pass hardened the owned frontend trust-boundary surfaces around API configuration and logging.

The main change is that the backend API key is no longer persisted in browser storage. The frontend now persists only the API base URL, keeps the API key in memory for the current tab, scrubs the legacy `mcas_api_config` blob on startup, and updates the Settings UX to describe the new memory-only behavior accurately.

## Files Touched

- `Frontend/src/context/ApiContext.jsx`
- `Frontend/src/pages/Settings.jsx`
- `Frontend/src/utils/logger.js`

## Security Issues Fixed

### 1. Removed persistent browser storage of the backend API key

- Replaced config persistence of `mcas_api_config` with a base-URL-only key: `mcas_api_base_url`.
- `ApiContext` now restores only `baseUrl` from storage and initializes `apiKey` as memory-only state.
- A legacy `mcas_api_config` object is deleted on load so previously persisted keys are scrubbed on the next app start.

### 2. Reduced secret reuse from utility code

- `logger.js` no longer reads a persisted config blob to discover the telemetry destination.
- Log shipping now reads only the non-secret base URL from `mcas_api_base_url`.

### 3. Hardened credential entry handling in Settings

- The API key field is now a real password input instead of a plain text field with CSS masking.
- Added browser-level hardening attributes:
  - `autoCapitalize="off"`
  - `autoCorrect="off"`
  - `spellCheck={false}`
- Save/test flows preserve the existing in-memory key when appropriate, but no longer normalize persistence as a feature.

### 4. Corrected unsafe UX language

- Settings copy now explicitly states:
  - the API key is memory-only for the current tab
  - Save persists only the base URL
  - a full reload clears the API key

### 5. Removed owned-file lint warning in logger

- Adjusted console emission in `logger.js` to comply with the repo’s ESLint rule set, so the remaining lint failures are outside this narrowed worker boundary.

## Residual Risk

### 1. The API key still exists in browser memory while the tab is open

This is materially safer than `localStorage`, but it is still not an ideal production model for a sensitive trading app. Any successful XSS in the app could still access the in-memory key.

### 2. The frontend still sends `X-API-Key` directly to the backend

That behavior is part of the current app contract and was not redesigned here. The persistence risk is fixed, but the broader architecture still relies on browser-held credentials during the active session.

### 3. Non-secret browser storage still exists

This update intentionally did not remove benign persistence such as:

- API base URL
- log level
- dev chaos/fixture toggles

Those are not treated as secrets in this pass.

## Shared / Conflict Files Intentionally Avoided

These were intentionally not modified to reduce merge risk:

- `Frontend/src/App.jsx`
- `Frontend/package.json`
- `Frontend/vite.config.js`
- Worker 3 / Worker 4 page decomposition files
- unrelated lint/test/tooling config

## Broader Architectural Follow-up Required

For a stronger production model, the next step should be to remove browser-held backend credentials entirely and replace them with a server-backed session/bootstrap design, ideally using:

- an authenticated backend session
- short-lived server-issued tokens or httpOnly cookies
- a bootstrap endpoint that returns non-secret runtime config only

That is outside this worker boundary, but it is the correct long-term fix.

## Validation Results

### `npm run lint:js:full`

Result: failed

Reason: existing repo-wide lint errors outside this narrowed worker boundary remain in files such as:

- `src/components/FeedbackOnset.jsx`
- `src/components/Layout.jsx`
- `src/components/StaleDataGhost.jsx`
- `src/components/pipeline/*`
- `src/pages/Pipeline.jsx`
- `src/pages/RunCompare.jsx`

No remaining lint warning/error from the owned `logger.js` file after the final change.

### `npm run test:unit`

Result: passed

- 25 tests passed
- 0 failed

### `npm run build`

Result: passed

- Vite production build completed successfully
- bundle budget check passed

## Notes

- A source grep after the change found no `apiKey` persistence via `localStorage` or `sessionStorage` in `Frontend/src`.
- The only remaining source reference to `mcas_api_config` is the legacy-cleanup path in `ApiContext`, which exists solely to remove the old blob on startup.
