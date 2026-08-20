"""CLI entrypoint for pipeline orchestration and local operations."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from src.config.logging_config import setup_logging
from src.config.settings import SETTINGS
from src.database.db import init_db
from src.pipeline.ingest_pipeline import run_ingestion
from src.pipeline.process_pipeline import run_processing
from scripts.bake_dashboard_data import run_bake
from src.pipeline.extract_core import run_core_extraction
from src.pipeline.ingest_core import run_core_ingestion
from src.pipeline.process_core import run_core_processing
from src.pipeline.resolve_core import run_core_resolution
from src.pipeline.review_core import (
    format_review_queue,
    run_export_review_labels,
    run_manual_merge,
    run_manual_split,
    run_review_decision,
)
from src.pipeline.retract_mojibake import run_retract_mojibake
from src.pipeline.translate_core import run_core_translation
from src.pipeline.run_pipeline import run_full_pipeline
from src.store.blob import get_blob_store
from src.store.database import get_core_session, init_core_db
from src.store.provenance import format_provenance_chain, get_provenance_chain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arabic OSINT Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database schema via SQLAlchemy ORM")
    sub.add_parser("ingest", help="Run source scraping and load raw_articles")
    sub.add_parser("process", help="Process raw_articles into processed_articles")
    sub.add_parser("run-pipeline", help="Run ingestion + processing")
    sub.add_parser("dashboard", help="Launch Streamlit dashboard")

    sub.add_parser("init-core-db", help="Create the entity-resolution core schema (M1)")
    sub.add_parser("process-core", help="Run topic/escalation/country classifiers over core-schema documents (M1.5)")
    ingest_core_parser = sub.add_parser("ingest-core", help="Scrape sources and write documents to the core schema (M1.5)")
    ingest_core_parser.add_argument("--limit-per-source", type=int, default=None)

    extract_parser = sub.add_parser("extract-core", help="Extract entity mentions from core-schema documents (M3)")
    extract_parser.add_argument("--limit", type=int, default=500)

    resolve_parser = sub.add_parser("resolve-core", help="Resolve mentions into entities (M4)")
    resolve_parser.add_argument("--max-block-size", type=int, default=100)
    resolve_parser.add_argument(
        "--review-margin",
        type=float,
        default=0.15,
        help="Queue pairs within this distance of the scorer threshold",
    )

    review_parser = sub.add_parser("review", help="Human review and manual resolution controls (M4)")
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)

    review_list = review_sub.add_parser("list", help="List scored pairs awaiting or carrying decisions")
    review_list.add_argument(
        "--status", choices=["pending", "accepted", "rejected", "all"], default="pending"
    )
    review_list.add_argument("--limit", type=int, default=20)

    review_decide = review_sub.add_parser("decide", help="Accept or reject a queued pair")
    review_decide.add_argument("review_pair_id", type=int)
    review_decide.add_argument("decision", choices=["accept", "reject"])
    review_decide.add_argument("--reviewer", required=True)
    review_decide.add_argument("--reason")

    review_merge = review_sub.add_parser("merge", help="Manually merge two live entities")
    review_merge.add_argument("left_entity_id", type=int)
    review_merge.add_argument("right_entity_id", type=int)
    review_merge.add_argument("--reviewer", required=True)
    review_merge.add_argument("--reason")

    review_split = review_sub.add_parser(
        "split", help="Keep two mentions in a live entity in separate clusters"
    )
    review_split.add_argument("entity_id", type=int)
    review_split.add_argument("left_mention_id", type=int)
    review_split.add_argument("right_mention_id", type=int)
    review_split.add_argument("--reviewer", required=True)
    review_split.add_argument("--reason")

    review_export = review_sub.add_parser(
        "export-labels", help="Export decided production pairs for scorer retraining"
    )
    review_export.add_argument("--out", type=Path, required=True)

    translate_parser = sub.add_parser("translate-core", help="Translate recent document titles AR->EN via DeepL (M5)")
    translate_parser.add_argument("--document-limit", type=int, default=200)
    translate_parser.add_argument("--max-new", type=int, default=None, help="Cap uncached strings sent to the API this run")

    mojibake_parser = sub.add_parser("retract-mojibake", help="Retract documents stored with a broken character encoding")
    mojibake_parser.add_argument("--apply", action="store_true", help="Actually retract; omit for a dry run")

    bake_parser = sub.add_parser("bake-dashboard", help="Write a static data.json snapshot for the Pages dashboard (M1.5)")
    bake_parser.add_argument("--out", type=str, default="dist/data.json")
    bake_parser.add_argument("--recent-limit", type=int, default=30)

    provenance_parser = sub.add_parser("provenance", help="Inspect provenance chains")
    provenance_sub = provenance_parser.add_subparsers(dest="provenance_command", required=True)
    show_parser = provenance_sub.add_parser("show", help="Show the provenance chain for a row")
    show_parser.add_argument(
        "table",
        choices=[
            "mentions",
            "entities",
            "links",
            "facts",
            "review_pairs",
            "resolution_decisions",
        ],
    )
    show_parser.add_argument("id", type=int)

    return parser


def main() -> None:
    setup_logging(SETTINGS.log_level)
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
    elif args.command == "ingest":
        print(run_ingestion())
    elif args.command == "process":
        print(run_processing())
    elif args.command == "run-pipeline":
        print(run_full_pipeline())
    elif args.command == "init-core-db":
        init_core_db()
        print("Core schema created.")
    elif args.command == "process-core":
        stats = run_core_processing()
        print(f"scanned={stats.scanned} processed={stats.processed} errors={stats.errors}")
    elif args.command == "ingest-core":
        stats = run_core_ingestion(limit_per_source=args.limit_per_source)
        print(f"attempted={stats.attempted} inserted={stats.inserted} skipped_existing={stats.skipped_existing}")
        for source, info in stats.sources.items():
            print(f"  {source}: {info}")
    elif args.command == "extract-core":
        stats = run_core_extraction(limit=args.limit)
        print(
            f"scanned={stats.documents_scanned} processed={stats.documents_processed} "
            f"mentions={stats.mentions_written} errors={stats.errors}"
        )
    elif args.command == "resolve-core":
        stats = run_core_resolution(
            max_block_size=args.max_block_size, review_margin=args.review_margin
        )
        print(
            f"mentions={stats.mentions} distinct_forms={stats.exact_duplicate_groups} "
            f"candidate_pairs={stats.candidate_pairs} "
            f"(reduction {stats.reduction_ratio:.4f}) matched={stats.matched_pairs} "
            f"entities={stats.entities_created} retracted={stats.entities_retracted} "
            f"split={stats.giant_components_split} queued={stats.review_pairs_queued} "
            f"human_constraints={stats.human_constraints_applied}"
        )
        print(f"cluster sizes: {stats.size_histogram}")
    elif args.command == "review" and args.review_command == "list":
        print(format_review_queue(status=args.status, limit=args.limit))
    elif args.command == "review" and args.review_command == "decide":
        decision_id = run_review_decision(
            args.review_pair_id,
            accept=args.decision == "accept",
            reviewer=args.reviewer,
            reason=args.reason,
        )
        print(f"recorded resolution decision {decision_id}; run resolve-core to apply it")
    elif args.command == "review" and args.review_command == "merge":
        decision_id = run_manual_merge(
            args.left_entity_id,
            args.right_entity_id,
            reviewer=args.reviewer,
            reason=args.reason,
        )
        print(f"recorded manual merge decision {decision_id}; run resolve-core to apply it")
    elif args.command == "review" and args.review_command == "split":
        decision_id = run_manual_split(
            args.entity_id,
            args.left_mention_id,
            args.right_mention_id,
            reviewer=args.reviewer,
            reason=args.reason,
        )
        print(f"recorded manual split decision {decision_id}; run resolve-core to apply it")
    elif args.command == "review" and args.review_command == "export-labels":
        count = run_export_review_labels(args.out)
        print(f"wrote {count} reviewed pairs to {args.out}")
    elif args.command == "translate-core":
        stats = run_core_translation(document_limit=args.document_limit, max_new=args.max_new)
        print(
            f"titles_seen={stats.titles_seen} already_cached={stats.already_cached} "
            f"newly_translated={stats.newly_translated}"
        )
    elif args.command == "retract-mojibake":
        stats = run_retract_mojibake(dry_run=not args.apply)
        mode = "RETRACTED" if args.apply else "DRY RUN (use --apply to retract)"
        print(f"{mode}: scanned={stats.scanned} corrupted={stats.retracted} ids={stats.document_ids}")
    elif args.command == "bake-dashboard":
        data = run_bake(Path(args.out), recent_limit=args.recent_limit)
        print(f"wrote {args.out}: total_raw={data['stats']['total_raw']} total_processed={data['stats']['total_processed']} recent={len(data['recent'])}")
    elif args.command == "provenance" and args.provenance_command == "show":
        blob_store = get_blob_store()
        with get_core_session() as session:
            entries = get_provenance_chain(session, args.table, args.id, blob_store)
            print(format_provenance_chain(entries))
    elif args.command == "dashboard":
        project_root = Path(__file__).resolve().parent
        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{project_root}{os.pathsep}{current_pythonpath}"
            if current_pythonpath
            else str(project_root)
        )
        subprocess.run(
            [
                "streamlit",
                "run",
                "src/dashboard/app.py",
                "--server.address=0.0.0.0",
                "--server.port=8501",
            ],
            check=True,
            cwd=project_root,
            env=env,
        )


if __name__ == "__main__":
    main()
