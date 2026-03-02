#!/usr/bin/env python3
"""
Genesis Import: Pre-Epoch Memory
=================================
Mines the repo for historical evidence and reconstructs pre-epoch records
in the meta schema. All records tagged with source='pre_epoch_reconstruction'.

Sources:
1. Git log — commits referencing experiments, parameter changes
2. CLAUDE.md — corrections log, pathfinding entries
3. Code comments — "why" explanations
4. Sweep scripts — parameter ranges, configs

Run: python3 genesis_import.py [--database-url URL] [--dry-run]
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

try:
    import psycopg
except ImportError:
    print("psycopg required: pip install psycopg[binary]", file=sys.stderr)
    sys.exit(1)


def get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    if os.path.isdir("/app"):
        return "postgresql://trading:trading@postgres:5432/trading"
    return "postgresql://trading:trading@localhost:5432/trading"


def parse_corrections_log(claude_md_path):
    """Parse CLAUDE.md corrections log into structured entries."""
    corrections = []
    try:
        with open(claude_md_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        return corrections

    # Find corrections log section
    section_match = re.search(
        r"#\s*5\)\s*Corrections Log.*?\n(.*?)(?=\n#\s*\d|\Z)",
        content, re.DOTALL
    )
    if not section_match:
        return corrections

    section = section_match.group(1)

    # Parse each entry: ### date — title
    entries = re.findall(
        r"###\s*([\d-]+)\s*—\s*(.*?)\n(.*?)(?=###|\Z)",
        section, re.DOTALL
    )

    for date_str, title, body in entries:
        # Extract rule
        rule_match = re.search(r"\*\*Rule:\*\*\s*(.*?)(?:\n\n|\n\*\*|$)", body, re.DOTALL)
        rule = rule_match.group(1).strip() if rule_match else title.strip()

        is_pathfinding = "[PATHFINDING]" in title

        corrections.append({
            "date": date_str.strip(),
            "title": title.strip().replace("[PATHFINDING]", "").strip(),
            "rule": rule,
            "is_pathfinding": is_pathfinding,
            "body": body.strip()[:2000],
        })

    return corrections


def parse_git_log(repo_path, since="2026-01-01"):
    """Extract meaningful commits from git log."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log",
             f"--since={since}", "--format=%H|%an|%aI|%s",
             "--no-merges"],
            capture_output=True, text=True, timeout=30,
        )
        lines = result.stdout.strip().split("\n")
    except Exception:
        return []

    commits = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append({
            "sha": parts[0],
            "author": parts[1],
            "date": parts[2],
            "message": parts[3],
        })

    return commits


def classify_commit(message):
    """Classify commit message into event type and confidence."""
    msg_lower = message.lower()

    if any(kw in msg_lower for kw in ["sweep", "parameter", "param", "config"]):
        return "optimisation_started", "medium"
    if any(kw in msg_lower for kw in ["backtest", "simulation", "sim_"]):
        return "backtest", "medium"
    if any(kw in msg_lower for kw in ["fix", "bug", "patch", "hotfix"]):
        return "code_change", "high"
    if any(kw in msg_lower for kw in ["add", "implement", "new", "create", "feature"]):
        return "code_change", "high"
    if any(kw in msg_lower for kw in ["refactor", "clean", "rename", "move"]):
        return "code_change", "low"

    return "code_change", "low"


def import_corrections(conn, corrections, dry_run=False):
    """Import corrections into meta.correction table."""
    imported = 0
    with conn.cursor() as cur:
        for c in corrections:
            if dry_run:
                print(f"  [DRY] correction: {c['date']} — {c['title'][:60]}")
                imported += 1
                continue

            cur.execute("""
                INSERT INTO meta.correction
                    (discovered_at, trigger_event, rule, scope, active, metadata)
                VALUES (%s, %s, %s, %s, true, %s)
                ON CONFLICT DO NOTHING
            """, (
                f"{c['date']}T00:00:00Z",
                c["title"],
                c["rule"],
                ["general"],
                json.dumps({
                    "genesis_confidence": "high",
                    "source": "pre_epoch_reconstruction",
                    "is_pathfinding": c["is_pathfinding"],
                }),
            ))
            imported += cur.rowcount

    return imported


def import_experiments_from_commits(conn, commits, dry_run=False):
    """Create experiment records from significant commits."""
    imported = 0
    with conn.cursor() as cur:
        # Group commits by date+type to avoid creating too many experiments
        seen_messages = set()
        for commit in commits:
            event_type, confidence = classify_commit(commit["message"])
            if confidence == "low":
                continue

            # Deduplicate similar messages
            msg_key = commit["message"][:50].lower()
            if msg_key in seen_messages:
                continue
            seen_messages.add(msg_key)

            if dry_run:
                print(f"  [DRY] commit event: {commit['sha'][:7]} — {commit['message'][:60]}")
                imported += 1
                continue

            # We don't create experiments for every commit, just log introspection events
            cur.execute("""
                INSERT INTO meta.introspection_event
                    (event_type, entity_type, entity_id, actor, payload)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                f"genesis_{event_type}",
                "commit",
                str(uuid4()),
                "genesis_import",
                json.dumps({
                    "source": "pre_epoch_reconstruction",
                    "genesis_confidence": confidence,
                    "commit_sha": commit["sha"],
                    "commit_message": commit["message"][:500],
                    "commit_author": commit["author"],
                    "commit_date": commit["date"],
                }),
            ))
            imported += cur.rowcount

    return imported


def main():
    dry_run = "--dry-run" in sys.argv

    # Resolve repo path — handle Docker (/scripts/genesis_import.py → /app or /srv/mcas)
    # and host (/home/darren/Desktop/MCAS/Backend/scripts/genesis_import.py)
    script_path = os.path.abspath(__file__)
    if os.path.isdir("/app"):
        # Inside Docker container — repo root is mounted at /app or we look for CLAUDE.md
        repo_path = "/app" if os.path.exists("/app/CLAUDE.md") else os.path.dirname(os.path.dirname(script_path))
    else:
        # Host — go up from Backend/scripts/ to repo root
        repo_path = os.path.dirname(os.path.dirname(os.path.dirname(script_path)))
    claude_md = os.path.join(repo_path, "CLAUDE.md")

    print(f"Genesis Import {'(DRY RUN)' if dry_run else ''}")
    print(f"  Repo: {repo_path}")
    print(f"  CLAUDE.md: {claude_md}")
    print()

    # 1. Parse corrections log
    corrections = parse_corrections_log(claude_md)
    print(f"Found {len(corrections)} corrections in CLAUDE.md")

    # 2. Parse git log
    commits = parse_git_log(repo_path)
    print(f"Found {len(commits)} commits since 2026-01-01")

    if dry_run:
        print("\n--- Corrections ---")
        for c in corrections:
            print(f"  {c['date']} — {c['title'][:60]}")
        print(f"\n--- Commits (significant only) ---")
        for commit in commits[:20]:
            event_type, conf = classify_commit(commit["message"])
            if conf != "low":
                print(f"  {commit['sha'][:7]} [{event_type}/{conf}] {commit['message'][:60]}")
        print("\nDry run complete. Re-run without --dry-run to import.")
        return

    # Connect to DB
    db_url = get_database_url()
    conn = psycopg.connect(db_url)

    try:
        # Check meta schema exists
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'meta' AND table_name = 'correction'
                )
            """)
            if not cur.fetchone()[0]:
                print("ERROR: meta.correction table not found. Run migrations first.")
                return

        # Import corrections
        n_corrections = import_corrections(conn, corrections)
        print(f"Imported {n_corrections} corrections")

        # Import commit events
        n_commits = import_experiments_from_commits(conn, commits)
        print(f"Imported {n_commits} commit-derived events")

        # Insert epoch marker event
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meta.introspection_event
                    (event_type, entity_type, entity_id, actor, payload)
                VALUES ('genesis_complete', 'system', %s, 'genesis_import', %s)
            """, (
                str(uuid4()),
                json.dumps({
                    "corrections_imported": n_corrections,
                    "commit_events_imported": n_commits,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
            ))

        conn.commit()
        print("\nGenesis import complete.")

    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
