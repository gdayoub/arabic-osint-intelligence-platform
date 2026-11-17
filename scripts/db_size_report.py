"""Read-only report of database storage: total size and the largest tables.

Neon's free tier caps a project at 1 GB. When the scheduled pipeline starts
failing with ``DiskFull``, run this (Maintenance workflow -> db-size-report)
to see which tables are growing before deciding what to prune.
"""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, text

TABLE_SIZES = text(
    """
    SELECT c.relname                                   AS table_name,
           c.reltuples::bigint                         AS approx_rows,
           pg_total_relation_size(c.oid)               AS total_bytes,
           pg_relation_size(c.oid)                     AS heap_bytes,
           pg_indexes_size(c.oid)                      AS index_bytes,
           COALESCE(s.n_dead_tup, 0)                   AS dead_rows
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY pg_total_relation_size(c.oid) DESC
    LIMIT :limit
    """
)


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    url = args.database_url or os.getenv("DATABASE_URL")
    if not url:
        parser.error("set DATABASE_URL or pass --database-url")

    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        db_bytes = conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        rows = conn.execute(TABLE_SIZES, {"limit": args.limit}).mappings().all()
        # Entity resolution rebuilds every run and retracts the previous
        # generation; show how much evidence belongs to live vs retracted rows.
        evidence = conn.execute(
            text(
                """
                SELECT e.retracted,
                       COUNT(DISTINCT e.id) AS entities,
                       COUNT(em.mention_id) AS entity_mentions,
                       (SELECT COUNT(*) FROM provenance p
                         JOIN entities e2 ON p.target_table = 'entities' AND p.target_id = e2.id
                        WHERE e2.retracted = e.retracted) AS provenance_rows
                FROM entities e
                LEFT JOIN entity_mentions em ON em.entity_id = e.id
                GROUP BY e.retracted
                """
            )
        ).mappings().all()
        stage_timings = conn.execute(
            text(
                """
                WITH recent AS (
                    SELECT run_id, MAX(occurred_at) AS last_at FROM pipeline_events
                    WHERE stage IS NOT NULL GROUP BY run_id ORDER BY last_at DESC LIMIT 4
                )
                SELECT e.run_id, e.stage,
                       MIN(e.occurred_at) FILTER (WHERE e.event_type = 'stage_started') AS started,
                       MAX(e.occurred_at) FILTER (WHERE e.event_type IN ('stage_succeeded','stage_failed')) AS ended,
                       MAX(e.event_type) FILTER (WHERE e.event_type IN ('stage_succeeded','stage_failed')) AS outcome
                FROM pipeline_events e JOIN recent r USING (run_id)
                WHERE e.stage IS NOT NULL AND e.source IS NULL
                GROUP BY e.run_id, e.stage
                ORDER BY e.run_id, started
                """
            )
        ).mappings().all()
        provenance_by_target = conn.execute(
            text("SELECT target_table, COUNT(*) AS n FROM provenance GROUP BY 1 ORDER BY 2 DESC")
        ).mappings().all()

    print(f"database size: {_mb(db_bytes)}\n")
    print(f"{'table':<36}{'rows':>12}{'total':>12}{'heap':>12}{'indexes':>12}{'dead rows':>12}")
    for r in rows:
        print(
            f"{r['table_name']:<36}{r['approx_rows']:>12,}{_mb(r['total_bytes']):>12}"
            f"{_mb(r['heap_bytes']):>12}{_mb(r['index_bytes']):>12}{r['dead_rows']:>12,}"
        )
    print("\nentity evidence by retraction state:")
    for r in evidence:
        state = "retracted" if r["retracted"] else "live"
        print(
            f"  {state:<10} entities={r['entities']:,} entity_mentions={r['entity_mentions']:,} "
            f"provenance={r['provenance_rows']:,}"
        )
    print("\nstage timings, last 4 runs with stage events:")
    for r in stage_timings:
        took = (
            f"{(r['ended'] - r['started']).total_seconds() / 60:6.1f} min"
            if r["started"] and r["ended"] else "   (no end)"
        )
        print(f"  {r['run_id']:<28}{r['stage']:<24}{took}  {r['outcome'] or 'unfinished'}  start={r['started']}")
    print("\nprovenance rows by target table:")
    for r in provenance_by_target:
        print(f"  {r['target_table']:<24}{r['n']:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
