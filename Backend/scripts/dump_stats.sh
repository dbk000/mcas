#!/bin/bash
# Dump unified opportunity views to CSV.
# Usage:
#   ./scripts/dump_stats.sh              # stats rollup only
#   ./scripts/dump_stats.sh --detail     # also dump per-opportunity detail
#   ./scripts/dump_stats.sh --detail 7d  # detail limited to last 7 days (default: 30d)

set -euo pipefail

OUT_DIR="$(dirname "$0")/output"
TS=$(date +%Y%m%d_%H%M%S)
CONTAINER="backend-postgres-1"

# --- Stats rollup (always) ---
STATS_FILE="$OUT_DIR/unified_stats_${TS}.csv"
docker exec "$CONTAINER" psql -U trading -d trading -c \
  "COPY (SELECT * FROM v_unified_opportunity_stats ORDER BY time_window, asset_class, setup_id) TO STDOUT WITH CSV HEADER" \
  > "$STATS_FILE"
echo "Stats:  $STATS_FILE ($(wc -l < "$STATS_FILE") rows)"

# --- Per-opportunity detail (optional) ---
if [[ "${1:-}" == "--detail" ]]; then
  WINDOW="${2:-30d}"
  case "$WINDOW" in
    24h) INTERVAL="24 hours" ;;
    7d)  INTERVAL="7 days" ;;
    30d) INTERVAL="30 days" ;;
    *)   echo "Invalid window '$WINDOW'. Use 24h, 7d, or 30d." >&2; exit 1 ;;
  esac
  DETAIL_FILE="$OUT_DIR/unified_detail_${WINDOW}_${TS}.csv"
  docker exec "$CONTAINER" psql -U trading -d trading -c \
    "COPY (SELECT * FROM v_unified_opportunity_pnl WHERE created_ts >= NOW() - INTERVAL '$INTERVAL' ORDER BY created_ts DESC) TO STDOUT WITH CSV HEADER" \
    > "$DETAIL_FILE"
  echo "Detail: $DETAIL_FILE ($(wc -l < "$DETAIL_FILE") rows)"
fi
