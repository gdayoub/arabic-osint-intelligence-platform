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

    print(f"database size: {_mb(db_bytes)}\n")
    print(f"{'table':<36}{'rows':>12}{'total':>12}{'heap':>12}{'indexes':>12}{'dead rows':>12}")
    for r in rows:
        print(
            f"{r['table_name']:<36}{r['approx_rows']:>12,}{_mb(r['total_bytes']):>12}"
            f"{_mb(r['heap_bytes']):>12}{_mb(r['index_bytes']):>12}{r['dead_rows']:>12,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
