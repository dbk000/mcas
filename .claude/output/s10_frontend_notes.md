# S10 Setup Frontend Integration Notes

**Date:** 2026-02-23  
**Status:** Research Complete - Ready for S10 Implementation

---

## Overview

This document maps all frontend integration points for adding a new setup (S10) to the MCAS trading UI. The frontend uses a decentralized setup registry pattern: each setup is hardcoded in UI option lists, and the API discovers available setups dynamically from Redis. This approach means **S10 will require updates in 4 specific locations** before appearing in the UI.

---

## 1. Frontend Setup Mapping & Display Names

### Location 1: `/Frontend/src/pages/Opportunities.jsx` (Lines 314-325)

**Purpose:** Setup filter dropdown in the Opportunities list view.

**Current State:**
```javascript
<Select
  value={filters.setup_id}
  onChange={(e) => setFilters({ ...filters, setup_id: e.target.value })}
  options={[
    { value: '', label: 'All Setups' },
    { value: 'S9', label: 'S9 (Short Reversion)' },
    { value: 'U1', label: 'U1 (Unicorn)' },
    { value: 'EQ_TSMOM', label: 'EQ: TSMOM (20d)' },
    { value: 'EQ_XSMOM', label: 'EQ: XS Momentum (20d)' },
    { value: 'EQ_REV1A', label: 'EQ: Reversion 1a (10d)' },
    { value: 'EQ_REV1B', label: 'EQ: Reversion 1b (10d)' },
    { value: 'EQ_VPD', label: 'EQ: Vol-Price Div (1d)' },
  ]}
  className="w-32"
/>
```

**Pattern Observed:**
- `S9` and `U1` are crypto setups (short names)
- `EQ_*` setups include descriptive labels: `EQ_TSMOM (20d)` = setup_id followed by timeframe hint
- Labels use parentheses for metadata: horizon days `(10d)`, `(20d)`, `(1d)`

**For S10:**
- If S10 is a crypto setup: `{ value: 'S10', label: 'S10 (Setup Name)' }`
- If S10 is an equities setup: `{ value: 'EQ_S10', label: 'EQ: Setup Name (Xd)' }` where X is the typical horizon

---

### Location 2: `/Frontend/src/pages/Opportunities.jsx` (Lines 461-475)

**Purpose:** MultiSelect for setups in the "Simulate Expired" modal.

**Current State:**
```javascript
<MultiSelect
  value={simConfig.setup_ids}
  onChange={(vals) => setSimConfig({ ...simConfig, setup_ids: vals })}
  options={[
    { value: 'S9', label: 'S9' },
    { value: 'U1', label: 'U1' },
    { value: 'EQ_TSMOM', label: 'EQ_TSMOM' },
    { value: 'EQ_XSMOM', label: 'EQ_XSMOM' },
    { value: 'EQ_REV1A', label: 'EQ_REV1A' },
    { value: 'EQ_REV1B', label: 'EQ_REV1B' },
    { value: 'EQ_VPD', label: 'EQ_VPD' },
  ]}
  placeholder="All"
/>
```

**Pattern Observed:**
- Shorter format than Location 1 (omits description)
- Uses plain setup_id as both value and label
- No parenthetical metadata

**For S10:** Add `{ value: 'S10', label: 'S10' }` or `{ value: 'EQ_S10', label: 'EQ_S10' }`

---

### Location 3: `/Frontend/src/pages/ControlPlane.jsx` (Lines 107-109)

**Purpose:** Setup checkboxes in the Control Plane for manual scan configuration.

**Current State:**
```javascript
{[
  { id: 'S9', label: 'S9 (Short Reversion)' },
  { id: 'U1', label: 'U1 (Unicorn)' },
].map(s => (
  <label key={s.id} className="flex items-center gap-2 cursor-pointer">
    <input type="checkbox" checked={selectedSetups.includes(s.id)} onChange={...} />
    {s.label}
  </label>
))}
```

**Pattern Observed:**
- Only crypto setups (S9, U1) appear here; equities setups are absent
- Uses `id` field instead of `value`
- Includes full display label with parenthetical description

**For S10 (if crypto):** Add `{ id: 'S10', label: 'S10 (Description)' }`

---

## 2. Backend Hardcoded Setup List

### Location 4: `/Backend/api/app/main.py` (Line 485)

**Purpose:** List of all active setups that the API will search for in Redis.

**Current State:**
```python
setup_ids = [setup_id] if setup_id else ["S9", "U1", "EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B", "EQ_VPD"]
```

**Context:**
- In the `_read_opportunities_from_redis()` function
- Used when API queries for active signals (status='active')
- If `setup_id` param is passed, only that setup is searched; otherwise all are searched
- The function iterates: `for sid in setup_ids: for tf in timeframes: ...`

**For S10:** Add `"S10"` or `"EQ_S10"` to the list:
```python
setup_ids = [setup_id] if setup_id else ["S9", "U1", "EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B", "EQ_VPD", "S10"]
```

---

## 3. Conditional Rendering Based on Setup Type

### Location 5: `/Frontend/src/pages/Opportunities.jsx` (Line 760)

**Purpose:** Display different trade level layouts for crypto vs. equities setups.

**Current Code:**
```javascript
{payload.entry_price && (
  <div className="border-t border-terminal-600 pt-3">
    {opportunity.setup_id?.startsWith('EQ_') ? (
      <div className="space-y-2">
        {/* Equities layout: direction, horizon_days, confidence, explain, features */}
        <div className="flex items-center gap-2">
          <DirectionBadge direction={payload.direction} />
          <Badge>{payload.horizon_days}d horizon</Badge>
          {payload.exploratory && <Badge variant="warning">EXPLORATORY</Badge>}
        </div>
        {/* ... equity-specific fields ... */}
      </div>
    ) : (
      <div className="grid grid-cols-2 gap-2 text-sm">
        {/* Crypto layout: entry, stop, tp1, tp2, r_value */}
        <div>
          <span className="text-terminal-500">Entry</span>
          <p className="font-mono text-terminal-100">{formatPrice(payload.entry_price)}</p>
        </div>
        {/* ... crypto fields ... */}
      </div>
    )}
  </div>
)}
```

**Key Logic:**
- **Equities (EQ_*) layouts** display:
  - Direction badge + horizon_days (e.g., "10d horizon")
  - Confidence percentage
  - `payload.explain` array (rendered as list items)
  - `payload.features` snapshot (optional)
  - `payload.exploratory` flag (if set)

- **Crypto (S9, U1) layouts** display:
  - Entry, Stop, TP1, TP2 prices (grid layout)
  - R-Value (dollar amount of 1R)
  - Score

**For S10:**
- If S10 is **crypto-like** (with entry/stop/TP levels): No changes needed; it will auto-render with crypto layout
- If S10 is **equities-like** (with horizon_days, confidence): Rename ID to `EQ_S10` and it will auto-render with equities layout

---

## 4. Payload Structure & Rendering

### Crypto Setup Payload (S9, U1)

```json
{
  "direction": "short",
  "entry_price": 45250.50,
  "stop_price": 45500.25,
  "tp1_price": 44800.75,
  "tp2_price": 44200.50,
  "r_value": 150.25,
  "why_items": [
    "Reason 1",
    "Reason 2",
    "Reason 3"
  ]
}
```

**Frontend Rendering:**
- `/Frontend/src/pages/Opportunities.jsx:790-806` — Grid layout with entry/stop/tp1/r_value
- `/Frontend/src/pages/OpportunityDetail.jsx:301-304` — LevelBox components for each price level

---

### Equities Setup Payload (EQ_TSMOM, EQ_XSMOM, etc.)

```json
{
  "direction": "LONG",
  "entry_price": 152.30,
  "horizon_days": 20,
  "expected_return_bps": 250,
  "confidence": 0.72,
  "strategy_version": "v1.2",
  "exploratory": false,
  "explain": [
    "20d momentum score in top 10%",
    "Technical: 200d SMA bullish cross",
    "Breadth: 65% of S&P 500 components positive",
    "RSI: 58 (neutral momentum zone)"
  ],
  "features": {
    "ret_20d": 0.0845,
    "rsi_14": 58.2,
    "breadth_pct": 0.65,
    "vol_20d": 0.183
  }
}
```

**Frontend Rendering:**
- `/Frontend/src/pages/Opportunities.jsx:761-787` — Direction, horizon_days, exploratory badge
- `/Frontend/src/pages/Opportunities.jsx:781-786` — `payload.explain` array rendered as `<ul>` with 3-8 items
- `/Frontend/src/pages/OpportunityDetail.jsx:274-282` — Expanded explain section in detail view
- `/Frontend/src/pages/OpportunityDetail.jsx:284-296` — Feature snapshot grid (if present)

---

## 5. Explain Payload Structure (Backend)

### Backend Definition: `/Backend/worker/app/setup_base.py:124-143`

```python
@dataclass
class ExplainPayload:
    """
    Canon 7A: Explainability payload for setup triggers.

    Every signal must produce 3-8 human-readable "why" items.
    """
    why_items: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return validation errors (empty if valid)."""
        errors = []
        if len(self.why_items) < 3:
            errors.append(f"Need at least 3 why_items, got {len(self.why_items)}")
        if len(self.why_items) > 8:
            errors.append(f"Maximum 8 why_items, got {len(self.why_items)}")
        return errors

    def to_dict(self) -> dict:
        return {"why_items": self.why_items}
```

**Constraints:**
- Minimum 3 items, maximum 8 items per explain payload
- Each item is a plain string (no formatting)
- Rendered verbatim in frontend UI

**API Conversion:** `/Backend/api/app/main.py:533`

```python
"reason": "; ".join(payload.get("why_items", [])) if payload.get("why_items") else None,
```

The `why_items` array is joined with `"; "` separator to create a single-line `reason` field in the opportunities list response. In detail views, the original `payload.explain` array is preserved as-is.

---

## 6. Data Flow: Backend → Frontend

### Signal Generation (Worker)

1. `/Backend/worker/app/setups/sN_*.py` — Setup evaluator returns `Signal` with payload
2. Payload includes `why_items: ExplainPayload` (3-8 items)
3. Signal stored in Redis: `sig:{symbol}:{tf}:{setup_id}` (JSON value)

### API Response (Opportunities List)

1. `/Backend/api/app/main.py:473-549` — `_read_opportunities_from_redis()` reads signals
2. For each signal, extracts:
   - `direction` from `payload.get("direction")`
   - `reason` from joining `payload.get("why_items", [])`
   - `payload` stored intact
3. Returns array with fields:
   ```json
   {
     "id": "S10:SYMBOL:1h",
     "symbol": "SYMBOL",
     "timeframe": "1h",
     "setup_id": "S10",
     "direction": "long",
     "score": 0.87,
     "reason": "why_item_1; why_item_2; why_item_3",
     "payload": { "direction": "long", ..., "why_items": [...] },
     "as_of_ts": 1708695234.5,
     "expires_at": 1708699834.5,
     "updated_ts": "2026-02-23T14:30:34Z",
     "source": "redis"
   }
   ```

### Frontend Rendering

1. `useOpportunities()` hook calls `/v1/opportunities?status=active&setup_id=S10`
2. React renders `OpportunityCard` for each opportunity
3. Card checks: `opportunity.setup_id?.startsWith('EQ_')`?
   - **If EQ_S10:** Render equities layout (direction, horizon, confidence, explain)
   - **If S10:** Render crypto layout (entry, stop, tp1, r_value)
4. Explain items rendered as `<ul><li>` list (if equities) or attached fields (if crypto)

---

## 7. Key Integration Points Summary

| Location | File | Line(s) | Purpose | For S10 |
|----------|------|---------|---------|---------|
| 1 | Frontend/src/pages/Opportunities.jsx | 314-325 | Setup filter dropdown (long form) | Add option with `S10` or `EQ_S10` |
| 2 | Frontend/src/pages/Opportunities.jsx | 461-475 | MultiSelect in simulation modal | Add option with `S10` or `EQ_S10` |
| 3 | Frontend/src/pages/ControlPlane.jsx | 107-109 | Control plane setup checkboxes (crypto only) | Add if S10 is crypto |
| 4 | Backend/api/app/main.py | 485 | Hardcoded setup list for Redis query | Add `"S10"` or `"EQ_S10"` |
| 5 | Frontend/src/pages/Opportunities.jsx | 760 | Conditional layout (crypto vs. equities) | Auto-works via `startsWith('EQ_')` check |

---

## 8. Environment & Configuration

### Backend Environment Variables

**Location:** `/Backend/.env` or docker-compose environment section

**Current Setup IDs:** `S9`, `U1`, `EQ_TSMOM`, `EQ_XSMOM`, `EQ_REV1A`, `EQ_REV1B`, `EQ_VPD`

**For S10:** Update `SETUP_IDS` env var to include `S10`:
```bash
SETUP_IDS=S9,U1,EQ_TSMOM,EQ_XSMOM,EQ_REV1A,EQ_REV1B,EQ_VPD,S10
```

This controls:
- Which setups the worker will evaluate
- Which setups the signal engine will compute indicators for

---

## 9. Testing Checklist for S10 Frontend Integration

After implementing S10 in backend and adding to the four frontend locations:

- [ ] **Opportunities List Filter:** Can select "S10" from dropdown, displays opportunities with correct setup_id
- [ ] **Opportunity Detail:** Click into S10 opportunity; verify correct payload rendering
  - If crypto: Entry, stop, TP prices render correctly
  - If equities: Direction, horizon_days, confidence, explain list render correctly
- [ ] **CSV Export:** Export opportunities with S10 filter applied; CSV contains correct rows
- [ ] **Simulation Modal:** Can select S10 in multi-select; can start simulation
- [ ] **Control Plane:** If S10 is crypto, verify checkbox appears and can enable scans
- [ ] **Live Signals:** Verify new S10 signals appear in Redis as `sig:{symbol}:{tf}:S10`
- [ ] **Expiry Handling:** Verify S10 signals expire after appropriate timeout (24h for equities, 1h for crypto)

---

## 10. Additional Observations

### Signal Lifecycle

1. **Generated:** Signal worker evaluates S10 → stores in Redis `sig:*` key
2. **Ranked:** Score stored in Redis ZSET `signals:score:S10:{tf}` by symbol score
3. **Expired:** API filters by `as_of_ts + max_age` (1h crypto, 24h equities)
4. **Archived:** After expiry, moved to Postgres `opportunities` table with `status='expired'`

### Display Name Conventions

- **Crypto setups:** Short ID with description — `S9 (Short Reversion)`, `U1 (Unicorn)`
- **Equities setups:** Prefix notation with timeframe — `EQ: TSMOM (20d)`, `EQ: Reversion 1a (10d)`

Recommend following this pattern for S10:
- If S10 is a crypto strategy: `S10 (Strategy Name)` in filter, `S10` in modal
- If S10 is an equities strategy: `EQ: Strategy Name (Xd)` in filter, `EQ_S10` in modal

### No UI Component Needed

The frontend already has generic components that auto-render signals:
- `Badge` component handles setup_id display (no special styling for S10)
- `DirectionBadge` handles direction rendering (long/short)
- Conditional render checks `setup_id.startsWith('EQ_')` — no hard-coded S10 logic needed

---

## 11. References

**Backend Signal Generation:**
- Signal base class: `/Backend/worker/app/setup_base.py`
- S9 example: `/Backend/worker/app/setups/s9_short_reversion.py`
- U1 example: `/Backend/worker/app/setups/u1_unicorn_ignition.py`

**API Endpoints:**
- Opportunities list: `/Backend/api/app/main.py:556-641` (`/v1/opportunities`)
- Live opportunity: `/Backend/api/app/main.py:721-772` (`/v1/opportunities/live/{setup_id}/{symbol}/{tf}`)

**Frontend Pages:**
- Opportunities list & card: `/Frontend/src/pages/Opportunities.jsx:148-854`
- Opportunity detail: `/Frontend/src/pages/OpportunityDetail.jsx:117-432`
- Control plane: `/Frontend/src/pages/ControlPlane.jsx`

**Styling & Components:**
- UI components (Badge, DirectionBadge, etc.): `/Frontend/src/components/ui.jsx:48-104`
- Formatters: `/Frontend/src/utils/formatters.js`

---

## Summary

To add S10 to the frontend:

1. **Add to filter dropdown** (Opportunities.jsx:314-325)
2. **Add to simulation modal** (Opportunities.jsx:461-475)
3. **Add to control plane** if crypto (ControlPlane.jsx:107-109)
4. **Add to backend setup list** (main.py:485)
5. **Update .env** `SETUP_IDS` variable
6. **No changes needed** for conditional rendering or payload handling — existing logic handles both crypto and equities setups generically

S10 will appear in the UI automatically once registered in these four locations and the backend begins generating S10 signals.
