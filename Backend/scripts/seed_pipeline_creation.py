#!/usr/bin/env python3
"""
Seed: Pipeline Creation Process
================================
Creates a "Pipeline Creation Process" pipeline in the meta schema,
demonstrating how the pipeline dev process works. Self-documenting template.

Nodes: requirement_capture → exploration → plan_design → implementation →
       layout_tuning → deploy_cf_pages → production_verify → lessons_learned

Run: python3 seed_pipeline_creation.py [--database-url URL]
"""

import json
import os
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


PIPELINE_NODES = [
    {
        "name": "Requirement Capture",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Gather user needs, constraints, and success criteria for the pipeline feature.",
        "owner": "darren",
    },
    {
        "name": "Exploration",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Research existing code, data model, and API surface. Identify reusable components.",
        "owner": "claude_code",
    },
    {
        "name": "Plan Design",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Write detailed implementation plan. Schema, endpoints, components, file changes.",
        "owner": "claude_code",
    },
    {
        "name": "Implementation",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Write code. Migrations, API routes, React components, hooks.",
        "owner": "claude_code",
    },
    {
        "name": "Layout Tuning",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Use layout-tuner.cjs to sweep node dimensions. Optimize for TV Fit View.",
        "owner": "darren",
    },
    {
        "name": "Deploy (CF Pages)",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Push to main → CF Pages auto-deploy. Backend: docker compose build + up.",
        "owner": "claude_code",
    },
    {
        "name": "Production Verify",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Verify at mcasui.com/pipeline. Check TV Fit View. Test interactions.",
        "owner": "darren",
    },
    {
        "name": "Lessons Learned",
        "node_type": "custom",
        "subsystem": "meta",
        "description": "Document what worked, what didn't. Add to LESSONS_LEARNED.md.",
        "owner": "darren",
    },
]


def main():
    db_url = get_database_url()
    conn = psycopg.connect(db_url)

    try:
        with conn.cursor() as cur:
            # Create pipeline
            cur.execute("""
                INSERT INTO meta.pipeline (name, description, config)
                VALUES (%s, %s, %s)
                RETURNING pipeline_id
            """, (
                "Pipeline Creation Process",
                "How MCAS pipeline features are designed, built, and shipped. Self-documenting template.",
                json.dumps({"template": True, "created_by": "seed_pipeline_creation.py"}),
            ))
            pipeline_id = str(cur.fetchone()[0])
            print(f"Created pipeline: {pipeline_id}")

            # Create nodes
            node_ids = []
            for i, node_def in enumerate(PIPELINE_NODES):
                cur.execute("""
                    INSERT INTO meta.node (name, node_type, subsystem, description, owner)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING node_id
                """, (
                    node_def["name"], node_def["node_type"],
                    node_def["subsystem"], node_def["description"],
                    node_def["owner"],
                ))
                nid = str(cur.fetchone()[0])
                node_ids.append(nid)

                # Link to pipeline
                cur.execute("""
                    INSERT INTO meta.pipeline_node (pipeline_id, node_id, ordinal)
                    VALUES (%s, %s, %s)
                """, (pipeline_id, nid, i))

                # Create initial version
                cur.execute("""
                    INSERT INTO meta.node_version (node_id, version_number, status, change_reason)
                    VALUES (%s, 1, 'deployed', 'Initial seed')
                    RETURNING version_id
                """, (nid,))
                vid = str(cur.fetchone()[0])

                # Create ports
                if i > 0:
                    cur.execute("""
                        INSERT INTO meta.port (node_id, name, direction, data_type)
                        VALUES (%s, 'input', 'input', 'artifact')
                        RETURNING port_id
                    """, (nid,))
                if i < len(PIPELINE_NODES) - 1:
                    cur.execute("""
                        INSERT INTO meta.port (node_id, name, direction, data_type)
                        VALUES (%s, 'output', 'output', 'artifact')
                        RETURNING port_id
                    """, (nid,))

                print(f"  Node {i+1}/{len(PIPELINE_NODES)}: {node_def['name']} ({nid[:8]}...)")

            # Create edges (linear chain)
            for i in range(len(node_ids) - 1):
                # Get output port of source
                cur.execute("""
                    SELECT port_id FROM meta.port
                    WHERE node_id = %s AND direction = 'output'
                    LIMIT 1
                """, (node_ids[i],))
                src_port = cur.fetchone()

                # Get input port of target
                cur.execute("""
                    SELECT port_id FROM meta.port
                    WHERE node_id = %s AND direction = 'input'
                    LIMIT 1
                """, (node_ids[i + 1],))
                tgt_port = cur.fetchone()

                if src_port and tgt_port:
                    cur.execute("""
                        INSERT INTO meta.edge (source_port_id, target_port_id)
                        VALUES (%s, %s)
                    """, (str(src_port[0]), str(tgt_port[0])))

            # Create version events for demonstration
            events_to_create = [
                (0, "decision_made", "darren", {"summary": "Pipeline page needs to surface everything. User can't read the codebase — this page must be the window."}),
                (1, "code_change", "claude_code", {"summary": "Explored 18+ meta tables, 15 API endpoints, React Flow setup. Full codebase research."}),
                (2, "decision_made", "claude_code", {"summary": "7-phase plan: schema first, visual states, toolbar, detail panel, pressure wave, chat, multi-pipeline."}),
                (3, "code_change", "claude_code", {"summary": "9 new files, 7 modified. Migration + API + hooks + components in single session."}),
                (4, "manual_observation", "darren", {"summary": "layout-tuner.cjs achieved 99% viewport fill for 16:9. Sweep > manual tuning."}),
                (5, "script_run", "claude_code", {"summary": "Push to main → CF Pages auto-deploy. Backend docker compose build + up."}),
                (6, "manual_observation", "darren", {"summary": "Verified on TV. Fit View readable. Click interactions working."}),
                (7, "conversation_note", "darren", {"summary": "Winning process: plan → parallel build → visual verify → correct → ship."}),
            ]

            for node_idx, event_type, actor, detail in events_to_create:
                # Get version for this node
                cur.execute("""
                    SELECT version_id FROM meta.node_version
                    WHERE node_id = %s LIMIT 1
                """, (node_ids[node_idx],))
                vid = cur.fetchone()
                if vid:
                    cur.execute("""
                        INSERT INTO meta.version_event
                            (version_id, event_type, event_detail, actor)
                        VALUES (%s, %s, %s, %s)
                    """, (str(vid[0]), event_type, json.dumps(detail), actor))

        conn.commit()
        print(f"\nSeeded 'Pipeline Creation Process' pipeline with {len(PIPELINE_NODES)} nodes and {len(events_to_create)} events.")

    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
