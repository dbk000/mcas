#!/usr/bin/env python3
"""
Auto-capture commit hook script — Node-Level Impact Analysis.
Called by .git/hooks/post-commit to map file changes to pipeline nodes,
classify impact by category, and propagate downstream staleness.

Impact categories per node type:
  Data: sources, schema, coverage, frequency
  Feature: inputs, calculations, outputs, parameters
  Signal: entry_logic, exit_logic, filters, expected_behaviour
  Parameter: values, ranges, dependencies
  Strategy: composition, weighting, risk_rules
  Backtest: methodology, validation_gates
  Risk/Execution: position_sizing, limits, execution_logic

Event types: code_change:parameter, code_change:logic, code_change:schema,
             code_change:config, code_change:annotation, upstream_change_detected

Usage: python3 auto_capture_commit.py [--database-url URL]
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    print("[auto_capture] psycopg not available, skipping auto-capture", file=sys.stderr)
    sys.exit(0)


def get_database_url():
    """Resolve DATABASE_URL from env or Docker-aware defaults."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    if os.path.isdir("/app"):
        return "postgresql://trading:trading@postgres:5432/trading"
    return "postgresql://trading:trading@localhost:5432/trading"


def get_commit_info():
    """Extract info from the latest commit."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        message = subprocess.check_output(
            ["git", "log", "-1", "--format=%B"], text=True
        ).strip()
        author = subprocess.check_output(
            ["git", "log", "-1", "--format=%an"], text=True
        ).strip()
        changed_files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "--name-only"], text=True
        ).strip().split("\n")
        diff_stat = subprocess.check_output(
            ["git", "diff", "HEAD~1", "--stat"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None

    return {
        "sha": sha,
        "message": message,
        "author": author,
        "changed_files": [f for f in changed_files if f.strip()],
        "diff_stat": diff_stat,
    }


def get_file_diff(filepath):
    """Get the unified diff for a specific file from the last commit."""
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "--", filepath], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def match_file_to_nodes(conn, changed_files):
    """Map changed files to pipeline nodes via node_file_mapping and source_file.
    Returns dict of {node_id: [matched_files]}."""
    matched = {}

    with conn.cursor() as cur:
        # Check node_file_mapping (glob patterns)
        cur.execute("SELECT node_id, file_pattern, mapping_type FROM meta.node_file_mapping")
        mappings = cur.fetchall()

        import fnmatch
        for node_id, pattern, mapping_type in mappings:
            nid = str(node_id)
            for f in changed_files:
                if fnmatch.fnmatch(f, pattern):
                    matched.setdefault(nid, []).append(f)

        # Check source_file exact match
        cur.execute("SELECT node_id, source_file FROM meta.node WHERE source_file IS NOT NULL")
        nodes = cur.fetchall()

        for node_id, source_file in nodes:
            nid = str(node_id)
            for f in changed_files:
                if source_file and (f.endswith(source_file) or source_file.endswith(f)):
                    matched.setdefault(nid, []).append(f)

    return matched


# =============================================================================
# Impact Classification Engine
# =============================================================================

# Patterns for classifying diff changes
PARAM_PATTERNS = [
    (r'[-+]\s*[\w_]+\s*[:=]\s*[\d.]+', 'parameters'),         # numeric assignments
    (r'[-+]\s*["\'][\w_]+["\']\s*[:=]\s*', 'parameters'),      # string-keyed assignments
    (r'[-+].*_params\b', 'parameters'),
    (r'[-+].*config\[', 'parameters'),
    (r'[-+].*\.threshold\b', 'parameters'),
    (r'[-+].*_threshold\b', 'parameters'),
    (r'[-+].*lookback\b', 'parameters'),
    (r'[-+].*window\b', 'parameters'),
    (r'[-+].*period\b', 'parameters'),
]

LOGIC_PATTERNS = [
    (r'[-+]\s*(if|elif|else)\b', 'entry_logic'),
    (r'[-+].*\.evaluate\b', 'entry_logic'),
    (r'[-+].*trigger\b', 'entry_logic'),
    (r'[-+].*signal\b', 'entry_logic'),
    (r'[-+].*filter\b', 'filters'),
    (r'[-+].*exit\b', 'exit_logic'),
    (r'[-+].*stop_loss\b', 'exit_logic'),
    (r'[-+].*take_profit\b', 'exit_logic'),
    (r'[-+].*trailing\b', 'exit_logic'),
]

SCHEMA_PATTERNS = [
    (r'[-+]\s*(CREATE|ALTER|DROP)\s+TABLE', 'schema'),
    (r'[-+]\s*(ADD|DROP)\s+COLUMN', 'schema'),
    (r'[-+].*\.columns\b', 'schema'),
    (r'[-+].*DataFrame\[', 'schema'),
]

IMPORT_PATTERNS = [
    (r'[-+]\s*(import|from)\s+', 'inputs'),
]

CALCULATION_PATTERNS = [
    (r'[-+].*np\.\w+\(', 'calculations'),
    (r'[-+].*pd\.\w+\(', 'calculations'),
    (r'[-+].*\.rolling\(', 'calculations'),
    (r'[-+].*\.ewm\(', 'calculations'),
    (r'[-+].*indicator', 'calculations'),
]

ANNOTATION_PATTERNS = [
    (r'[-+]\s*#', None),  # comment-only lines
    (r'[-+]\s*"""', None),
    (r"[-+]\s*'''", None),
]

ALL_PATTERNS = PARAM_PATTERNS + LOGIC_PATTERNS + SCHEMA_PATTERNS + IMPORT_PATTERNS + CALCULATION_PATTERNS


def extract_param_values(diff_text):
    """Extract before/after parameter values from diff hunks."""
    changes = []
    lines = diff_text.split('\n')

    for i, line in enumerate(lines):
        if not line.startswith(('+', '-')) or line.startswith(('+++', '---')):
            continue

        # Match numeric parameter assignments: name = 2.5 or name: 2.5
        m = re.match(r'([+-])\s*([\w_.]+)\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)\s*', line)
        if m:
            sign, param_name, value = m.groups()
            try:
                val = float(value)
            except ValueError:
                val = value

            # Look for the counterpart (- → +, + → -)
            opposite = '+' if sign == '-' else '-'
            for j in range(max(0, i - 5), min(len(lines), i + 5)):
                m2 = re.match(
                    rf'[{re.escape(opposite)}]\s*{re.escape(param_name)}\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)\s*',
                    lines[j]
                )
                if m2:
                    try:
                        other_val = float(m2.group(1))
                    except ValueError:
                        other_val = m2.group(1)

                    if sign == '-':
                        changes.append({
                            "category": "parameters",
                            "description": f"{param_name} changed from {val} to {other_val}",
                            "before": {param_name: val},
                            "after": {param_name: other_val},
                            "confidence": "high",
                        })
                    else:
                        changes.append({
                            "category": "parameters",
                            "description": f"{param_name} changed from {other_val} to {val}",
                            "before": {param_name: other_val},
                            "after": {param_name: val},
                            "confidence": "high",
                        })
                    break

    # Deduplicate (same param seen from both - and + sides)
    seen = set()
    unique = []
    for c in changes:
        key = tuple(sorted(c.get("before", {}).keys()))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def classify_diff(diff_text):
    """Classify a diff into impact categories with confidence levels."""
    if not diff_text:
        return [], "code_change:annotation"

    lines = diff_text.split('\n')
    changed_lines = [l for l in lines if l.startswith(('+', '-'))
                     and not l.startswith(('+++', '---'))]

    if not changed_lines:
        return [], "code_change:annotation"

    categories_found = set()
    changes = []

    # Check if only annotations changed
    non_annotation_changes = 0
    for line in changed_lines:
        is_annotation = any(re.search(p, line) for p, _ in ANNOTATION_PATTERNS)
        if not is_annotation:
            non_annotation_changes += 1

    if non_annotation_changes == 0:
        return [{"category": "annotation", "description": "Comment/docstring changes only",
                 "before": None, "after": None, "confidence": "high"}], "code_change:annotation"

    # Extract parameter value changes (high confidence)
    param_changes = extract_param_values(diff_text)
    changes.extend(param_changes)
    if param_changes:
        categories_found.add("parameters")

    # Pattern matching for medium-confidence classification
    for line in changed_lines:
        for pattern, category in ALL_PATTERNS:
            if category and re.search(pattern, line, re.IGNORECASE):
                categories_found.add(category)

    # Generate medium-confidence change records for non-parameter categories
    for cat in categories_found:
        if cat == "parameters":
            continue  # already handled with high confidence
        changes.append({
            "category": cat,
            "description": f"Detected changes in {cat} (pattern-inferred)",
            "before": None,
            "after": None,
            "confidence": "medium",
        })

    # If we found changes but couldn't classify, mark as low confidence
    if not changes and non_annotation_changes > 0:
        changes.append({
            "category": "unknown",
            "description": f"Structural change detected ({non_annotation_changes} lines changed)",
            "before": None,
            "after": None,
            "confidence": "low",
        })
        categories_found.add("unknown")

    # Determine event_type from categories
    if categories_found <= {"parameters"}:
        event_type = "code_change:parameter"
    elif categories_found & {"schema"}:
        event_type = "code_change:schema"
    elif categories_found & {"entry_logic", "exit_logic", "filters", "calculations"}:
        event_type = "code_change:logic"
    elif categories_found <= {"annotation"}:
        event_type = "code_change:annotation"
    else:
        event_type = "code_change:config"

    return changes, event_type


def infer_downstream_implications(changes, categories):
    """Infer likely downstream effects from the change categories."""
    implications = []

    cats = {c["category"] for c in changes}

    if "entry_logic" in cats or "filters" in cats:
        implications.append("Signal frequency likely affected (entry logic or filter change)")
    if "exit_logic" in cats:
        implications.append("Trade duration and PnL distribution likely affected (exit logic change)")
    if "parameters" in cats:
        implications.append("Existing backtest metrics may need revalidation (parameter change)")
    if "calculations" in cats:
        implications.append("Computed feature values have changed (calculation logic modified)")
    if "schema" in cats:
        implications.append("Data shape changed — downstream consumers may need updating")
    if "inputs" in cats:
        implications.append("New dependency added — may affect feature availability")

    return implications


def build_impact_record(node_id, commit_info, matched_files, all_changes, event_type, implications):
    """Build the structured impact record for a node."""
    categories = list({c["category"] for c in all_changes})
    stale_metrics = event_type not in ("code_change:annotation",)
    requires_rebacktest = event_type in ("code_change:logic", "code_change:parameter", "code_change:schema")

    return {
        "node_id": node_id,
        "commit_sha": commit_info["sha"],
        "commit_message": commit_info["message"][:500],
        "matched_files": matched_files[:20],
        "diff_stat": commit_info["diff_stat"][:500],
        "impact_categories": categories,
        "changes": all_changes[:20],  # cap to prevent bloat
        "downstream_implications": implications,
        "stale_metrics": stale_metrics,
        "requires_rebacktest": requires_rebacktest,
    }


# =============================================================================
# Downstream Staleness Propagation
# =============================================================================

def get_downstream_nodes(conn, node_id):
    """Walk downstream from a node via edges (port → port → node).
    Returns list of (node_id, node_name) tuples."""
    downstream = []

    with conn.cursor() as cur:
        # BFS via edges
        cur.execute("""
            WITH RECURSIVE downstream AS (
                -- Start: output ports of the source node
                SELECT DISTINCT e.target_port_id, tp.node_id AS target_node_id
                FROM meta.port sp
                JOIN meta.edge e ON e.source_port_id = sp.port_id
                JOIN meta.port tp ON tp.port_id = e.target_port_id
                WHERE sp.node_id = %s AND sp.direction = 'output'

                UNION

                -- Recurse: from each target node's output ports
                SELECT DISTINCT e2.target_port_id, tp2.node_id AS target_node_id
                FROM downstream d
                JOIN meta.port sp2 ON sp2.node_id = d.target_node_id AND sp2.direction = 'output'
                JOIN meta.edge e2 ON e2.source_port_id = sp2.port_id
                JOIN meta.port tp2 ON tp2.port_id = e2.target_port_id
            )
            SELECT DISTINCT n.node_id, n.name
            FROM downstream d
            JOIN meta.node n ON n.node_id = d.target_node_id
        """, (node_id,))

        downstream = [(str(r[0]), r[1]) for r in cur.fetchall()]

    return downstream


def propagate_staleness(conn, source_node_id, source_event_id, source_change_summary, commit_info):
    """Create upstream_change_detected events on all downstream nodes."""
    downstream = get_downstream_nodes(conn, source_node_id)
    created = 0

    with conn.cursor() as cur:
        # Get source node name
        cur.execute("SELECT name FROM meta.node WHERE node_id = %s", (source_node_id,))
        source_name_row = cur.fetchone()
        source_name = source_name_row[0] if source_name_row else source_node_id[:8]

        for ds_node_id, ds_node_name in downstream:
            # Get current version for downstream node
            cur.execute("""
                SELECT version_id FROM meta.node_version
                WHERE node_id = %s
                ORDER BY
                    CASE status WHEN 'deployed' THEN 0 WHEN 'promising' THEN 1 ELSE 2 END,
                    version_number DESC
                LIMIT 1
            """, (ds_node_id,))
            row = cur.fetchone()
            if not row:
                continue

            version_id = str(row[0])

            cur.execute("""
                INSERT INTO meta.version_event
                    (version_id, event_type, event_detail, actor, caused_by_event_id)
                VALUES (%s, 'upstream_change_detected', %s, %s, %s)
            """, (
                version_id,
                json.dumps({
                    "source_node": source_name,
                    "source_node_id": source_node_id,
                    "source_change": source_change_summary,
                    "implication": "metrics may no longer reflect current upstream behaviour",
                    "requires_revalidation": True,
                    "commit_sha": commit_info["sha"],
                }),
                "auto_capture",
                source_event_id,
            ))
            created += 1

    return created


# =============================================================================
# Main
# =============================================================================

def create_version_events(conn, commit_info, node_files_map):
    """Create version_event records with impact analysis for each affected node.
    Returns (direct_events_created, downstream_events_created)."""
    if not node_files_map:
        return 0, 0

    direct_created = 0
    downstream_created = 0

    with conn.cursor() as cur:
        for node_id, matched_files in node_files_map.items():
            # Get current deployed version for this node
            cur.execute("""
                SELECT version_id FROM meta.node_version
                WHERE node_id = %s
                ORDER BY
                    CASE status WHEN 'deployed' THEN 0 WHEN 'promising' THEN 1 ELSE 2 END,
                    version_number DESC
                LIMIT 1
            """, (node_id,))
            row = cur.fetchone()
            if not row:
                continue

            version_id = str(row[0])

            # Analyse diffs for matched files
            all_changes = []
            combined_event_type = "code_change:annotation"
            priority_order = ["code_change:logic", "code_change:schema",
                              "code_change:parameter", "code_change:config",
                              "code_change:annotation"]

            for filepath in matched_files:
                diff_text = get_file_diff(filepath)
                changes, event_type = classify_diff(diff_text)
                all_changes.extend(changes)

                # Take the highest-priority event type
                if priority_order.index(event_type) < priority_order.index(combined_event_type):
                    combined_event_type = event_type

            # Infer downstream implications
            implications = infer_downstream_implications(all_changes, None)

            # Build impact record
            impact = build_impact_record(
                node_id, commit_info, matched_files,
                all_changes, combined_event_type, implications
            )

            # Create version_event with full impact analysis
            cur.execute("""
                INSERT INTO meta.version_event
                    (version_id, event_type, event_detail, actor)
                VALUES (%s, %s, %s, %s)
                RETURNING event_id
            """, (
                version_id,
                combined_event_type,
                json.dumps(impact),
                commit_info["author"],
            ))
            event_id = str(cur.fetchone()[0])
            direct_created += 1

            # Populate caused_by on the node_version (if column exists)
            # Links the version to the commit that changed it
            try:
                cur.execute("""
                    UPDATE meta.node_version
                    SET caused_by = %s, caused_by_type = 'commit'
                    WHERE version_id = %s
                      AND caused_by IS NULL
                """, (commit_info["sha"], version_id))
            except Exception:
                pass  # Column may not exist yet (migration not applied)

            # Propagate downstream staleness if the change is functional
            if combined_event_type != "code_change:annotation":
                change_summary = f"{combined_event_type.split(':')[1]} change in {len(matched_files)} file(s)"
                ds_count = propagate_staleness(
                    conn, node_id, event_id, change_summary, commit_info
                )
                downstream_created += ds_count

    return direct_created, downstream_created


def main():
    commit_info = get_commit_info()
    if not commit_info:
        print("[auto_capture] Could not get commit info, skipping", file=sys.stderr)
        return

    if not commit_info["changed_files"]:
        return

    db_url = get_database_url()
    try:
        conn = psycopg.connect(db_url)
    except Exception as e:
        print(f"[auto_capture] DB connection failed: {e}", file=sys.stderr)
        return

    try:
        # Check if meta schema exists
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'meta' AND table_name = 'version_event'
                )
            """)
            if not cur.fetchone()[0]:
                print("[auto_capture] meta.version_event table not found, skipping", file=sys.stderr)
                return

        # Match files to nodes
        node_files_map = match_file_to_nodes(conn, commit_info["changed_files"])
        if not node_files_map:
            print(f"[auto_capture] No pipeline nodes matched for {len(commit_info['changed_files'])} files", file=sys.stderr)
            return

        # Create events with impact analysis + downstream propagation
        direct, downstream = create_version_events(conn, commit_info, node_files_map)
        conn.commit()
        print(
            f"[auto_capture] Commit {commit_info['sha'][:7]}: "
            f"{direct} direct event(s), {downstream} downstream staleness event(s)",
            file=sys.stderr
        )

    except Exception as e:
        print(f"[auto_capture] Error: {e}", file=sys.stderr)
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
