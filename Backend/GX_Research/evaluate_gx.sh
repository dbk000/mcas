#!/usr/bin/env bash
# GX Research — 6-stage wrapper. Runs Stage A → F in sequence.
# --auto         skip blocking reads; halt on gate failure (default; set for unattended).
# --interactive  block on `read` between stages (plan §12 wrapper spec).
# --from X       start at stage X (A..F) assuming prior stage output exists.
#
# GX_BATCH_ID env var controls the batch tag for all stage outputs. Defaults to
# gx_YYYYMMDDTHHMMSSZ if unset.
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

MODE="auto"
START_FROM="A"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto)        MODE="auto"; shift ;;
    --interactive) MODE="interactive"; shift ;;
    --from)        START_FROM="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${GX_BATCH_ID:-}" ]]; then
  export GX_BATCH_ID="gx_$(date -u +%Y%m%dT%H%M%SZ)"
fi
echo "[evaluate_gx] MODE=$MODE  START_FROM=$START_FROM  GX_BATCH_ID=$GX_BATCH_ID"

# Detect runtime: in-container vs host.
if [[ -d /app/GX_Research ]]; then
  PYTHON="python3"
  SCRIPTS_DIR="/app/GX_Research/scripts"
else
  PYTHON="python3"
  SCRIPTS_DIR="$SCRIPT_DIR/scripts"
fi

run_stage() {
  local stage_letter="$1"
  local script_name="$2"
  local human_desc="$3"
  echo "================================================================="
  echo "[evaluate_gx] Stage $stage_letter — $human_desc"
  echo "================================================================="
  if [[ "$MODE" == "interactive" && "$stage_letter" != "A" ]]; then
    read -p "Press enter to run Stage $stage_letter (or Ctrl-C to abort)..." _
  fi
  "$PYTHON" "$SCRIPTS_DIR/$script_name"
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "[evaluate_gx] Stage $stage_letter exited $rc — halting pipeline."
    exit $rc
  fi
  # Optional tracker commit check in interactive mode (plan §12a.1)
  if [[ "$MODE" == "interactive" ]]; then
    if ! git diff --quiet -- GX_Research_Tracker.xlsx 2>/dev/null; then
      echo "[evaluate_gx] Tracker not committed — commit before next stage."
      read -p "Press enter once tracker is committed..." _
    fi
  fi
}

# Ensure tracker exists
if [[ ! -f "$SCRIPT_DIR/GX_Research_Tracker.xlsx" ]]; then
  echo "[evaluate_gx] Tracker not found; initialising."
  "$PYTHON" "$SCRIPTS_DIR/init_tracker.py"
fi

declare -A STAGE_MAP=(
  [A]="01_coarse_trigger_sweep.py|Stage A coarse trigger geometry sweep"
  [B]="02_stop_exit_sweep.py|Stage B stop + exit sweep on A survivors"
  [C]="03_dow_hour_split.py|Stage C DOW × hour split on B survivors"
  [D]="04_filter_discovery.py|Stage D filter + regime discovery (4-fold WF)"
  [E]="05_cross_tf_sweep.py|Stage E cross-TF agreement sweep"
  [F]="06_portfolio_sim.py|Stage F holdout portfolio sim + SHORTLIST decision"
)

STAGE_ORDER=(A B C D E F)
start_idx=-1
for i in "${!STAGE_ORDER[@]}"; do
  if [[ "${STAGE_ORDER[$i]}" == "$START_FROM" ]]; then start_idx=$i; break; fi
done
if [[ $start_idx -lt 0 ]]; then
  echo "[evaluate_gx] Invalid --from value: $START_FROM"; exit 2
fi

for (( i=start_idx; i<${#STAGE_ORDER[@]}; i++ )); do
  s="${STAGE_ORDER[$i]}"
  IFS='|' read -r script desc <<< "${STAGE_MAP[$s]}"
  run_stage "$s" "$script" "$desc"
done

echo "[evaluate_gx] Writing report..."
"$PYTHON" "$SCRIPTS_DIR/report.py"
echo "[evaluate_gx] DONE — batch $GX_BATCH_ID"
